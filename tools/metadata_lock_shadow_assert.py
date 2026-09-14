#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assert that regular-channel titles survive the metadata-locked shadow merge."""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

TARGETS = [
    "MBC3.ae@SD",
    "MBC5.ae@SD",
    "MBCDrama.ae@SD",
    "MBCIraq.iq@SD",
    "MBCMasr.eg@SD",
    "MBCMasr2.eg@SD",
    "MBCPlusDrama.sa@SD",
    "AlHadath.sa@SD",
    "AlQuranAlKareemTV.sa@SD",
]


def read_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def text(p, tag="title"):
    n = p.find(tag)
    return ((n.text or "").strip() if n is not None else "")


def norm(v):
    return " ".join(re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", (v or "").casefold()).split())


def by_channel(root):
    out = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            out[cid].append(p)
    for rows in out.values():
        rows.sort(key=lambda x: x.get("start") or "")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--enforce", action="store_true")
    a = ap.parse_args()

    raw = by_channel(read_root(a.raw))
    merged = by_channel(read_root(a.merged))
    final = by_channel(read_root(a.final))
    results = []
    total_changes = 0

    for cid in TARGETS:
        rmap = {(p.get("start") or ""): p for p in raw.get(cid, [])}
        mmap = {(p.get("start") or ""): p for p in merged.get(cid, [])}
        fmap = {(p.get("start") or ""): p for p in final.get(cid, [])}
        common = sorted(set(rmap) & set(mmap))
        changed = []
        for start in common:
            rt = text(rmap[start])
            mt = text(mmap[start])
            if norm(rt) != norm(mt):
                changed.append({"start": start, "raw": rt, "merged": mt})
        total_changes += len(changed)
        results.append({
            "id": cid,
            "raw_events": len(rmap),
            "merged_events": len(mmap),
            "final_events": len(fmap),
            "matched_slots": len(common),
            "title_changes": changed[:20],
            "raw_preview": [text(x) for x in raw.get(cid, [])[:3]],
            "merged_preview": [text(x) for x in merged.get(cid, [])[:3]],
            "final_preview": [text(x) for x in final.get(cid, [])[:3]],
        })

    # A non-zero count is diagnostic in shadow mode. Production integration can
    # later call this tool with --enforce once the remaining arbitration case is
    # understood and eliminated.
    status = "PASS" if total_changes == 0 else "FAIL"
    out = {"schema": 2, "status": status, "title_changes": total_changes, "targets": results}
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["METADATA LOCK SHADOW ASSERT", "status=%s title_changes=%d" % (status, total_changes), ""]
    for x in results:
        lines.append("- %s raw=%d merged=%d final=%d matched=%d title_changes=%d" % (
            x["id"], x["raw_events"], x["merged_events"], x["final_events"],
            x["matched_slots"], len(x["title_changes"])))
        if x["raw_preview"]:
            lines.append("    raw=%s" % " | ".join(x["raw_preview"]))
        if x["merged_preview"]:
            lines.append("    merged=%s" % " | ".join(x["merged_preview"]))
        for c in x["title_changes"][:3]:
            lines.append("    CHANGE %s | %s -> %s" % (c["start"], c["raw"], c["merged"]))
    payload = "\n".join(lines) + "\n"
    Path(a.text).write_text(payload, encoding="utf-8")
    print(payload, end="")
    if a.enforce and status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
