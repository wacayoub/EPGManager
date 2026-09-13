#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def read_xml(path: Path | None):
    if not path or not path.is_file() or path.stat().st_size == 0:
        return None
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def clone(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def channel_map(root):
    out = {}
    if root is not None:
        for c in root.findall("channel"):
            cid = (c.get("id") or "").strip()
            if cid:
                out[cid] = c
    return out


def programme_map(root):
    out = defaultdict(list)
    if root is not None:
        for p in root.findall("programme"):
            cid = (p.get("channel") or "").strip()
            if cid:
                out[cid].append(p)
    return out


def useful(rows) -> bool:
    starts = {(p.get("start") or "").strip() for p in rows if (p.get("start") or "").strip()}
    return len(rows) >= 3 and len(starts) >= 3


def event_key(p: ET.Element):
    title = p.find("title")
    return (
        (p.get("channel") or "").strip(),
        (p.get("start") or "").strip(),
        ((title.text if title is not None else "") or "").strip().casefold(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--basename", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--min-channels", type=int, default=10)
    ap.add_argument("--min-programmes", type=int, default=50)
    args = ap.parse_args()

    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    meta = {x["xmltv_id"]: x for x in catalog.get("channels", []) if x.get("xmltv_id")}
    candidate = read_xml(Path(args.candidate))
    previous = read_xml(Path(args.previous)) if args.previous else None
    cand_channels, prev_channels = channel_map(candidate), channel_map(previous)
    cand_programmes, prev_programmes = programme_map(candidate), programme_map(previous)

    states = Counter()
    selected = {}
    for cid in sorted(set(meta) | set(cand_programmes) | set(prev_programmes), key=str.casefold):
        fresh = cand_programmes.get(cid, [])
        old = prev_programmes.get(cid, [])
        if useful(fresh):
            selected[cid] = fresh
            states["fresh"] += 1
        elif cid in meta and useful(old):
            selected[cid] = old
            states["lkg"] += 1
        else:
            states["no_epg"] += 1

    root = ET.Element("tv", {
        "generator-info-name": f"EPGManager SAT.TV Cloud - {args.label}",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    txt = []
    seen = set()
    programmes = 0

    for cid in sorted(selected, key=str.casefold):
        c = cand_channels.get(cid) or prev_channels.get(cid)
        if c is None:
            c = ET.Element("channel", {"id": cid})
            ET.SubElement(c, "display-name").text = meta.get(cid, {}).get("name") or cid
        else:
            c = clone(c)
            if c.find("display-name") is None:
                ET.SubElement(c, "display-name").text = meta.get(cid, {}).get("name") or cid
        root.append(c)
        txt.append(f"{cid}|{meta.get(cid, {}).get('name') or cid}")

    for cid in sorted(selected, key=str.casefold):
        for p in sorted(selected[cid], key=lambda x: x.get("start") or ""):
            key = event_key(p)
            if not key[1] or key in seen:
                continue
            seen.add(key)
            root.append(clone(p))
            programmes += 1

    channels = len(selected)
    if channels < args.min_channels or programmes < args.min_programmes:
        raise SystemExit(f"{args.label} output too small: {channels} channels / {programmes} programmes")

    ET.indent(root, space="  ")
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    gz_bytes = gzip.compress(xml_bytes, compresslevel=9, mtime=0)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.basename}.xml.gz").write_bytes(gz_bytes)
    (out / f"{args.basename}.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
    manifest = {
        "schema": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": "sat.tv",
        "satellite": args.label,
        "status": "ok",
        "catalogue_channels": catalog.get("unique_channels", 0),
        "channels": channels,
        "programmes": programmes,
        "fresh_channels": states["fresh"],
        "lkg_channels": states["lkg"],
        "no_epg_channels": states["no_epg"],
        "size_bytes": len(gz_bytes),
        "sha256": hashlib.sha256(gz_bytes).hexdigest(),
    }
    (out / f"{args.basename}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{args.label}: {channels} channels / {programmes} programmes / "
        f"fresh={states['fresh']} lkg={states['lkg']} no_epg={states['no_epg']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
