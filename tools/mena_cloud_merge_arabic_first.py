#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arabic-first policy layer for the MENA Cloud merge.

Rules:
- regular MENA/Nilesat/Badr-style channels: native Arabic programme metadata wins
  before English/international metadata whenever a structurally safe Arabic feed exists;
- English remains fallback only when no trustworthy Arabic candidate exists;
- beIN/OSN premium policy remains handled by the existing premium pipeline;
- known Arabic/Latin aliases are collapsed for selected local channels;
- quarantine technical IDs, placeholder guides and exact cloned timelines shared
  by unrelated channels before they can enter logical-channel arbitration;
- keep legitimate quarantined channel identities as NO-EPG mapping candidates;
- retired SSC linear-channel identities are removed from the active 2026 EPG set;
- recovery feeds may only fill an already-known identity that has no clean timetable;
- obvious foreign-guide contamination (for example Al Jazeera English schedule on
  an unrelated local channel) is rejected rather than published as false EPG.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe
import mena_integrity_guard as guard

_original_choose_timeline = safe._choose_timeline
_original_clean_timeline = safe._clean_timeline
_original_logical_key = base.logical_key
_original_load_candidates = base.load_candidates
_original_source_base = base.source_base
_QUARANTINE_FINDINGS = []
_KNOWN_KEYS = set()
_CLEAN_PROGRAMME_KEYS = set()

# Recovery framework intentionally starts empty. A recovery source is enabled
# only after its live endpoint and identity mapping have passed an explicit
# source-integrity audit. Dead/static legacy URLs must never remain here.
_RECOVERY_SOURCES = []
for _row in _RECOVERY_SOURCES:
    if _row not in base.REMOTE_SOURCES:
        base.REMOTE_SOURCES.append(_row)

# SSC stopped linear broadcasting in October 2025 and the Saudi Sports Company
# closed in July 2026. Keep stale upstream IDs out of current Smart Mapping.
_RETIRED_SSC_RE = re.compile(
    r"(?:^|\s)ssc(?:\s*(?:[1-7]|extra(?:\s*[1-3])?|news|sports?))?(?:\s|$)",
    re.I,
)


def recovery_source_base(origin, site):
    if origin == "iptvorg":
        return 88
    return _original_source_base(origin, site)


base.source_base = recovery_source_base


def _probe(cid, name):
    return safe._compact("%s %s" % (cid or "", name or ""))


def _is_retired_ssc(candidate):
    return bool(_RETIRED_SSC_RE.search(_probe(candidate.cid, candidate.name)))


def arabic_first_logical_key(cid, name):
    """Collapse a few proven Latin/Arabic spellings before language arbitration."""
    p = _probe(cid, name)

    if (re.search(r"(?:^| )aden(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة |تلفزيون )?عدن(?: |$)", p)):
        return "aden tv"
    if (re.search(r"(?:^| )afaq(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة )?(?:آفاق|افاق)(?: |$)", p)):
        return "afaq tv"
    if (re.search(r"(?:^| )al nada(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة )?الندى(?: |$)", p)):
        return "al nada tv"

    return _original_logical_key(cid, name)


base.logical_key = arabic_first_logical_key


