# -*- coding: utf-8 -*-
"""Local SmartMatch scorer for manual/IPTV mapping only.

This module deliberately does NOT participate in the beta12/beta1 automatic
SAT mapping path.  It ranks Smart Mapping suggestions using channel identity,
source geography, bouquet hints and locally learned manual corrections.
"""
from __future__ import print_function
import json
import os
import re
import time
import copy

from . import channel_mapper, source_quality, programme_feed_policy

PLUGIN_ROOT = os.path.dirname(os.path.dirname(__file__))
IDENTITY_PATH = os.path.join(PLUGIN_ROOT, "data", "channel_identity.json")
LEARNING_PATH = "/etc/enigma2/epgmanager_smartmatch_learning.json"

# beta77: these two JSON files used to be reopened and reparsed inside the
# candidate scorer.  One receiver cursor move can score dozens of candidates,
# so that behaviour turned a tiny name comparison into repeated flash I/O and
# JSON CPU work.  Keep signature-aware process caches; manual learning still
# invalidates immediately after an atomic write.
_IDENTITY_CACHE = None
_IDENTITY_SIG = None
_LEARNING_CACHE = None
_LEARNING_SIG = None

def _file_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None

_COUNTRY_HINTS = {
    "MA": ("morocco", "maroc", "maghreb", "snrt", "2m"),
    "EG": ("egypt", "egypte", "masr"),
    "QA": ("qatar", "alkass", "al kass"),
    "SA": ("saudi", "ksa", "saudia"),
    "AE": ("uae", "emirates", "dubai", "abu dhabi"),
    "IL": ("israel", "israeli", "i24"),
    "IR": ("iran", "iranian", "al alam", "alalam"),
    "FR": ("france", "french", "fr "),
    "GB": ("uk", "british", "united kingdom"),
}
_MENA = set(("MA","DZ","TN","LY","EG","SA","AE","QA","KW","BH","OM","JO","LB","PS","IQ","SY","YE"))

_EUROPE_PROVIDER_TOKENS = (
    "sky italia", "sky deutschland", "sky uk", "cyfrowy", "polsat", "platforma canal",
    "canal+", "canalsat", "nc+", "tivusat", "rai", "mediaset", "m7", "hd+",
    "movistar", "orange france", "orange polska", "tvn", "telenor", "viasat",
    "a1 broadcasting", "nova", "total tv", "upc", "freeview", "freesat"
)
_MENA_PROVIDER_TOKENS = (
    "snrt", "bein", "be in", "osn", "mbc", "rotana", "abu dhabi", "dubai",
    "al jazeera", "alkass", "ksa", "saudi", "qatar", "nilesat", "arabsat",
    "shahid", "ssc", "etisalat", "ooredoo"
)
_PROVIDER_COUNTRY_HINTS = (
    (("cyfrowy polsat", "polsat", "platforma canal+", "nc+", "tvn"), {"PL"}),
    (("sky italia", "tivusat", "tivùsat", "mediaset", "rai"), {"IT"}),
    (("sky deutschland", "hd+", "austriasat"), {"DE", "AT"}),
    (("movistar", "digital+ esp", "canal+ esp"), {"ES"}),
    (("canal digitaal", "canaldigitaal"), {"NL"}),
    (("telesat belg", "tv vlaanderen"), {"BE"}),
    (("freesat", "freeview", "sky uk", "bbc sat"), {"UK", "IE"}),
)
_AMBIGUOUS_GLOBAL_BRANDS = (
    "disney", "discovery", "national geographic", "nat geo", "bein sports",
    "cartoon network", "nickelodeon", "cnn", "bbc", "sky news", "animal planet"
)
_EXPLICIT_MENA_NAMES = (
    "al jazeera", "mbc", "rotana", "abu dhabi", "dubai sports", "dubai racing",
    "dw arabia", "trt arabi", "sky news arabia", "cnbc arabia", "2m", "al aoula",
    "tamazight", "al maghribia", "assadissa", "attaqafia", "arryadia", "medi1"
)

def _provider_family(value):
    text = str(value or "").lower()
    if any(token in text for token in _MENA_PROVIDER_TOKENS):
        return "MENA"
    if any(token in text for token in _EUROPE_PROVIDER_TOKENS):
        return "EUROPE"
    return ""

