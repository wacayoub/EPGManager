#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pack a standalone XMLTV source for receiver use.

Important receiver rules:
- keep every source/catalogue XMLTV ID, even when that channel currently has
  zero programmes in the selected window;
- never invent programmes;
- normalize every valid XMLTV start/stop timestamp to UTC (+0000), so Enigma2
  can apply the receiver timezone without provider-specific offset mistakes;
- keep deterministic gzip output plus a complete ID index.
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
try:
    from tools.programme_authenticity import clean_programme
except ModuleNotFoundError:
    from programme_authenticity import clean_programme


def read_xml(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def parse_xmltv_dt(value: str):
    value = (value or "").strip()
    m = re.match(r"^(\d{14}|\d{12})(?:\s*([+-]\d{4}|Z))?$", value)
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


def fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def display_name(node: ET.Element) -> str:
    for child in node.findall("display-name"):
        text = (child.text or "").strip()
        if text:
            return text
    text = (node.text or "").strip()
    return text or (node.get("id") or node.get("xmltv_id") or "").strip()


def copy_node(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def catalogue_channel(node: ET.Element) -> tuple[str, ET.Element] | None:
    cid = (node.get("id") or node.get("xmltv_id") or "").strip()
    if not cid:
        return None
    if node.get("id"):
        out = copy_node(node)
        out.set("id", cid)
        return cid, out

    out = ET.Element("channel", {"id": cid})
    name = (node.text or "").strip() or cid
    lang = (node.get("lang") or "").strip()
    attrs = {"lang": lang} if lang else {}
    ET.SubElement(out, "display-name", attrs).text = name
    site_id = (node.get("site_id") or "").strip()
    if site_id:
        ET.SubElement(out, "url", {"system": "site-id"}).text = site_id
    return cid, out


def load_channels(root: ET.Element, channels: dict[str, ET.Element]) -> None:
    for c in root.findall("channel"):
        item = catalogue_channel(c)
        if not item:
            continue
        cid, node = item
        # A channel from the actual raw XMLTV feed is richer than a catalogue
        # placeholder, so later loads replace earlier placeholders.
        channels[cid] = node


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--catalogue", help="Optional .channels.xml used to retain IDs even with zero EPG")
    ap.add_argument("--output-gz", required=True)
    ap.add_argument("--output-txt", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    ap.add_argument("--keep-past-hours", type=int, default=2)
    args = ap.parse_args()

    root = read_xml(Path(args.input))
    channels: dict[str, ET.Element] = {}

    # Load the full provider catalogue first, then let raw XMLTV channel nodes
    # replace placeholders where available.
    if args.catalogue:
        cp = Path(args.catalogue)
        if cp.exists() and cp.stat().st_size:
            load_channels(read_xml(cp), channels)
    load_channels(root, channels)

    now = datetime.now(timezone.utc)
    min_stop = now - timedelta(hours=max(0, args.keep_past_hours))
    max_start = now + timedelta(hours=max(1, args.window_hours))
    programmes = defaultdict(list)
    rejected_time = 0
    rejected_missing_title = 0
    inferred_stop = 0
    rejected_placeholder = 0
    removed_unavailable_descriptions = 0

    raw_by_channel = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in channels:
            continue
        # An empty title is not authentic programme information. Keep the
        # channel catalogue ID but do not publish a phantom programme.
        if not any((t.text or "").strip() for t in p.findall("title")):
            rejected_missing_title += 1
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
                    stop = next_start
                    inferred_stop += 1
                else:
                    rejected_time += 1
                    continue
            if stop <= min_stop or start >= max_start:
                continue

            # Canonicalize all provider offsets to UTC. This is a timezone
            # representation change only; the absolute programme instant stays
            # exactly the same.
            pp = clean_programme(p, [n.text for n in channels[cid].findall("display-name")])
            if pp is None:
                rejected_placeholder += 1
                continue
            removed_unavailable_descriptions += len(p.findall("desc")) - len(pp.findall("desc"))
            pp.set("start", fmt_utc(start))
            pp.set("stop", fmt_utc(stop))
            programmes[cid].append(pp)

    all_ids = sorted(channels, key=str.casefold)
    active_ids = sorted((cid for cid in all_ids if programmes.get(cid)), key=str.casefold)
    zero_ids = [cid for cid in all_ids if cid not in set(active_ids)]
    # Historical/future entries must not masquerade as an EPG programme
    # available *now*. Expose the current coverage as separate metadata.
    current_ids = [
        cid for cid in active_ids
        if any(
            (parse_xmltv_dt(p.get("start") or "") or now) <= now <
            (parse_xmltv_dt(p.get("stop") or "") or now)
            for p in programmes.get(cid, ())
        )
    ]
    current_id_set = set(current_ids)
    missing_now_ids = [cid for cid in all_ids if cid not in current_id_set]

    out = ET.Element("tv", {
        "generator-info-name": "EPGManager standalone source - %s" % args.label,
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    for cid in all_ids:
        out.append(copy_node(channels[cid]))

    event_count = 0
    for cid in active_ids:
        seen = set()
        for p in sorted(programmes[cid], key=lambda x: (x.get("start") or "", x.get("stop") or "")):
            key = (
                (p.get("start") or "").strip(),
                (p.get("stop") or "").strip(),
                "|".join((t.text or "").strip() for t in p.findall("title")),
            )
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
        "\n".join("%s|%s" % (cid, display_name(channels[cid])) for cid in all_ids)
        + ("\n" if all_ids else ""),
        encoding="utf-8",
    )
    stats = {
        "label": args.label,
        "channels": len(all_ids),
        "active_channels": len(active_ids),
        "zero_epg_channels": len(zero_ids),
        "zero_epg_ids": zero_ids,
        "input_channels": len(channels),
        "coverage_pct": round((len(active_ids) * 100.0 / len(all_ids)), 1) if all_ids else 0.0,
        "currently_on_air_channels": len(current_ids),
        "current_coverage_pct": round((100.0 * len(current_ids) / len(all_ids)), 1) if all_ids else 0.0,
        "currently_uncovered_ids": missing_now_ids,
        "programmes": event_count,
        "rejected_invalid_time": rejected_time,
        "rejected_missing_title": rejected_missing_title,
        "rejected_placeholder_title": rejected_placeholder,
        "removed_unavailable_descriptions": removed_unavailable_descriptions,
        "inferred_stop": inferred_stop,
        "window_hours": args.window_hours,
        "timezone_output": "UTC +0000",
        "size_bytes": len(gz_bytes),
        "sha256": hashlib.sha256(gz_bytes).hexdigest(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(args.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0 if all_ids and event_count else 3


if __name__ == "__main__":
    raise SystemExit(main())
