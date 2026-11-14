# -*- coding: utf-8 -*-
"""Conservative deterministic service-name matcher for beta41.

This is not AI and does not learn.  It is used only after exact/canonical alias
matching failed.  A result is accepted only when it is unique, high confidence,
keeps numeric variants intact and passes provider/satellite safety checks.
"""
from __future__ import print_function

import re

from . import channel_mapper
from .channel_identity_resolver import normalize, resolve as resolve_identity, canonical_key

_MENA = set(("MA","DZ","TN","LY","EG","SA","AE","QA","KW","BH","OM","JO","LB","PS","IQ","SY","YE","MENA"))
_EUROPE = set(("FR","BE","NL","DE","AT","CH","IT","ES","PT","UK","IE","GR","TR","RO","BG","HR","RS","SI","SK","CZ","DK","SE","NO","FI","PL"))
_EUROPE_PROVIDER_TOKENS = (
    "sky italia", "sky deutschland", "sky uk", "cyfrowy", "polsat", "platforma canal",
    "canal+", "canalsat", "nc+", "tivusat", "rai", "mediaset", "m7", "hd+",
    "movistar", "orange france", "orange polska", "tvn", "telenor", "viasat",
    "nova", "total tv", "upc", "freeview", "freesat")
_MENA_PROVIDER_TOKENS = (
    "snrt", "bein", "be in", "osn", "mbc", "rotana", "abu dhabi", "admc", "dubai",
    "al jazeera", "alkass", "ksa", "saudi", "qatar", "nilesat", "arabsat",
    "shahid", "ssc", "etisalat", "ooredoo")
_AMBIGUOUS_GLOBAL = (
    "disney", "discovery", "national geographic", "nat geo", "bein sports",
    "cartoon network", "nickelodeon", "cnn", "bbc", "sky news", "animal planet",
    "star movies", "star world", "star action", "baby tv", "cbeebies",
    "food network", "bloomberg", "fatafeat")
_EXPLICIT_MENA = (
    "al jazeera", "mbc", "rotana", "abu dhabi", "ad sport", "dubai sports", "dubai racing",
    "dw arabia", "trt arabi", "sky news arabia", "cnbc arabia", "2m", "al aoula",
    "tamazight", "al maghribia", "assadissa", "attaqafia", "arryadia", "medi1",
    "saudi", "ksa", "alkass", "ssc")
_SOFT_DROP = set(("tv", "television", "network", "channel", "digital", "hd", "fhd", "uhd", "sd", "hdr", "hevc", "mpeg4"))
_GENERIC_SOFT_TOKENS = set(("al", "el", "saudi", "ksa", "arabia", "arabic", "english", "french", "sports", "sport", "plus"))


def _source_countries(source):
    return set(str(x).upper() for x in ((source or {}).get("countries") or []) if x)


