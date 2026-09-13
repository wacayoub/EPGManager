#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate, merge with per-channel LKG and publish the MENA Arabic XMLTV feed."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")


def read_xml(path: Path):
    if not path or not path.is_file() or path.stat().st_size == 0:
        return None
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def display_name(channel: ET.Element) -> str:
    names = channel.findall("display-name")
    for n in names:
        if (n.text or "").strip():
            return (n.text or "").strip()
    return channel.get("id") or ""


def programme_groups(root):
    groups = defaultdict(list)
    if root is None:
        return groups
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            groups[cid].append(p)
    return groups


def channel_map(root):
    out = {}
    if root is None:
        return out
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            out[cid] = c
    return out


def event_key(p: ET.Element):
    title = p.find("title")
    return (
        (p.get("channel") or "").strip(),
        (p.get("start") or "").strip(),
        ((title.text if title is not None else "") or "").strip().casefold(),
    )


def useful(rows) -> bool:
    if len(rows) < 3:
        return False
    starts = {(p.get("start") or "").strip() for p in rows if (p.get("start") or "").strip()}
    return len(starts) >= 3


def copy_element(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    candidate_path = Path(args.candidate)
    previous_path = Path(args.previous) if args.previous else None
    catalog = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    source_by_id = {x["xmltv_id"]: x for x in catalog.get("channels", [])}

    candidate = read_xml(candidate_path)
    previous = read_xml(previous_path) if previous_path and previous_path.is_file() else None
    cand_channels, prev_channels = channel_map(candidate), channel_map(previous)
    cand_programmes, prev_programmes = programme_groups(candidate), programme_groups(previous)

    selected_programmes = {}
    states = Counter()
    ids = set(source_by_id)
    ids.update(cand_programmes)
    for cid in sorted(ids, key=str.casefold):
        fresh = cand_programmes.get(cid, [])
        old = prev_programmes.get(cid, [])
        if useful(fresh):
            selected_programmes[cid] = fresh
            states["fresh"] += 1
        elif cid in source_by_id and useful(old):
            selected_programmes[cid] = old
            states["lkg"] += 1
        else:
            states["no_epg"] += 1

    out_root = ET.Element("tv", {
        "generator-info-name": "EPGManager MENA Arabic Cloud",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    seen_programmes = set()
    programme_count = 0
    arabic_titles = 0
    source_counts = Counter()
    text_lines = []

    for cid in sorted(selected_programmes, key=str.casefold):
        source_meta = source_by_id.get(cid, {})
        c = cand_channels.get(cid)
        if c is None:
            c = prev_channels.get(cid)
        if c is None:
            c = ET.Element("channel", {"id": cid})
            ET.SubElement(c, "display-name", {"lang": "ar"}).text = source_meta.get("name") or cid
        out_root.append(copy_element(c))
        site = source_meta.get("site") or "unknown"
        source_counts[site] += 1
        text_lines.append("%s|%s|%s" % (cid, display_name(c), site))

    for cid in sorted(selected_programmes, key=str.casefold):
        for p in sorted(selected_programmes[cid], key=lambda x: x.get("start") or ""):
            key = event_key(p)
            if not key[1] or key in seen_programmes:
                continue
            seen_programmes.add(key)
            cp = copy_element(p)
            out_root.append(cp)
            programme_count += 1
            t = cp.find("title")
            if t is not None and AR_RE.search((t.text or "")):
                arabic_titles += 1

    channel_count = len(selected_programmes)
    if channel_count < 25 or programme_count < 100:
        raise SystemExit("Candidate/LKG output too small: %d channels / %d programmes" %
                         (channel_count, programme_count))

    ET.indent(out_root, space="  ")
    xml_bytes = ET.tostring(out_root, encoding="utf-8", xml_declaration=True)
    gz_bytes = gzip.compress(xml_bytes, compresslevel=9)
    sha = hashlib.sha256(gz_bytes).hexdigest()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mena-arabic.xml.gz").write_bytes(gz_bytes)
    (out_dir / "mena-arabic.txt").write_text("\n".join(text_lines) + "\n", encoding="utf-8")
    manifest = {
        "schema": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "channels": channel_count,
        "programmes": programme_count,
        "catalogue_channels": catalog.get("unique_channels", 0),
        "fresh_channels": states["fresh"],
        "lkg_channels": states["lkg"],
        "no_epg_channels": states["no_epg"],
        "arabic_title_ratio": round(arabic_titles / float(programme_count), 4) if programme_count else 0,
        "source_counts": dict(sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "size_bytes": len(gz_bytes),
        "sha256": sha,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("MENA output: %d channels / %d programmes / fresh=%d lkg=%d no_epg=%d / %.1f%% Arabic titles" %
          (channel_count, programme_count, states["fresh"], states["lkg"], states["no_epg"],
           manifest["arabic_title_ratio"] * 100.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
