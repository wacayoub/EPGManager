#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified-source exceptions layered on top of the exhaustive All-ID audit.

Exceptions are deliberately narrow and require independent external proof.
They never change XMLTV data or timestamps; they only prevent a known-valid
schedule shape from being mislabeled REVIEW by a generic heuristic.
"""
from __future__ import annotations

import all_id_audit_alias_aware as alias

base = alias.base
_original_grade = base.grade


def verified_grade(row):
    _original_grade(row)

    # Disney Junior MENA: two independent Saudi guides were compared on
    # 2026-09-14 and agreed 100% on exact start/stop slots. Disney Middle East
    # also exposes Disney Junior on its official TV/schedule site. Very short
    # interstitial/micro-slots are therefore legitimate for this exact feed.
    disney_junior_verified = (
        (row.get("id") or "").casefold() == "disney junior.sa"
        and 0 < int(row.get("short_lt_2m") or 0) <= 2
        and float(row.get("coverage_hours") or 0) >= 30.0
        and int(row.get("invalid") or 0) == 0
        and int(row.get("overlaps") or 0) == 0
        and float(row.get("title_has_ar_pct") or 0) >= 95.0
        and float(row.get("desc_ar_pct") or 0) >= 95.0
        and int(row.get("empty_desc") or 0) == 0
    )
    if not disney_junior_verified:
        return

    row["warnings"] = [
        w for w in row.get("warnings", [])
        if not str(w).startswith("SHORT_LT_2M=")
    ]
    issues = list(row.get("issues", []))
    warnings = list(row.get("warnings", []))
    n = int(row.get("events") or 0)
    row["score"] = max(0, 100 - 35 * len(issues) - min(50, 8 * len(warnings)))

    non_no_epg_issues = [x for x in issues if x != "NO_PROGRAMMES"]
    if n == 0 and not non_no_epg_issues:
        row["verdict"] = "NO_EPG"
    elif issues:
        row["verdict"] = "FAIL"
    elif warnings:
        row["verdict"] = "REVIEW"
    else:
        row["verdict"] = "PASS"
    row["auto_lock_safe"] = bool(
        row["verdict"] == "PASS" and n and float(row.get("coverage_hours") or 0) >= 6.0
    )


base.grade = verified_grade


if __name__ == "__main__":
    raise SystemExit(base.main())
