#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and apply an official OSN English-title donor.

MENA Cloud keeps the Arabic OSN guide as the schedule/description authority.
This helper performs two deliberately narrow operations:

1. ``build-channels`` clones only the canonical OSN rows already selected from
   ``osn.com`` in catalog.json and changes ``lang`` to ``en``. No channel ID is
   invented here; the IDs have already passed the catalogue identity guards.
2. ``enrich`` matches donor programmes to the published provider-osn shard by
   canonical channel + exact start/stop instant. Only ``<title>`` is replaced,
   and only when the official English donor contains Latin text. Start/stop,
   description, categories and every other field remain untouched.

Legacy Ya Hala mapping IDs use the donor of their verified canonical service.
The script never fuzzy-matches time slots or titles.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


LEGACY_ALIASES = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}

# Frozen canonical OSN set currently published by provider-osn. Keeping this
# explicit prevents unrelated channels hosted by osn.com from becoming donors.
CANONICAL_OSN_IDS = {
    "OSNComedy.ae@SD",
    "OSNKids.ae@SD",
    "OSNMezze.ae@SD",
    "OSNMoviesAction.ae@SD",
    "OSNMoviesHollywood.ae@SD",
    "OSNMoviesPremiere.ae@SD",
    "OSNShowcase.ae@SD",
    "OSNtv Crime.sa",
    "OSNtv Documentary.sa",
    "OSNtv iQIYI.sa",
    "OSNtv Movies Comedy.sa",
    "OSNtv Movies Family.sa",
    "OSNtv Movies Horror.sa",
    "OSNtv Now.sa",
    "OSNtv One.sa",
    "OSNtv Pop Up.sa",
    "OSNtv Showcase Classics.sa",
    "OSNYahala.ae@SD",
    "OSNYahalaAflam.ae@SD",
    "OSNYahalaBilArabi.ae@SD",
}

LATIN_RE = re.compile(r"[A-Za-z]")


