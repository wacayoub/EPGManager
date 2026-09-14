#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final frozen-provider regression gate for canonical MBC receiver output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import mbc_id_audit_policy as policy

# Exact source decisions validated during the 2026-09-14 MBC provider audit.
# Every receiver canonical is pinned so upstream priority changes cannot silently
# switch a healthy mapping to a weaker guide.
EXPECTED_SOURCE_PINS = {
    "Alarabiya.ae@SD": "shahid.mbc.net",
    "AlHadath.sa@SD": "osn.com",
    "MBC1.ae@SD": "shahid.mbc.net",
    "MBC2.ae@SD": "shahid.mbc.net",
    "MBC3.ae@SD": "osn.com",
    "MBC4.ae@SD": "shahid.mbc.net",
    "MBC5.ae@SD": "osn.com",
    "MBCAction.ae@SD": "shahid.mbc.net",
    "MBCBollywood.ae@SD": "shahid.mbc.net",
    "MBCDrama.ae@SD": "osn.com",
    "MBCIraq.iq@SD": "osn.com",
    "MBCMasr.eg@SD": "osn.com",
    "MBCMasr2.eg@SD": "osn.com",
    "MBCMasrDrama.sa@SD": "elcinema.com",
    "MBCMax.ae@SD": "shahid.mbc.net",
    "MBCPersia.ae@SD": "shahid.mbc.net",
    "MBCPlusDrama.sa@SD": "osn.com",
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

    xml_path = Path(args.xml)
    if not xml_path.exists() or xml_path.stat().st_size <= 0:
        errors.append("MBC_PROVIDER_XML_MISSING_OR_EMPTY")

    audit = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    rows = {row.get("id"): row for row in audit.get("channels", []) if row.get("id")}
    summary = audit.get("summary") or {}

    if summary.get("status") != "PASS":
        errors.append("MBC_AUDIT_STATUS=%s" % summary.get("status"))
    if audit.get("unexpected_ids"):
        errors.append("NEW_UNAUDITED_IDS=%s" % ",".join(audit["unexpected_ids"]))
    if audit.get("missing_core_ids"):
        errors.append("MISSING_FROZEN_CORE=%s" % ",".join(audit["missing_core_ids"]))
    if audit.get("receiver_extra_ids"):
        errors.append("NONCANONICAL_RECEIVER_IDS=%s" % ",".join(audit["receiver_extra_ids"]))

    actual_ids = set(rows)
    expected_ids = set(policy.FROZEN_CORE_IDS)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids, key=str.casefold)
        extra = sorted(actual_ids - expected_ids, key=str.casefold)
        if missing:
            errors.append("RECEIVER_CANONICAL_MISSING=%s" % ",".join(missing))
        if extra:
            errors.append("RECEIVER_CANONICAL_EXTRA=%s" % ",".join(extra))

    for cid in sorted(policy.FROZEN_CORE_IDS, key=str.casefold):
        row = rows.get(cid)
        if not row:
            errors.append("FROZEN_ID_MISSING=%s" % cid)
            continue
        if row.get("class") != "FROZEN_CORE" or row.get("verdict") != "FROZEN":
            errors.append("FROZEN_ID_NOT_CLEAN=%s:%s/%s" % (
                cid, row.get("class"), row.get("verdict")))
        if row.get("auto_lock_safe") is not True:
            errors.append("FROZEN_ID_NOT_AUTOLOCK_SAFE=%s" % cid)

    catalogue = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    selected = {
        row.get("xmltv_id"): row
        for row in catalogue.get("channels", [])
        if row.get("xmltv_id")
    }
    for cid, wanted_site in sorted(EXPECTED_SOURCE_PINS.items(), key=lambda kv: kv[0].casefold()):
        row = selected.get(cid)
        if not row:
            errors.append("PINNED_SOURCE_ID_MISSING=%s" % cid)
            continue
        actual_site = row.get("site") or ""
        if actual_site != wanted_site:
            errors.append("PINNED_SOURCE_WRONG=%s:%s!=%s" % (cid, actual_site, wanted_site))

    notes.append("canonical_receiver_ids=%d" % len(policy.FROZEN_CORE_IDS))
    notes.append("actual_provider_ids=%d" % len(rows))
    notes.append("minimum_clean_coverage=%.0fh" % policy.MIN_COVERAGE_HOURS)
    notes.append("source_pins=%d" % len(EXPECTED_SOURCE_PINS))
    notes.append("MBCMasrDrama_source=%s" % (
        (selected.get("MBCMasrDrama.sa@SD") or {}).get("site", "<missing>")))

    status = "FAIL" if errors else "PASS"
    lines = [
        "MBC FINAL REGRESSION GATE: %s" % status,
        "expected_core=%d actual_provider_ids=%d frozen_ok=%s" % (
            len(policy.FROZEN_CORE_IDS), len(rows), summary.get("frozen_ok", 0)),
        "",
        "Checks:",
    ]
    lines.extend("- %s" % note for note in notes)
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend("- %s" % error for error in errors)
    else:
        lines.append("- all canonical MBC receiver/source invariants passed")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
