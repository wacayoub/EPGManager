#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build XMLTV from STARZPLAY Live's publicly rendered metadata.

This adapter deliberately does not bypass geo controls, authentication, DRM or
anti-bot protection. It reads the public /<lang>/live page and extracts schedule
objects from JSON embedded in the HTML. If STARZPLAY does not expose schedule
metadata to the current runner, it exits non-zero so the production workflow
keeps the previous last-known-good feed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import html as html_mod
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
NONWORD_RE = re.compile(r"[^a-z0-9]+")
TITLE_KEYS = ("title", "name", "displayTitle", "display_title", "programTitle", "programmeTitle", "eventName")
DESC_KEYS = ("description", "synopsis", "shortDescription", "short_description", "summary", "overview")
START_KEYS = ("start", "startTime", "start_time", "startsAt", "startDate", "start_date", "eventStart", "broadcastStart")
STOP_KEYS = ("stop", "end", "endTime", "end_time", "endsAt", "endDate", "end_date", "eventEnd", "broadcastEnd")
CHANNEL_NAME_KEYS = ("channelName", "channel_name", "stationName", "station_name", "networkName", "network_name")
CHANNEL_ID_KEYS = ("channelId", "channel_id", "stationId", "station_id", "networkId", "network_id")
NESTED_CHANNEL_KEYS = ("channel", "station", "network", "liveChannel", "live_channel")


def norm_name(value: str) -> str:
    text = html_mod.unescape(value or "").casefold().replace("&", " and ")
    return " ".join(NONWORD_RE.sub(" ", text).split())


