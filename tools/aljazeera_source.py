#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Al Jazeera Network official public schedules -> XMLTV.

Primary source: https://www.aljazeera.net/video/live and its official schedule pages.
The adapter uses only publicly rendered pages and does not bypass authentication,
geo controls, DRM, or anti-bot protections.

Five receiver IDs are always retained so EPGManager can map them even when an
individual public schedule is temporarily unavailable:
- AlJazeera.qa@Arabic
- AlJazeera2.qa@HD
- AlJazeeraMubasher.qa@SD
- AlJazeeraDocumentary.qa@SD
- AlJazeera.qa@English
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.aljazeera.net"
DOHA = ZoneInfo("Asia/Qatar")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
NOISE = {
    "البث الحي", "جدول البث", "جدول البث الحي", "كل الأوقات بتوقيت مكة",
    "أمس", "اليوم", "غداً", "غدا", "يعرض الآن", "التالي", "استمع",
    "قناة الجزيرة", "الجزيرة 2", "الجزيرة مباشر", "الجزيرة الوثائقية",
    "اضغط هنا", "اعرض المزيد",
}


@dataclass(frozen=True)
class ChannelSpec:
    xmltv_id: str
    display_name: str
    live_path: str
    schedule_path: str | None = None


CHANNELS = (
    ChannelSpec("AlJazeera.qa@Arabic", "Al Jazeera", "/video/live", "/schedule"),
    ChannelSpec("AlJazeera2.qa@HD", "Al Jazeera 2", "/video/live/الجزيرة-2", "/schedule-aj2"),
    ChannelSpec("AlJazeeraMubasher.qa@SD", "Al Jazeera Mubasher", "/video/live/الجزيرة-مباشر"),
    ChannelSpec("AlJazeeraDocumentary.qa@SD", "Al Jazeera Documentary", "/video/live/الجزيرة-الوثائقية"),
    ChannelSpec("AlJazeera.qa@English", "Al Jazeera English", "https://www.aljazeera.com/video/live/", "https://www.aljazeera.com/schedule"),
)

GRAPHQL_URL = "https://www.aljazeera.com/graphql"
# The maintained public guide uses ArchipelagoSchedulePageQuery on
# aljazeera.com. Keep the rendered aljazeera.net HTML parser as fallback.
GRAPHQL_PROFILES = {
    "AlJazeera.qa@Arabic": ("aja", "schedule"),
    "AlJazeera2.qa@HD": ("aja", "schedule-aj2"),
    "AlJazeera.qa@English": ("aje", "schedule"),
}


def headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-QA,ar;q=0.9,en;q=0.5",
        "Referer": BASE + "/",
        "Cache-Control": "no-cache",
    }


def fetch(session: requests.Session, url: str, timeout: int) -> tuple[str, int]:
    r = session.get(url, headers=headers(), timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text, int(r.status_code)


def norm_text(value: str) -> str:
    return " ".join((value or "").replace("\u200f", " ").replace("\u200e", " ").split())


def is_noise(value: str) -> bool:
    s = norm_text(value)
    if not s:
        return True
    if s in NOISE:
        return True
    if s.startswith("كل الأوقات بتوقيت"):
        return True
    if s.startswith("قد يحتوي هذا الفيديو"):
        return True
    if s.startswith("اذا واجهتك مشكلة") or s.startswith("إذا واجهتك مشكلة"):
        return True
    if re.fullmatch(r"\d{1,2}", s) or s == "/":
        return True
    return False


def discover_schedule_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        label = norm_text(" ".join(a.stripped_strings))
        href = urljoin(base_url, str(a.get("href") or ""))
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc not in {"www.aljazeera.net", "aljazeera.net", "www.ajnet.me", "ajnet.me", "www.aljazeera.com", "aljazeera.com", "www.aljazeera.com", "aljazeera.com"}:
            continue
        if "schedule" in parsed.path.casefold() or "جدول" in label:
            if href not in out:
                out.append(href)
    return out


def discover_day_links(html: str, base_url: str, today: date) -> list[tuple[str, date]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, date]] = []
    labels = {"أمس": -1, "اليوم": 0, "غداً": 1, "غدا": 1}
    for a in soup.find_all("a", href=True):
        label = norm_text(" ".join(a.stripped_strings))
        off = None
        for marker, delta in labels.items():
            if marker in label:
                off = delta
                break
        if off is None:
            continue
        href = urljoin(base_url, str(a.get("href") or ""))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc not in {"www.aljazeera.net", "aljazeera.net", "www.ajnet.me", "ajnet.me"}:
            continue
        pair = (href, today + timedelta(days=off))
        if pair not in out:
            out.append(pair)
    return out


