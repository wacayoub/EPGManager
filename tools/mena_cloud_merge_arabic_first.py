#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arabic-first policy layer for the MENA Cloud merge.

Rules:
- regular MENA/Nilesat/Badr-style channels: native Arabic programme metadata wins
  before English/international metadata whenever a structurally safe Arabic feed exists;
- English remains fallback only when no trustworthy Arabic candidate exists;
- beIN/OSN premium policy remains handled by the existing premium pipeline;
- known Arabic/Latin aliases are collapsed for selected local channels;
- obvious foreign-guide contamination (for example Al Jazeera English schedule on
  an unrelated local channel) is rejected rather than published as false EPG.
"""
from __future__ import annotations

import re

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe

_original_choose_timeline = safe._choose_timeline
_original_clean_timeline = safe._clean_timeline
_original_logical_key = base.logical_key


def _probe(cid, name):
    return safe._compact("%s %s" % (cid or "", name or ""))


def arabic_first_logical_key(cid, name):
    """Collapse a few proven Latin/Arabic spellings before language arbitration."""
    p = _probe(cid, name)

    # Yemen: Aden TV / قناة عدن / تلفزيون عدن.
    if (re.search(r"(?:^| )aden(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة |تلفزيون )?عدن(?: |$)", p)):
        return "aden tv"

    # Iraq: AFAQ / Afaq TV / آفاق / افاق.
    if (re.search(r"(?:^| )afaq(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة )?(?:آفاق|افاق)(?: |$)", p)):
        return "afaq tv"

    # Egypt: Al Nada TV / قناة الندى / الندى.
    if (re.search(r"(?:^| )al nada(?: tv)?(?: |$)", p)
            or re.search(r"(?:^| )(?:قناة )?الندى(?: |$)", p)):
        return "al nada tv"

    return _original_logical_key(cid, name)


# load_candidates() resolves base.logical_key at runtime.
base.logical_key = arabic_first_logical_key


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
    """Hard language tier: Arabic quality outranks source brand for regular MENA."""
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
    """Detect a distinctive foreign channel schedule on an unrelated local identity."""
    if _is_aljazeera_identity(candidate):
        return False
    hits = set()
    for programme in candidate.programmes:
        title_items = base.text_items(programme, "title")
        title = title_items[0][0].strip() if title_items else ""
        if _AJE_SIGNATURE_RE.match(title):
            hits.add(title.casefold())
    # Requiring several distinct signature shows avoids rejecting a coincidental title.
    return len(hits) >= 3


def arabic_first_choose_timeline(candidates):
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
        # Arabic truly comes first. Source/provider score only breaks ties inside
        # the best available Arabic tier.
        pool = [x for x in clean if x[0] == best_tier] if best_tier > 0 else clean
        pool.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[4], x[5], x[6], x[7]))
        chosen = pool[0]
        return chosen[8], chosen[9]

    # Every candidate was contaminated: preserve identity but let clean_timeline
    # publish no false programmes.
    return _original_choose_timeline(candidates)


def arabic_first_clean_timeline(candidate, premium):
    if not premium and _foreign_contamination(candidate):
        return []
    return _original_clean_timeline(candidate, premium)


# safe_cluster_programmes() resolves these globals from mena_cloud_merge_safe
# at runtime, so this layer changes arbitration without duplicating merge code.
safe._choose_timeline = arabic_first_choose_timeline
safe._clean_timeline = arabic_first_clean_timeline


if __name__ == "__main__":
    raise SystemExit(base.main())