def _read_xml(path: Path) -> ET.Element:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def _write_xml_gz(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def _parse_xmltv_time(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    # XMLTV commonly uses YYYYMMDDHHMMSS +ZZZZ or YYYYMMDDHHMM +ZZZZ.
    parts = value.split()
    stamp = parts[0]
    fmt = "%Y%m%d%H%M%S" if len(stamp) >= 14 else "%Y%m%d%H%M"
    stamp = stamp[:14] if len(stamp) >= 14 else stamp[:12]
    try:
        dt = datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    if len(parts) >= 2 and re.fullmatch(r"[+-]\d{4}", parts[1]):
        sign = 1 if parts[1][0] == "+" else -1
        hours = int(parts[1][1:3])
        mins = int(parts[1][3:5])
        offset = sign * (hours * 3600 + mins * 60)
        return int(dt.timestamp()) - offset
    # All MENA Cloud implicit timestamps are normalized consistently; using the
    # naive epoch value is sufficient for exact equality inside the same run.
    return int(dt.timestamp())


def _slot(programme: ET.Element, cid: str) -> tuple[str, int, int] | None:
    start = _parse_xmltv_time(programme.get("start") or "")
    stop = _parse_xmltv_time(programme.get("stop") or "")
    if start is None or stop is None:
        return None
    return cid, start, stop


def build_channels(args) -> int:
    manifest = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    rows = {row.get("xmltv_id"): row for row in manifest.get("channels", []) if row.get("xmltv_id")}
    root = ET.Element("channels")
    missing = []
    wrong_site = []
    for cid in sorted(CANONICAL_OSN_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            missing.append(cid)
            continue
        if row.get("site") != "osn.com":
            wrong_site.append("%s:%s" % (cid, row.get("site") or ""))
            continue
        site_id = str(row.get("site_id") or "").strip()
        name = str(row.get("name") or cid).strip()
        if not site_id:
            missing.append(cid + "(site_id)")
            continue
        ch = ET.SubElement(root, "channel", {
            "site": "osn.com",
            "site_id": site_id,
            "lang": "en",
            "xmltv_id": cid,
        })
        ch.text = name

    if missing or wrong_site:
        raise SystemExit("OSN English donor identity guard failed: missing=%s wrong_site=%s" % (
            ",".join(missing) or "NONE", ",".join(wrong_site) or "NONE"))
    if len(root.findall("channel")) != len(CANONICAL_OSN_IDS):
        raise SystemExit("OSN English donor channel count mismatch")

    ET.indent(root, space="  ")
    Path(args.output).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    print("OSN English donor catalogue: channels=%d site=osn.com lang=en" % len(CANONICAL_OSN_IDS))
    return 0


def enrich(args) -> int:
    target_path = Path(args.xml)
    root = _read_xml(target_path)
    donor = _read_xml(Path(args.donor))

    donor_titles = {}
    donor_events_by_id = Counter()
    donor_english_by_id = Counter()
    duplicate_donor_slots = 0
    for p in donor.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in CANONICAL_OSN_IDS:
            continue
        donor_events_by_id[cid] += 1
        key = _slot(p, cid)
        if key is None:
            continue
        title_node = p.find("title")
        title = ((title_node.text or "").strip() if title_node is not None else "")
        if not title or not LATIN_RE.search(title):
            continue
        donor_english_by_id[cid] += 1
        if key in donor_titles and donor_titles[key] != title:
            duplicate_donor_slots += 1
            continue
        donor_titles[key] = title

    if duplicate_donor_slots:
        raise SystemExit("OSN donor has conflicting exact slots: %d" % duplicate_donor_slots)

    canonical_present = {
        (c.get("id") or "").strip()
        for c in root.findall("channel")
        if (c.get("id") or "").strip() in CANONICAL_OSN_IDS
    }
    missing_canonical = sorted(CANONICAL_OSN_IDS - canonical_present, key=str.casefold)
    if missing_canonical:
        raise SystemExit("provider-osn missing canonical IDs: %s" % ", ".join(missing_canonical))

    stats = defaultdict(lambda: Counter())
    total = Counter()
    samples = []
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        canonical = LEGACY_ALIASES.get(cid, cid)
        if canonical not in CANONICAL_OSN_IDS:
            continue
        total["events"] += 1
        stats[cid]["events"] += 1
        key = _slot(p, canonical)
        donor_title = donor_titles.get(key) if key is not None else None
        if not donor_title:
            total["unmatched_or_no_english"] += 1
            stats[cid]["unmatched_or_no_english"] += 1
            continue
        total["matched"] += 1
        stats[cid]["matched"] += 1
        title_node = p.find("title")
        if title_node is None:
            title_node = ET.SubElement(p, "title")
        old = (title_node.text or "").strip()
        if old != donor_title or (title_node.get("lang") or "").casefold() != "en":
            title_node.text = donor_title
            title_node.set("lang", "en")
            total["replaced"] += 1
            stats[cid]["replaced"] += 1
            if len(samples) < 30 and old != donor_title:
                samples.append({"id": cid, "old": old, "new": donor_title})

    _write_xml_gz(target_path, root)

    channel_rows = []
    for cid in sorted(stats, key=str.casefold):
        s = stats[cid]
        matched_pct = 100.0 * s["matched"] / max(1, s["events"])
        channel_rows.append({
            "id": cid,
            "canonical": LEGACY_ALIASES.get(cid, cid),
            "events": s["events"],
            "matched": s["matched"],
            "matched_pct": round(matched_pct, 1),
            "replaced": s["replaced"],
            "unmatched_or_no_english": s["unmatched_or_no_english"],
        })

    payload = {
        "schema": 1,
        "policy": "official OSN EN title donor by exact canonical channel/start/stop; AR schedule+description preserved",
        "canonical_ids": len(CANONICAL_OSN_IDS),
        "donor_events": int(sum(donor_events_by_id.values())),
        "donor_english_titles": int(sum(donor_english_by_id.values())),
        "events": int(total["events"]),
        "matched": int(total["matched"]),
        "replaced": int(total["replaced"]),
        "unmatched_or_no_english": int(total["unmatched_or_no_english"]),
        "match_pct": round(100.0 * total["matched"] / max(1, total["events"]), 1),
        "channels": channel_rows,
        "samples": samples,
    }
    Path(args.report_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "OSN OFFICIAL BILINGUAL ENRICHMENT",
        "canonical_ids=%d donor_events=%d donor_english_titles=%d" % (
            payload["canonical_ids"], payload["donor_events"], payload["donor_english_titles"]),
        "provider_events=%d exact_title_matches=%d replaced=%d unmatched_or_no_english=%d match=%.1f%%" % (
            payload["events"], payload["matched"], payload["replaced"],
            payload["unmatched_or_no_english"], payload["match_pct"]),
        "policy=keep Arabic schedule/description; replace title only from official English OSN exact slot",
        "",
    ]
    for row in channel_rows:
        lines.append("%s events=%d matched=%d (%.1f%%) replaced=%d unmatched=%d" % (
            row["id"], row["events"], row["matched"], row["matched_pct"],
            row["replaced"], row["unmatched_or_no_english"]))
    Path(args.report_text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    print(lines[2])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-channels")
    b.add_argument("--catalog-manifest", required=True)
    b.add_argument("--output", required=True)
    b.set_defaults(func=build_channels)

    e = sub.add_parser("enrich")
    e.add_argument("--xml", required=True)
    e.add_argument("--donor", required=True)
    e.add_argument("--report-json", required=True)
    e.add_argument("--report-text", required=True)
    e.set_defaults(func=enrich)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