def schedule_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [norm_text(x) for x in soup.stripped_strings if norm_text(x)]


def parse_schedule(html: str, day: date) -> list[dict]:
    lines = schedule_lines(html)
    rows: list[dict] = []
    i = 0
    while i < len(lines):
        if not TIME_RE.match(lines[i]):
            i += 1
            continue
        clock = lines[i]
        j = i + 1
        while j < len(lines) and (is_noise(lines[j]) or TIME_RE.match(lines[j])):
            if TIME_RE.match(lines[j]):
                break
            j += 1
        if j >= len(lines) or TIME_RE.match(lines[j]):
            i += 1
            continue
        title = lines[j]
        if len(title) > 180 or title.startswith("http"):
            i += 1
            continue
        desc_parts: list[str] = []
        k = j + 1
        while k < len(lines) and not TIME_RE.match(lines[k]):
            s = lines[k]
            if not is_noise(s) and not s.startswith("http") and len(s) <= 1200:
                if s in {"تقارير إخبارية", "الجزيرة الإخبارية", "الجزيرة 360"}:
                    break
                desc_parts.append(s)
                if sum(len(x) for x in desc_parts) >= 1600:
                    break
            k += 1
        hh, mm = map(int, clock.split(":"))
        start = datetime.combine(day, time(hh, mm), tzinfo=DOHA)
        rows.append({
            "start": start,
            "title": title,
            "desc": norm_text(" ".join(desc_parts))[:2000],
        })
        i = max(k, i + 1)

    dedup: list[dict] = []
    seen = set()
    for row in rows:
        key = (row["start"], row["title"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(row)
    dedup.sort(key=lambda r: r["start"])
    for idx, row in enumerate(dedup):
        nxt = dedup[idx + 1]["start"] if idx + 1 < len(dedup) else None
        row["stop"] = nxt if nxt and nxt > row["start"] else row["start"] + timedelta(hours=1)
    return dedup


def _duration_delta(value: object) -> timedelta | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, m, s = 0, nums[0], nums[1]
    else:
        return None
    if h < 0 or m < 0 or s < 0:
        return None
    return timedelta(hours=h, minutes=m, seconds=s)


def parse_graphql_schedule(payload: object, default_day: date) -> list[dict]:
    """Extract SchedulePageQuery rows without depending on GraphQL nesting."""
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            clock = norm_text(str(node.get("showTimeslot") or ""))
            title = norm_text(str(node.get("showName") or ""))
            if TIME_RE.match(clock) and title:
                day = default_day
                raw_stamp = str(node.get("startDate") or "").strip()
                if raw_stamp.isdigit():
                    try:
                        stamp = int(raw_stamp)
                        if stamp > 10_000_000_000:
                            stamp //= 1000
                        # showTimeslot from this official GraphQL operation
                        # is UTC (not Doha local). Decode startDate to the
                        # SAME clock before combining the UTC time-of-day:
                        # converting the date to Doha first can shift late
                        # 23:xx UTC shows onto the wrong broadcast day.
                        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
                    except (OverflowError, OSError, ValueError):
                        pass
                hh, mm = map(int, clock.split(":"))
                # Current aljazeera.com ArchipelagoSchedulePageQuery exposes
                # showTimeslot as UTC; mirror the maintained iptv-org adapter.
                start = datetime.combine(day, time(hh, mm), tzinfo=timezone.utc)
                found.append({
                    "start": start,
                    "title": title,
                    "desc": norm_text(str(node.get("showDescription") or ""))[:2000],
                    "duration": _duration_delta(node.get("duration")),
                })
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)

    rows: list[dict] = []
    seen = set()
    for row in sorted(found, key=lambda x: (x["start"], x["title"])):
        key = (row["start"], row["title"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    for idx, row in enumerate(rows):
        nxt = rows[idx + 1]["start"] if idx + 1 < len(rows) else None
        duration = row.pop("duration", None)
        if duration and duration.total_seconds() > 0:
            stop = row["start"] + duration
        elif nxt and nxt > row["start"]:
            stop = nxt
        else:
            stop = row["start"] + timedelta(hours=1)
        if nxt and nxt > row["start"] and stop > nxt:
            stop = nxt
        row["stop"] = stop
    return rows


def fetch_graphql_schedule(
    session: requests.Session, wp_site: str, post_name: str, timeout: int, default_day: date
) -> tuple[list[dict], int, str]:
    params = {
        "wp-site": wp_site,
        "operationName": "ArchipelagoSchedulePageQuery",
        "variables": json.dumps({"postName": post_name, "preview": ""}, ensure_ascii=False, separators=(",", ":")),
        "extensions": "{}",
    }
    h = headers()
    h["wp-site"] = wp_site
    h["Accept"] = "application/json,text/plain,*/*"
    r = session.get(GRAPHQL_URL, params=params, headers=h, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    payload = r.json()
    return parse_graphql_schedule(payload, default_day), int(r.status_code), r.url


def fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_source(hours: int, timeout: int) -> tuple[ET.Element, dict]:
    now_utc = datetime.now(timezone.utc)
    today_doha = now_utc.astimezone(DOHA).date()
    lower = now_utc - timedelta(hours=2)
    upper = now_utc + timedelta(hours=max(1, hours))
    root = ET.Element("tv", {
        "generator-info-name": "EPGManager Al Jazeera official",
        "generator-info-url": BASE + "/video/live",
    })
    arabic_names = {
        "Al Jazeera": "الجزيرة",
        "Al Jazeera 2": "الجزيرة 2",
        "Al Jazeera Mubasher": "الجزيرة مباشر",
        "Al Jazeera Documentary": "الجزيرة الوثائقية",
        "Al Jazeera English": "الجزيرة الإنجليزية",
    }
    for spec in CHANNELS:
        ch = ET.SubElement(root, "channel", {"id": spec.xmltv_id})
        ET.SubElement(ch, "display-name", {"lang": "en"}).text = spec.display_name
        ET.SubElement(ch, "display-name", {"lang": "ar"}).text = arabic_names[spec.display_name]

    stats = {
        "source": "Al Jazeera Network official public schedules",
        "source_url": BASE + "/video/live",
        "generated_utc": now_utc.isoformat(),
        "timezone_source": "Asia/Qatar",
        "timezone_output": "UTC +0000",
        "window_hours": hours,
        "channels": len(CHANNELS),
        "active_channels": 0,
        "programmes": 0,
        "channel_reports": {},
    }

    session = requests.Session()
    for spec in CHANNELS:
        report = {
            "xmltv_id": spec.xmltv_id,
            "live_url": urljoin(BASE, spec.live_path),
            "schedule_urls": [],
            "http": [],
            "programmes": 0,
            "error": "",
        }
        stats["channel_reports"][spec.xmltv_id] = report
        candidates: list[str] = []
        if spec.schedule_path:
            candidates.append(urljoin(BASE, spec.schedule_path))
        try:
            live_html, live_status = fetch(session, report["live_url"], timeout)
            report["http"].append({"url": report["live_url"], "status": live_status})
            for u in discover_schedule_links(live_html, report["live_url"]):
                if u not in candidates:
                    candidates.append(u)
        except Exception as exc:
            report["error"] = f"live:{exc}"

        # Mubasher and Documentary expose official LIVE pages, but those
        # pages currently carry no schedule link or supported GraphQL
        # profile. Do NOT probe guessed /schedule-ajm or /schedule-ajd
        # endpoints (which return 404), and especially do not turn live
        # broadcast metadata into fabricated programme times.
        if not candidates and spec.xmltv_id not in GRAPHQL_PROFILES:
            report["guide_status"] = "NO_VERIFIED_OFFICIAL_SCHEDULE_ENDPOINT"

        all_rows: list[dict] = []
        graph_rows: list[dict] = []
        profile = GRAPHQL_PROFILES.get(spec.xmltv_id)
        if profile:
            try:
                graph_rows, graph_status, graph_url = fetch_graphql_schedule(
                    session, profile[0], profile[1], timeout, today_doha
                )
                report["graphql"] = {
                    "status": graph_status,
                    "url": graph_url,
                    "programmes_found": len(graph_rows),
                }
                all_rows.extend(graph_rows)
            except Exception as exc:
                report["graphql"] = {"error": str(exc), "programmes_found": 0}

        # Never mix the API clock (UTC) with the rendered Arabic page clock
        # (Mecca/Doha). HTML is a fallback only when the structured API yields
        # no rows for that channel.
        visited = set()
        for schedule_url in ([] if graph_rows else candidates):
            if schedule_url in visited:
                continue
            visited.add(schedule_url)
            try:
                html, status = fetch(session, schedule_url, timeout)
                report["http"].append({"url": schedule_url, "status": status})
                text_content = norm_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
                if "جدول البث" not in text_content and "schedule" not in schedule_url.casefold():
                    continue
                report["schedule_urls"].append(schedule_url)
                all_rows.extend(parse_schedule(html, today_doha))
                for day_url, day_hint in discover_day_links(html, schedule_url, today_doha):
                    if day_url in visited:
                        continue
                    visited.add(day_url)
                    try:
                        day_html, day_status = fetch(session, day_url, timeout)
                        report["http"].append({"url": day_url, "status": day_status})
                        report["schedule_urls"].append(day_url)
                        all_rows.extend(parse_schedule(day_html, day_hint))
                    except Exception as exc:
                        report.setdefault("day_errors", []).append(f"{day_url}: {exc}")
            except Exception as exc:
                report.setdefault("schedule_errors", []).append(f"{schedule_url}: {exc}")

        seen = set()
        kept = []
        for row in sorted(all_rows, key=lambda x: x["start"]):
            key = (row["start"], row["title"])
            if key in seen:
                continue
            seen.add(key)
            if row["stop"].astimezone(timezone.utc) <= lower:
                continue
            if row["start"].astimezone(timezone.utc) >= upper:
                continue
            kept.append(row)

        kept.sort(key=lambda x: x["start"])
        for idx, row in enumerate(kept):
            # The GraphQL API supplies a real duration. The previous code
            # overwrote it with the NEXT start unconditionally, producing
            # false programmes that lasted many hours, and hiding gaps.
            # Only clamp a documented broadcast overlap; never extend.
            if idx + 1 < len(kept) and kept[idx + 1]["start"] > row["start"]:
                if row["stop"] > kept[idx + 1]["start"]:
                    row["stop"] = kept[idx + 1]["start"]
            if row["stop"] <= row["start"]:
                report["invalid_interval_rejected"] = (
                    report.get("invalid_interval_rejected", 0) + 1)
                continue
            p = ET.SubElement(root, "programme", {
                "channel": spec.xmltv_id,
                "start": fmt_utc(row["start"]),
                "stop": fmt_utc(row["stop"]),
            })
            language = "en" if spec.xmltv_id == "AlJazeera.qa@English" else "ar"
            ET.SubElement(p, "title", {"lang": language}).text = row["title"]
            if row.get("desc"):
                ET.SubElement(p, "desc", {"lang": language}).text = row["desc"]
        accepted = [p for p in root.findall("programme")
                    if p.get("channel") == spec.xmltv_id]
        report["programmes"] = len(accepted)
        if accepted:
            stats["active_channels"] += 1
            stats["programmes"] += len(accepted)
            report["first_start_utc"] = kept[0]["start"].astimezone(timezone.utc).isoformat()
            report["last_stop_utc"] = max(
                row["stop"] for row in kept if row["stop"] > row["start"]
            ).astimezone(timezone.utc).isoformat()

    stats["zero_epg_ids"] = [
        cid for cid, row in stats["channel_reports"].items() if not row["programmes"]
    ]
    return root, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=25)
    args = ap.parse_args()

    out = Path(args.output)
    report = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)

    root, stats = build_source(args.hours, args.timeout)
    ET.indent(root, space="  ")
    out.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    report.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {k: v for k, v in stats.items() if k != "channel_reports"}
    summary["graphql"] = {
        cid: row.get("graphql")
        for cid, row in stats["channel_reports"].items()
        if row.get("graphql")
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if stats["programmes"] > 0 else 11


if __name__ == "__main__":
    raise SystemExit(main())
