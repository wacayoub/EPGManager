#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prune ElCinema donor channels without real EPG and report description coverage."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET


def _text(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", required=True)
    ap.add_argument("--programmes", required=True)
    ap.add_argument("--output-channels", required=True)
    ap.add_argument("--output-programmes", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    ch_root = ET.parse(args.channels).getroot()
    tv_root = ET.parse(args.programmes).getroot()

    counts = Counter()
    desc_counts = Counter()
    for p in tv_root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if not cid:
            continue
        counts[cid] += 1
        if _text(p, "desc"):
            desc_counts[cid] += 1

    keep = {cid for cid, n in counts.items() if n > 0}

    out_channels = ET.Element("channels")
    removed = []
    kept = []
    for ch in ch_root.findall("channel"):
        cid = (ch.get("xmltv_id") or "").strip()
        name = (ch.text or cid).strip()
        row = {
            "xmltv_id": cid,
            "name": name,
            "programmes": counts.get(cid, 0),
            "descriptions": desc_counts.get(cid, 0),
        }
        if cid in keep:
            out_channels.append(ch)
            kept.append(row)
        else:
            removed.append(row)

    out_tv = ET.Element("tv", tv_root.attrib)
    # Keep channel declarations if the grabber emitted them, but only for live EPG IDs.
    for ch in tv_root.findall("channel"):
        if (ch.get("id") or "").strip() in keep:
            out_tv.append(ch)
    for p in tv_root.findall("programme"):
        if (p.get("channel") or "").strip() in keep:
            out_tv.append(p)

    ET.indent(out_channels, space="  ")
    ET.indent(out_tv, space="  ")
    Path(args.output_channels).write_bytes(
        ET.tostring(out_channels, encoding="utf-8", xml_declaration=True)
    )
    Path(args.output_programmes).write_bytes(
        ET.tostring(out_tv, encoding="utf-8", xml_declaration=True)
    )

    total_programmes = sum(x["programmes"] for x in kept)
    total_desc = sum(x["descriptions"] for x in kept)
    report = {
        "schema": 1,
        "channels_input": len(ch_root.findall("channel")),
        "channels_kept": len(kept),
        "channels_removed_zero_epg": len(removed),
        "programmes_kept": total_programmes,
        "programmes_with_description": total_desc,
        "description_ratio": (float(total_desc) / total_programmes) if total_programmes else 0.0,
        "kept": kept,
        "removed": removed,
    }
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "ELCINEMA_PRUNE "
        f"input={report['channels_input']} kept={report['channels_kept']} "
        f"removed_zero={report['channels_removed_zero_epg']} "
        f"programmes={total_programmes} desc={total_desc} "
        f"desc_ratio={report['description_ratio']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
