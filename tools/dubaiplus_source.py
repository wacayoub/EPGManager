#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dubai+ official EPG -> XMLTV, with EPGManager hybrid language policy.

Policy:
- Arabic-native DMI channels: official Arabic title/description.
- Dubai One + Dubai Sports/Racing: English title when the bilingual guide exposes
  the same event, Arabic description from the Arabic guide when available.
- Radio services are ignored.

The adapter uses only publicly rendered Dubai+ EPG pages. It does not bypass
authentication, geo controls, DRM, or anti-bot protection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.dubaiplus.net"
EPG = BASE + "/web/epg"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DUBAI_TZ = timezone(timedelta(hours=4))
AR_RE = re.compile(r"[\u0600-\u06ff]")
TIME_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*$",
    re.I,
)

CHANNELS = {
    "dubai": ("DubaiTV.ae@SD", "Dubai TV", "arabic_native"),
    "dubai tv": ("DubaiTV.ae@SD", "Dubai TV", "arabic_native"),
    "sama dubai": ("SamaDubai.ae@SD", "Sama Dubai", "arabic_native"),
    "sama dubai tv": ("SamaDubai.ae@SD", "Sama Dubai", "arabic_native"),
    "dubai sports 1": ("DubaiSports1.ae@SD", "Dubai Sports 1", "hybrid"),
    "dubai sport 1": ("DubaiSports1.ae@SD", "Dubai Sports 1", "hybrid"),
    "dubai sports 2": ("DubaiSports2.ae@SD", "Dubai Sports 2", "hybrid"),
    "dubai sport 2": ("DubaiSports2.ae@SD", "Dubai Sports 2", "hybrid"),
    "dubai racing": ("DubaiRacing1.ae@SD", "Dubai Racing 1", "hybrid"),
    "dubai racing 1": ("DubaiRacing1.ae@SD", "Dubai Racing 1", "hybrid"),
    "dubai racing 2": ("DubaiRacing2.ae@SD", "Dubai Racing 2", "hybrid"),
    "noor dubaitv": ("NoorDubaiTV.ae@SD", "Noor Dubai TV", "arabic_native"),
    "noor dubai tv": ("NoorDubaiTV.ae@SD", "Noor Dubai TV", "arabic_native"),
    "one tv": ("DubaiOne.ae@SD", "Dubai One", "hybrid"),
    "dubai one": ("DubaiOne.ae@SD", "Dubai One", "hybrid"),
    "dubai one tv": ("DubaiOne.ae@SD", "Dubai One", "hybrid"),
    "dubai zaman": ("DubaiZaman.ae@SD", "Dubai Zaman", "arabic_native"),
}
RADIO_TOKENS = ("radio", "quran")
IGNORE_LINES = {
    "schedule", "currently playing:", "currently playing", "available live channels (12)",
    "experience premium live content", "home", "movies", "series", "epg",
}


