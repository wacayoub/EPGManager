#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan safe EPG repairs from the unified Global Health Gate report.

This module is intentionally conservative: it never edits EPG data itself.  It
classifies FAIL codes into existing, production-tested recovery paths:
- MENA source/freshness/timeline failures -> rebuild MENA Cloud;
- MENA canonical namespace/identity failures -> Canonical ID Reset;
- Morocco failures -> rebuild Morocco Cloud, whose source-specific LKG logic and
  2M arbitration remain authoritative.

Unknown FAIL codes are never guessed. They stop automatic repair and remain for
manual review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MENA_REFRESH = {
    "MISSING_XML", "MISSING_MANIFEST", "INVALID_XML", "INVALID_MANIFEST",
    "EMPTY_TITLES", "INVALID_TIMELINE", "OVERLAPS_GE_10M",
    "NO_FUTURE_EPG", "CRITICAL_LOW_COVERAGE", "STALE_MANIFEST",
}
MENA_CANONICAL = {
    "NAMESPACE_MISMATCH", "DUPLICATE_CHANNEL_IDS", "ORPHAN_PROGRAMMES",
    "ZERO_EPG_IDS", "MANIFEST_CHANNEL_MISMATCH", "MANIFEST_PROGRAMME_MISMATCH",
    "MANIFEST_SHA256_MISMATCH", "MANIFEST_SIZE_MISMATCH",
}
MOROCCO_REFRESH = {
    "MISSING_XML", "MISSING_MANIFEST", "INVALID_XML", "INVALID_MANIFEST",
    "DUPLICATE_CHANNEL_IDS", "ORPHAN_PROGRAMMES", "EMPTY_TITLES",
    "INVALID_TIMELINE", "2M_PLACEHOLDER_TITLE", "NAMESPACE_MISMATCH",
    "ZERO_EPG_IDS", "OVERLAPS_GE_10M", "NO_FUTURE_EPG",
    "CRITICAL_LOW_COVERAGE", "STALE_MANIFEST", "MANIFEST_CHANNEL_MISMATCH",
    "MANIFEST_PROGRAMME_MISMATCH", "MANIFEST_SHA256_MISMATCH",
    "MANIFEST_SIZE_MISMATCH", "MISSING_REQUIRED_IDS",
}


def _fails(report):
    return [row for row in report.get("checks", []) if row.get("severity") == "FAIL"]


def build_plan(report):
    fails = _fails(report)
    if not fails:
        return {
            "schema": 1,
            "status": report.get("status", "PASS"),
            "fail_count": 0,
            "repairable": True,
            "actions": [],
            "unresolved": [],
        }

    by_feed = {"mena": [], "morocco": [], "global": []}
    for row in fails:
        by_feed.setdefault(row.get("feed", "global"), []).append(row.get("code", ""))

    actions = []
    unresolved = []

    mena_codes = set(by_feed.get("mena", []))
    if mena_codes:
        unknown = mena_codes - MENA_REFRESH - MENA_CANONICAL
        if unknown:
            unresolved.extend({"feed": "mena", "code": code} for code in sorted(unknown))
        # Rebuild has priority whenever source/timeline/freshness is involved.
        # The MENA workflow already runs canonical finalization on its candidate.
        if mena_codes & MENA_REFRESH:
            actions.append({
                "id": "mena_refresh",
                "workflow": "mena-epg.yml",
                "reason_codes": sorted(mena_codes),
            })
        elif mena_codes & MENA_CANONICAL:
            actions.append({
                "id": "canonical_reset_mena",
                "workflow": "canonical-id-reset.yml",
                "reason_codes": sorted(mena_codes),
                "inputs": {"scope": "MENA", "dry_run": "false"},
            })

    morocco_codes = set(by_feed.get("morocco", []))
    if morocco_codes:
        unknown = morocco_codes - MOROCCO_REFRESH
        if unknown:
            unresolved.extend({"feed": "morocco", "code": code} for code in sorted(unknown))
        if morocco_codes & MOROCCO_REFRESH:
            actions.append({
                "id": "morocco_refresh",
                "workflow": "morocco-epg.yml",
                "reason_codes": sorted(morocco_codes),
            })

    for code in sorted(set(by_feed.get("global", []))):
        unresolved.append({"feed": "global", "code": code})

    # Never auto-run a partial plan when a new FAIL family is unknown.
    return {
        "schema": 1,
        "status": report.get("status", "FAIL"),
        "fail_count": len(fails),
        "repairable": not unresolved,
        "actions": actions if not unresolved else [],
        "unresolved": unresolved,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    plan = build_plan(report)
    text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if plan["repairable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
