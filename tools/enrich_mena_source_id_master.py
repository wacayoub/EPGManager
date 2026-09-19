#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enrich docs/mena-source-id-master.csv with the programme currently displayed
by the final canonical MENA receiver feed.

The source master remains provenance-oriented (one row per source/site_id).
For every resolved XMLTV identity we resolve raw -> canonical receiver ID using
receiver-id-aliases.json, then copy the *current* final event title/description
from mena-arabic.xml.gz. Losing source rows therefore show the same receiver
programme as the winning canonical identity, while retaining their source
provenance and WINNER/LOSER status.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

DT_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")


def parse_dt(value: str):
    m = DT_RE.match((value or "").strip())
    if not m:
        return None
    digits, offset = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits, fmt)
        if not offset or offset == "Z":
            return dt.replace(tzinfo=timezone.utc)
        return datetime.strptime(digits + offset, fmt + "%z").astimezone(timezone.utc)
    except ValueError:
        return None


def read_xml(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def text(node: ET.Element, tag: str) -> str:
    vals = []
    for child in node.findall(tag):
        value = (child.text or "").strip()
        if value:
            vals.append(value)
    return " | ".join(vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--xml", required=True)
    ap.add_argument("--aliases", required=True)
    ap.add_argument("--reference-utc")
    args = ap.parse_args()

    master = Path(args.master)
    root = read_xml(Path(args.xml))
    aliases = json.loads(Path(args.aliases).read_text(encoding="utf-8"))
    mapping = aliases.get("mapping") if isinstance(aliases, dict) else {}
    mapping = mapping if isinstance(mapping, dict) else {}

    now = (
        datetime.fromisoformat(args.reference_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        if args.reference_utc
        else datetime.now(timezone.utc)
    )

    channels = {(c.get("id") or "").strip() for c in root.findall("channel")}
    events = {}
    next_events = {}
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        start = parse_dt(p.get("start") or "")
        stop = parse_dt(p.get("stop") or "")
        if not cid or not start:
            continue
        if stop and start <= now < stop:
            old = events.get(cid)
            if old is None or start > old[0]:
                events[cid] = (start, stop, p)
        elif start > now:
            old = next_events.get(cid)
            if old is None or start < old[0]:
                next_events[cid] = (start, stop, p)

    with master.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        base_fields = list(rows[0].keys()) if rows else []

    extra = [
        "receiver_canonical_id",
        "now_status",
        "now_title",
        "now_desc",
        "now_start_utc",
        "now_stop_utc",
        "next_title",
        "next_start_utc",
        "epg_snapshot_utc",
    ]
    fields = [x for x in base_fields if x not in extra] + extra

    current_count = next_count = missing_count = unresolved_count = 0
    for row in rows:
        raw = (row.get("xmltv_id") or "").strip()
        canonical = str(mapping.get(raw) or raw).strip()
        row["receiver_canonical_id"] = canonical

        current = events.get(canonical) or events.get(raw)
        nxt = next_events.get(canonical) or next_events.get(raw)
        if not raw:
            row["now_status"] = "NO_XMLTV_ID"
            unresolved_count += 1
        elif current:
            row["now_status"] = "NOW"
            current_count += 1
        elif nxt:
            row["now_status"] = "NEXT_ONLY"
            next_count += 1
        elif canonical not in channels and raw not in channels:
            row["now_status"] = "NOT_PUBLISHED"
            missing_count += 1
        else:
            row["now_status"] = "NO_CURRENT_EVENT"
            missing_count += 1

        if current:
            start, stop, p = current
            row["now_title"] = text(p, "title")
            row["now_desc"] = text(p, "desc")
            row["now_start_utc"] = start.isoformat()
            row["now_stop_utc"] = stop.isoformat() if stop else ""
        else:
            row["now_title"] = ""
            row["now_desc"] = ""
            row["now_start_utc"] = ""
            row["now_stop_utc"] = ""

        if nxt:
            start, _stop, p = nxt
            row["next_title"] = text(p, "title")
            row["next_start_utc"] = start.isoformat()
        else:
            row["next_title"] = ""
            row["next_start_utc"] = ""
        row["epg_snapshot_utc"] = now.isoformat()

    with master.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(
        "MENA_MASTER_NOW "
        f"rows={len(rows)} now={current_count} next_only={next_count} "
        f"missing={missing_count} no_xmltv={unresolved_count} snapshot={now.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