def read_id_index(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "|" not in line:
            continue
        cid, name = line.split("|", 1)
        cid, name = cid.strip(), name.strip()
        if cid and name:
            out.setdefault(norm_name(name), cid)
    return out


def first_value(obj: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("text", "value", "name", "title", "en", "ar"):
            if key in value and value[key] not in (None, ""):
                return scalar_text(value[key])
    return ""


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_dt(int(text))
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def channel_context(obj: dict[str, Any], inherited: dict[str, str]) -> dict[str, str]:
    ctx = dict(inherited)
    for key in NESTED_CHANNEL_KEYS:
        nested = obj.get(key)
        if isinstance(nested, dict):
            name = scalar_text(first_value(nested, ("name", "title", "displayName", "display_name")))
            sid = scalar_text(first_value(nested, ("id", "contentId", "content_id", "channelId", "channel_id")))
            if name:
                ctx["name"] = name
            if sid:
                ctx["source_id"] = sid
    name = scalar_text(first_value(obj, CHANNEL_NAME_KEYS))
    sid = scalar_text(first_value(obj, CHANNEL_ID_KEYS))
    if name:
        ctx["name"] = name
    if sid:
        ctx["source_id"] = sid
    kind = scalar_text(first_value(obj, ("type", "kind", "entityType", "entity_type"))).casefold()
    if "channel" in kind or "station" in kind:
        name = scalar_text(first_value(obj, ("name", "title", "displayName", "display_name")))
        sid = scalar_text(first_value(obj, ("id", "contentId", "content_id")))
        if name:
            ctx["name"] = name
        if sid:
            ctx["source_id"] = sid
    return ctx


def collect_events(obj: Any, inherited: dict[str, str] | None = None) -> list[dict[str, Any]]:
    inherited = inherited or {}
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        ctx = channel_context(obj, inherited)
        title = scalar_text(first_value(obj, TITLE_KEYS))
        start = parse_dt(first_value(obj, START_KEYS))
        stop = parse_dt(first_value(obj, STOP_KEYS))
        if title and start and (ctx.get("name") or ctx.get("source_id")):
            if stop is None or stop <= start:
                duration = first_value(obj, ("duration", "durationSeconds", "duration_seconds", "durationMinutes", "duration_minutes"))
                try:
                    n = float(duration)
                    if "minute" in " ".join(obj.keys()).casefold() or n < 600:
                        stop = start + timedelta(minutes=n)
                    else:
                        stop = start + timedelta(seconds=n)
                except Exception:
                    stop = None
            found.append({
                "source_id": ctx.get("source_id", ""),
                "channel_name": ctx.get("name", ""),
                "title": title,
                "description": scalar_text(first_value(obj, DESC_KEYS)),
                "start": start,
                "stop": stop,
            })
        for value in obj.values():
            if isinstance(value, (dict, list)):
                found.extend(collect_events(value, ctx))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(collect_events(value, inherited))
    return found


def extract_json_blobs(page: str) -> list[Any]:
    soup = BeautifulSoup(page, "html.parser")
    blobs: list[Any] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text("", strip=False)
        if not text:
            continue
        typ = (script.get("type") or "").casefold()
        if "json" in typ or script.get("id") in {"__NEXT_DATA__", "__NUXT_DATA__"}:
            try:
                blobs.append(json.loads(text))
                continue
            except Exception:
                pass
        for marker in ("__INITIAL_STATE__=", "__PRELOADED_STATE__=", "__NEXT_DATA__="):
            pos = text.find(marker)
            if pos < 0:
                continue
            tail = text[pos + len(marker):].lstrip()
            try:
                value, _ = json.JSONDecoder().raw_decode(tail)
                blobs.append(value)
            except Exception:
                pass
    return blobs


def fetch_page(url: str, lang: str, timeout: int) -> tuple[int, str, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en;q=0.8" if lang == "ar" else "en,ar;q=0.8",
        "Referer": "https://www.starzplay.com/",
    }
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return r.status_code, r.text, r.url


def make_channel_id(name: str, source_id: str, index: dict[str, str]) -> str:
    normalized = norm_name(name)
    stable = source_id.strip()

    # The STARZPLAY content/channel ID is the cross-locale identity key. Use it
    # before names so Arabic and English pages can never diverge just because a
    # channel display name was localized.
    source_aliases = {
        "720335400126": "AbuDhabiSports1.ae@SD",
        "720335400128": "NationalGeographicAbuDhabi.ae@SD",
        "558369320039": "ZeeAflam.ae@SD",
    }
    if stable in source_aliases:
        return source_aliases[stable]
    if stable:
        stable = re.sub(r"[^A-Za-z0-9_.-]+", "-", stable).strip("-")
        return f"starzplay.{stable}"

    # Name matching is only a fallback when STARZPLAY did not expose a stable ID.
    if normalized in index:
        return index[normalized]
    aliases = {
        "abu dhabi sports 1": "AbuDhabiSports1.ae@SD",
        "ad sports 1": "AbuDhabiSports1.ae@SD",
        "abu dhabi tv": "AbuDhabiTV.ae@SD",
        "national geographic abu dhabi": "NationalGeographicAbuDhabi.ae@SD",
        "nat geo abu dhabi": "NationalGeographicAbuDhabi.ae@SD",
        "cartoon network arabic": "CartoonNetworkArabic.ae@SD",
        "sky news arabia": "SkyNewsArabia.ae@SD",
        "zee alwan": "ZeeAlwan.ae@SD",
        "zee aflam": "ZeeAflam.ae@SD",
    }
    if normalized in aliases:
        return aliases[normalized]
    stable = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:14]
    return f"starzplay.{stable}"


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_xml(events: list[dict[str, Any]], lang: str, index: dict[str, str], hours: int) -> tuple[ET.Element, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    max_start = now + timedelta(hours=max(1, hours))
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    channels: dict[str, dict[str, str]] = {}
    for e in events:
        start = e.get("start")
        if not isinstance(start, datetime) or start >= max_start:
            continue
        stop = e.get("stop")
        if isinstance(stop, datetime) and stop <= now - timedelta(hours=3):
            continue
        cid = make_channel_id(e.get("channel_name", ""), e.get("source_id", ""), index)
        channels.setdefault(cid, {"name": e.get("channel_name") or cid, "source_id": e.get("source_id", "")})
        dedup[(cid, fmt_dt(start), e.get("title", ""))] = {**e, "cid": cid}

    by_cid: dict[str, list[dict[str, Any]]] = {}
    for e in dedup.values():
        by_cid.setdefault(e["cid"], []).append(e)
    for rows in by_cid.values():
        rows.sort(key=lambda x: x["start"])
        for i, e in enumerate(rows):
            if not isinstance(e.get("stop"), datetime) or e["stop"] <= e["start"]:
                nxt = rows[i + 1]["start"] if i + 1 < len(rows) else None
                e["stop"] = nxt if isinstance(nxt, datetime) and nxt > e["start"] else e["start"] + timedelta(minutes=60)

    root = ET.Element("tv", {
        "generator-info-name": f"EPGManager STARZPLAY public metadata ({lang})",
        "generator-info-url": "https://www.starzplay.com/",
    })
    for cid in sorted(channels, key=str.casefold):
        c = ET.SubElement(root, "channel", {"id": cid})
        dn = ET.SubElement(c, "display-name", {"lang": lang})
        dn.text = channels[cid]["name"]
        if channels[cid]["source_id"]:
            url = ET.SubElement(c, "url", {"system": "starzplay-id"})
            url.text = channels[cid]["source_id"]
    count = 0
    for cid in sorted(by_cid, key=str.casefold):
        for e in by_cid[cid]:
            p = ET.SubElement(root, "programme", {"channel": cid, "start": fmt_dt(e["start"]), "stop": fmt_dt(e["stop"])})
            t = ET.SubElement(p, "title", {"lang": lang})
            t.text = e["title"]
            if e.get("description"):
                d = ET.SubElement(p, "desc", {"lang": lang})
                d.text = e["description"]
            count += 1
    return root, {"channels": len(channels), "programmes": count}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=("ar", "en"), required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--id-index", default="feeds/mena.txt")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--html-file", help="Offline/test input; skips network fetch")
    ap.add_argument("--base-url", default="https://www.starzplay.com")
    ap.add_argument("--country", default="AE")
    ap.add_argument("--city", default="Dubai")
    args = ap.parse_args()

    query = urlencode({"selectcountry": args.country.upper(), "selectcity": args.city})
    url = f"{args.base_url.rstrip('/')}/{args.lang}/live?{query}"
    status = 200
    final_url = url
    error = ""
    try:
        if args.html_file:
            page = Path(args.html_file).read_text(encoding="utf-8")
            final_url = f"file://{Path(args.html_file).resolve()}"
        else:
            status, page, final_url = fetch_page(url, args.lang, args.timeout)
    except Exception as exc:
        page = ""
        status = 0
        error = str(exc)

    blobs = extract_json_blobs(page) if page else []
    events: list[dict[str, Any]] = []
    for blob in blobs:
        events.extend(collect_events(blob))
    index = read_id_index(Path(args.id_index) if args.id_index else None)
    root, stats = build_xml(events, args.lang, index, args.hours)
    ET.indent(root, space="  ")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "language": args.lang,
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "html_bytes": len(page.encode("utf-8")) if page else 0,
        "json_blobs": len(blobs),
        "events_detected": len(events),
        **stats,
        "error": error,
        "policy": "public metadata only; no geo/auth/DRM/anti-bot bypass",
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if status in {401, 403, 451} or status == 0:
        return 10
    return 0 if stats["channels"] and stats["programmes"] else 11


if __name__ == "__main__":
    raise SystemExit(main())
