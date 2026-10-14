#!/usr/bin/env python3
"""Fail-closed application-only receiver parity check for a staging IPK.

The official rc78 payload is NOT proof of parity with the currently working
Vu+ installation. Compare verified hashes of protected installed modules.
Do not compare or copy EPG sources, XMLTV, mappings or configuration content.

Without exact equality or separately verified merged implementations, this
staging candidate is NOT authorized for device installation.
"""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RECEIVER = {
    "receiver_version": "2026.10.08-rc79.1",
    "python_version": "3.14.7",
    "config_schema_version": 15,
    "python_modules_count": 138,
    "protected": {
        "ui/channel_mapping.py": "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002",
        "core/direct_native_import.py": "43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31",
        "core/epg_cycle.py": "f49e92ae9c18919554a010af1f9fb6e5ba4b6a8358c9652b575c652227200887",
        "core/github_direct_stream.py": "52180c29f691e2764fd63d14906bcf851ed72c8447c6b07c4a2f272ec0b98686",
        "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
        "ui/settings.py": "ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa",
        "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
    },
}
REL = Path("usr/lib/enigma2/python/Plugins/Extensions/EPGManager")


def audit(unpacked):
    base = Path(unpacked) / REL
    rows = {}
    for relative, expected in RECEIVER["protected"].items():
        path = base / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows[relative] = {
            "receiver_sha256": expected,
            "candidate_sha256": actual,
            "preserved_exactly": actual == expected,
            "present": actual is not None,
        }
    problems = [name for name, check in rows.items() if not check["preserved_exactly"]]
    return {
        "audit": "EPGManager plugin-only receiver parity",
        "receiver_version": RECEIVER["receiver_version"],
        "receiver_python_version": RECEIVER["python_version"],
        "receiver_schema": RECEIVER["config_schema_version"],
        "reference_origin": "user-supplied read-only diagnostic, 2026-10-14 13:20 UTC",
        "protected_modules": rows,
        "mismatched_or_missing": problems,
        "verified_receiver_parity": not bool(problems),
        "safe_to_install": False,
        "reason": ("Protected modules mismatch; obtain working receiver Python files and"
                   " manually merge and validate before any install.")
                  if problems else "Exact hashes alone do not replace hardware and rollback testing.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unpacked", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = audit(args.unpacked)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("RECEIVER PARITY: " + ("PASS (still not hardware certified)" if report["verified_receiver_parity"] else "NO-GO"))
    return 1 if args.strict and not report["verified_receiver_parity"] else 0


if __name__ == "__main__":
    sys.exit(main())