def guarded_load_candidates(root, origin, source_name, site_by_id, now, end):
    """Quarantine bad programmes and constrain recovery to true NO-EPG gaps."""
    rows = _original_load_candidates(root, origin, source_name, site_by_id, now, end)
    is_recovery = source_name.startswith("recovery-")

    retired = [c for c in rows if _is_retired_ssc(c)]
    retired_ids = sorted({c.cid for c in retired}, key=str.casefold)
    if retired:
        rows = [c for c in rows if not _is_retired_ssc(c)]

    clean, findings = guard.sanitize_candidate_rows(
        rows,
        source_name=source_name,
        detect_clones=(origin in {"openepg", "epgshare", "iptvorg"}),
    )

    clean_obj_ids = {id(c) for c in clean}
    reason_by_id = {
        q.get("id", ""): set(q.get("reasons") or [])
        for q in findings.get("quarantined", [])
    }
    identity_only = []
    dropped = list(retired_ids)
    for c in rows:
        if id(c) in clean_obj_ids:
            continue
        reasons = reason_by_id.get(c.cid, set())
        if "TECHNICAL_OR_ASSET_ID" in reasons or "SUSPICIOUS_BEIN_SPORTS66_ID" in reasons:
            dropped.append(c.cid)
            continue
        if is_recovery:
            dropped.append(c.cid)
            continue
        c.programmes = []
        identity_only.append(c)

    rejected_unknown = []
    rejected_already_clean = []
    if is_recovery:
        accepted = []
        for c in clean:
            if not c.key or c.key not in _KNOWN_KEYS:
                rejected_unknown.append(c.cid)
                continue
            if c.key in _CLEAN_PROGRAMME_KEYS:
                rejected_already_clean.append(c.cid)
                continue
            accepted.append(c)
        clean = accepted

    result = clean + identity_only
    if not is_recovery:
        _KNOWN_KEYS.update(c.key for c in result if c.key)
        _CLEAN_PROGRAMME_KEYS.update(c.key for c in clean if c.key and c.programmes)
    else:
        _CLEAN_PROGRAMME_KEYS.update(c.key for c in clean if c.key and c.programmes)

    findings["identity_only_candidates"] = len(identity_only)
    findings["retired_ssc_ids"] = retired_ids
    findings["retired_ssc_dropped"] = len(retired_ids)
    findings["dropped_ids"] = sorted(set(dropped), key=str.casefold)
    findings["recovery_rejected_unknown_identity"] = len(rejected_unknown)
    findings["recovery_rejected_unknown_ids"] = sorted(set(rejected_unknown), key=str.casefold)
    findings["recovery_rejected_already_clean"] = len(rejected_already_clean)
    findings["recovery_rejected_already_clean_ids"] = sorted(set(rejected_already_clean), key=str.casefold)
    findings["recovery_accepted"] = len(clean) if is_recovery else 0

    if findings.get("quarantined_candidates") or is_recovery or retired_ids:
        _QUARANTINE_FINDINGS.append(findings)
        if is_recovery:
            print("Recovery source: %s accepted=%d already-clean=%d unknown=%d quarantined=%d" % (
                source_name, len(clean), len(rejected_already_clean), len(rejected_unknown),
                findings["quarantined_candidates"]))
        else:
            print("Integrity quarantine: %s clean=%d identity-only=%d dropped=%d retired-ssc=%d quarantined=%d" % (
                source_name, len(clean), len(identity_only), len(set(dropped)), len(retired_ids),
                findings["quarantined_candidates"]))
    return result


base.load_candidates = guarded_load_candidates


def _event_has_lang(programme, role, wanted):
    for text, declared in base.text_items(programme, role):
        detected = base.language_of(text)
        if declared == wanted or detected == wanted:
            return True
    return False


def _arabic_profile(candidate):
    rows = candidate.programmes or []
    n = max(1, len(rows))
    t_ar = sum(1 for p in rows if _event_has_lang(p, "title", "ar")) / float(n)
    d_ar = sum(1 for p in rows if _event_has_lang(p, "desc", "ar")) / float(n)
    t_en = sum(1 for p in rows if _event_has_lang(p, "title", "en")) / float(n)
    return t_ar, d_ar, t_en


def _structurally_safe(stats):
    valid = max(1, int(stats.get("valid", 0)))
    if int(stats.get("valid", 0)) < 2:
        return False
    if stats.get("invalid", 0):
        return False
    if stats.get("mixed_tz", 0):
        return False
    if stats.get("overlaps", 0) > max(1, int(valid * 0.05)):
        return False
    if stats.get("long", 0) > max(1, int(valid * 0.05)):
        return False
    if stats.get("placeholders", 0) > max(1, int(valid * 0.15)):
        return False
    return True


def _arabic_tier(candidate, stats):
    title_ar, desc_ar, _title_en = _arabic_profile(candidate)
    if not _structurally_safe(stats):
        return 0
    if title_ar >= 0.80:
        return 4
    if title_ar >= 0.55:
        return 3
    if title_ar >= 0.30:
        return 2
    if title_ar >= 0.10 or desc_ar >= 0.60:
        return 1
    return 0


_AJE_SIGNATURE_RE = re.compile(
    r"^(?:newshour|inside story|al jazeera world|the listening post|the bottom line|"
    r"people\s*&?\s*power|people and power|101 east|witness|upfront)$",
    re.I,
)


def _is_aljazeera_identity(candidate):
    p = _probe(candidate.cid, candidate.name)
    return "aljazeera" in p or "al jazeera" in p or "الجزيرة" in p


def _foreign_contamination(candidate):
    if _is_aljazeera_identity(candidate):
        return False
    hits = set()
    for programme in candidate.programmes:
        title_items = base.text_items(programme, "title")
        title = title_items[0][0].strip() if title_items else ""
        if _AJE_SIGNATURE_RE.match(title):
            hits.add(title.casefold())
    return len(hits) >= 3


