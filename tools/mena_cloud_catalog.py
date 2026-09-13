#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an official-first Arabic XMLTV channel catalogue from iptv-org/epg.

The script scans every *.channels.xml shipped by iptv-org/epg and selects
Arabic listings. Duplicate xmltv_id entries are resolved deterministically:
broadcaster-owned/official sites first, broad Arabic TV guides second, and
generic aggregators last. Morocco is intentionally excluded because the
project already publishes a higher-quality dedicated morocco.xml.gz feed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

SITE_PRIORITY = {
    "shahid.mbc.net": 10,
    "rotana.net": 11,
    "roya-tv.com": 12,
    "aljazeera.com": 13,
    "artonline.tv": 14,
    "ayn.om": 15,
    "bein.com": 16,
    "beinsports.com": 17,
    "saudiatv.sa": 18,
    "sba.net.ae": 19,
    "dmi.gov.ae": 20,
    "elcinema.com": 100,
    "sat.tv": 120,
    "osn.com": 140,
    "epgshare01.online": 900,
}

EXCLUDED_ID_SUFFIXES = (".ma",)


def site_score(site: str) -> tuple[int, str]:
    return (SITE_PRIORITY.get(site, 500), site)


def is_arabic_channel(node: ET.Element) -> bool:
    lang = (node.get("lang") or "").strip().lower()
    return lang == "ar" or lang.startswith("ar-")


def channel_key(node: ET.Element) -> str:
    return (node.get("xmltv_id") or "").strip()


def copy_channel(node: ET.Element) -> ET.Element:
    out = ET.Element("channel")
    for key in ("site", "site_id", "lang", "xmltv_id"):
        value = node.get(key)
        if value is not None:
            out.set(key, value)
    out.text = (node.text or "").strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epg-root", required=True)
    ap.add_argument("--output-channels", required=True)
    ap.add_argument("--output-manifest", required=True)
    ap.add_argument("--include-morocco", action="store_true")
    args = ap.parse_args()

    root = Path(args.epg_root)
    files = sorted(root.glob("sites/**/*.channels.xml"))
    if not files:
        raise SystemExit("No iptv-org *.channels.xml files found under %s" % root)

    winners: dict[str, tuple[tuple[int, str], ET.Element, str, str]] = {}
    seen_ar = 0
    invalid = 0
    parse_errors = []

    for path in files:
        try:
            tree = ET.parse(str(path))
        except Exception as exc:
            parse_errors.append({"path": str(path), "error": str(exc)[:180]})
            continue
        for node in tree.getroot().findall("channel"):
            if not is_arabic_channel(node):
                continue
            seen_ar += 1
            cid = channel_key(node)
            site = (node.get("site") or path.parent.name or "unknown").strip()
            if not cid or not node.get("site_id") or not site:
                invalid += 1
                continue
            if not args.include_morocco and cid.casefold().endswith(EXCLUDED_ID_SUFFIXES):
                continue
            score = site_score(site)
            current = winners.get(cid)
            candidate = (score, copy_channel(node), site, str(path.relative_to(root)))
            if current is None or score < current[0]:
                winners[cid] = candidate

    channels = ET.Element("channels")
    selected = []
    source_counts = Counter()
    for cid in sorted(winners, key=lambda x: x.casefold()):
        score, node, site, source_file = winners[cid]
        channels.append(node)
        source_counts[site] += 1
        selected.append({
            "xmltv_id": cid,
            "name": (node.text or cid).strip(),
            "site": site,
            "site_id": node.get("site_id") or "",
            "priority": score[0],
            "source_file": source_file,
        })

    if len(selected) < 25:
        raise SystemExit("Abnormally small Arabic catalogue: %d channels" % len(selected))

    out_xml = Path(args.output_channels)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(channels, space="  ")
    out_xml.write_bytes(ET.tostring(channels, encoding="utf-8", xml_declaration=True))

    manifest = {
        "schema": 1,
        "strategy": "official-first-all-arabic",
        "source_project": "iptv-org/epg",
        "input_channel_files": len(files),
        "arabic_rows_seen": seen_ar,
        "invalid_rows_skipped": invalid,
        "unique_channels": len(selected),
        "morocco_excluded": not args.include_morocco,
        "source_counts": dict(sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "parse_errors": parse_errors[:20],
        "channels": selected,
    }
    Path(args.output_manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("MENA Arabic catalogue: %d unique channels from %d source sites" %
          (len(selected), len(source_counts)))
    for site, count in source_counts.most_common(20):
        print("  %-28s %4d" % (site, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
