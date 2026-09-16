#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final regression gate for the production beIN provider shard.

Publication is blocked only by real structural/source regressions. REVIEW status
and coverage length are diagnostic: production already caps the receiver window
at 48 hours and the user accepts shorter real guides. The gate remains strict on
missing Sports IDs, invalid durations, overlaps, excessive gaps, verified alias
integrity, known false Live/replay defects, NEWS Arabic-label regressions and
MAX/XTRA mapping safety.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

SPORTS_CANONICAL = {
    1: "beIN SPORTS 1.qa",
    2: "beINSports2.qa@MENA",
    3: "beINSports3.qa@MENA",
    4: "beINSports4.qa@MENA",
    5: "beINSports5.qa@MENA",
    6: "beINSports6.qa@MENA",
    7: "beINSports7.qa@MENA",
    8: "beINSports8.qa@MENA",
    9: "beINSports9.qa@MENA",
}

EXPECTED_ALIASES = {
    "beIN_SPORTS3_DIGITAL_Mono_EN.bein": "beINSports3.qa@MENA",
    "beIN_SPORTS4_DIGITAL_Mono_EN.bein": "beINSports4.qa@MENA",
    "beIN_SPORTS5_DIGITAL_Mono_EN.bein": "beINSports5.qa@MENA",
    "beIN_SPORTS7_DIGITAL_Mono_EN.bein": "beINSports7.qa@MENA",
}

# Historical reference only; REVIEW identities are now diagnostic and must not
# block an otherwise structurally safe receiver feed.
ALLOWED_REVIEW = {
    "beINSports6.qa@MENA",
    "beINSeries2.qa@SD",
}

DEFAULT_SPORTS_MIN_COVERAGE_H = 30.0
SPORTS6_MIN_COVERAGE_H = 27.5

NEWS_ENGLISH_EXACT = {
    "the issue of the day", "news bulletin", "special interview", "super monday",
    "al hassila", "al hassad", "the big interview", "sports news", "football news",
    "news summary", "breaking news", "morning news", "evening news", "world news",
    "international news", "press conference", "sports today",
}

LIVE_FULHAM_RE = re.compile(r"^\s*Live\s*:\s*Liverpool\s+v(?:s)?\s+Fulham\b", re.I)
DATE_SUFFIX_RE = re.compile(r"\s*[-–—|]\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$")


