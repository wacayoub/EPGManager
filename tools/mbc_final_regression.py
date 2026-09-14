#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final frozen-provider regression gate for canonical MBC receiver output.

The MBC gate is also the umbrella step for the already-audited Rotana provider
and accepted upstream source adapters. Keeping these companion checks in the
existing Arabic-first regression step makes publication atomic: MBC, Rotana and
source-health must all be green before the later beIN/OSN gates can run.

Rotana keeps the global two-day source strategy: its own companion gate applies
the narrowly audited evening-boundary floor only to official rotana.net core IDs;
this umbrella gate never relaxes MBC or source-health rules to accommodate it.
Rotana Clip is allowed to remain an internal standby whenever zero-useful-EPG
pruning removes it from the receiver shard.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import mbc_id_audit_policy as policy

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


def run_companion_gates(xml_path: Path, catalog_path: Path):
    tools = Path(__file__).resolve().parent
    final_dir = xml_path.parent
    output_dir = final_dir.parent
    errors = []
    notes = []

    rotana_xml = final_dir / "provider-rotana.xml.gz"
    rotana_json = final_dir / "rotana-id-audit-policy.json"
    rotana_text = final_dir / "rotana-id-audit-policy.txt"
    rotana_reg_text = final_dir / "rotana-final-regression.txt"
    rotana_audit = subprocess.run([
        sys.executable, str(tools / "rotana_id_audit_policy.py"),
        "--xml", str(rotana_xml),
        "--json", str(rotana_json),
        "--text", str(rotana_text),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if rotana_audit.returncode == 0:
        rotana_reg = subprocess.run([
            sys.executable, str(tools / "rotana_final_regression.py"),
            "--xml", str(rotana_xml),
            "--audit-json", str(rotana_json),
            "--catalog-manifest", str(catalog_path),
            "--text", str(rotana_reg_text),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    else:
        rotana_reg = None
    rotana_rc = rotana_reg.returncode if rotana_reg is not None else 1
    rotana_output = rotana_reg.stdout.strip() if rotana_reg is not None else rotana_audit.stdout.strip()
    if rotana_audit.returncode or rotana_rc:
        errors.append("ROTANA_GATE_FAIL")
    notes.append("Rotana=%s" % ("PASS" if not (rotana_audit.returncode or rotana_rc) else "FAIL"))

    source_json = final_dir / "source-health.json"
    source_text = final_dir / "source-health.txt"
    source_run = subprocess.run([
        sys.executable, str(tools / "source_health_regression.py"),
        "--raw", str(output_dir / "raw.xml"),
        "--catalog-manifest", str(catalog_path),
        "--json", str(source_json),
        "--text", str(source_text),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if source_run.returncode:
        errors.append("SOURCE_HEALTH_GATE_FAIL")
    notes.append("SourceHealth=%s" % ("PASS" if source_run.returncode == 0 else "FAIL"))

    return {
        "errors": errors,
        "notes": notes,
        "rotana_output": rotana_output,
        "source_output": source_run.stdout.strip(),
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

    catalogue_path = Path(args.catalog_manifest)
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
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
    notes.append("minimum_clean_coverage=%.1fh" % policy.MIN_COVERAGE_HOURS)
    notes.append("source_pins=%d" % len(EXPECTED_SOURCE_PINS))
    notes.append("MBCMasrDrama_source=%s" % (
        (selected.get("MBCMasrDrama.sa@SD") or {}).get("site", "<missing>")))

    companions = run_companion_gates(xml_path, catalogue_path)
    errors.extend(companions["errors"])
    notes.extend(companions["notes"])
    notes.append("RotanaPolicy=2-day official evening boundary + optional Clip standby")

    status = "FAIL" if errors else "PASS"
    lines = [
        "MBC + ROTANA + SOURCE HEALTH REGRESSION GATE: %s" % status,
        "MBC expected_core=%d actual_provider_ids=%d frozen_ok=%s" % (
            len(policy.FROZEN_CORE_IDS), len(rows), summary.get("frozen_ok", 0)),
        "",
        "Checks:",
    ]
    lines.extend("- %s" % note for note in notes)
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend("- %s" % error for error in errors)
        if "ROTANA_GATE_FAIL" in errors:
            lines.extend(["", "Rotana details:", companions["rotana_output"][-2200:]])
        if "SOURCE_HEALTH_GATE_FAIL" in errors:
            lines.extend(["", "Source-health details:", companions["source_output"][-2200:]])
    else:
        lines.append("- all frozen MBC, Rotana and upstream source invariants passed")

    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