def arabic_first_choose_timeline(candidates):
    with_programmes = [c for c in candidates if c.programmes]
    if with_programmes:
        candidates = with_programmes

    premium = any(base.is_premium(c.cid, c.name) for c in candidates)
    if premium:
        return _original_choose_timeline(candidates)

    ranked = []
    for c in candidates:
        st = safe._candidate_stats(c, False)
        contaminated = _foreign_contamination(c)
        tier = 0 if contaminated else _arabic_tier(c, st)
        title_ar, desc_ar, _title_en = _arabic_profile(c)
        ranked.append((
            tier,
            title_ar,
            desc_ar,
            0 if contaminated else 1,
            st["score"],
            st["valid"],
            base.source_base(c.origin, c.site),
            c.cid.casefold(),
            c,
            st,
        ))

    clean = [x for x in ranked if x[3] == 1]
    if clean:
        best_tier = max(x[0] for x in clean)
        pool = [x for x in clean if x[0] == best_tier] if best_tier > 0 else clean
        pool.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[4], x[5], x[6], x[7]))
        chosen = pool[0]
        return chosen[8], chosen[9]

    return _original_choose_timeline(candidates)


def arabic_first_clean_timeline(candidate, premium):
    if not candidate.programmes:
        return []
    if not premium and _foreign_contamination(candidate):
        return []
    return _original_clean_timeline(candidate, premium)


safe._choose_timeline = arabic_first_choose_timeline
safe._clean_timeline = arabic_first_clean_timeline


def _report_path():
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--report" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1])
        if arg.startswith("--report="):
            return Path(arg.split("=", 1)[1])
    return None


def _write_quarantine_report():
    path = _report_path()
    if path is None or not path.is_file():
        return
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        report = {}
    quarantined = [q for finding in _QUARANTINE_FINDINGS for q in finding.get("quarantined", [])]
    identity_only_total = sum(int(x.get("identity_only_candidates", 0) or 0) for x in _QUARANTINE_FINDINGS)
    dropped_ids = sorted({cid for x in _QUARANTINE_FINDINGS for cid in x.get("dropped_ids", [])}, key=str.casefold)
    retired_ssc_ids = sorted({cid for x in _QUARANTINE_FINDINGS for cid in x.get("retired_ssc_ids", [])}, key=str.casefold)
    recovery_accepted = sum(int(x.get("recovery_accepted", 0) or 0) for x in _QUARANTINE_FINDINGS)
    recovery_unknown = sum(int(x.get("recovery_rejected_unknown_identity", 0) or 0) for x in _QUARANTINE_FINDINGS)
    recovery_already_clean = sum(int(x.get("recovery_rejected_already_clean", 0) or 0) for x in _QUARANTINE_FINDINGS)

    by_source = {x.get("source"): x for x in _QUARANTINE_FINDINGS}
    for row in report.get("source_stats", []):
        finding = by_source.get(row.get("name"))
        if not finding:
            continue
        identity_only = int(finding.get("identity_only_candidates", 0) or 0)
        row["channels_with_current_48h_epg"] = max(
            0, int(row.get("channels_with_current_48h_epg", 0) or 0) - identity_only
        )
        row["quarantined_identity_only"] = identity_only
        row["retired_ssc_dropped"] = int(finding.get("retired_ssc_dropped", 0) or 0)
        if row.get("name", "").startswith("recovery-"):
            row["recovery_accepted"] = int(finding.get("recovery_accepted", 0) or 0)
            row["recovery_rejected_unknown_identity"] = int(finding.get("recovery_rejected_unknown_identity", 0) or 0)
            row["recovery_rejected_already_clean"] = int(finding.get("recovery_rejected_already_clean", 0) or 0)
            row["channels_with_current_48h_epg"] = int(finding.get("recovery_accepted", 0) or 0)

    report["integrity_quarantine"] = {
        "sources": _QUARANTINE_FINDINGS,
        "quarantined_candidates": len(quarantined),
        "identity_only_candidates": identity_only_total,
        "dropped_ids": dropped_ids,
        "retired_ssc_ids": retired_ssc_ids,
        "quarantined_ids": sorted({q.get("id", "") for q in quarantined if q.get("id")}, key=str.casefold),
        "policy": "wrong/generic programmes are stripped but legitimate channel IDs are retained as NO EPG; technical/asset, known impossible and retired SSC IDs are dropped",
    }
    report["recovery"] = {
        "sources": [x[0] for x in _RECOVERY_SOURCES],
        "accepted_candidate_rows": recovery_accepted,
        "rejected_unknown_identity_rows": recovery_unknown,
        "rejected_already_clean_rows": recovery_already_clean,
        "policy": "recovery feeds can only fill exact existing logical MENA identities with no clean timetable and must pass the same integrity guard",
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    rc = base.main()
    if rc == 0:
        _write_quarantine_report()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