def load_xml(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def title_text(programme):
    node = programme.find("title")
    return ((node.text or "").strip() if node is not None else "")


def norm_english_exact(title):
    core = DATE_SUFFIX_RE.sub("", (title or "").strip())
    return re.sub(r"[^a-z0-9]+", " ", core.casefold()).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--audit-json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    root = load_xml(Path(args.xml))
    audit = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    rows = {r.get("id"): r for r in audit.get("channels", []) if r.get("id")}
    canonical_namespace = bool(rows) and all(str(cid).startswith("beIN.") for cid in rows)
    sports_ids = (
        {number: "beIN.Sports.%d.qa" % number for number in range(1, 10)}
        if canonical_namespace else SPORTS_CANONICAL
    )
    expected_aliases = {} if canonical_namespace else EXPECTED_ALIASES
    errors = []
    notes = []

    counts = ((audit.get("summary") or {}).get("counts") or {})
    fail_count = int(counts.get("FAIL", 0) or 0)
    if fail_count:
        errors.append("FAIL_IDS=%d" % fail_count)

    review_ids = {cid for cid, row in rows.items() if row.get("verdict") == "REVIEW"}
    unexpected_reviews = sorted(review_ids - ALLOWED_REVIEW, key=str.casefold)
    notes.append("review_ids=%s" % (",".join(sorted(review_ids, key=str.casefold)) or "NONE"))
    if unexpected_reviews:
        notes.append("new_review_ids_diagnostic=%s" % ",".join(unexpected_reviews))

    coverages = []
    for number, cid in sports_ids.items():
        row = rows.get(cid)
        if not row:
            errors.append("SPORTS_%d_MISSING=%s" % (number, cid))
            continue
        events = int(row.get("events", 0) or 0)
        coverage = float(row.get("coverage_hours", 0.0) or 0.0)
        coverages.append(coverage)
        if events <= 0:
            errors.append("SPORTS_%d_NO_EPG" % number)
        minimum = SPORTS6_MIN_COVERAGE_H if number == 6 else DEFAULT_SPORTS_MIN_COVERAGE_H
        if coverage < minimum:
            notes.append("sports_%d_coverage_info=%.1fh<legacy-ref-%.1fh" % (number, coverage, minimum))
        if int(row.get("invalid", 0) or 0):
            errors.append("SPORTS_%d_INVALID=%s" % (number, row.get("invalid")))
        if int(row.get("overlaps", 0) or 0):
            errors.append("SPORTS_%d_OVERLAPS=%s" % (number, row.get("overlaps")))
        if int(row.get("long_gt_12h", 0) or 0):
            errors.append("SPORTS_%d_VERY_LONG=%s" % (number, row.get("long_gt_12h")))
        gaps = int(row.get("gaps_gt_2h", 0) or 0)
        gap_hours = float(row.get("gap_hours", 0.0) or 0.0)
        if number == 6:
            if gaps > 1 or gap_hours > 3.5:
                errors.append("SPORTS_6_GAP_REGRESSION=%d/%.1fh" % (gaps, gap_hours))
        elif gaps:
            errors.append("SPORTS_%d_GAPS_GT_2H=%d" % (number, gaps))

    if coverages:
        drift = max(coverages) - min(coverages)
        notes.append("sports_1_9_coverage=%.1f..%.1fh" % (min(coverages), max(coverages)))
        if drift > 15.0:
            notes.append("sports_1_9_horizon_drift_info=%.1fh" % drift)
    notes.append("coverage_gate=INFORMATIONAL_ONLY receiver_max=48h")

    for alias, canonical in expected_aliases.items():
        row = rows.get(alias)
        if not row:
            errors.append("ALIAS_MISSING=%s" % alias)
            continue
        if row.get("compat_canonical") != canonical:
            errors.append("ALIAS_CANONICAL_WRONG=%s->%s" % (alias, row.get("compat_canonical")))
        if row.get("verdict") != "ALIAS_OK" or row.get("alias_exact_match") is not True:
            errors.append("ALIAS_NOT_EXACT=%s" % alias)

    news_programmes = []
    false_live = []
    for programme in root.findall("programme"):
        cid = (programme.get("channel") or "").strip()
        title = title_text(programme)
        if not title:
            continue
        if LIVE_FULHAM_RE.search(title):
            false_live.append((cid, title))
        if "bein" in cid.casefold() and "news" in cid.casefold():
            news_programmes.append((cid, title))

    if false_live:
        errors.append("FALSE_LIVE_FULHAM=%d" % len(false_live))

    leaked = []
    for cid, title in news_programmes:
        if norm_english_exact(title) in NEWS_ENGLISH_EXACT:
            leaked.append((cid, title))
    if leaked:
        errors.append("NEWS_PLAIN_ENGLISH_EXACT=%d" % len(leaked))
        notes.extend("news_leak=%s:%s" % item for item in leaked[:5])
    notes.append("news_events_checked=%d" % len(news_programmes))

    event_sources = 0
    for cid, row in rows.items():
        if row.get("kind") not in {"max", "xtra"} or int(row.get("events", 0) or 0) <= 0:
            continue
        event_sources += 1
        verdict = row.get("verdict")
        if verdict not in {"KEEP_SOURCE", "ALIAS_OK", "PASS"}:
            errors.append("EVENT_SOURCE_BAD_VERDICT=%s:%s" % (cid, verdict))
        warnings = row.get("warnings") or []
        generic = any(str(w).startswith(("GENERIC_GUIDE=", "REPEATED_TITLE=", "LOW_TITLE_DIVERSITY=")) for w in warnings)
        if generic and row.get("auto_lock_safe") is True:
            errors.append("GENERIC_EVENT_SOURCE_AUTOLOCK=%s" % cid)
    notes.append("max_xtra_sources_checked=%d" % event_sources)

    for cid in ("NEWS_DIGITAL_Mono_AR.bein", "NEWS_DIGITAL_Mono_EN.bein"):
        row = rows.get(cid)
        if row and row.get("auto_lock_safe") is True:
            errors.append("LEGACY_NEWS_AUTOLOCK=%s" % cid)

    status = "FAIL" if errors else "PASS"
    lines = [
        "beIN FINAL REGRESSION GATE: %s" % status,
        "FAIL=%d REVIEW=%d review_diagnostic=%d" % (fail_count, len(review_ids), len(unexpected_reviews)),
        "",
        "Checks:",
    ]
    lines.extend("- %s" % n for n in notes)
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend("- %s" % e for e in errors)
    else:
        lines.append("- all hard beIN invariants passed")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