def _source_family(source):
    countries = _source_countries(source)
    region = str((source or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return ""


def _provider_family(provider):
    text = str(provider or "").lower()
    if any(x in text for x in _MENA_PROVIDER_TOKENS):
        return "MENA"
    if any(x in text for x in _EUROPE_PROVIDER_TOKENS):
        return "EUROPE"
    return ""


def _explicit_mena(name):
    text = str(name or "").lower()
    return any(x in text for x in _EXPLICIT_MENA)


def _ambiguous_global(name):
    text = str(name or "").lower()
    return any(x in text for x in _AMBIGUOUS_GLOBAL)


def provider_satellite_allowed(service, source):
    """Reject only strong provider/satellite contradictions."""
    sf = _source_family(source)
    pf = _provider_family((service or {}).get("provider_name"))
    name = str((service or {}).get("name") or "")
    try:
        orbital = float((service or {}).get("orbital"))
    except Exception:
        orbital = None
    if sf == "MENA" and pf == "EUROPE":
        return False
    explicit_mena = _explicit_mena(name) or pf == "MENA"
    if sf == "MENA" and orbital is not None and (
            (18.5 <= orbital <= 20.0) or (22.5 <= orbital <= 24.0) or (27.5 <= orbital <= 29.0)) and not explicit_mena:
        return False
    if sf == "MENA" and orbital is not None and 12.0 <= orbital <= 14.0 and _ambiguous_global(name) and not explicit_mena:
        return False
    # Global brands have region-specific schedules.  For a MENA feed, bind an
    # ambiguous brand only to the core MENA orbital positions unless the DVB
    # provider/name itself proves a MENA variant.  This prevents Qatar/Egypt
    # guides from leaking onto unrelated 30W/13E/19.2E services.
    if sf == "MENA" and orbital is not None and _ambiguous_global(name) and not explicit_mena:
        core_mena = ((-9.5 <= orbital <= -6.0) or (24.0 <= orbital <= 26.8))
        if not core_mena:
            return False
    return True


def numeric_signature(value):
    text = str(value or "").lower()
    # Keep real channel variants but ignore resolution years/codecs.
    nums = []
    for token in re.findall(r"\d+", text):
        if token in ("720", "1080", "2160", "265", "264"):
            continue
        nums.append(int(token))
    return tuple(nums)




def language_signature(value):
    text = " %s " % str(value or "").lower().replace("_", " ").replace("-", " ")
    out = set()
    if re.search(r"\b(?:arabic|arabe|arabia)\b", text):
        out.add("AR")
    if re.search(r"\b(?:english|eng)\b", text):
        out.add("EN")
    if re.search(r"\b(?:french|francais|français)\b", text):
        out.add("FR")
    return out

def quality_signature(value):
    text = str(value or "").lower()
    return {"4k": bool(re.search(r"(?:\b4k\b|\buhd\b)", text)),
            "plus1": bool(re.search(r"(?:\+\s*1\b|plus\s*1\b)", text)),
            "plus": bool(re.search(r"(?:\+|\bplus\b)", text))}


def soft_normalize(value):
    n = normalize(value)
    if not n:
        return ""
    words = [w for w in n.split() if w not in _SOFT_DROP]
    return " ".join(words).strip()


def token_keys(value):
    words = [w for w in soft_normalize(value).split() if len(w) >= 3 and not w.isdigit()]
    # longest tokens discriminate best (kahera, dhabi, wataniya, etc.)
    return sorted(set(words), key=lambda x: (-len(x), x))


def compatible_variant(a, b):
    ia, ib = resolve_identity(a), resolve_identity(b)
    same_identity = bool(ia and ib and str(ia.get("id") or "") == str(ib.get("id") or ""))
    la, lb = language_signature(a), language_signature(b)
    if not same_identity and (la or lb) and la != lb:
        # Explicit Arabic/English/French variants are distinct unless a validated
        # canonical alias explicitly declares them the same service identity.
        return False
    na, nb = numeric_signature(a), numeric_signature(b)
    if na and nb and na != nb:
        return False
    if bool(na) != bool(nb):
        # Do not map numbered channels to an unnumbered sibling automatically.
        return False
    qa, qb = quality_signature(a), quality_signature(b)
    if qa["4k"] != qb["4k"]:
        return False
    if qa["plus1"] != qb["plus1"]:
        return False
    if qa.get("plus") != qb.get("plus"):
        return False
    if ia and ib and str(ia.get("id") or "") != str(ib.get("id") or ""):
        return False
    return True


def score(a, b):
    """Deterministic 0..100 similarity; callers must still enforce uniqueness."""
    if not compatible_variant(a, b):
        return 0
    sa, sb = soft_normalize(a), soft_normalize(b)
    if not sa or not sb:
        return 0
    if sa == sb or sa.replace(" ", "") == sb.replace(" ", ""):
        return 100
    wa = set(x for x in sa.split() if len(x) >= 3)
    wb = set(x for x in sb.split() if len(x) >= 3)
    distinctive = set(x for x in wa if x not in _GENERIC_SOFT_TOKENS)
    overlap = distinctive & wb
    if not distinctive:
        # Never soft-map a label made only of generic/short words such as
        # ``A Plus`` or ``M Plus``.  Such labels produced false positives on
        # unrelated European services.  Exact/canonical aliases may still map.
        return 0
    needed = 1 if len(distinctive) == 1 else 2
    if len(overlap) < needed:
        # A two-word identity such as ``News One`` must not collapse to an
        # unrelated service named only ``One``.  Require two distinctive tokens
        # when the candidate actually provides them.
        return 0
    # Frozen beta1 token scorer is deterministic and battle-tested.  Use it as
    # a final conservative fallback, never as an AI/learning component.
    try:
        return int(channel_mapper._token_score(sa, sb))
    except Exception:
        return 0


def unique_soft_matches(candidate_names, source, token_index):
    """Return ([services], score) for one unambiguous identity group >=94.

    The same TV channel can legitimately have several DVB Service References on
    7W/8W/25.5E/26E.  beta38 treated equal top scores as ambiguity and therefore
    lost multi-satellite links.  beta39 groups candidates by canonical channel
    identity (or normalized service name when no alias is known) and accepts all
    SRPs in the single winning identity group.
    """
    ranked = {}
    for cname in candidate_names or []:
        keys = token_keys(cname)
        if not keys:
            continue
        pools = [token_index.get(k) or [] for k in keys[:3]]
        pools = [pool for pool in pools if pool]
        if not pools:
            continue
        pool = min(pools, key=len)
        for svc in pool:
            if not provider_satellite_allowed(svc, source):
                continue
            svc_name = str((svc or {}).get("name") or "")
            sc = score(cname, svc_name)
            if sc < 94:
                continue
            ref = str((svc or {}).get("ref") or "")
            if not ref:
                continue
            group = canonical_key(svc_name)
            if not group or not group.startswith("id:"):
                group = "name:" + soft_normalize(svc_name).replace(" ", "")
            row = ranked.setdefault(group, {"score": 0, "services": {}})
            row["score"] = max(int(row.get("score") or 0), int(sc))
            prev = row["services"].get(ref)
            if prev is None or sc > prev[0]:
                row["services"][ref] = (sc, svc)

    groups = sorted(ranked.items(), key=lambda pair: (-int(pair[1].get("score") or 0), pair[0]))
    if not groups:
        return [], 0
    _best_key, best_row = groups[0]
    best_score = int(best_row.get("score") or 0)
    second_score = int(groups[1][1].get("score") or 0) if len(groups) > 1 else 0
    if best_score < 94 or (second_score and best_score - second_score < 4):
        return [], 0
    services = [pair[1] for pair in sorted(best_row.get("services", {}).values(), key=lambda x: str((x[1] or {}).get("ref") or ""))]
    return services, best_score


def unique_soft_match(candidate_names, source, token_index):
    """Backward-compatible single-result wrapper."""
    services, score_value = unique_soft_matches(candidate_names, source, token_index)
    return (services[0] if services else None), score_value