def _provider_countries(value):
    text = str(value or "").lower()
    out = set()
    for tokens, countries in _PROVIDER_COUNTRY_HINTS:
        if any(token in text for token in tokens):
            out.update(countries)
    return out

def _source_family(source):
    countries = _source_countries(source)
    region = _source_region(source)
    if countries and any(c in _MENA for c in countries):
        return "MENA"
    if "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries and any(c in ("FR","BE","NL","DE","AT","CH","IT","ES","PT","UK","IE","GR","TR","RO","BG","HR","RS","SI","SK","CZ","DK","SE","NO","FI","PL") for c in countries):
        return "EUROPE"
    if "EUROPE" in region:
        return "EUROPE"
    return ""

def _explicit_mena_service(name):
    text = str(name or "").lower()
    return any(token in text for token in _EXPLICIT_MENA_NAMES)

def _ambiguous_global_service(name):
    text = str(name or "").lower()
    return any(token in text for token in _AMBIGUOUS_GLOBAL_BRANDS)

def _provider_satellite_gate(service, source):
    """Strong DVB provider/orbital guard for SAT Smart Mapping suggestions."""
    if str((service or {}).get("service_type") or "").upper() not in ("SAT", ""):
        return True, ""
    sf = _source_family(source)
    name = str((service or {}).get("name") or "")
    provider = str((service or {}).get("provider_name") or "")
    pf = _provider_family(provider)
    provider_countries = _provider_countries(provider)
    source_countries = _source_countries(source)
    try:
        orbital = float((service or {}).get("orbital"))
    except Exception:
        orbital = None

    if sf == "MENA" and pf == "EUROPE":
        return False, "DVB provider is European (%s)" % (provider or "provider")
    explicit_mena = _explicit_mena_service(name) or pf == "MENA"
    if sf == "MENA" and orbital is not None and (
            (18.5 <= orbital <= 20.0) or (22.5 <= orbital <= 24.0) or
            (27.5 <= orbital <= 29.0)) and not explicit_mena:
        return False, "European satellite %.1fE/W with no MENA DVB-provider signal" % abs(orbital)
    if sf == "MENA" and orbital is not None and 12.0 <= orbital <= 14.0 and _ambiguous_global_service(name) and not explicit_mena:
        return False, "Hotbird global brand requires DVB-provider/language agreement"

    # If DVB provider strongly identifies a European country, reject another
    # country's EPG for ambiguous global brands on Hotbird/Astra.
    if sf == "EUROPE" and provider_countries and source_countries:
        if not (provider_countries & source_countries):
            if (_ambiguous_global_service(name) or
                    (orbital is not None and (12.0 <= orbital <= 14.0 or 18.5 <= orbital <= 20.0))):
                return False, "DVB provider country conflicts with source country"
    return True, ""



def _prefer_arabic_content(service):
    try:
        orbital = float((service or {}).get("orbital"))
    except Exception:
        orbital = None
    if _provider_family((service or {}).get("provider_name")) == "MENA" or _explicit_mena_service((service or {}).get("name")):
        return True
    return orbital is not None and ((-9.5 <= orbital <= -6.0) or (24.0 <= orbital <= 26.8))


def _norm(value):
    return channel_mapper.normalize_name(str(value or ""))


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def identities():
    global _IDENTITY_CACHE, _IDENTITY_SIG
    sig = _file_sig(IDENTITY_PATH)
    if _IDENTITY_CACHE is None or sig != _IDENTITY_SIG:
        _IDENTITY_CACHE = _load_json(IDENTITY_PATH, {"identities": []})
        _IDENTITY_SIG = sig
    return ((_IDENTITY_CACHE or {}).get("identities") or [])


def identify(name):
    n = _norm(name)
    if not n:
        return None
    best = None
    best_len = 0
    for row in identities():
        for alias in row.get("aliases") or []:
            a = _norm(alias)
            if not a:
                continue
            # Keep identity matching conservative: exact normalized name or
            # distinctive alias surrounded by ordinary service decorations.
            if n == a or (len(a) >= 6 and (n.startswith(a + " ") or n.endswith(" " + a))):
                if len(a) > best_len:
                    best, best_len = row, len(a)
    return best


