#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the ADM quality audit against the final receiver-facing ADM.* namespace.

The upstream ADM identity policy intentionally uses pre-normalization IDs while
building/merging candidates.  The published provider-adm shard is normalized by
global_receiver_id_normalize.py, so receiver-facing audits must validate the
final ADM.* IDs instead of treating them as unexpected aliases.

This wrapper reuses the existing structural/language policy unchanged and only
swaps the identity sets to their final published namespace.  It therefore fixes
false namespace FAILs without weakening coverage, Arabic-quality, overlap or
autolock gates.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import adm_id_audit_policy as policy
import adm_final_regression as final_regression


ARABIC_CORE_IDS = {
    "ADM.Abu.Dhabi.TV.ae",
    "ADM.Al.Emarat.TV.ae",
    "ADM.Abu.Dhabi.Sports.1.ae",
    "ADM.Abu.Dhabi.Sports.2.ae",
    "ADM.Majid.ae",
    "ADM.National.Geographic.Abu.Dhabi.ae",
}
PREMIUM_CORE_IDS = {
    "ADM.AD.Sports.Premium.1.ae",
    "ADM.AD.Sports.Premium.2.ae",
}
REVIEW_CORE_IDS = {"ADM.Yas.TV.ae"}
SECONDARY_IDS = {
    "ADM.AD.Sports.Extra.ae",
    "ADM.YAS.TV.Extra.ae",
}
OPTIONAL_STANDBY_IDS = {"ADM.Baynounah.TV.ae"}

# Anything from the old pre-normalization receiver namespace is legacy once the
# canonical publication boundary has run.
PRE_NORMALIZED_IDS = (
    set(policy.ARABIC_CORE_IDS)
    | set(policy.PREMIUM_CORE_IDS)
    | set(policy.REVIEW_CORE_IDS)
    | set(policy.SECONDARY_IDS)
    | set(policy.OPTIONAL_STANDBY_IDS)
)


def install_receiver_namespace() -> None:
    policy.ARABIC_CORE_IDS = set(ARABIC_CORE_IDS)
    policy.PREMIUM_CORE_IDS = set(PREMIUM_CORE_IDS)
    policy.INHERITED_CORE_IDS = set()
    policy.REVIEW_CORE_IDS = set(REVIEW_CORE_IDS)
    policy.CORE_IDS = (
        policy.ARABIC_CORE_IDS
        | policy.PREMIUM_CORE_IDS
        | policy.INHERITED_CORE_IDS
        | policy.REVIEW_CORE_IDS
    )
    policy.SECONDARY_IDS = set(SECONDARY_IDS)
    policy.OPTIONAL_STANDBY_IDS = set(OPTIONAL_STANDBY_IDS)
    policy.REQUIRED_IDS = set(policy.CORE_IDS)
    policy.ALLOWED_IDS = (
        policy.CORE_IDS | policy.SECONDARY_IDS | policy.OPTIONAL_STANDBY_IDS
    )
    policy.LEGACY_IDS = set(policy.LEGACY_IDS) | PRE_NORMALIZED_IDS


def call_main(func, argv):
    old = sys.argv[:]
    try:
        sys.argv = argv
        return int(func() or 0)
    finally:
        sys.argv = old


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--final-text", required=True)
    args = ap.parse_args()

    install_receiver_namespace()

    audit_rc = call_main(
        policy.main,
        [
            "adm_id_audit_policy.py",
            "--xml", args.xml,
            "--json", args.json,
            "--text", args.text,
        ],
    )
    if audit_rc:
        # Keep the refreshed report published for diagnosis; final regression
        # would only duplicate the same hard failure set.
        return audit_rc

    return call_main(
        final_regression.main,
        [
            "adm_final_regression.py",
            "--audit-json", args.json,
            "--text", args.final_text,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
