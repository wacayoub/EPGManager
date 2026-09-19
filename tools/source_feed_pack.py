#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pack a standalone XMLTV source for receiver use.

The packer keeps only channels with real programmes, clips the guide to a
receiver-friendly future window, writes deterministic gzip bytes and emits a
small channel-id text index plus JSON stats. It never invents programmes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def read_xml(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def parse_xmltv_dt(value: str):
    value = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value)
    if not m:
        return None
    digits = m.group(1)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    off = m.group(2)
    if not off or off == "Z":
        return dt.replace(tzinfo=timezone.utc)
    sign = 1 if off[0] == "+" else -1
    minutes = sign * (int(off[1:3]) * 60 + int(off[3:5]))
    return dt.replace(tzinfo=timezone(timedelta(minutes=minutes))).astimezone(timezone.utc)


def display_name(node: ET.Element) -> str:
    for child in node.findall("display-name"):
        text = (child.text or "").strip()
        if text:
            return text
    return (node.get("id") or "").strip()


def copy_node(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-gz", required=True)
    ap.add_argument("--output-txt", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    ap.add_argument("--keep-past-hours", type=int, default=2)
    args = ap.parse_args()

    root = read_xml(Path(args.input))
    channels = {}
    for c in root.findall("channel"):
        cid = (c.get("id") or c.get("xmltv_id") or "").strip()
        if not cid:
            continue
        if not c.get("id"):
            c = copy_node(c)
            c.set("id", cid)
        channels[cid] = c

    now = datetime.now(timezone.utc)
    min_stop = now - timedelta(hours=max(0, args.keep_past_hours))
    max_start = now + timedelta(hours=max(1, args.window_hours))
    programmes = defaultdict(list)
    rejected_time = 0
    inferred_stop = 0

    raw_by_channel = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in channels:
            continue
        start = parse_xmltv_dt(p.get("start") or "")
        if not start:
            rejected_time += 1
            continue
        raw_by_channel[cid].append((start, p))

    for cid, items in raw_by_channel.items():
        items.sort(key=lambda x: x[0])
        for idx, (start, p) in enumerate(items):
            stop = parse_xmltv_dt(p.get("stop") or "")
            if not stop or stop <= start:
                next_start = items[idx + 1][0] if idx + 1 < len(items) else None
                if next_start and next_start > start:
                    p = copy_node(p)
                    p.set("stop", next_start.strftime("%Y%m%d%H%M%S +0000"))
                    stop = next_start
                    inferred_stop += 1
                else:
                    rejected_time += 1
                    continue
            if stop <= min_stop or start >= max_start:
                continue
            programmes[cid].append(p)

    active_ids = sorted((cid for cid in channels if programmes.get(cid)), key=str.casefold)
    out = ET.Element("tv", {
        "generator-info-name": "EPGManager standalone source - %s" % args.label,
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    for cid in active_ids:
        out.append(copy_node(channels[cid]))
    event_count = 0
    for cid in active_ids:
        seen = set()
        for p in sorted(programmes[cid], key=lambda x: (x.get("start") or "", x.get("stop") or "")):
            key = ((p.get("start") or "").strip(), (p.get("stop") or "").strip(),
                   "|".join((t.text or "").strip() for t in p.findall("title")))
            if key in seen:
                continue
            seen.add(key)
            out.append(copy_node(p))
            event_count += 1

    ET.indent(out, space="  ")
    xml_bytes = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    gz_bytes = gzip.compress(xml_bytes, compresslevel=9, mtime=0)
    Path(args.output_gz).write_bytes(gz_bytes)
    Path(args.output_txt).write_text(
        "\n".join("%s|%s" % (cid, display_name(channels[cid])) for cid in active_ids)
        + ("\n" if active_ids else ""),
        encoding="utf-8",
    )
    stats = {
        "label": args.label,
        "channels": len(active_ids),
        "input_channels": len(channels),
        "coverage_pct": round((len(active_ids) * 100.0 / len(channels)), 1) if channels else 0.0,
        "programmes": event_count,
        "rejected_invalid_time": rejected_time,
        "inferred_stop": inferred_stop,
        "window_hours": args.window_hours,
        "size_bytes": len(gz_bytes),
        "sha256": hashlib.sha256(gz_bytes).hexdigest(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(args.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0 if active_ids and event_count else 3


if __name__ == "__main__":
    raise SystemExit(main())