def infer_country(service):
    explicit = str((service or {}).get("explicit_region") or "").upper().strip()
    if len(explicit) == 2:
        return explicit
    text = " %s %s " % (_norm((service or {}).get("name")), _norm((service or {}).get("bouquet_label")))
    for code, hints in _COUNTRY_HINTS.items():
        for hint in hints:
            h = _norm(hint)
            if h and (" " + h + " ") in text:
                return code
    ident = identify((service or {}).get("name"))
    countries = (ident or {}).get("countries") or []
    return str(countries[0]).upper() if len(countries) == 1 else ""


def _source_countries(source):
    return set(str(x).upper() for x in ((source or {}).get("countries") or []) if x)


def _source_region(source):
    return str((source or {}).get("region") or "").upper()


def _learning():
    global _LEARNING_CACHE, _LEARNING_SIG
    sig = _file_sig(LEARNING_PATH)
    if _LEARNING_CACHE is None or sig != _LEARNING_SIG:
        _LEARNING_CACHE = _load_json(LEARNING_PATH, {"version": 1, "aliases": {}})
        _LEARNING_SIG = sig
    # Callers such as learn() mutate the returned structure before an atomic
    # save.  Keep the process cache immutable until that save succeeds.
    return copy.deepcopy(_LEARNING_CACHE or {"version": 1, "aliases": {}})


def _learning_keys(service):
    """Context-safe learning keys.

    beta123 keyed learning only by channel name, so a correction for a common
    name could leak to another satellite/provider/feed.  beta124 learns the
    exact receiver ServiceRef first, with a strict contextual fallback.
    """
    service = service or {}
    out = []
    ref = str(service.get("ref") or "").strip().rstrip(":").lower()
    if ref:
        out.append("ref:" + ref)
    name = _norm(service.get("clean_name") or service.get("name") or "")
    provider = _norm(service.get("provider_name") or "")
    try:
        orbital = "%.1f" % float(service.get("orbital"))
    except Exception:
        orbital = ""
    try:
        family, variant = programme_feed_policy._known_family_variant(service.get("clean_name") or service.get("name") or "")
        feed = "%s:%s" % (family, variant or "base") if family else ""
    except Exception:
        feed = ""
    if name:
        out.append("ctx:%s|%s|%s|%s" % (name, provider, orbital, feed))
    return out


def _learning_row(service):
    data = _learning()
    records = data.get("records") or {}
    for key in _learning_keys(service):
        row = records.get(key)
        if isinstance(row, dict):
            return dict(row)
    # Old beta<=123 name-only aliases are deliberately NOT trusted for an
    # automatic suggestion.  The user's MappingStore manual mapping still
    # remains authoritative; this only prevents cross-satellite learning leaks.
    return {}


def learn(service, source, epg):
    """Remember one manual correction against the exact receiver context."""
    keys = _learning_keys(service)
    if not keys or not epg:
        return False
    data = _learning(); records = data.setdefault("records", {})
    row = {
        "source_id": str((epg or {}).get("source_id") or (source or {}).get("source_id") or ""),
        "channel_id": str((epg or {}).get("channel_id") or ""),
        "display_name": str((epg or {}).get("display_name") or ""),
        "countries": list((source or {}).get("countries") or []),
        "service_name": str((service or {}).get("name") or ""),
        "provider_name": str((service or {}).get("provider_name") or ""),
        "service_ref": str((service or {}).get("ref") or ""),
        "ts": int(time.time()),
    }
    for key in keys:
        records[key] = dict(row)
    data["version"] = 2
    parent = os.path.dirname(LEARNING_PATH)
    try:
        if parent and not os.path.isdir(parent): os.makedirs(parent)
        tmp = LEARNING_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, LEARNING_PATH)
        global _LEARNING_CACHE, _LEARNING_SIG
        _LEARNING_CACHE = copy.deepcopy(data)
        _LEARNING_SIG = _file_sig(LEARNING_PATH)
        return True
    except Exception:
        return False


