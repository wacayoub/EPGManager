#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final receiver regression gate for Abu Dhabi Media.

FAIL blocks publication.  REVIEW is intentionally non-fatal: it means receiver
identity and timeline invariants are safe, but at least one core feed is still an
upstream language fallback and must not be labelled FROZEN or auto-locked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adm_id_audit_policy as policy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    audit = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    summary = audit.get("summary") or {}
    rows = {r.get("id"): r for r in audit.get("channels", []) if r.get("id")}
    errors = []
    notes = []

    if summary.get("status") == "FAIL":
        errors.append("ADM_AUDIT_STATUS=FAIL")
    actual = set(rows)
    missing = sorted(policy.REQUIRED_IDS - actual, key=str.casefold)
    extra = sorted(actual - policy.ALLOWED_IDS, key=str.casefold)
    legacy = sorted(actual & policy.LEGACY_IDS, key=str.casefold)
    if missing:
        errors.append("ADM_REQUIRED_MISSING=%s" % ",".join(missing))
    if extra:
        errors.append("ADM_UNAUDITED_EXTRA=%s" % ",".join(extra))
    if legacy:
        errors.append("ADM_LEGACY_IDS=%s" % ",".join(legacy))

    for cid in sorted(policy.CORE_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            continue
        if row.get("verdict") not in {"FROZEN", "REVIEW"}:
            errors.append("ADM_CORE_BAD=%s:%s" % (cid, row.get("verdict")))
        if cid in policy.REVIEW_CORE_IDS:
            if row.get("auto_lock_safe") is True:
                errors.append("ADM_REVIEW_CORE_AUTOLOCK=%s" % cid)
        elif row.get("verdict") == "FROZEN" and row.get("auto_lock_safe") is not True:
            errors.append("ADM_FROZEN_CORE_NOT_AUTOLOCK_SAFE=%s" % cid)

    for cid in sorted(policy.SECONDARY_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            continue
        if row.get("verdict") != "SECONDARY":
            errors.append("ADM_SECONDARY_BAD=%s:%s" % (cid, row.get("verdict")))
        if row.get("auto_lock_safe") is True:
            errors.append("ADM_SECONDARY_AUTOLOCK=%s" % cid)

    for cid in sorted(policy.OPTIONAL_STANDBY_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            notes.append("optional_standby_absent=%s" % cid)
            continue
        if row.get("verdict") != "SECONDARY":
            errors.append("ADM_OPTIONAL_BAD=%s:%s" % (cid, row.get("verdict")))
        if row.get("auto_lock_safe") is True:
            errors.append("ADM_OPTIONAL_AUTOLOCK=%s" % cid)
        notes.append("optional_standby_present=%s" % cid)

    notes.append("canonical_required_ids=%d" % len(policy.REQUIRED_IDS))
    notes.append("core=%d" % len(policy.CORE_IDS))
    notes.append("event_secondary_no_autolock=%d" % len(policy.SECONDARY_IDS))
    notes.append("optional_standby=%d" % len(policy.OPTIONAL_STANDBY_IDS))
    notes.append("legacy_receiver_ids=0")

    if errors:
        status = "FAIL"
    elif summary.get("status") == "REVIEW":
        status = "REVIEW"
    else:
        status = "PASS"

    lines = [
        "ABU DHABI MEDIA FINAL REGRESSION GATE: %s" % status,
        "core_frozen=%s/%s core_review=%s secondary=%s/%s actual_ids=%d" % (
            summary.get("core_frozen", 0), summary.get("core_expected", len(policy.CORE_IDS)),
            summary.get("core_review", 0), summary.get("secondary_ok", 0),
            summary.get("secondary_expected", len(policy.SECONDARY_IDS)), len(rows)),
        "",
        "Checks:",
    ]
    lines.extend("- " + x for x in notes)
    if audit.get("reviews"):
        lines.extend(["", "Open quality reviews:"] + ["- " + x for x in audit["reviews"]])
    if errors:
        lines.extend(["", "Errors:"] + ["- " + x for x in errors])
    elif status == "PASS":
        lines.append("- all ADM receiver identities and core quality rules are frozen")
    else:
        lines.append("- receiver identities are safe; REVIEW core remains no-autolock until Arabic upstream improves")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
