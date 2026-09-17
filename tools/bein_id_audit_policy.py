#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""beIN audit policy aligned with the production MENA language/source rules.

Descriptions are optional metadata: a valid schedule/title must not be downgraded
just because <desc> is absent. When descriptions exist, their language/quality
can still be reported diagnostically, but a channel with no descriptions at all
is not REVIEW for that reason alone.

A premium/sports beIN title may be English/Latin. Box Office and FTA are valid
mapping targets and are not downgraded just because they are not ordinary
sports-linear services.

MAX/XTRA are event-channel sources. A generic/repeated holding guide between
real events is a mapping-quality warning, not a reason to reject/remove the
source. Healthy canonical MAX/XTRA IDs therefore receive KEEP_SOURCE while
remaining no-autolock/event-only candidates when their guide is generic.

Legacy NEWS Mono IDs are compatibility/source identities, not preferred mapping
targets. A structurally healthy Arabic NEWS source is retained as KEEP_SOURCE
with auto-lock disabled; the EN twin remains handled by the strict sharder as a
verified compatibility alias of the Arabic source.
"""
from __future__ import annotations

import bein_id_audit as base

_original_initial_verdict = base.initial_verdict
_original_build_profile = base.build_profile

LEGACY_NEWS_SOURCE_IDS = {
    "NEWS_DIGITAL_Mono_AR.bein",
    "NEWS_DIGITAL_Mono_EN.bein",
}


def _text(node, tag):
    el = node.find(tag)
    return ((el.text or "").strip() if el is not None else "")


def policy_build_profile(cid, name, programmes):
    row = _original_build_profile(cid, name, programmes)

    # Diagnostic-only detail. Missing descriptions remain visible in reports but
    # are optional metadata and do not by themselves make an ID REVIEW.
    missing = []
    for p in programmes or []:
        if _text(p, "desc"):
            continue
        missing.append({
            "start": p.get("start") or "",
            "stop": p.get("stop") or "",
            "title": _text(p, "title"),
        })
    row["missing_desc_events"] = missing

    if row.get("kind") in {"max", "xtra"}:
        generic_pct = row.get("generic", 0) / float(max(1, row.get("events", 0))) * 100.0
        repeated = row.get("top_title_pct", 0.0) >= 70.0
        low_diversity = row.get("events", 0) >= 8 and row.get("unique_title_pct", 100.0) <= 20.0
        if row.get("events", 0) <= 0:
            mapping_mode = "standby_no_autolock"
        elif generic_pct >= 70.0 or repeated or low_diversity:
            mapping_mode = "event_only_or_manual"
        else:
            mapping_mode = "normal_candidate"
        row["source_status"] = "KEEP_SOURCE" if row.get("events", 0) > 0 else "STANDBY_SOURCE"
        row["mapping_mode"] = mapping_mode
        row["auto_lock_safe"] = mapping_mode == "normal_candidate"
    elif cid in LEGACY_NEWS_SOURCE_IDS:
        # Keep legacy NEWS IDs for saved mappings/source continuity but never let
        # them compete with the preferred canonical NEWS mapping during auto-lock.
        row["source_status"] = "KEEP_SOURCE" if row.get("events", 0) > 0 else "STANDBY_SOURCE"
        row["mapping_mode"] = "compat_only"
        row["auto_lock_safe"] = False
    else:
        row["source_status"] = "NORMAL"
        row["mapping_mode"] = "normal_candidate"
        row["auto_lock_safe"] = True

    return row


def _healthy_linear_low_diversity(row):
    """Low diversity alone is normal for repeating linear schedules.

    Do not hide real holding-guide/event-channel problems: this exemption applies
    only to ordinary linear services with non-generic titles and a structurally
    clean timeline.  MAX/XTRA keep their stricter event-source handling above.
    """
    if row.get("kind") in {"max", "xtra"}:
        return False
    if int(row.get("events", 0) or 0) < 8:
        return False
    if int(row.get("generic", 0) or 0) or int(row.get("placeholder", 0) or 0):
        return False
    if float(row.get("top_title_pct", 100.0) or 100.0) >= 50.0:
        return False
    if int(row.get("invalid", 0) or 0) or int(row.get("overlaps", 0) or 0):
        return False
    if int(row.get("gaps_gt_2h", 0) or 0):
        return False
    return float(row.get("coverage_hours", 0.0) or 0.0) >= 24.0


def policy_initial_verdict(row, non_recommended):
    verdict, issues, warnings = _original_initial_verdict(row, non_recommended)

    # Description is optional. Keep EMPTY_DESC visible in the profile counters,
    # but never use it as a REVIEW reason.
    warnings = [w for w in warnings if not w.startswith("EMPTY_DESC=")]

    # If the service publishes no descriptions at all, AR_DESC_LOW only means
    # "metadata absent", not "bad EPG". Do not downgrade the mapping for it.
    if row.get("events", 0) > 0 and row.get("empty_desc", 0) >= row.get("events", 0):
        warnings = [w for w in warnings if not w.startswith("AR_DESC_LOW=")]

    # English/Latin premium titles + Arabic descriptions are intentional. Do not
    # demand Arabic text in the title when the title itself is a healthy Latin one.
    if row.get("title_has_latin_pct", 0.0) >= 80.0 and row.get("desc_ar_pct", 0.0) >= 75.0:
        warnings = [w for w in warnings if not w.startswith("TITLE_AR_ENRICHMENT_LOW=")]

    # Repeating linear channels such as beIN 4K and beIN Drama can legitimately
    # have few unique titles across a 24-48h window.  If the schedule is otherwise
    # clean and contains no generic placeholders, diversity remains diagnostic
    # rather than a mapping/release REVIEW reason.
    if _healthy_linear_low_diversity(row):
        warnings = [w for w in warnings if not w.startswith("LOW_TITLE_DIVERSITY=")]

    # Box Office is a legitimate beIN EPG/mapping target even though it is not a
    # conventional sports-linear channel.
    warnings = [w for w in warnings if w != "BOXOFFICE_NOT_SPORTS_LINEAR"]

    if issues:
        verdict = "FAIL"
    elif row.get("id") in LEGACY_NEWS_SOURCE_IDS and row.get("events", 0) > 0:
        # Known compatibility/source identity. Keep warnings visible (for example
        # repetitive news labels) but do not turn a known safe source into a new
        # release-blocking REVIEW. It remains no-autolock by policy_build_profile.
        verdict = "KEEP_SOURCE"
    elif row.get("kind") in {"max", "xtra"} and row.get("events", 0) > 0:
        # Keep warnings visible for diagnostics, but do not equate an event-source
        # holding guide with a bad source. Alias handling in the base auditor may
        # subsequently convert verified compatibility IDs to ALIAS_OK.
        verdict = "KEEP_SOURCE"
    elif warnings:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    return verdict, issues, warnings


base.build_profile = policy_build_profile
base.initial_verdict = policy_initial_verdict


if __name__ == "__main__":
    raise SystemExit(base.main())