def learned_match(service, source, epg):
    row = _learning_row(service)
    if not row:
        return False
    return (str(row.get("source_id") or "").lower() == str((epg or {}).get("source_id") or (source or {}).get("source_id") or "").lower()
            and str(row.get("channel_id") or "").lower() == str((epg or {}).get("channel_id") or "").lower())


def learned_target(service):
    """Return the exact context-bound learned target, never a name-only leak."""
    return _learning_row(service)

def score(service, source, epg):
    """Return {allowed, score, reasons, reason} for one Smart Mapping candidate."""
    sname = str((service or {}).get("name") or "")
    ename = str((epg or {}).get("display_name") or (epg or {}).get("channel_id") or "")
    name_score = int(channel_mapper._token_score(sname, ename))
    reasons = [("Name", name_score)]

    if learned_match(service, source, epg):
        return {"allowed": True, "score": 100, "reasons": [("Learned manual correction", 100)], "reason": "Learned manual correction"}

    sid = identify(sname); eid = identify(ename)
    scountries = _source_countries(source)
    service_country = infer_country(service)

    provider_ok, provider_reason = _provider_satellite_gate(service, source)
    if not provider_ok:
        return {"allowed": False, "score": 0, "reasons": reasons + [("DVB provider/satellite", -100)],
                "reason": "Rejected: %s" % provider_reason}
    if str((service or {}).get("provider_name") or "").strip():
        reasons.append(("DVB provider", 6))

    # beta25: programme quality is part of Smart Mapping, not just name match.
    # A complete profile proving that this XMLTV ID has zero events, or a
    # repeated channel-name placeholder schedule, is not a safe auto-match.
    try:
        qmetrics = source_quality.get_channel((epg or {}).get("source_id") or (source or {}).get("source_id") or (source or {}).get("id"),
                                              (epg or {}).get("channel_id"))
    except Exception:
        qmetrics = {}
    if qmetrics:
        if qmetrics.get("specific") and qmetrics.get("profile_complete") and int(qmetrics.get("programs") or 0) <= 0:
            return {"allowed": False, "score": 0, "reasons": reasons + [("EPG programmes", -100)],
                    "reason": "Rejected: source has no programme rows for this channel"}
        if qmetrics.get("placeholder"):
            return {"allowed": False, "score": 0, "reasons": reasons + [("EPG quality", -100)],
                    "reason": "Rejected: placeholder/repeated programme feed"}
        qscore = source_quality.quality_score(qmetrics, prefer_arabic=_prefer_arabic_content(service))
        if not qmetrics.get("specific"):
            qscore = int(qscore * 0.25)
        if qscore > 0:
            bonus = min(10, max(1, int(qscore / 55)))
            reasons.append(("EPG quality", bonus)); name_score = min(100, name_score + bonus)

    # Known, distinctive channel identities are stronger than fuzzy text.
    if sid and eid and sid.get("id") != eid.get("id"):
        return {"allowed": False, "score": 0, "reasons": reasons + [("Identity conflict", -100)], "reason": "Rejected: channel identity conflict"}

    target_ident = sid or eid
    if target_ident:
        reasons.append(("Known identity", 20))
        name_score = min(100, name_score + 12)
        known = set(str(x).upper() for x in (target_ident.get("countries") or []) if x)
        if known and scountries and known.isdisjoint(scountries):
            return {"allowed": False, "score": 0, "reasons": reasons + [("Country conflict", -100)], "reason": "Rejected: source country conflicts with known channel identity"}
        if known and scountries and not known.isdisjoint(scountries):
            reasons.append(("Country", 16)); name_score = min(100, name_score + 8)
        region = str(target_ident.get("region") or "").upper()
        if region == "MENA" and scountries:
            if any(c in _MENA for c in scountries):
                reasons.append(("MENA region", 10)); name_score = min(100, name_score + 5)
            elif _source_region(source) not in ("MENA", "ARAB", "ARABIC"):
                return {"allowed": False, "score": 0, "reasons": reasons + [("Region conflict", -100)], "reason": "Rejected: MENA channel on non-MENA source"}

    if service_country and scountries:
        if service_country in scountries:
            reasons.append(("Bouquet/country", 12)); name_score = min(100, name_score + 6)
        elif service_country in ("IL", "IR") or (service_country in _MENA and not any(c in _MENA for c in scountries)):
            return {"allowed": False, "score": 0, "reasons": reasons + [("Bouquet/country conflict", -100)], "reason": "Rejected: geographic conflict"}

    # Strong provider conflicts for known networks.
    wanted_provider = str((target_ident or {}).get("provider") or "").upper()
    source_provider = str((source or {}).get("provider") or "").upper()
    if wanted_provider and source_provider:
        if wanted_provider in source_provider or source_provider in wanted_provider:
            reasons.append(("Provider", 8)); name_score = min(100, name_score + 4)
        elif source_provider == "EPG MANAGER" and wanted_provider in ("SNRT","2M"):
            reasons.append(("Provider bundle", 5)); name_score = min(100, name_score + 2)

    allowed = name_score >= 82
    reason = "SmartMatch %d%%" % name_score if allowed else "Name/identity confidence too low"
    return {"allowed": allowed, "score": name_score, "reasons": reasons, "reason": reason}


