#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import html
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

UA = "EPGManager-Official-Iraq-Lebanon-Audit/1.0"
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*(?:am|pm))?$", re.I)
DURATION_RE = re.compile(r"^(?:المدة\s*:\s*)?(\d+)\s*(?:دقيقة|minute(?:s)?)$", re.I)
GENERIC = {
    "view more", "live", "يعرض الان", "التالي", "جدول البرامج", "duration", "image",
    "facebook", "twitter", "whatsapp", "telegram", "share", "messenger", "print",
}
TARGET_IDS = {
    "alsumaria": "Alsumaria.iq@SD",
    "aljadeed": "AlJadeed.lb@SD",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def clean_lines(markup: str):
    soup = BeautifulSoup(markup, "html.parser")
    for n in soup(["script", "style", "noscript", "svg"]):
        n.decompose()
    raw = [html.unescape(x).strip() for x in soup.stripped_strings]
    out = []
    for x in raw:
        x = re.sub(r"\s+", " ", x).strip()
        if not x:
            continue
        out.append(x)
    return out


def is_noise(s: str):
    low = s.casefold().strip(" :.-")
    if low in GENERIC:
        return True
    if low.startswith(("image", "copyright", "حقوق التأليف", "حمّل تطبيق", "follow", "search")):
        return True
    if len(s) > 180:
        return True
    return False


def normalize_time(s: str):
    s = s.strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$", s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    ap = m.group(3)
    if ap:
        if hh == 12:
            hh = 0
        if ap == "pm":
            hh += 12
    if hh > 23:
        return None
    return "%02d:%02d" % (hh, mm)


def extract_events(markup: str):
    lines = clean_lines(markup)
    events = []
    seen = set()
    for i, line in enumerate(lines):
        if not TIME_RE.match(line):
            continue
        tm = normalize_time(line)
        if not tm:
            continue
        candidates = []
        # Official pages vary: title may appear immediately before or after the time.
        for j in range(max(0, i - 4), min(len(lines), i + 7)):
            if j == i:
                continue
            s = lines[j]
            if TIME_RE.match(s) or DURATION_RE.match(s) or is_noise(s):
                continue
            if re.fullmatch(r"\d+", s):
                continue
            candidates.append((abs(j - i), 0 if j > i else 1, j, s))
        if not candidates:
            continue
        candidates.sort()
        title = candidates[0][3]
        # Reject obvious nav/day/date labels.
        if re.search(r"^(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة|السبت|الأحد)$", title, re.I):
            continue
        key = (tm, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        events.append({"time": tm, "title": title})
    return events


def read_xml(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def current_programs(data_dir: Path, cid: str):
    rows = []
    for path in data_dir.glob("*.xml.gz"):
        try:
            root = read_xml(path)
        except Exception:
            continue
        for p in root.findall("programme"):
            if (p.get("channel") or "").strip() != cid:
                continue
            title = p.find("title")
            rows.append({"start": p.get("start", ""), "title": ((title.text or "").strip() if title is not None else ""), "file": path.name})
    rows.sort(key=lambda x: x["start"])
    return rows


def unique_ratio(events):
    titles = [re.sub(r"\s+", " ", x["title"].casefold()).strip() for x in events if x.get("title")]
    return (len(set(titles)) / len(titles)) if titles else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    a = ap.parse_args()
    today = datetime.now(timezone.utc).date()

    sources = {
        "alsumaria": ["https://www.alsumaria.tv/TV-grid"],
        "aljadeed": [
            "https://www.aljadeed.tv/schedule-channels-date/1/%04d/%02d/%02d/en" % (d.year, d.month, d.day)
            for d in (today, today + timedelta(days=1))
        ],
    }
    out = {"schema": 1, "date_utc": today.isoformat(), "sources": {}}
    data_dir = Path(a.data_dir)

    for name, urls in sources.items():
        merged = []
        fetches = []
        for url in urls:
            try:
                markup = fetch(url)
                ev = extract_events(markup)
                fetches.append({"url": url, "status": "OK", "bytes": len(markup.encode("utf-8")), "events": len(ev), "samples": ev[:6]})
                merged.extend(ev)
            except Exception as exc:
                fetches.append({"url": url, "status": "ERROR", "error": str(exc)[:220]})
        dedup = []
        seen = set()
        for e in merged:
            k = (e["time"], e["title"].casefold())
            if k in seen:
                continue
            seen.add(k); dedup.append(e)
        cid = TARGET_IDS[name]
        current = current_programs(data_dir, cid)
        verdict = "CANDIDATE_OFFICIAL" if len(dedup) >= 8 and unique_ratio(dedup) >= 0.25 else ("REVIEW" if dedup else "NO_DATA")
        out["sources"][name] = {
            "id": cid,
            "verdict": verdict,
            "official_events": len(dedup),
            "official_unique_ratio": round(unique_ratio(dedup), 3),
            "official_samples": dedup[:12],
            "current_events": len(current),
            "current_samples": current[:6],
            "fetches": fetches,
        }

    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["OFFICIAL IRAQ/LEBANON EPG AUDIT", "date_utc=%s" % out["date_utc"], ""]
    for name, x in out["sources"].items():
        lines.append("[%s] %s | %s | official=%d current=%d unique=%.1f%%" % (
            x["verdict"], x["id"], name, x["official_events"], x["current_events"], x["official_unique_ratio"] * 100.0))
        lines.append("  OFFICIAL:")
        for e in x["official_samples"][:8]:
            lines.append("    %s | %s" % (e["time"], e["title"]))
        lines.append("  CURRENT CLOUD:")
        for e in x["current_samples"][:5]:
            lines.append("    %s | %s | %s" % (e["start"], e["title"], e["file"]))
        for f in x["fetches"]:
            if f["status"] != "OK":
                lines.append("  FETCH ERROR %s | %s" % (f["url"], f.get("error", "")))
        lines.append("")
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