def norm(value: str) -> str:
    value = (value or "").casefold().replace("–", "-").replace("—", "-")
    return " ".join(re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", value).split())


def is_ar(value: str) -> bool:
    return bool(AR_RE.search(value or ""))


def channel_spec(name: str):
    n = norm(name)
    if any(tok in n for tok in RADIO_TOKENS):
        return None
    if n in CHANNELS:
        return CHANNELS[n]
    for key, spec in CHANNELS.items():
        if n == key or n.startswith(key + " ") or key.startswith(n + " "):
            return spec
    return None


def _headers(lang: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-AE,ar;q=0.9,en;q=0.6" if lang.startswith("ar") else "en-US,en;q=0.9,ar;q=0.5",
        "Referer": BASE + "/",
    }


def fetch(url: str, lang: str, timeout: int) -> str:
    r = requests.get(url, headers=_headers(lang), timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def discover_channels(page: str) -> dict[str, str]:
    soup = BeautifulSoup(page, "html.parser")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        qs = parse_qs(urlparse(urljoin(EPG, href)).query)
        cid = (qs.get("channel") or [""])[0].strip()
        if not cid:
            continue
        label = " ".join(a.stripped_strings)
        label = re.sub(r"^watch\s+", "", label, flags=re.I)
        label = re.sub(r"\s+live$", "", label, flags=re.I).strip()
        spec = channel_spec(label)
        if spec:
            out.setdefault(spec[1], cid)
    return out


def _clean_lines(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    raw = soup.get_text("\n", strip=True)
    lines: list[str] = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if norm(line) in IGNORE_LINES:
            continue
        if line.lower().startswith("watch ") and line.lower().endswith(" live"):
            continue
        lines.append(line)
    return lines


def _clock(h: int, m: int, ap: str) -> tuple[int, int]:
    h %= 12
    if ap.upper() == "PM":
        h += 12
    return h, m


def parse_schedule(page: str, channel_name: str, now_utc: datetime | None = None) -> list[dict]:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(DUBAI_TZ)
    lines = _clean_lines(page)
    rows: list[dict] = []
    i = 0
    day = now_local.date()
    last_start_min: int | None = None
    rollover = 0
    while i < len(lines):
        m = TIME_RE.match(lines[i])
        if not m:
            i += 1
            continue
        sh, sm = _clock(int(m.group(1)), int(m.group(2)), m.group(3))
        eh, em = _clock(int(m.group(4)), int(m.group(5)), m.group(6))
        start_min = sh * 60 + sm
        if last_start_min is not None and start_min + 8 * 60 < last_start_min:
            rollover += 1
        last_start_min = start_min
        start_local = datetime.combine(day + timedelta(days=rollover), datetime.min.time(), tzinfo=DUBAI_TZ).replace(hour=sh, minute=sm)
        stop_local = datetime.combine(day + timedelta(days=rollover), datetime.min.time(), tzinfo=DUBAI_TZ).replace(hour=eh, minute=em)
        if stop_local <= start_local:
            stop_local += timedelta(days=1)

        title = lines[i + 1].strip() if i + 1 < len(lines) else ""
        desc_parts: list[str] = []
        j = i + 2
        while j < len(lines) and not TIME_RE.match(lines[j]):
            if channel_spec(lines[j]) and norm(lines[j]) != norm(channel_name):
                break
            if lines[j].lower().startswith("watch ") or norm(lines[j]) in IGNORE_LINES:
                break
            if lines[j].casefold() not in {"nan", "none", "null", "n/a", "-"}:
                desc_parts.append(lines[j])
            j += 1
        if title and title.casefold() not in {"nan", "none", "null", "n/a", "-"}:
            rows.append({
                "start": start_local.astimezone(timezone.utc),
                "stop": stop_local.astimezone(timezone.utc),
                "title": title,
                "desc": " ".join(desc_parts).strip(),
            })
        i = max(j, i + 1)

    if rows and max(r["stop"] for r in rows) < now_utc - timedelta(hours=6):
        for r in rows:
            r["start"] += timedelta(days=1)
            r["stop"] += timedelta(days=1)
    return rows


def parse_multi_schedule(page: str, now_utc: datetime | None = None) -> dict[str, list[dict]]:
    """Parse the public Dubai+ EPG page in one pass.

    Dubai+ currently renders all TV channels on the same public EPG page. This
    avoids the old per-channel query assumption and reduces load to one request
    per language.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(DUBAI_TZ)
    lines = _clean_lines(page)
    out: dict[str, list[dict]] = {}
    current: str | None = None
    i = 0
    day = now_local.date()
    last_start: dict[str, int] = {}
    rollover: dict[str, int] = {}
    while i < len(lines):
        spec = channel_spec(lines[i])
        if spec:
            current = spec[1]
            out.setdefault(current, [])
            i += 1
            continue
        m = TIME_RE.match(lines[i])
        if not current or not m:
            i += 1
            continue
        sh, sm = _clock(int(m.group(1)), int(m.group(2)), m.group(3))
        eh, em = _clock(int(m.group(4)), int(m.group(5)), m.group(6))
        start_min = sh * 60 + sm
        prev = last_start.get(current)
        if prev is not None and start_min + 8 * 60 < prev:
            rollover[current] = rollover.get(current, 0) + 1
        last_start[current] = start_min
        ro = rollover.get(current, 0)
        start_local = datetime.combine(day + timedelta(days=ro), datetime.min.time(), tzinfo=DUBAI_TZ).replace(hour=sh, minute=sm)
        stop_local = datetime.combine(day + timedelta(days=ro), datetime.min.time(), tzinfo=DUBAI_TZ).replace(hour=eh, minute=em)
        if stop_local <= start_local:
            stop_local += timedelta(days=1)
        title = lines[i + 1].strip() if i + 1 < len(lines) else ""
        desc_parts: list[str] = []
        j = i + 2
        while j < len(lines):
            if TIME_RE.match(lines[j]) or channel_spec(lines[j]):
                break
            low = lines[j].casefold()
            if low.startswith("watch ") or norm(lines[j]) in IGNORE_LINES:
                j += 1
                continue
            if low not in {"nan", "none", "null", "n/a", "-"}:
                desc_parts.append(lines[j])
            j += 1
        if title and title.casefold() not in {"nan", "none", "null", "n/a", "-"}:
            out[current].append({
                "start": start_local.astimezone(timezone.utc),
                "stop": stop_local.astimezone(timezone.utc),
                "title": title,
                "desc": " ".join(desc_parts).strip(),
            })
        i = max(j, i + 1)
    # If the page's clock-only schedule was interpreted as yesterday, move that
    # channel forward one day. Never invent programmes; only normalize date.
    for cname, rows in out.items():
        if rows and max(r["stop"] for r in rows) < now_utc - timedelta(hours=6):
            for row in rows:
                row["start"] += timedelta(days=1)
                row["stop"] += timedelta(days=1)
    return out


def match_english(ar_event: dict, english: Iterable[dict]) -> dict | None:
    best = None
    best_delta = 10**9
    for e in english:
        delta = abs((e["start"] - ar_event["start"]).total_seconds())
        if delta < best_delta and delta <= 180:
            best, best_delta = e, delta
    return best


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build(ar_pages: dict[str, str], en_pages: dict[str, str], channel_ids: dict[str, str], hours: int) -> tuple[ET.Element, dict]:
    now = datetime.now(timezone.utc)
    lower = now - timedelta(hours=3)
    upper = now + timedelta(hours=max(1, hours))
    root = ET.Element("tv", {
        "generator-info-name": "EPGManager Dubai+ official hybrid",
        "generator-info-url": EPG,
    })
    stats = {
        "generated_utc": now.isoformat(), "channels": 0, "programmes": 0,
        "hybrid_programmes": 0, "arabic_native_programmes": 0,
        "english_title_applied": 0, "arabic_description_present": 0,
        "source": "Dubai+ official public EPG",
        "policy": "EN title + AR desc for international/sports; AR/AR for Arabic-native",
        "channel_ids": channel_ids,
    }

    seen_ids: set[str] = set()
    for cname in sorted(set(ar_pages) | set(en_pages), key=str.casefold):
        spec = channel_spec(cname)
        if not spec:
            continue
        xmlid, display, profile = spec
        if xmlid in seen_ids:
            continue
        seen_ids.add(xmlid)
        ar_all = parse_multi_schedule(ar_pages.get(cname, ""), now) if ar_pages.get(cname) else {}
        en_all = parse_multi_schedule(en_pages.get(cname, ""), now) if en_pages.get(cname) else {}
        ar_rows = ar_all.get(cname, [])
        en_rows = en_all.get(cname, [])
        base = ar_rows or en_rows
        if not base:
            continue

        ch = ET.SubElement(root, "channel", {"id": xmlid})
        ET.SubElement(ch, "display-name", {"lang": "en"}).text = display
        if channel_ids.get(cname):
            ET.SubElement(ch, "url", {"system": "dubaiplus-id"}).text = channel_ids[cname]
        stats["channels"] += 1

        for ev in base:
            if ev["stop"] <= lower or ev["start"] >= upper:
                continue
            p = ET.SubElement(root, "programme", {
                "channel": xmlid, "start": fmt_dt(ev["start"]), "stop": fmt_dt(ev["stop"]),
            })
            title = ev["title"]
            desc = ev.get("desc", "")
            title_lang = "ar" if is_ar(title) else "en"
            if profile == "hybrid":
                stats["hybrid_programmes"] += 1
                en = match_english(ev, en_rows)
                if en and en.get("title"):
                    title = en["title"]
                    title_lang = "en"
                    stats["english_title_applied"] += 1
                if desc and not is_ar(desc):
                    desc = ""
            else:
                stats["arabic_native_programmes"] += 1
                title_lang = "ar" if is_ar(title) else title_lang
                if desc and not is_ar(desc):
                    desc = ""
            ET.SubElement(p, "title", {"lang": title_lang}).text = title
            if desc:
                ET.SubElement(p, "desc", {"lang": "ar"}).text = desc
                stats["arabic_description_present"] += 1
            stats["programmes"] += 1
    return root, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--base-url", default=BASE)
    ap.add_argument("--debug-dir", help="Optional directory to save fetched HTML for diagnostics")
    args = ap.parse_args()

    epg_url = args.base_url.rstrip("/") + "/web/epg"
    report_path = Path(args.report)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    error = ""
    ar_pages: dict[str, str] = {}
    en_pages: dict[str, str] = {}
    channel_ids: dict[str, str] = {}
    status = 200
    try:
        ar_index = fetch(epg_url + "?lang=ar-AE", "ar-AE", args.timeout)
        en_index = fetch(epg_url + "?lang=en-US", "en-US", args.timeout)
        if args.debug_dir:
            dbg = Path(args.debug_dir)
            dbg.mkdir(parents=True, exist_ok=True)
            (dbg / "dubaiplus-ar.html").write_text(ar_index, encoding="utf-8")
            (dbg / "dubaiplus-en.html").write_text(en_index, encoding="utf-8")
        # Dubai+ renders the complete EPG on one page. Parse that directly to
        # avoid unnecessary per-channel requests and runner rate limiting.
        ar_multi = parse_multi_schedule(ar_index)
        en_multi = parse_multi_schedule(en_index)
        for cname in sorted(set(ar_multi) | set(en_multi)):
            ar_pages[cname] = ar_index
            en_pages[cname] = en_index
        channel_ids.update(discover_channels(ar_index))
        channel_ids.update(discover_channels(en_index))
    except requests.HTTPError as exc:
        status = int(exc.response.status_code) if exc.response is not None else 0
        error = str(exc)
    except Exception as exc:
        status = 0
        error = str(exc)

    root, stats = build(ar_pages, en_pages, channel_ids, args.hours)
    ET.indent(root, space="  ")
    out_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    stats.update({"http_status": status, "error": error, "epg_url": epg_url})
    report_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "channel_ids"}, ensure_ascii=False))
    if status in {401, 403, 451} or status == 0:
        return 10
    return 0 if stats["channels"] and stats["programmes"] else 11


if __name__ == "__main__":
    raise SystemExit(main())
