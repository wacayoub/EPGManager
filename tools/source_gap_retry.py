#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a retry catalogue for source channels with zero programme rows and
merge a focused retry grab back into the first-pass XMLTV result.

This does not invent EPG. It only retries channels for which the first upstream
grab returned no programme at all.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def read(path: str) -> ET.Element:
    return ET.parse(path).getroot()


def cid_from_catalogue(node: ET.Element) -> str:
    return (node.get("xmltv_id") or node.get("id") or "").strip()


def copy(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def build(catalogue: str, raw: str, output: str, fallback_catalogue: str | None = None) -> int:
    cat = read(catalogue)
    src = read(raw)
    fallback = read(fallback_catalogue) if fallback_catalogue else None
    fallback_by_id = {}
    if fallback is not None:
        fallback_by_id = {
            cid_from_catalogue(ch): ch
            for ch in fallback.findall("channel")
            if cid_from_catalogue(ch)
        }
    program_ids = {
        (p.get("channel") or "").strip()
        for p in src.findall("programme")
        if (p.get("channel") or "").strip()
    }
    out = ET.Element("channels")
    for ch in cat.findall("channel"):
        cid = cid_from_catalogue(ch)
        if cid and cid not in program_ids:
            out.append(copy(fallback_by_id.get(cid, ch)))
    ET.indent(out, space="  ")
    Path(output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    print(f"SOURCE_GAP_BUILD total={len(cat.findall('channel'))} gaps={len(out)} output={output}")
    return 0


def merge(primary: str, retry: str, output: str) -> int:
    base = read(primary)
    add = read(retry)

    channel_ids = {(c.get("id") or "").strip() for c in base.findall("channel")}
    for ch in add.findall("channel"):
        cid = (ch.get("id") or "").strip()
        if cid and cid not in channel_ids:
            base.append(copy(ch))
            channel_ids.add(cid)

    seen = set()
    for p in base.findall("programme"):
        key = (
            (p.get("channel") or "").strip(),
            (p.get("start") or "").strip(),
            (p.get("stop") or "").strip(),
            "|".join((t.text or "").strip() for t in p.findall("title")),
        )
        seen.add(key)

    added = 0
    recovered_ids = set()
    for p in add.findall("programme"):
        key = (
            (p.get("channel") or "").strip(),
            (p.get("start") or "").strip(),
            (p.get("stop") or "").strip(),
            "|".join((t.text or "").strip() for t in p.findall("title")),
        )
        if key in seen:
            continue
        base.append(copy(p))
        seen.add(key)
        recovered_ids.add(key[0])
        added += 1

    ET.indent(base, space="  ")
    Path(output).write_bytes(ET.tostring(base, encoding="utf-8", xml_declaration=True))
    print(f"SOURCE_GAP_MERGE recovered_channels={len(recovered_ids)} added_programmes={added}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--catalogue", required=True)
    b.add_argument("--raw", required=True)
    b.add_argument("--output", required=True)
    b.add_argument("--fallback-catalogue")

    m = sub.add_parser("merge")
    m.add_argument("--primary", required=True)
    m.add_argument("--retry", required=True)
    m.add_argument("--output", required=True)

    args = ap.parse_args()
    if args.cmd == "build":
        return build(args.catalogue, args.raw, args.output, args.fallback_catalogue)
    return merge(args.primary, args.retry, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