def explain(result):
    result = result or {}
    lines = [str(result.get("reason") or "SmartMatch")]
    for label, value in result.get("reasons") or []:
        sign = "+" if isinstance(value, (int, float)) and value > 0 else ""
        lines.append("%s: %s%s" % (label, sign, value))
    lines.append("Final confidence: %d%%" % int(result.get("score") or 0))
    return "\n".join(lines)

def source_affinity(service, source):
    """Cheap source pre-ranking without parsing XMLTV programme data."""
    provider_ok, _provider_reason = _provider_satellite_gate(service, source)
    if not provider_ok:
        return -1000
    ident = identify((service or {}).get("name"))
    scountries = _source_countries(source)
    service_country = infer_country(service)
    score_value = 0
    reasons = []
    if service_country and scountries:
        if service_country in scountries:
            score_value += 70; reasons.append("country")
        elif service_country in ("IL", "IR"):
            return -100
    if ident:
        known = set(str(x).upper() for x in (ident.get("countries") or []) if x)
        if known and scountries:
            if not known.isdisjoint(scountries): score_value += 80
            else: return -100
        wanted = str(ident.get("provider") or "").upper()
        provider = str((source or {}).get("provider") or "").upper()
        sname = str((source or {}).get("source_name") or (source or {}).get("name") or "").upper()
        if wanted and (wanted in provider or wanted in sname): score_value += 55
        if str(ident.get("region") or "").upper() == "MENA" and (any(c in _MENA for c in scountries) or _source_region(source) == "MENA"):
            score_value += 35
    # Prefer the requested Arabic OpenEPG families when DVB/name context agrees.
    source_id = str((source or {}).get("source_id") or (source or {}).get("id") or "").lower()
    service_text = ("%s %s" % (str((service or {}).get("name") or ""), str((service or {}).get("provider_name") or ""))).lower()
    if source_id in ("ext_openepg_saudi_arabia_1", "ext_openepg_saudi_arabia_3", "ext_openepg_saudi_arabia_5"):
        score_value += 5
        if any(x in service_text for x in ("saudi", "ksa", "ssc", "quran", "sunnah", "alekhb", "thikrayat", "sbc")):
            score_value += 90
    elif source_id == "ext_openepg_egypt_1":
        score_value += 5
        if any(x in service_text for x in ("egypt", "masr", "maspero", "cbc", "dmc", "on time", "on sport", "al nahar", "el nahar", "al hayat", "cairo", "sada el balad", "mehwar")):
            score_value += 90
    # Cached source quality is a tie-breaker before XMLTV IDs are inspected.
    try:
        q = source_quality.load((source or {}).get("source_id") or (source or {}).get("id"))
        overall = q.get("overall") or {}
        if overall:
            qv = source_quality.quality_score(overall, prefer_arabic=_prefer_arabic_content(service))
            score_value += max(-80, min(80, int(qv * 0.16)))
    except Exception:
        pass
    # Retain a light lexical source hint only as tie-breaker, never as identity.
    score_value += int(channel_mapper._token_score((service or {}).get("name", ""), (source or {}).get("source_name") or (source or {}).get("name") or "") * 0.15)
    return score_value
