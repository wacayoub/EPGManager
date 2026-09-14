#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exhaustive receiver-facing MBC/Shahid production audit.

provider-mbc is canonical-only: the receiver shard must expose exactly the
reviewed MENA targets below. Legacy/foreign/operator-specific IDs may remain in
combined/internal MENA data for compatibility, audit and recovery, but any such
ID leaking into provider-mbc is a release blocker.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import bein_id_audit as base

# Canonical receiver-facing MBC services frozen on 2026-09-14 after ID-by-ID EPG
# testing. Strategy: MENA feed identity, Arabic-first metadata, >=30h clean guide,
# no structural timeline errors. Low title diversity is diagnostic-only for
# news/thematic services.
FROZEN_CORE_IDS = {
    "Alarabiya.ae@SD",
    "AlHadath.sa@SD",
    "MBC1.ae@SD",
    "MBC2.ae@SD",
    "MBC3.ae@SD",
    "MBC4.ae@SD",
    "MBC5.ae@SD",
    "MBCAction.ae@SD",
    "MBCBollywood.ae@SD",
    "MBCDrama.ae@SD",
    "MBCIraq.iq@SD",
    "MBCMasr.eg@SD",
    "MBCMasr2.eg@SD",
    "MBCMasrDrama.sa@SD",
    "MBCMax.ae@SD",
    "MBCPersia.ae@SD",
    "MBCPlusDrama.sa@SD",
}

# Valid but internal-only MBC identities. They are retained in the wider MENA
# dataset for audit/recovery and must not be receiver Smart Mapping targets.
SECONDARY_REVIEW_IDS = {
    "Al Arabiya Business.sa",
    "MBC Plus eLife HD.sa",
    "MBC Plus Variety HD.sa",
    "MBC VARIETY.sa",
    "MBCMood.sa@HD",
    "Wanasah.sa",
}

# Known internal-only aliases/weak feeds. These must never compete with the
# receiver canonical set merely because a later upstream source returns data.
QUARANTINED_IDS = {
    "Al Arabiya.sa",
    "AlArabiyaBusiness.ae@SD",
    "AlarabiyaPortrait.ae@SD",
    "EN:.MBC1.Iraq.sa",
    "EN:.MBC1.Masr.sa",
    "MBC Egypt.eg",
    "MBC Maser 2.sa",
    "MBC Maser.sa",
    "MBC MASR 2.sa",
    "MBC Masr Drama.eg",
    "MBC.eg",
    "MBC1Egypt.eg@HD",
    "MBC1USA.us@SD",
    "MBC3USA.us@SD",
    "MBCDramaUSA.us@SD",
    "MBCMasrUSA.us@SD",
}

EXPECTED_IDS = FROZEN_CORE_IDS | SECONDARY_REVIEW_IDS | QUARANTINED_IDS
MIN_COVERAGE_HOURS = 30.0
MIN_TITLE_AR_PCT = 60.0
MIN_DESC_AR_PCT = 90.0
# A handful of missing descriptions must not invalidate an otherwise clean
# linear MBC guide. MBC1 can have a small number of empty descriptions while all
# populated descriptions remain Arabic and its timeline stays structurally sound.
MAX_EMPTY_DESC_RATIO = 0.15
_XMLTV_DT = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?")


def load_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def _parse_dt(value):
    m = _XMLTV_DT.match((value or "").strip())
    if not m:
        return None
    stamp, offset = m.groups()
    try:
        if offset:
            return datetime.strptime(stamp + offset, "%Y%m%d%H%M%S%z")
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _title(programme):
    el = programme.find("title")
    return (el.text or "").strip() if el is not None else ""


def _gap_details(programmes):
    """Return exact >2h timeline gaps with the adjacent programmes."""
    rows = []
    for programme in programmes:
        start = _parse_dt(programme.get("start"))
        stop = _parse_dt(programme.get("stop"))
        if start is None or stop is None or stop <= start:
            continue
        rows.append((start, stop, _title(programme)))
    rows.sort(key=lambda item: item[0])
    gaps = []
    previous = None
    for start, stop, title in rows:
        if previous is not None:
            prev_start, prev_stop, prev_title = previous
            delta = (start - prev_stop).total_seconds()
            if delta > 7200:
                gaps.append({
                    "hours": round(delta / 3600.0, 3),
                    "from_stop": prev_stop.isoformat(),
                    "from_title": prev_title,
                    "to_start": start.isoformat(),
                    "to_title": title,
                })
        if previous is None or stop > previous[1]:
            previous = (start, stop, title)
    return gaps


