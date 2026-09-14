#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final regression gate for the receiver-facing Rotana provider shard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import rotana_id_audit_policy as policy

# Official adapter identities verified on 2026-09-14. These pins prevent an
# upstream priority reshuffle from silently replacing a reviewed rotana.net guide
# with an older Egypt/UAE/legacy feed.
EXPECTED_SOURCE_PINS = {
    "RotanaCinemaEgypt.eg@SD": "rotana.net",
    "RotanaCinemaKSA.sa@SD": "rotana.net",
    "RotanaClassic.sa@SD": "rotana.net",
    "RotanaClip.sa@SD": "rotana.net",
    "RotanaComedy.sa@SD": "rotana.net",
    "RotanaDrama.sa@SD": "rotana.net",
    "RotanaKhalijia.sa@SD": "rotana.net",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--audit-json", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    errors = []
    notes = []
    audit = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    rows = {r.get("id"): r for r in audit.get("channels", []) if r.get("id")}
    summary = audit.get("summary") or {}

    if summary.get("status") != "PASS":
        errors.append("ROTANA_AUDIT_STATUS=%s" % summary.get("status"))
    if audit.get("unexpected_ids"):
        errors.append("NEW_UNAUDITED_IDS=%s" % ",".join(audit["unexpected_ids"]))
    if audit.get("missing_ids"):
        errors.append("MISSING_RECEIVER_IDS=%s" % ",".join(audit["missing_ids"]))

    actual_ids = set(rows)
    if actual_ids != policy.EXPECTED_RECEIVER_IDS:
        missing = sorted(policy.EXPECTED_RECEIVER_IDS - actual_ids, key=str.casefold)
        extra = sorted(actual_ids - policy.EXPECTED_RECEIVER_IDS, key=str.casefold)
        if missing:
            errors.append("ROTANA_CANONICAL_MISSING=%s" % ",".join(missing))
        if extra:
            errors.append("ROTANA_CANONICAL_EXTRA=%s" % ",".join(extra))

    for cid in sorted(policy.FROZEN_CORE_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            continue
        if row.get("class") != "FROZEN_CORE" or row.get("verdict") != "FROZEN":
            errors.append("ROTANA_CORE_NOT_CLEAN=%s:%s/%s" % (
                cid, row.get("class"), row.get("verdict")))
        if row.get("auto_lock_safe") is not True:
            errors.append("ROTANA_CORE_NOT_AUTOLOCK_SAFE=%s" % cid)

    for cid in sorted(policy.SECONDARY_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            continue
        if row.get("class") != "SECONDARY" or row.get("verdict") != "SECONDARY":
            errors.append("ROTANA_SECONDARY_BAD=%s:%s/%s" % (
                cid, row.get("class"), row.get("verdict")))
        if row.get("auto_lock_safe") is True:
            errors.append("ROTANA_SECONDARY_AUTOLOCK=%s" % cid)

    catalogue = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    selected = {
        row.get("xmltv_id"): row
        for row in catalogue.get("channels", [])
        if row.get("xmltv_id")
    }
    for cid, wanted_site in sorted(EXPECTED_SOURCE_PINS.items(), key=lambda kv: kv[0].casefold()):
        row = selected.get(cid)
        if not row:
            errors.append("ROTANA_PIN_ID_MISSING=%s" % cid)
            continue
        actual_site = row.get("site") or ""
        if actual_site != wanted_site:
            errors.append("ROTANA_PIN_WRONG=%s:%s!=%s" % (cid, actual_site, wanted_site))

    notes.append("receiver_ids=%d" % len(policy.EXPECTED_RECEIVER_IDS))
    notes.append("frozen_core=%d" % len(policy.FROZEN_CORE_IDS))
    notes.append("secondary_no_autolock=%d" % len(policy.SECONDARY_IDS))
    notes.append("minimum_core_coverage=%.1fh" % policy.MIN_COVERAGE_HOURS)
    notes.append("official_rotana_source_pins=%d" % len(EXPECTED_SOURCE_PINS))

    status = "FAIL" if errors else "PASS"
    lines = [
        "ROTANA FINAL REGRESSION GATE: %s" % status,
        "core=%s/%s secondary=%s/%s actual_ids=%d" % (
            summary.get("core_ok", 0), summary.get("core_expected", len(policy.FROZEN_CORE_IDS)),
            summary.get("secondary_ok", 0), summary.get("secondary_expected", len(policy.SECONDARY_IDS)),
            len(rows)),
        "",
        "Checks:",
    ]
    lines.extend("- %s" % n for n in notes)
    if errors:
        lines.extend(["", "Errors:"] + ["- " + e for e in errors])
    else:
        lines.append("- all canonical Rotana receiver/source invariants passed")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
