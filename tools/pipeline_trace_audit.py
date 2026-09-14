#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trace selected XMLTV IDs through the MENA production pipeline.

Diagnostic only. Records catalogue source and programme counts/samples at raw,
merged and final combined stages so a lost timetable can be localized exactly.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

DEFAULT_TARGETS = [
    "AlQuranAlKareemTV.sa@SD",
    "AlHadath.sa@SD",
    "MBC3.ae@SD",
    "MBC5.ae@SD",
    "MBCDrama.ae@SD",
    "MBCIraq.iq@SD",
    "MBCMasr.eg@SD",
    "MBCMasr2.eg@SD",
    "MBCPlusDrama.sa@SD",
    "AlArabiyaEnglish.sa@SD",
    "AlArabiyaPrograms.ae",
    "Wanasah.ae@SD",
    "AlEkhbariya.sa@SD",
    "AlSaudiyaAlaan.sa@SD",
    "AlSunnahAlNabawiyahTV.sa@SD",
    "SaudiThaqafiyaTV.sa@SD",
    "Tarab.sa@SD",
    "SpacetoonArabic.ae@SD",
]


def read_root(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return ET.Element("tv")
    data = p.read_bytes()
    if p.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def collect(path):
    root = read_root(path)
    channels = {(c.get("id") or "").strip() for c in root.findall("channel")}
    rows = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            rows[cid].append(p)
    return channels, rows


def text(p, tag):
    n = p.find(tag)
    return ((n.text or "").strip() if n is not None else "")


def stage_info(channels, rows, cid):
    ps = sorted(rows.get(cid, []), key=lambda p: p.get("start") or "")
    return {
        "channel_present": cid in channels,
        "events": len(ps),
        "samples": [
            {
                "start": p.get("start") or "",
                "stop": p.get("stop") or "",
                "title": text(p, "title"),
                "desc": text(p, "desc")[:160],
            }
            for p in ps[:3]
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--targets", nargs="*")
    args = ap.parse_args()

    targets = args.targets or DEFAULT_TARGETS
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    by_id = {x.get("xmltv_id", ""): x for x in catalog.get("channels", []) or []}
    stages = {
        "raw": collect(args.raw),
        "merged": collect(args.merged),
        "final": collect(args.final),
    }

    results = []
    for cid in targets:
        cat = by_id.get(cid, {})
        item = {
            "id": cid,
            "catalog": {
                "present": bool(cat),
                "name": cat.get("name", ""),
                "site": cat.get("site", ""),
                "site_id": cat.get("site_id", ""),
                "override_site": cat.get("override_site", ""),
            },
        }
        for name, (channels, rows) in stages.items():
            item[name] = stage_info(channels, rows, cid)
        counts = [item[s]["events"] for s in ("raw", "merged", "final")]
        if counts[0] > 0 and counts[1] == 0:
            loss = "RAW_TO_MERGED"
        elif counts[1] > 0 and counts[2] == 0:
            loss = "MERGED_TO_FINAL"
        elif counts[0] == 0:
            loss = "NO_PRIMARY_RAW"
        elif counts[2] > 0:
            loss = "SURVIVES_FINAL"
        else:
            loss = "OTHER"
        item["diagnosis"] = loss
        results.append(item)

    out = {"schema": 1, "mode": "mena-pipeline-stage-trace", "targets": results}
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["MENA PIPELINE TRACE", "targets=%d" % len(results), ""]
    for x in results:
        c = x["catalog"]
        lines.append("- %s | catalog=%s site=%s override=%s | raw=%d merged=%d final=%d | %s" % (
            x["id"], "YES" if c["present"] else "NO", c["site"] or "-", c["override_site"] or "-",
            x["raw"]["events"], x["merged"]["events"], x["final"]["events"], x["diagnosis"]))
        for stage in ("raw", "merged", "final"):
            samples = x[stage]["samples"]
            if samples:
                lines.append("    %s: %s" % (stage, " | ".join(s["title"] for s in samples if s["title"])))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Pipeline trace targets=%d" % len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