def core_policy(row):
    issues = []
    diagnostics = []
    n = int(row.get("events", 0) or 0)

    if n <= 0:
        issues.append("NO_PROGRAMMES")
    if float(row.get("coverage_hours", 0.0) or 0.0) < MIN_COVERAGE_HOURS:
        issues.append("LOW_COVERAGE=%.1fh" % float(row.get("coverage_hours", 0.0) or 0.0))
    if int(row.get("invalid", 0) or 0):
        issues.append("INVALID_DURATION=%d" % int(row["invalid"]))
    if int(row.get("overlaps", 0) or 0):
        issues.append("OVERLAPS=%d" % int(row["overlaps"]))
    if int(row.get("mixed_tz_days", 0) or 0):
        issues.append("MIXED_TIMEZONE_DAYS=%d" % int(row["mixed_tz_days"]))
    if int(row.get("long_gt_12h", 0) or 0):
        issues.append("VERY_LONG_12H=%d" % int(row["long_gt_12h"]))
    if int(row.get("empty_title", 0) or 0):
        issues.append("EMPTY_TITLE=%d" % int(row["empty_title"]))
    if int(row.get("gaps_gt_2h", 0) or 0):
        issues.append("GAPS_GT_2H=%d/TOTAL=%.1fh" % (
            int(row["gaps_gt_2h"]), float(row.get("gap_hours", 0.0) or 0.0)))
    if int(row.get("placeholder", 0) or 0):
        issues.append("PLACEHOLDER=%d" % int(row["placeholder"]))

    empty_desc = int(row.get("empty_desc", 0) or 0)
    empty_ratio = empty_desc / float(max(1, n))
    if empty_ratio > MAX_EMPTY_DESC_RATIO:
        issues.append("EMPTY_DESC_HIGH=%d(%.0f%%)" % (empty_desc, empty_ratio * 100.0))
    elif empty_desc:
        diagnostics.append("EMPTY_DESC=%d(%.0f%%)" % (empty_desc, empty_ratio * 100.0))

    title_ar = float(row.get("title_has_ar_pct", 0.0) or 0.0)
    desc_ar = float(row.get("desc_ar_pct", 0.0) or 0.0)
    if title_ar < MIN_TITLE_AR_PCT:
        issues.append("TITLE_AR_LOW=%.0f%%" % title_ar)
    if desc_ar < MIN_DESC_AR_PCT:
        issues.append("DESC_AR_LOW=%.0f%%" % desc_ar)

    if int(row.get("long_gt_6h", 0) or 0):
        diagnostics.append("LONG_GT_6H=%d" % int(row["long_gt_6h"]))
    if int(row.get("short_lt_2m", 0) or 0):
        diagnostics.append("SHORT_LT_2M=%d" % int(row["short_lt_2m"]))
    if n >= 5 and float(row.get("top_title_pct", 0.0) or 0.0) >= 70.0:
        diagnostics.append("REPEATED_TITLE=%.0f%%" % float(row["top_title_pct"]))
    if n >= 8 and float(row.get("unique_title_pct", 100.0) or 0.0) <= 20.0:
        diagnostics.append("LOW_TITLE_DIVERSITY=%.0f%%" % float(row["unique_title_pct"]))

    return issues, diagnostics


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

    rows = []
    errors = []
    for cid in sorted(channels, key=str.casefold):
        row = base.build_profile(cid, channels[cid], events.get(cid, []))
        row["gap_details"] = _gap_details(events.get(cid, []))
        if cid in FROZEN_CORE_IDS:
            issues, diagnostics = core_policy(row)
            row["class"] = "FROZEN_CORE"
            row["verdict"] = "FAIL" if issues else "FROZEN"
            row["auto_lock_safe"] = not issues
            row["issues"] = issues
            row["diagnostic"] = diagnostics
            if issues:
                errors.append("%s:%s" % (cid, ",".join(issues)))
        elif cid in SECONDARY_REVIEW_IDS:
            issues, diagnostics = core_policy(row)
            row["class"] = "SECONDARY_REVIEW"
            row["verdict"] = "REVIEW"
            row["auto_lock_safe"] = False
            row["issues"] = issues
            row["diagnostic"] = diagnostics
        elif cid in QUARANTINED_IDS:
            issues, diagnostics = core_policy(row)
            row["class"] = "QUARANTINE"
            row["verdict"] = "QUARANTINE"
            row["auto_lock_safe"] = False
            row["issues"] = issues
            row["diagnostic"] = diagnostics
        else:
            issues, diagnostics = core_policy(row)
            row["class"] = "UNCLASSIFIED"
            row["verdict"] = "FAIL"
            row["auto_lock_safe"] = False
            row["issues"] = ["NEW_UNAUDITED_MBC_ID"] + issues
            row["diagnostic"] = diagnostics
            errors.append("NEW_UNAUDITED_MBC_ID=%s" % cid)
        rows.append(row)

    actual = set(channels)
    missing_core = sorted(FROZEN_CORE_IDS - actual, key=str.casefold)
    receiver_extras = sorted(actual - FROZEN_CORE_IDS, key=str.casefold)
    unexpected = sorted(actual - EXPECTED_IDS, key=str.casefold)
    if missing_core:
        errors.append("MISSING_FROZEN_CORE=%s" % ",".join(missing_core))
    if receiver_extras:
        errors.append("NONCANONICAL_RECEIVER_IDS=%s" % ",".join(receiver_extras))
    if unexpected:
        errors.append("NEW_UNAUDITED_MBC_IDS=%s" % ",".join(unexpected))

    counts = Counter(row["class"] for row in rows)
    frozen_ok = sum(1 for row in rows if row.get("verdict") == "FROZEN")
    frozen_fail = sum(1 for row in rows if row.get("class") == "FROZEN_CORE" and row.get("verdict") == "FAIL")

    lines = [
        "VIRTUAL EPGMANAGER - EXHAUSTIVE MBC CANONICAL RECEIVER AUDIT",
        "channels=%d programmes=%d frozen=%d/%d frozen_fail=%d secondary=%d quarantine=%d unclassified=%d" % (
            len(rows), sum(int(r.get("events", 0) or 0) for r in rows), frozen_ok,
            len(FROZEN_CORE_IDS), frozen_fail, counts["SECONDARY_REVIEW"],
            counts["QUARANTINE"], counts["UNCLASSIFIED"]),
        "policy=provider-mbc must contain exactly 17 canonical receiver IDs; >=30h clean Arabic-first EPG",
        "",
    ]

    for idx, row in enumerate(rows, 1):
        notes = list(row.get("issues") or []) + list(row.get("diagnostic") or [])
        lines.append("%02d. [%s/%s] %s | %s" % (
            idx, row["class"], row["verdict"], row["id"], row["name"]))
        lines.append("    events=%d coverage=%.1fh span=%.1fh gaps>2h=%d overlaps=%d invalid=%d empty_desc=%d" % (
            row["events"], row["coverage_hours"], row["span_hours"], row["gaps_gt_2h"],
            row["overlaps"], row["invalid"], row["empty_desc"]))
        lines.append("    language: title_AR=%.0f%% title_Latin=%.0f%% desc_AR=%.0f%%" % (
            row["title_has_ar_pct"], row["title_has_latin_pct"], row["desc_ar_pct"]))
        lines.append("    notes=%s" % (", ".join(notes) if notes else "NONE"))
        for gap in row.get("gap_details", []):
            lines.append("    GAP %.1fh | %s [%s] -> %s [%s]" % (
                gap["hours"], gap["from_stop"], gap["from_title"],
                gap["to_start"], gap["to_title"]))
        for ev in row.get("preview", [])[:2]:
            lines.append("    • %s | %s" % (ev.get("start", ""), ev.get("title", "")))
        lines.append("")

    status = "FAIL" if errors else "PASS"
    lines.append("MBC FREEZE GATE: %s" % status)
    if errors:
        lines.extend("- %s" % x for x in errors)

    payload = {
        "schema": 3,
        "mode": "virtual-epgmanager-canonical-mbc-receiver-policy",
        "summary": {
            "status": status,
            "channels": len(rows),
            "programmes": sum(int(r.get("events", 0) or 0) for r in rows),
            "frozen_expected": len(FROZEN_CORE_IDS),
            "frozen_ok": frozen_ok,
            "frozen_fail": frozen_fail,
            "secondary": counts["SECONDARY_REVIEW"],
            "quarantine": counts["QUARANTINE"],
            "unclassified": counts["UNCLASSIFIED"],
        },
        "frozen_core_ids": sorted(FROZEN_CORE_IDS, key=str.casefold),
        "secondary_review_ids": sorted(SECONDARY_REVIEW_IDS, key=str.casefold),
        "quarantined_ids": sorted(QUARANTINED_IDS, key=str.casefold),
        "missing_core_ids": missing_core,
        "receiver_extra_ids": receiver_extras,
        "unexpected_ids": unexpected,
        "errors": errors,
        "channels": [{k: v for k, v in row.items() if k != "rows"} for row in rows],
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    print("MBC FREEZE GATE: %s" % status)
    if errors:
        for row in rows:
            if row.get("class") == "FROZEN_CORE" and row.get("verdict") == "FAIL":
                print("FAIL %s: %s" % (row["id"], ", ".join(row.get("issues") or [])))
                for gap in row.get("gap_details", []):
                    print("  GAP %.1fh %s [%s] -> %s [%s]" % (
                        gap["hours"], gap["from_stop"], gap["from_title"],
                        gap["to_start"], gap["to_title"]))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
