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

from global_receiver_id_normalize import canonical_id, identity_name
from bein_receiver_id_normalize import RAW_TO_CANON

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
    ap.add_argument("--aliases")
    ap.add_argument("--reference-utc")
    args = ap.parse_args()

    master = Path(args.master)
    root = read_xml(Path(args.xml))
    mapping = {}
    if args.aliases and Path(args.aliases).exists():
        aliases = json.loads(Path(args.aliases).read_text(encoding="utf-8"))
        rawmap = aliases.get("mapping") if isinstance(aliases, dict) else {}
        if isinstance(rawmap, dict):
            mapping.update(rawmap)

    site_to_stem = {
        "osn.com": "provider-osn",
        "shahid.mbc.net": "provider-mbc",
        "rotana.net": "provider-rotana",
        "artonline.tv": "provider-art",
        "bein.com": "provider-bein",
        "beinsports.com": "provider-bein",
        "roya-tv.com": "mena-jo",
        "aljazeera.com": "mena-qa",
        "ayn.om": "mena-om",
    }

    def country_stem(cid: str):
        m = re.search(r"\.([a-z]{2})(?:@[^.]*)?$", cid or "", re.I)
        return ("mena-" + m.group(1).lower()) if m else "mena-other"

    now = (
        datetime.fromisoformat(args.reference_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        if args.reference_utc
        else datetime.now(timezone.utc)
    )

    channel_nodes = {(c.get("id") or "").strip(): c for c in root.findall("channel")}
    channels = set(channel_nodes)
    name_to_ids = {}
    for cid, ch in channel_nodes.items():
        names = [(n.text or "").strip() for n in ch.findall("display-name") if (n.text or "").strip()]
        if not names:
            names = [cid]
        for nm in names:
            key = identity_name(nm)
            if key:
                name_to_ids.setdefault(key, set()).add(cid)

    events = {}
    next_events = {}
    latest_stop = None
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        start = parse_dt(p.get("start") or "")
        stop = parse_dt(p.get("stop") or "")
        if not cid or not start:
            continue
        if stop and (latest_stop is None or stop > latest_stop):
            latest_stop = stop
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

    winner_name = {}
    for row in rows:
        cid = (row.get("xmltv_id") or "").strip()
        if cid and (row.get("source") or "").strip() == (row.get("winner_source") or "").strip():
            winner_name[cid] = (row.get("channel_name") or cid).strip()

    def resolve_canonical(row):
        raw = (row.get("xmltv_id") or "").strip()
        if not raw:
            return ""
        if raw in mapping:
            return str(mapping[raw]).strip()
        if raw in RAW_TO_CANON:
            return RAW_TO_CANON[raw]
        if raw.startswith("beIN.") and raw.endswith(".qa"):
            return raw
        source = (row.get("winner_source") or row.get("source") or "").strip()
        name = winner_name.get(raw) or (row.get("channel_name") or raw).strip()
        stem = site_to_stem.get(source)
        if source == "elcinema.com":
            stem = country_stem(raw)
        if stem == "provider-bein":
            return RAW_TO_CANON.get(raw, raw)
        if stem:
            return canonical_id(stem, raw, name)
        return raw

    drop_fields = {
        "site_id",
        "recovered_from_blank",
        "status",
        "now_start_utc",
        "now_stop_utc",
        "next_title",
        "next_start_utc",
    }
    extra = [
        "receiver_canonical_id",
        "now_status",
        "now_title",
        "now_desc",
        "epg_snapshot_utc",
    ]
    fields = [x for x in base_fields if x not in drop_fields and x not in extra] + extra

    current_count = next_count = missing_count = unresolved_count = 0
    for row in rows:
        raw = (row.get("xmltv_id") or "").strip()
        canonical = resolve_canonical(row)
        if canonical not in channels and raw not in channels and raw:
            wname = winner_name.get(raw) or (row.get("channel_name") or "").strip()
            key = identity_name(wname)
            matches = sorted(name_to_ids.get(key, set()))
            if len(matches) == 1:
                canonical = matches[0]
            elif len(matches) > 1:
                # Prefer a candidate sharing the raw country suffix when possible.
                m = re.search(r"\.([a-z]{2})(?:@[^.]*)?$", raw, re.I)
                cc = m.group(1).lower() if m else ""
                country_matches = [x for x in matches if x.casefold().endswith("." + cc)] if cc else []
                if len(country_matches) == 1:
                    canonical = country_matches[0]
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
            if latest_stop and latest_stop <= now:
                row["now_status"] = "STALE_RELEASE_MISSING_ID"
            else:
                row["now_status"] = "NOT_PUBLISHED"
            missing_count += 1
        elif latest_stop and latest_stop <= now:
            row["now_status"] = "STALE_FEED"
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
        f"missing={missing_count} no_xmltv={unresolved_count} "
        f"latest_stop={latest_stop.isoformat() if latest_stop else 'none'} snapshot={now.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
