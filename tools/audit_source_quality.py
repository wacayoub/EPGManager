#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-destructive, source-aware XMLTV content-quality audit.

Designed to run AFTER publish_lkg on the exact published GitHub feed. Does not
change provider programme rows, mappings or the last-known-good mechanism.
"""
import argparse
import gzip
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
try:
    from bein_occasional_policy import is_occasional_bein
    from programme_authenticity import generated_placeholder
except ModuleNotFoundError:
    from tools.bein_occasional_policy import is_occasional_bein
    from tools.programme_authenticity import generated_placeholder

BAD_DESC = (
    "دليل البرامج غير متوفر", "جدول البرامج غير متوفر",
    "program guide not available", "epg not available",
    "program information unavailable", "programme information unavailable",
)
BAD_TITLE = ("program guide", "epg not available", "دليل البرامج غير متوفر")


def parse_time(value):
    m = re.match(r"^(\d{14}|\d{12})(?:\s*([+-]\d{4}|Z))?", (value or "").strip())
    if not m:
        return None
    digits, tz = m.groups()
    dt = datetime.strptime(digits, "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M")
    if not tz or tz == "Z":
        return dt.replace(tzinfo=timezone.utc)
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5]))
    if tz.startswith("-"):
        offset = -offset
    return dt.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)


def norm(value):
    return re.sub(r"[\W_]+", "", (value or "").casefold(), flags=re.UNICODE)


def analyze(path, source, now):
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b" or str(path).endswith(".gz"):
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    if root.tag.rsplit("}", 1)[-1] != "tv":
        raise ValueError("Not XMLTV")
    names = {}
    for ch in root.findall("channel"):
        cid = ch.get("id") or ""
        names[cid] = [(n.text or "").strip() for n in ch.findall("display-name")]
    occasional = {cid for cid, display in names.items()
                  if source == 'bein' and is_occasional_bein(cid, display)}
    by_id = defaultdict(list)
    bad_channels = defaultdict(int)
    future_counts = defaultdict(int)
    future_titles = defaultdict(set)
    missing_desc = 0
    placeholder_desc = 0
    placeholder_title = 0
    all_programmes = 0
    future_programmes = 0
    invalid_dates = 0
    for p in root.findall("programme"):
        cid = p.get("channel") or ""
        start = parse_time(p.get("start"))
        stop = parse_time(p.get("stop"))
        if not start or not stop or stop <= start:
            invalid_dates += 1
            continue
        all_programmes += 1
        if stop <= now:
            continue
        future_programmes += 1
        future_counts[cid] += 1
        by_id[cid].append((start, stop))
        title = next(((x.text or "").strip() for x in p.findall("title") if x.text), "")
        if title:
            future_titles[cid].add(norm(title))
        desc = next(((x.text or "").strip() for x in p.findall("desc") if x.text), "")
        if not desc:
            missing_desc += 1
        if any(t in desc.casefold() for t in BAD_DESC):
            placeholder_desc += 1
            bad_channels[cid] += 1
        if generated_placeholder(title) or any(t in title.casefold() for t in BAD_TITLE) or (
            len(norm(title)) >= 7 and norm(title) in
            {norm(n) for n in names.get(cid, []) if len(norm(n)) >= 7}
        ):
            placeholder_title += 1
            bad_channels[cid] += 1
    end_window = now + timedelta(hours=48)
    holes = {}
    short = []
    occasional_without_events = []
    occasional_with_short_or_gap = []
    for cid in names:
        intervals = sorted(by_id.get(cid, []))
        cursor = now
        gap = timedelta()
        for start, stop in intervals:
            if stop <= cursor:
                continue
            if start > cursor:
                gap = max(gap, min(start, end_window) - min(cursor, end_window))
            cursor = max(cursor, stop)
        if cid in occasional:
            if not intervals:
                occasional_without_events.append(cid)
            elif cursor < now + timedelta(hours=24) or gap > timedelta(minutes=90):
                occasional_with_short_or_gap.append(cid)
        else:
            if not intervals or cursor < now + timedelta(hours=24):
                short.append(cid)
            if gap > timedelta(minutes=90):
                holes[cid] = int(gap.total_seconds() / 60)
    quality_bad = sorted(bad_channels, key=lambda x: (-bad_channels[x], x))
    # Low diversity is a *suspicion* only (some genuine thematic guides repeat).
    # A high placeholder ratio is stronger evidence of artificial schedule rows.
    repeated_all = sorted(cid for cid,count in future_counts.items()
                          if count >= 10 and len(future_titles.get(cid, set())) <= 2)
    repeated = [cid for cid in repeated_all if cid not in occasional]
    result = {
        "source": source,
        "generated_utc": now.isoformat(),
        "source_file": Path(path).name,
        "channels": len(names),
        "programmes": all_programmes,
        "future_programmes": future_programmes,
        "channels_with_future_epg": sum(bool(future_counts.get(cid)) for cid in names),
        "channels_without_future_epg": [cid for cid in names if not future_counts.get(cid)],
        "channels_with_shorter_than_24h": sorted(short),
        "channels_with_internal_gap_over_90min": holes,
        "future_programmes_without_description": missing_desc,
        "future_programmes_with_unavailable_description": placeholder_desc,
        "future_programmes_with_placeholder_or_channel_title": placeholder_title,
        "suspected_placeholder_channels": [{"id": cid, "count": bad_channels[cid]} for cid in quality_bad],
        "future_programmes_by_channel": dict(sorted(future_counts.items())),
        "suspected_repeated_title_channels": repeated,
        "occasional_event_channel_ids": sorted(occasional),
        "occasional_event_channels_without_future_match": sorted(occasional_without_events),
        "occasional_event_channels_with_short_or_gapped_schedule": sorted(occasional_with_short_or_gap),
        "occasional_event_channels_with_repeated_titles": sorted(set(repeated_all) & occasional),
        "occasional_event_channels_are_not_continuous_tv": True,
        "invalid_dates": invalid_dates,
    }
    result["quality_status"] = (
        "WARN" if (short or holes or bad_channels or invalid_dates) else "PASS"
    )
    return result


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--input", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--report", required=True)
    ns = a.parse_args()
    data = analyze(ns.input, ns.source, datetime.now(timezone.utc))
    dst = Path(ns.report)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("%s %s: %s/%s future channels; %s short; %s gaps; %s placeholder titles; %s placeholder descriptions; %s missing descriptions" %
          (data["quality_status"], ns.source, data["channels_with_future_epg"], data["channels"],
           len(data["channels_with_shorter_than_24h"]),
           len(data["channels_with_internal_gap_over_90min"]),
           data["future_programmes_with_placeholder_or_channel_title"],
           data["future_programmes_with_unavailable_description"],
           data["future_programmes_without_description"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
