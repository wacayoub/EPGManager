#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed gate for publishing source quality/48h reports.

Reports must describe the *actually published* gzip, not a rejected candidate.
This tool only reads publication metadata and the staged published bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

MAX_GZIP = 8 * 1024 * 1024


def gate(source, decision_file, feed_dir):
    if not re.fullmatch(r"[a-z][a-z0-9]{1,25}", source):
        raise ValueError("invalid source")
    result = json.loads(Path(decision_file).read_text(encoding="utf-8"))
    row = result["sources"][source]
    decision = row.get("decision")
    if decision == "KEEP_LKG" and row.get("published") is False:
        return "KEEP"
    if decision != "PUBLISH" or row.get("published") is not True:
        raise ValueError("unverified publication decision")
    base = Path(feed_dir)
    meta = json.loads((base / (source + ".json")).read_text(encoding="utf-8"))
    if meta.get("label") != source:
        raise ValueError("published source metadata label mismatch")
    digest = meta.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("published SHA256 is invalid")
    expected = meta.get("size_bytes")
    if not isinstance(expected, int) or isinstance(expected, bool) or not 0 < expected <= MAX_GZIP:
        raise ValueError("published size is invalid")
    feed = base / (source + ".xml.gz")
    if feed.is_symlink() or not feed.is_file() or feed.stat().st_size != expected:
        raise ValueError("published XMLTV missing, symlinked, or wrong size")
    if hashlib.sha256(feed.read_bytes()).hexdigest() != digest:
        raise ValueError("published gzip SHA256 differs from declared metadata")
    if not isinstance(meta.get("generated_utc"), str) or not meta["generated_utc"]:
        raise ValueError("missing original provider publication timestamp")
    return "REBUILD"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--feeds-dir", default="feeds")
    args = parser.parse_args()
    print(gate(args.source, args.decision, args.feeds_dir))


if __name__ == "__main__":
    main()
