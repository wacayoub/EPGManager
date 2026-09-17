#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a standalone Sport24 XMLTV feed without affecting MENA authority.

Only explicit machine-readable start/stop times are accepted. Sport24 changes
its frontend regularly, so the extractor understands ISO datetimes, Unix epoch
seconds/milliseconds, common data-* timestamp attributes and recursively nested
JSON state. Programme cards without a trustworthy clock remain diagnostic-only:
no schedule time is invented.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html as html_lib
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.sport24.rest/channels"
TARGETS = [
    ("sport24.adsports.1", "Abu Dhabi Sports 1", f"{BASE}/adsports/1"),
    ("sport24.adsports.2", "Abu Dhabi Sports 2", f"{BASE}/adsports/2"),
    ("sport24.adsports.3", "Abu Dhabi Sports 3", f"{BASE}/adsports/3"),
    ("sport24.adsports.4", "Abu Dhabi Sports 4", f"{BASE}/adsports/4"),
    ("sport24.dubaisports.1", "Dubai Sports 1", f"{BASE}/dubaisports/1"),
    ("sport24.dubaisports.2", "Dubai Sports 2", f"{BASE}/dubaisports/2"),
    ("sport24.thmanyah.1", "Thmanyah 1", f"{BASE}/thmanyah/1"),
    ("sport24.thmanyah.2", "Thmanyah 2", f"{BASE}/thmanyah/2"),
    ("sport24.thmanyah.3", "Thmanyah 3", f"{BASE}/thmanyah/3"),
    ("sport24.bein.news", "beIN SPORTS News", f"{BASE}/bein/news"),
    ("sport24.bein.0", "beIN SPORTS Free", f"{BASE}/bein/0"),
] + [
    (f"sport24.bein.{n}", f"beIN SPORTS {n}", f"{BASE}/bein/{n}") for n in range(1, 10)
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager/1.1"

START_KEYS = (
    "start", "start_time", "startTime", "start_at", "startAt", "starts_at",
    "startsAt", "begin", "begin_at", "beginAt", "datetime", "dateTime",
    "timestamp", "time", "utc", "start_timestamp", "startTimestamp",
)
STOP_KEYS = (
    "stop", "end", "end_time", "endTime", "end_at", "endAt", "ends_at",
    "endsAt", "finish", "finish_at", "finishAt", "stop_timestamp", "endTimestamp",
)
TITLE_KEYS = ("title", "name", "program", "programme", "program_title", "eventTitle", "event_name")
DESC_KEYS = ("description", "desc", "summary", "details", "subtitle")


def parse_dt(value):
    """Parse only timezone-aware/absolute values; never assume a local timezone."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
        # Current web timestamps are normally seconds or milliseconds.
        if number > 10_000_000_000:
            number /= 1000.0
        if 500_000_000 <= number <= 5_000_000_000:
            try:
                return datetime.fromtimestamp(number, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        return None

    raw = str(value).strip()
    if not raw:
        return None

    if re.fullmatch(r"\d{10,13}(?:\.0+)?", raw):
        try:
            return parse_dt(float(raw))
        except ValueError:
            return None

    # Normalise common JS/ISO variants. A timezone/offset is mandatory.
    candidate = raw
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # RFC/JS strings such as "Thu, 17 Sep 2026 18:00:00 GMT".
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt and dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


def xmltv_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()


def first_value(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    # Case-insensitive / punctuation-insensitive fallback.
    normalized = {re.sub(r"[^a-z0-9]", "", str(k).casefold()): v for k, v in mapping.items()}
    for key in keys:
        hit = normalized.get(re.sub(r"[^a-z0-9]", "", key.casefold()))
        if hit not in (None, ""):
            return hit
    return None


def node_absolute_time(node, start=True):
    attrs = (
        ("data-start", "data-start-time", "data-begin", "datetime",
         "data-time", "data-timestamp", "data-date", "data-utc",
         "data-start-ts", "data-start-timestamp", "data-start-date")
        if start else
        ("data-stop", "data-end", "data-end-time", "data-finish",
         "data-stop-ts", "data-end-ts", "data-end-timestamp", "data-end-date")
    )
    for attr in attrs:
        dt = parse_dt(node.get(attr))
        if dt:
            return dt

    # The clock can be attached to a descendant rather than the programme card.
    for child in node.find_all(True):
        for attr in attrs:
            dt = parse_dt(child.get(attr))
            if dt:
                return dt
    return None


def event_from_node(node):
    start = node_absolute_time(node, start=True)
    if not start:
        return None
    stop = node_absolute_time(node, start=False)

    title = ""
    desc = ""
    for sel in (
        ".title", ".program-title", ".programme-title", ".event-title",
        "[class*='title']", "h2", "h3", "h4", "strong",
    ):
        hit = node.select_one(sel)
        if hit and clean(hit.get_text(" ", strip=True)):
            title = clean(hit.get_text(" ", strip=True))
            break
    for sel in (
        ".description", ".program-description", ".programme-description",
        ".event-description", "[class*='description']", "p",
    ):
        hit = node.select_one(sel)
        if hit and clean(hit.get_text(" ", strip=True)):
            desc = clean(hit.get_text(" ", strip=True))
            break
    if not title:
        title = clean(node.get("data-title") or node.get("aria-label") or "")
    if not title:
        return None
    return {"start": start, "stop": stop, "title": title, "desc": desc, "via": "dom"}


def event_from_mapping(row):
    if not isinstance(row, dict):
        return None
    start = parse_dt(first_value(row, START_KEYS))
    if not start:
        return None
    stop = parse_dt(first_value(row, STOP_KEYS))
    title = clean(str(first_value(row, TITLE_KEYS) or ""))
    desc = clean(str(first_value(row, DESC_KEYS) or ""))
    if not title:
        return None
    return {"start": start, "stop": stop, "title": title, "desc": desc, "via": "json"}


def walk_json(value):
    """Yield every nested mapping/list item without assuming a frontend schema."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def json_blobs_from_script(raw: str, script_type: str):
    raw = raw.strip()
    if not raw:
        return []

    blobs = []
    if "json" in script_type:
        blobs.append(raw)

    # Common SSR/app state assignments.
    patterns = (
        r"__NEXT_DATA__\s*=\s*({[\s\S]*?})\s*;?\s*$",
        r"__NUXT__\s*=\s*({[\s\S]*?})\s*;?\s*$",
        r"(?:programs|programmes|schedule|events)\s*[:=]\s*(\[[\s\S]*?\])\s*[;,<]",
        r"(?:programs|programmes|schedule|events)\s*[:=]\s*({[\s\S]*?})\s*[;,<]",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw, re.I):
            blobs.append(match.group(1))
    return blobs


def embedded_json_events(soup):
    out = []
    parsed_blobs = 0
    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=False) or ""
        if not raw:
            continue
        stype = (script.get("type") or "").casefold()
        for blob in json_blobs_from_script(raw, stype):
            try:
                data = json.loads(blob)
            except Exception:
                continue
            parsed_blobs += 1
            for row in walk_json(data):
                ev = event_from_mapping(row)
                if ev:
                    out.append(ev)
    return out, parsed_blobs


