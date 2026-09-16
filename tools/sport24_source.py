#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a standalone Sport24 XMLTV feed without affecting MENA authority.

Only explicit machine-readable start/stop times are accepted. If Sport24
returns programme cards without trustworthy clock data, the channel is kept in
the diagnostic report but no programme is invented. This makes the source safe
for receiver use and suitable as an independent candidate source.
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

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager/1.0"


def parse_iso(value: str):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def xmltv_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()


def event_from_node(node):
    start = None
    stop = None
    for attr in ("data-start", "data-start-time", "data-begin", "datetime"):
        start = parse_iso(node.get(attr))
        if start:
            break
    for attr in ("data-stop", "data-end", "data-end-time"):
        stop = parse_iso(node.get(attr))
        if stop:
            break
    if not start:
        time_node = node.find("time", attrs={"datetime": True})
        if time_node:
            start = parse_iso(time_node.get("datetime"))
    if not start:
        return None
    if not stop:
        stop_node = node.find("time", attrs={"data-end": True})
        if stop_node:
            stop = parse_iso(stop_node.get("data-end"))
    title = ""
    desc = ""
    for sel in (".title", ".program-title", ".programme-title", "h2", "h3", "h4", "strong"):
        hit = node.select_one(sel)
        if hit and clean(hit.get_text(" ", strip=True)):
            title = clean(hit.get_text(" ", strip=True))
            break
    for sel in (".description", ".program-description", ".programme-description", "p"):
        hit = node.select_one(sel)
        if hit and clean(hit.get_text(" ", strip=True)):
            desc = clean(hit.get_text(" ", strip=True))
            break
    if not title:
        title = clean(node.get("data-title") or "")
    if not title:
        return None
    return {"start": start, "stop": stop, "title": title, "desc": desc}


def embedded_json_events(soup):
    out = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=False) or ""
        if not raw or ("start" not in raw.casefold() and "date" not in raw.casefold()):
            continue
        candidates = []
        stype = (script.get("type") or "").casefold()
        if "json" in stype:
            candidates.append(raw.strip())
        for m in re.finditer(r"(?:programs|programmes|schedule|events)\s*[:=]\s*(\[[\s\S]*?\])\s*[;,<]", raw, re.I):
            candidates.append(m.group(1))
        for blob in candidates:
            try:
                data = json.loads(blob)
            except Exception:
                continue
            stack = data if isinstance(data, list) else []
            for row in stack:
                if not isinstance(row, dict):
                    continue
                start = parse_iso(str(row.get("start") or row.get("start_time") or row.get("datetime") or ""))
                stop = parse_iso(str(row.get("stop") or row.get("end") or row.get("end_time") or ""))
                title = clean(str(row.get("title") or row.get("name") or row.get("program") or ""))
                desc = clean(str(row.get("description") or row.get("desc") or ""))
                if start and title:
                    out.append({"start": start, "stop": stop, "title": title, "desc": desc})
    return out


def scrape(session: requests.Session, url: str):
    r = session.get(url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    events = []
    selectors = [
        "[data-start]", "[data-start-time]", "[data-begin]",
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
    events.extend(embedded_json_events(soup))
    dedup = {}
    for ev in events:
        key = (ev["start"].isoformat(), ev["title"].casefold())
        dedup[key] = ev
    rows = sorted(dedup.values(), key=lambda x: x["start"])
    # Fill only a missing stop from the next explicit event. Never create a
    # synthetic clock for the first/last event.
    for i, row in enumerate(rows[:-1]):
        if row.get("stop") is None and rows[i + 1]["start"] > row["start"]:
            row["stop"] = rows[i + 1]["start"]
    return [x for x in rows if x.get("stop") and x["stop"] > x["start"]]


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
    session.headers.update({"User-Agent": UA, "Accept-Language": "ar,en;q=0.8"})
    now = datetime.now(timezone.utc)
    max_start = now + timedelta(hours=max(1, args.window_hours))
    report = {"source": "https://www.sport24.rest", "channels": [], "programmes": 0}
    channel_rows = []

    for cid, name, url in TARGETS:
        row = {"id": cid, "name": name, "url": url, "programmes": 0, "status": "NO_TIMELINE"}
        try:
            events = scrape(session, url)
            events = [e for e in events if e["stop"] > now - timedelta(hours=2) and e["start"] < max_start]
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
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"active_channels": report["active_channels"], "programmes": report["programmes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
