#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""beIN audit policy aligned with the production MENA language rules.

A premium/sports beIN title may be English/Latin when the description is valid
Arabic. Box Office and FTA are valid mapping targets and are not downgraded just
because they are not ordinary sports-linear services.
"""
from __future__ import annotations

import bein_id_audit as base

_original_initial_verdict = base.initial_verdict


def policy_initial_verdict(row, non_recommended):
    verdict, issues, warnings = _original_initial_verdict(row, non_recommended)

    # English/Latin premium titles + Arabic descriptions are intentional. Do not
    # demand Arabic text in the title when the title itself is a healthy Latin one.
    if row.get("title_has_latin_pct", 0.0) >= 80.0 and row.get("desc_ar_pct", 0.0) >= 75.0:
        warnings = [w for w in warnings if not w.startswith("TITLE_AR_ENRICHMENT_LOW=")]

    # Box Office is a legitimate beIN EPG/mapping target even though it is not a
    # conventional sports-linear channel.
    warnings = [w for w in warnings if w != "BOXOFFICE_NOT_SPORTS_LINEAR"]

    if issues:
        verdict = "FAIL"
    elif warnings:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    return verdict, issues, warnings


base.initial_verdict = policy_initial_verdict


if __name__ == "__main__":
    raise SystemExit(base.main())