def scrape(session: requests.Session, url: str):
    r = session.get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    events = []

    selectors = [
        "[data-start]", "[data-start-time]", "[data-begin]",
        "[data-time]", "[data-timestamp]", "[data-start-ts]",
        ".program", ".programme", ".schedule-item", ".event", "article",
    ]
    seen_nodes = set()
    for sel in selectors:
        for node in soup.select(sel):
            ident = id(node)
            if ident in seen_nodes:
                continue
            seen_nodes.add(ident)
            ev = event_from_node(node)
            if ev:
                events.append(ev)

    json_events, parsed_json_blobs = embedded_json_events(soup)
    events.extend(json_events)

    dedup = {}
    for ev in events:
        key = (ev["start"].isoformat(), ev["title"].casefold())
        old = dedup.get(key)
        # Prefer records that carry an explicit stop.
        if old is None or (old.get("stop") is None and ev.get("stop") is not None):
            dedup[key] = ev

    rows = sorted(dedup.values(), key=lambda x: x["start"])
    # Fill only a missing stop from the next explicit event. The start times
    # remain source-provided, and no duration is guessed for the last event.
    for i, row in enumerate(rows[:-1]):
        if row.get("stop") is None and rows[i + 1]["start"] > row["start"]:
            row["stop"] = rows[i + 1]["start"]

    valid = [x for x in rows if x.get("stop") and x["stop"] > x["start"]]
    diagnostics = {
        "http_status": r.status_code,
        "html_bytes": len(r.content),
        "candidate_events": len(events),
        "parsed_json_blobs": parsed_json_blobs,
        "valid_timeline_events": len(valid),
    }
    return valid, diagnostics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    ap.add_argument("--delay-ms", type=int, default=350)
    args = ap.parse_args()

    root = ET.Element("tv", {
        "generator-info-name": "EPGManager Sport24 standalone source",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ar,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    now = datetime.now(timezone.utc)
    max_start = now + timedelta(hours=max(1, args.window_hours))
    report = {
        "source": "https://www.sport24.rest",
        "policy": "explicit absolute timestamps only; no guessed local clocks",
        "channels": [],
        "programmes": 0,
    }
    channel_rows = []

    for cid, name, url in TARGETS:
        row = {"id": cid, "name": name, "url": url, "programmes": 0, "status": "NO_TIMELINE"}
        try:
            events, diagnostics = scrape(session, url)
            row.update(diagnostics)
            events = [
                e for e in events
                if e["stop"] > now - timedelta(hours=2) and e["start"] < max_start
            ]
            if events:
                c = ET.Element("channel", {"id": cid})
                ET.SubElement(c, "display-name", {"lang": "en"}).text = name
                root.append(c)
                channel_rows.append((cid, name))
                for ev in events:
                    p = ET.Element("programme", {
                        "channel": cid,
                        "start": xmltv_dt(ev["start"]),
                        "stop": xmltv_dt(ev["stop"]),
                    })
                    ET.SubElement(p, "title", {"lang": "ar"}).text = ev["title"]
                    if ev.get("desc"):
                        ET.SubElement(p, "desc", {"lang": "ar"}).text = ev["desc"]
                    root.append(p)
                row["programmes"] = len(events)
                row["status"] = "OK"
                report["programmes"] += len(events)
        except Exception as exc:
            row["status"] = "ERROR"
            row["error"] = str(exc)[:240]
        report["channels"].append(row)
        time.sleep(max(0, args.delay_ms) / 1000.0)

    ET.indent(root, space="  ")
    Path(args.output).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    report["active_channels"] = len(channel_rows)
    report["generated_utc"] = datetime.now(timezone.utc).isoformat()
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "active_channels": report["active_channels"],
        "programmes": report["programmes"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
