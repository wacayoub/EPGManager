#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared programme-integrity guard for MENA Cloud.

This module is intentionally conservative. It detects source artefact IDs,
placeholder guides and byte-equivalent timelines assigned to unrelated channel
identities. Suspect programme data is quarantined; channel identity may still be
retained elsewhere by the catalogue/mapping layer.

beIN MAX/XTRA are a deliberate exception to the generic/clone quarantine rule:
they are event-channel source feeds and often carry a repeated holding guide
between events. Their source rows must stay available so a real event can appear
without the ID being rediscovered. They are still subject to bad-ID/technical-ID
checks and should not be auto-locked from a generic holding title alone.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
import re

TECH_PATTERNS = [
    re.compile(r"brand\s*logo", re.I),
    re.compile(r"logo\.svg", re.I),
    re.compile(r"(?:^|[-_ ])logo(?:[-_ .]|$)", re.I),
    re.compile(r"colour[-_ ]?blue", re.I),
    re.compile(r"\b200x200\b", re.I),
    re.compile(r"\bstacked\s+nov\b", re.I),
    re.compile(r"\bupdatez[-_ ]?ngw\b", re.I),
    re.compile(r"\bplaceholder\b", re.I),
    re.compile(r"\bdummy\b", re.I),
]
TECH_ALLOWLIST = {"logos.tv.ae"}
KNOWN_BAD_IDS = {
    "bein sports66 digital -01.qa": "SUSPICIOUS_BEIN_SPORTS66_ID",
    "bein_sports66_digital_mono-01_en.bein": "SUSPICIOUS_BEIN_SPORTS66_ID",
}

GENERIC_TITLE_PATTERNS = [
    re.compile(r"^(?:tv\s+)?guide\s+is\s+not\s+available$", re.I),
    re.compile(r"^(?:programme|program)\s+schedule\s+unavailable$", re.I),
    re.compile(r"^schedule\s+unavailable$", re.I),
    re.compile(r"^(?:no\s+(?:information|info)|tba)$", re.I),
    re.compile(r"^(?:جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$"),
    re.compile(r"^bein\s+sports\s+max(?:\s*\d+)?$", re.I),
    re.compile(r"^bein\s+sports\s+xtra(?:\s*\d+)?$", re.I),
    re.compile(r"^bein\s+sports\s+xtra\s+for\s+live\s+and\s+exclusive\s+coverage\b", re.I),
]

DROP_ID_WORDS = {
    "hd", "sd", "uhd", "4k", "tv", "channel", "digital", "mono",
    "ar", "en", "english", "arabic", "fhd", "full",
}


def _text(programme, role="title"):
    node = programme.find(role)
    return ((node.text or "").strip() if node is not None else "")


def _norm(value):
    value = (value or "").casefold()
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def bad_id_reason(cid):
    return KNOWN_BAD_IDS.get((cid or "").strip().casefold(), "")


def is_technical_id(cid):
    low = (cid or "").strip().casefold()
    if low in TECH_ALLOWLIST:
        return False
    if low.startswith(("logos-_", "logos_")):
        return True
    return any(p.search(cid or "") for p in TECH_PATTERNS)


def is_bein_event_source(cid, name=""):
    """True for beIN MAX/XTRA event-channel source identities.

    Match on both ID and display name because upstream providers use several
    spellings (beINSportsMax1, beIN SPORTS XTRA 3, etc.).
    """
    probe = _norm("%s %s" % (cid or "", name or ""))
    compact = probe.replace(" ", "")
    has_bein = "bein" in compact
    has_event = "max" in probe.split() or "xtra" in probe.split() or "max" in compact or "xtra" in compact
    return has_bein and has_event


def identity_name(value):
    value = (value or "").casefold()
    value = re.sub(r"\.(?:ae|sa|qa|eg|bh|kw|om|jo|lb|iq|ps|ye|dz|tn|ly|sd|sy|mr|mena|bein)(?:@.*)?$", "", value)
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    bits = [x for x in value.split() if x not in DROP_ID_WORDS]
    return " ".join(bits)


