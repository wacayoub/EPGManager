#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production-policy audit for every receiver-facing OSN XMLTV ID.

OSN is treated as a premium linear provider:
- schedule integrity is mandatory (valid duration, no overlaps, no empty title);
- gaps above two hours remain REVIEW until verified as a legitimate off-air gap;
- Arabic descriptions are preferred when metadata exists;
- a missing description is reported but is not, by itself, a broken EPG;
- low title diversity/repetition is diagnostic only because Kids/Comedy/Crime/
  iQIYI/Pop Up can legitimately repeat a small catalogue within a 48h window;
- verified legacy Ya Hala aliases must be exact copies of their official OSN
  canonicals and are marked ALIAS_OK.

The script is diagnostic only and never edits the XML.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import bein_id_audit as base


EXPECTED_COMPAT_ALIASES = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}


def osn_family_kind(cid, name):
    probe = ("%s %s" % (cid or "", name or "")).casefold()
    if "movies" in probe or "aflam" in probe:
        return "OSN Movies", "movies"
    if "kids" in probe:
        return "OSN Kids", "kids"
    if "documentary" in probe:
        return "OSN Documentary", "documentary"
    if "crime" in probe:
        return "OSN Crime", "crime"
    if "mezze" in probe:
        return "OSN Mezze", "lifestyle"
    if "iqiyi" in probe:
        return "OSN iQIYI", "series"
    if "yahala" in probe or "ya hala" in probe:
        return "OSN Yahala", "arabic_entertainment"
    if "comedy" in probe:
        return "OSN Comedy", "comedy"
    if "showcase" in probe:
        return "OSN Showcase", "entertainment"
    if "pop up" in probe or "popup" in probe:
        return "OSN Pop Up", "thematic"
    if "osntv one" in probe:
        return "OSN One", "entertainment"
    if "osntv now" in probe:
        return "OSN Now", "entertainment"
    return "OSN", "other"


