#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare provider catalogues with the currently published source feeds.

The report distinguishes:
- source/catalogue entries exposed by the provider adapter;
- valid XMLTV IDs retained by our scraper;
- IDs published to feeds/*.xml.gz;
- active IDs with at least one programme;
- zero-EPG IDs that are intentionally retained;
- genuine catalogue IDs missing from the published feed.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import gzip
import json
import xml.etree.ElementTree as ET

ROOT = Path(".")
OUT = Path("reports/catalogue-feed-audit.json")

KEYS = (
    "morocco", "bein", "osn", "shahid", "elcinema",
    "rotana", "dubaiplus", "sport24", "stctv",
)

CATALOGUES = {
    "bein": Path("output/source-build/bein.channels.xml"),
    "osn": Path("output/source-build/osn.channels.xml"),
    "shahid": Path("output/source-build/shahid.channels.xml"),
    "elcinema": Path("output/source-build/elcinema.channels.xml"),
    "rotana": Path("output/source-build/rotana.channels.xml"),
}

RAW_SOURCES = {
    "dubaiplus": Path("output/source-build/dubaiplus.raw.xml"),
    "sport24": Path("output/source-build/sport24.raw.xml"),
    "stctv": Path("output/source-build/stctv.raw.xml"),
}


def read_root(path: Path):
    if not path.exists() or not path.stat().st_size:
        return None
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def xml_ids(path: Path) -> set[str]:
    root = read_root(path)
    if root is None:
        return set()
    out = set()
    for c in root.findall("channel"):
        cid = (c.get("id") or c.get("xmltv_id") or "").strip()
        if cid:
            out.add(cid)
    return out


def feed_state(path: Path):
    root = read_root(path)
    if root is None:
        return set(), set()
    ids = {
        (c.get("id") or "").strip()
        for c in root.findall("channel")
        if (c.get("id") or "").strip()
    }
    active = {
        (p.get("channel") or "").strip()
        for p in root.findall("programme")
        if (p.get("channel") or "").strip() in ids
    }
    return ids, active


def json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def morocco_catalogue_count():
    p = Path("output/morocco/morocco.txt")
    if p.exists():
        return sum(1 for line in p.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip())
    ids, _ = feed_state(Path("output/final/morocco.xml.gz"))
    return len(ids)


def main():
    rows = {}
    for key in KEYS:
        published_ids, active_ids = feed_state(Path("feeds") / f"{key}.xml.gz")

        source_ids = set()
        if key in CATALOGUES:
            source_ids = xml_ids(CATALOGUES[key])
        elif key in RAW_SOURCES:
            source_ids = xml_ids(RAW_SOURCES[key])
        elif key == "morocco":
            source_ids = xml_ids(Path("output/final/morocco.xml.gz"))

        source_total = len(source_ids)
        metadata_issues = []

        if key == "bein":
            r = json_file(Path("output/source-build/bein-catalogue.json"))
            source_total = int(r.get("catalogue_ids") or source_total)
        elif key == "stctv":
            r = json_file(Path("reports/stctv-scrape.json"))
            source_total = int(r.get("source_channels") or source_total)
            metadata_issues = r.get("source_metadata_issues") or []
        elif key == "rotana":
            r = json_file(Path("reports/rotana-scrape.json"))
            source_total = int(r.get("channels_configured") or source_total)
        elif key == "morocco":
            source_total = morocco_catalogue_count()

        missing = sorted(source_ids - published_ids, key=str.casefold)
        extra = sorted(published_ids - source_ids, key=str.casefold) if source_ids else []
        zero = sorted(published_ids - active_ids, key=str.casefold)

        if missing:
            status = "GAP"
        elif source_total > len(source_ids) and key == "stctv":
            status = "SOURCE_METADATA_GAP"
        else:
            status = "OK"

        rows[key] = {
            "source_or_catalogue_channels": source_total,
            "valid_catalogue_ids": len(source_ids),
            "published_ids": len(published_ids),
            "active_ids": len(active_ids),
            "zero_epg_ids": len(zero),
            "missing_from_published": missing,
            "extra_in_published": extra,
            "source_metadata_issues": metadata_issues,
            "status": status,
        }

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "sources": rows,
        "gaps": [k for k, v in rows.items() if v["status"] != "OK"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("SOURCE      SITE/CAT VALID  FEED ACTIVE ZERO STATUS")
    for key, row in rows.items():
        print(
            f"{key:10} {row['source_or_catalogue_channels']:8} "
            f"{row['valid_catalogue_ids']:5} {row['published_ids']:5} "
            f"{row['active_ids']:6} {row['zero_epg_ids']:4} {row['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
