#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final regression gate for the production OSN provider shard.

The gate blocks publication when OSN regresses structurally or when the exact
official-source recovery introduced for OSN-owned channels is not useful. It does
not invent programmes and it does not downgrade thematic channels merely because
they repeat titles within the 48h window.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_IDS = {
    "Osn Ya Hala Aflam.eg",
    "OSN Ya Hala.eg",
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

EXPECTED_ALIASES = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}

# These are the OSN-owned rows that were previously ignored solely because the
# current iptv-org osn.com channel file leaves xmltv_id blank. Catalogue guards
# separately prove that these IDs were selected from exact osn.com identities.
OFFICIAL_RECOVERED_IDS = {
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
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--audit-json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    # XML existence is intentional: the gate is tied to the exact receiver shard,
    # even though all detailed metrics are already serialized by the OSN audit.
    xml_path = Path(args.xml)
    if not xml_path.exists() or xml_path.stat().st_size <= 0:
        raise SystemExit("OSN provider XML missing or empty: %s" % xml_path)

    audit = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    rows = {row.get("id"): row for row in audit.get("channels", []) if row.get("id")}
    counts = ((audit.get("summary") or {}).get("counts") or {})
    errors = []
    notes = []

    missing = sorted(EXPECTED_IDS - set(rows), key=str.casefold)
    if missing:
        errors.append("MISSING_EXPECTED_IDS=%s" % ",".join(missing))
    unexpected = sorted(set(rows) - EXPECTED_IDS, key=str.casefold)
    if unexpected:
        # New OSN channels are not inherently bad, but adding them without an
        # explicit provider audit would defeat the frozen-provider guarantee.
        errors.append("NEW_UNAUDITED_OSN_IDS=%s" % ",".join(unexpected))

    fail_ids = sorted(
        [cid for cid, row in rows.items() if row.get("verdict") == "FAIL"],
        key=str.casefold,
    )
    review_ids = sorted(
        [cid for cid, row in rows.items() if row.get("verdict") == "REVIEW"],
        key=str.casefold,
    )
    if fail_ids:
        errors.append("FAIL_IDS=%s" % ",".join(fail_ids))
    if review_ids:
        errors.append("REVIEW_IDS=%s" % ",".join(review_ids))
    notes.append("verdicts=PASS:%d ALIAS_OK:%d REVIEW:%d FAIL:%d" % (
        int(counts.get("PASS", 0) or 0), int(counts.get("ALIAS_OK", 0) or 0),
        int(counts.get("REVIEW", 0) or 0), int(counts.get("FAIL", 0) or 0)))

    # Every canonical service should expose a useful receiver horizon. Legacy
    # aliases are checked against their canonicals separately.
    coverages = []
    for cid, row in rows.items():
        if cid in EXPECTED_ALIASES:
            continue
        events = int(row.get("events", 0) or 0)
        coverage = float(row.get("coverage_hours", 0.0) or 0.0)
        coverages.append(coverage)
        if events <= 0:
            errors.append("NO_EPG=%s" % cid)
        if coverage < 24.0:
            errors.append("LOW_COVERAGE=%s:%.1fh" % (cid, coverage))
        if int(row.get("invalid", 0) or 0):
            errors.append("INVALID=%s:%s" % (cid, row.get("invalid")))
        if int(row.get("overlaps", 0) or 0):
            errors.append("OVERLAPS=%s:%s" % (cid, row.get("overlaps")))
        if int(row.get("long_gt_12h", 0) or 0):
            errors.append("VERY_LONG=%s:%s" % (cid, row.get("long_gt_12h")))
        if int(row.get("empty_title", 0) or 0):
            errors.append("EMPTY_TITLE=%s:%s" % (cid, row.get("empty_title")))

    if coverages:
        notes.append("canonical_coverage=%.1f..%.1fh" % (min(coverages), max(coverages)))

    # The ten rescued official rows must all be populated and clean. Documentary
    # was the main pre-fix defect (25h gap), so this explicitly prevents that bad
    # secondary-source timeline from being published again.
    for cid in sorted(OFFICIAL_RECOVERED_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            continue
        if float(row.get("coverage_hours", 0.0) or 0.0) < 24.0:
            errors.append("OFFICIAL_RECOVERY_LOW_COVERAGE=%s:%.1fh" % (
                cid, float(row.get("coverage_hours", 0.0) or 0.0)))
        if int(row.get("gaps_gt_2h", 0) or 0):
            errors.append("OFFICIAL_RECOVERY_GAP=%s:%s" % (cid, row.get("gaps_gt_2h")))
    notes.append("official_recovered_ids_checked=%d" % len(OFFICIAL_RECOVERED_IDS))

    # Saved Vu+ mappings to the two Egypt-era IDs remain valid but must now be
    # byte-equivalent logical copies of the official canonical schedules.
    alias_ok = 0
    for alias, canonical in EXPECTED_ALIASES.items():
        row = rows.get(alias)
        if not row:
            errors.append("ALIAS_MISSING=%s" % alias)
            continue
        if row.get("compat_canonical") != canonical:
            errors.append("ALIAS_CANONICAL_WRONG=%s->%s" % (alias, row.get("compat_canonical")))
        if row.get("verdict") != "ALIAS_OK" or row.get("alias_exact_match") is not True:
            errors.append("ALIAS_NOT_EXACT=%s" % alias)
        else:
            alias_ok += 1
    notes.append("verified_legacy_aliases=%d/%d" % (alias_ok, len(EXPECTED_ALIASES)))

    documentary = rows.get("OSNtv Documentary.sa")
    if documentary:
        notes.append("documentary=events:%d coverage:%.1fh gaps:%d empty_desc:%d desc_ar:%.0f%%" % (
            int(documentary.get("events", 0) or 0),
            float(documentary.get("coverage_hours", 0.0) or 0.0),
            int(documentary.get("gaps_gt_2h", 0) or 0),
            int(documentary.get("empty_desc", 0) or 0),
            float(documentary.get("desc_ar_pct", 0.0) or 0.0),
        ))

    status = "FAIL" if errors else "PASS"
    lines = [
        "OSN FINAL REGRESSION GATE: %s" % status,
        "expected_ids=%d actual_ids=%d REVIEW=%d FAIL=%d" % (
            len(EXPECTED_IDS), len(rows), len(review_ids), len(fail_ids)),
        "",
        "Checks:",
    ]
    lines.extend("- %s" % note for note in notes)
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend("- %s" % error for error in errors)
    else:
        lines.append("- all frozen OSN invariants passed")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