def load_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def policy_verdict(row):
    issues = []
    warnings = []
    diagnostic = []
    n = int(row.get("events", 0) or 0)

    if n <= 0:
        issues.append("NO_PROGRAMMES")
    if row.get("invalid"):
        issues.append("INVALID_DURATION=%d" % row["invalid"])
    if row.get("overlaps"):
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row.get("mixed_tz_days"):
        issues.append("MIXED_TIMEZONE_DAYS=%d" % row["mixed_tz_days"])
    if row.get("long_gt_12h"):
        issues.append("VERY_LONG_12H=%d" % row["long_gt_12h"])
    if row.get("empty_title"):
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])

    if row.get("gaps_gt_2h"):
        warnings.append("GAPS_GT_2H=%d/TOTAL=%.1fh" % (
            row["gaps_gt_2h"], float(row.get("gap_hours", 0.0) or 0.0)))
    if row.get("long_gt_6h"):
        warnings.append("LONG_6H=%d" % row["long_gt_6h"])
    if row.get("short_lt_2m"):
        warnings.append("SHORT_LT_2M=%d" % row["short_lt_2m"])
    if row.get("desc_same_title"):
        warnings.append("DESC_EQUALS_TITLE=%d" % row["desc_same_title"])

    # Descriptions are useful premium metadata but remain optional. If there are
    # descriptions, however, an OSN/MENA guide should be Arabic-rich.
    empty_desc = int(row.get("empty_desc", 0) or 0)
    nonempty_desc = max(0, n - empty_desc)
    if empty_desc:
        diagnostic.append("EMPTY_DESC=%d(%.0f%%)" % (
            empty_desc, empty_desc / float(max(1, n)) * 100.0))
    if nonempty_desc and float(row.get("desc_ar_pct", 0.0) or 0.0) < 70.0:
        warnings.append("AR_DESC_LOW=%.0f%%" % float(row.get("desc_ar_pct", 0.0) or 0.0))

    # Repetition/low diversity is not automatically bad for thematic channels.
    if n >= 5 and float(row.get("top_title_pct", 0.0) or 0.0) >= 70.0:
        diagnostic.append("REPEATED_TITLE=%.0f%%" % float(row["top_title_pct"]))
    if n >= 8 and float(row.get("unique_title_pct", 100.0) or 0.0) <= 20.0:
        diagnostic.append("LOW_TITLE_DIVERSITY=%.0f%%" % float(row["unique_title_pct"]))

    if issues:
        verdict = "FAIL"
    elif warnings:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    return verdict, issues, warnings, diagnostic


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    root = load_root(args.xml)
    channels = {}
    for ch in root.findall("channel"):
        cid = (ch.get("id") or "").strip()
        if cid:
            channels[cid] = base.display_name(ch)

    events = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            events[cid].append(p)

    # Read strict-sharder alias metadata. The expected table is also recorded in
    # this audit so a missing manifest bridge cannot silently become a PASS.
    manifest_path = Path(args.xml).with_name("shards.json")
    compat = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            shard = (manifest.get("shards") or {}).get("provider-osn") or {}
            compat = {
                alias: value.get("canonical")
                for alias, value in (shard.get("compat_aliases") or {}).items()
                if value.get("canonical")
            }
        except Exception:
            compat = {}

    rows = []
    for cid in sorted(channels, key=str.casefold):
        row = base.build_profile(cid, channels[cid], events.get(cid, []))
        family, kind = osn_family_kind(cid, channels[cid])
        row["family"] = family
        row["kind"] = kind
        verdict, issues, warnings, diagnostic = policy_verdict(row)
        row["verdict"] = verdict
        row["issues"] = issues
        row["warnings"] = warnings
        row["diagnostic"] = diagnostic
        row["source_status"] = "NORMAL"
        row["mapping_mode"] = "normal_candidate"
        row["auto_lock_safe"] = verdict != "FAIL"
        row["compat_canonical"] = compat.get(cid)
        row["alias_exact_match"] = None
        rows.append(row)

    by_id = {row["id"]: row for row in rows}

    # Exact alias validation. Missing or wrong bridges are structural regressions.
    for alias, expected in EXPECTED_COMPAT_ALIASES.items():
        row = by_id.get(alias)
        canonical = by_id.get(expected)
        if not row:
            continue
        if compat.get(alias) != expected:
            row["issues"].append("COMPAT_ALIAS_MISSING_OR_WRONG=%s" % expected)
            row["verdict"] = "FAIL"
            continue
        if not canonical:
            row["issues"].append("CANONICAL_MISSING=%s" % expected)
            row["verdict"] = "FAIL"
            continue
        same = row["events"] == canonical["events"] and row["fingerprint"] == canonical["fingerprint"]
        row["alias_exact_match"] = same
        if same:
            row["verdict"] = "ALIAS_OK"
            row["warnings"] = []
            row["auto_lock_safe"] = True
        else:
            row["issues"].append("ALIAS_TIMELINE_MISMATCH=%s" % expected)
            row["verdict"] = "FAIL"

    # Exact duplicate schedules across unrelated OSN services are suspicious.
    fp_groups = defaultdict(list)
    for row in rows:
        if row["events"]:
            fp_groups[row["fingerprint"]].append(row)
    duplicate_groups = []
    for members in fp_groups.values():
        if len(members) < 2:
            continue
        ids = [m["id"] for m in members]
        duplicate_groups.append({"ids": ids, "families": sorted(set(m["family"] for m in members))})
        for row in members:
            canonical = EXPECTED_COMPAT_ALIASES.get(row["id"])
            known = canonical in ids if canonical else any(EXPECTED_COMPAT_ALIASES.get(x) == row["id"] for x in ids)
            if not known and row["verdict"] == "PASS":
                row["warnings"].append("DUPLICATE_TIMELINE_OTHER_OSN_SERVICE")
                row["verdict"] = "REVIEW"

    counts = Counter(row["verdict"] for row in rows)
    lines = [
        "VIRTUAL EPGMANAGER - EXHAUSTIVE OSN ID-BY-ID PRODUCTION AUDIT",
        "channels=%d programmes=%d PASS=%d ALIAS_OK=%d REVIEW=%d FAIL=%d" % (
            len(rows), sum(r["events"] for r in rows), counts["PASS"], counts["ALIAS_OK"],
            counts["REVIEW"], counts["FAIL"]),
        "Low title diversity/repetition is diagnostic-only; structural timeline defects remain blocking.",
        "",
    ]

    for idx, row in enumerate(rows, 1):
        alias = " -> %s" % row["compat_canonical"] if row.get("compat_canonical") else ""
        notes = row["issues"] + row["warnings"] + row["diagnostic"]
        lines.append("%02d. [%s] %s%s" % (idx, row["verdict"], row["id"], alias))
        lines.append("    name=%s | family=%s | kind=%s" % (row["name"], row["family"], row["kind"]))
        lines.append("    events=%d coverage=%.1fh span=%.1fh gaps>2h=%d gap_total=%.1fh overlaps=%d invalid=%d >6h=%d >12h=%d" % (
            row["events"], row["coverage_hours"], row["span_hours"], row["gaps_gt_2h"], row["gap_hours"],
            row["overlaps"], row["invalid"], row["long_gt_6h"], row["long_gt_12h"]))
        lines.append("    title: AR=%.0f%% Latin=%.0f%% unique=%.0f%% | desc: AR=%.0f%% empty=%d" % (
            row["title_has_ar_pct"], row["title_has_latin_pct"], row["unique_title_pct"],
            row["desc_ar_pct"], row["empty_desc"]))
        lines.append("    notes=%s" % (", ".join(notes) if notes else "NONE"))
        for ev in row["preview"][:2]:
            lines.append("    • %s" % (ev["title"] or "<NO TITLE>"))
        lines.append("")

    lines.append("EXACT DUPLICATE TIMELINE GROUPS")
    if duplicate_groups:
        for group in duplicate_groups:
            lines.append("- %s | families=%s" % (" ; ".join(group["ids"]), " ; ".join(group["families"])))
    else:
        lines.append("- NONE")

    payload = {
        "schema": 1,
        "mode": "virtual-epgmanager-exhaustive-osn-production-policy",
        "summary": {
            "channels": len(rows),
            "programmes": sum(r["events"] for r in rows),
            "counts": dict(counts),
        },
        "expected_compat_aliases": EXPECTED_COMPAT_ALIASES,
        "compat_aliases": compat,
        "channels": [{k: v for k, v in row.items() if k != "rows"} for row in rows],
        "duplicate_timeline_groups": duplicate_groups,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