def identity_similarity(a, b):
    aa, bb = identity_name(a), identity_name(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    return SequenceMatcher(None, aa, bb).ratio()


def candidate_identity(candidate):
    return "%s %s" % (getattr(candidate, "cid", "") or "", getattr(candidate, "name", "") or "")


def candidate_is_bein_event_source(candidate):
    return is_bein_event_source(
        getattr(candidate, "cid", "") or "",
        getattr(candidate, "name", "") or "",
    )


def timeline_fingerprint(programmes, min_events=3):
    rows = []
    for p in programmes or []:
        start = (p.get("start") or "").strip()
        stop = (p.get("stop") or "").strip()
        title = _norm(_text(p, "title"))
        if start and title:
            rows.append((start, stop, title))
    rows.sort()
    if len(rows) < min_events:
        return None
    return tuple(rows)


def generic_ratio(programmes):
    titles = [_text(p, "title") for p in programmes or []]
    titles = [x for x in titles if x]
    if not titles:
        return 0.0
    bad = 0
    for title in titles:
        if any(rx.search(title.strip()) for rx in GENERIC_TITLE_PATTERNS):
            bad += 1
    return bad / float(len(titles))


def _unrelated_group(members, identity_getter):
    if len(members) < 3:
        return False
    identities = [identity_getter(x) for x in members]
    for i in range(len(identities)):
        for j in range(i + 1, len(identities)):
            if identity_similarity(identities[i], identities[j]) < 0.40:
                return True
    return False


def sanitize_candidate_rows(rows, source_name="", detect_clones=True):
    """Return (clean_rows, findings) for one raw source invocation."""
    rows = list(rows or [])
    reasons = defaultdict(set)
    clone_groups = []
    event_sources_preserved = []

    for c in rows:
        cid = getattr(c, "cid", "") or ""
        name = getattr(c, "name", "") or ""
        programmes = getattr(c, "programmes", []) or []
        event_source = is_bein_event_source(cid, name)
        bad = bad_id_reason(cid)
        if bad:
            reasons[id(c)].add(bad)
        if is_technical_id(cid):
            reasons[id(c)].add("TECHNICAL_OR_ASSET_ID")
        if len(programmes) >= 3 and generic_ratio(programmes) >= 0.80:
            if event_source:
                event_sources_preserved.append(cid)
            else:
                reasons[id(c)].add("GENERIC_OR_PLACEHOLDER_GUIDE")

    if detect_clones:
        groups = defaultdict(list)
        for c in rows:
            fp = timeline_fingerprint(getattr(c, "programmes", []) or [])
            if fp is not None:
                groups[fp].append(c)
        for members in groups.values():
            if not _unrelated_group(members, candidate_identity):
                continue
            ids = [getattr(c, "cid", "") or "" for c in members]
            clone_groups.append(ids)
            tag = "CLONED_UNRELATED_TIMELINE=%d" % len(members)
            for c in members:
                # Identical MAX/XTRA holding timelines are expected outside real
                # events. Retain those source rows; only non-event identities in
                # the group remain quarantine candidates.
                if candidate_is_bein_event_source(c):
                    if (getattr(c, "cid", "") or "") not in event_sources_preserved:
                        event_sources_preserved.append(getattr(c, "cid", "") or "")
                    continue
                reasons[id(c)].add(tag)

    clean, quarantined = [], []
    for c in rows:
        why = sorted(reasons.get(id(c), set()))
        if why:
            quarantined.append({
                "source": source_name,
                "id": getattr(c, "cid", "") or "",
                "name": getattr(c, "name", "") or "",
                "reasons": why,
                "programmes": len(getattr(c, "programmes", []) or []),
            })
        else:
            clean.append(c)

    findings = {
        "source": source_name,
        "input_candidates": len(rows),
        "kept_candidates": len(clean),
        "quarantined_candidates": len(quarantined),
        "quarantined": quarantined,
        "clone_groups": clone_groups,
        "bein_event_sources_preserved": sorted(set(event_sources_preserved), key=str.casefold),
    }
    return clean, findings


def sanitize_programme_groups(groups):
    """Filter final/LKG programme groups using the same hard integrity rules."""
    groups = dict(groups or {})
    blocked = defaultdict(set)
    event_sources_preserved = []

    for cid, programmes in groups.items():
        event_source = is_bein_event_source(cid)
        bad = bad_id_reason(cid)
        if bad:
            blocked[cid].add(bad)
        if is_technical_id(cid):
            blocked[cid].add("TECHNICAL_OR_ASSET_ID")
        if len(programmes) >= 3 and generic_ratio(programmes) >= 0.80:
            if event_source:
                event_sources_preserved.append(cid)
            else:
                blocked[cid].add("GENERIC_OR_PLACEHOLDER_GUIDE")

    fp_groups = defaultdict(list)
    for cid, programmes in groups.items():
        fp = timeline_fingerprint(programmes)
        if fp is not None:
            fp_groups[fp].append(cid)

    clone_groups = []
    for members in fp_groups.values():
        if len(members) < 3:
            continue
        if not _unrelated_group(members, lambda x: x):
            continue
        clone_groups.append(list(members))
        tag = "CLONED_UNRELATED_TIMELINE=%d" % len(members)
        for cid in members:
            if is_bein_event_source(cid):
                if cid not in event_sources_preserved:
                    event_sources_preserved.append(cid)
                continue
            blocked[cid].add(tag)

    clean = {cid: rows for cid, rows in groups.items() if cid not in blocked}
    reason_counts = Counter(reason for vals in blocked.values() for reason in vals)
    findings = {
        "input_channels": len(groups),
        "kept_channels": len(clean),
        "blocked_channels": len(blocked),
        "reason_counts": dict(reason_counts),
        "blocked": {cid: sorted(vals) for cid, vals in sorted(blocked.items())},
        "clone_groups": clone_groups,
        "bein_event_sources_preserved": sorted(set(event_sources_preserved), key=str.casefold),
    }
    return clean, findings
