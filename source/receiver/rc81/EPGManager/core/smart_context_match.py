# -*- coding: utf-8 -*-
"""Context-aware Smart Mapping resolver (manual UI only).

This layer never writes Service References and never participates in the frozen
beta1/beta12 SAT mapper.  It only ranks candidates already present in Smart
Mapping by combining channel identity + receiver context + source language.
"""
from __future__ import print_function
import re
import unicodedata

from . import source_variant_policy, programme_feed_policy, channel_identity_resolver

_MENA = set("MA DZ TN LY EG SA QA AE PS LB JO IQ KW BH OM YE SY MENA".split())
_EUROPE = set("FR ES IT DE PT UK GB IE BE NL CH AT GR TR RO BG HR RS SI SK CZ DK SE NO FI PL UA HU LU MT CY".split())

_TAG_COUNTRY = {
    "FR":"FR", "IT":"IT", "ES":"ES", "DE":"DE", "PT":"PT", "UK":"UK", "GB":"UK",
    "IE":"IE", "BE":"BE", "NL":"NL", "PL":"PL", "TR":"TR", "US":"US", "CA":"CA",
}
_TAG_LANGUAGE = {
    "AR":"ar", "ARA":"ar", "MENA":"ar", "FR":"fr", "FRA":"fr", "EN":"en", "ENG":"en",
    "IT":"it", "ITA":"it", "ES":"es", "ESP":"es", "DE":"de", "GER":"de", "PT":"pt",
    "TR":"tr", "PL":"pl", "NL":"nl",
}

# Explicit operator-validated identity bridges.  These are intentionally small
# and conservative.  They solve presentation names/IDs, not DVB/SRP mapping.
_RULES = (
    # Al Jazeera is a channel family, not a generic exact-name hit. On MENA
    # reception the Arabic service must stay on the specialist/MENA catalogue
    # and must never drift to France simply because a French guide also carries
    # an entry called "Al Jazeera". English/Documentary/Mubasher are separate
    # identities and are intentionally locked to their own catalogue labels.
    {
        "service": ("al jazeera", "al jazeera hd", "aljazeera", "aljazeera hd",
                    "al jazeera arabic", "al jazeera arabic hd"),
        "candidate": ("aljazeera arabic net", "al jazeera arabic net",
                      "al jazeera arabic", "aljazeera arabic", "al jazeera", "aljazeera"),
        "preferred_sources": ("ext_epgshare_al_jazeera",),
        "fallback_sources": ("ext_openepg_qatar_4", "ext_openepg_qatar_1"),
        "region": "MENA",
        "prefer_language": "ar",
    },
    {
        "service": ("al jazeera english", "al jazeera english hd",
                    "aljazeera english", "aljazeera english hd"),
        "candidate": ("aljazeera english net", "al jazeera english net",
                      "al jazeera english", "aljazeera english"),
        "preferred_sources": ("ext_epgshare_al_jazeera",),
        "fallback_sources": ("ext_openepg_qatar_5",),
        "region": "MENA",
        "prefer_language": "en",
    },
    {
        "service": ("al jazeera documentary", "al jazeera documentary hd",
                    "aljazeera documentary", "aljazeera documentary hd"),
        "candidate": ("aljazeera documentary net", "al jazeera documentary net",
                      "al jazeera documentary", "aljazeera documentary"),
        "preferred_sources": ("ext_epgshare_al_jazeera",),
        "fallback_sources": ("ext_openepg_qatar_4",),
        "region": "MENA",
        "prefer_language": "ar",
    },
    {
        "service": ("al jazeera mubasher", "al jazeera mubasher hd",
                    "aljazeera mubasher", "aljazeera mubasher hd",
                    "al jazeera mobasher", "aljazeera mobasher"),
        "candidate": ("aljazeera mubasher net", "al jazeera mubasher net",
                      "al jazeera mubasher", "aljazeera mubasher",
                      "al jazeera mobasher", "aljazeera mobasher"),
        "preferred_sources": ("ext_epgshare_al_jazeera",),
        "fallback_sources": ("ext_openepg_qatar_4",),
        "region": "MENA",
        "prefer_language": "ar",
    },
    {
        "service": ("bein movies", "bein movie", "bein movies 1", "bein movie 1", "bein movies 1 hd", "bein movies 1 uhd"),
        "candidate": ("movie1 premier digital", "movie 1 premier digital", "bein movies 1", "bein movies premiere", "bein movie 1"),
        "preferred_sources": ("ext_openepg_qatar_4",),
        "fallback_sources": ("ext_openepg_qatar_5",),
        "region": "MENA",
    },
    {
        "service": ("bbc earth",),
        "candidate": ("bbc earth", "bbcearth", "bbc earth new", "bbcearth new", "bbceahrt new"),
        "preferred_sources_mena": ("ext_openepg_qatar_4", "ext_openepg_qatar_5"),
    },
    {
        "service": ("sky sports 1 ultra", "sky sports 1 uhd", "sky sports 1 4k", "sky sports 1"),
        "candidate": ("sky sports 1 ultra", "sky sports 1 uhd", "sky sports 1 4k", "sky sports 1", "sky uhd 1", "skyuhd1"),
        "preferred_source_contains": ("uk_ireland_sky", "uk_ireland_sport_movies", "epgshare_uk", "united_kingdom"),
        "countries": ("UK", "GB", "IE"),
        "region": "EUROPE",
    },
    {
        "service": ("sky news arabia", "skynews arabia", "سكاي نيوز عربية"),
        "candidate": ("sky news arabia", "skynews arabia", "سكاي نيوز عربية"),
        "preferred_sources": ("ext_openepg_saudi_arabia_5",),
        "fallback_sources": ("ext_openepg_saudi_arabia_6",),
        "region": "MENA",
        "prefer_language": "ar",
    },
)

_QUALITY = set("hd sd fhd uhd 4k ultra hdr hevc h265 h264 1080p 2160p 720p".split())
_PRESENTATION = set("tv television channel network digital feed backup".split())
# Tokens that are too broad to justify a fuzzy 94% containment match by
# themselves.  This specifically blocks beIN Movies -> beIN 4K.
_BROAD_BRAND = set("bein be bbc sky osn mbc rai rts canal plus".split())
_SEMANTIC = set("movie movies cinema film sports sport news earth kids drama documentary family action premiere premier arabia racing comedy".split())


def _ascii_fold(text):
    try:
        text = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        return text


def norm(value):
    text = _ascii_fold(str(value or "").strip()).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _compact(value):
    return re.sub(r"\s+", "", norm(value))


def _strip_quality_words(value):
    return " ".join(w for w in norm(value).split() if w not in _QUALITY and w not in _PRESENTATION)


def _extract_prefix_tag(raw):
    text = str(raw or "").strip()
    patterns = (
        r"^\s*\|\s*([A-Za-z]{2,5})\s*\|\s*",
        r"^\s*\[\s*([A-Za-z]{2,5})\s*\]\s*",
        r"^\s*\(\s*([A-Za-z]{2,5})\s*\)\s*",
        r"^\s*([A-Za-z]{2,5})\s*[:|]\s*",
    )
    for pat in patterns:
        m = re.match(pat, text)
        if m:
            code = m.group(1).upper()
            if code in _TAG_LANGUAGE or code in _TAG_COUNTRY:
                return code, text[m.end():].strip()
    # Some IPTV lists prepend a group name before the language marker, e.g.
    # ``VIP |FR| TF1``. Only recognise known short tags and remove that marker.
    m = re.search(r"\|\s*([A-Za-z]{2,5})\s*\|", text)
    if m:
        code = m.group(1).upper()
        if code in _TAG_LANGUAGE or code in _TAG_COUNTRY:
            clean = (text[:m.start()] + " " + text[m.end():]).strip()
            clean = re.sub(r"\s{2,}", " ", clean)
            return code, clean
    return "", text


def receiver_profile(service):
    service = service or {}
    raw_name = str(service.get("name") or "")
    tag, clean = _extract_prefix_tag(raw_name)
    # channel_registry already strips supported prefixes into clean_name; use it
    # when available while retaining the original tag for language context.
    clean = str(service.get("clean_name") or clean or raw_name).strip()
    explicit = str(service.get("explicit_region") or "").upper().strip()
    country_hint = _TAG_COUNTRY.get(tag, "")
    language_hint = _TAG_LANGUAGE.get(tag, "")
    if not country_hint and explicit and explicit not in ("MENA",):
        country_hint = explicit
    if not language_hint and explicit == "MENA":
        language_hint = "ar"
    # beta119: one source of truth.  A language explicitly present in the SAT
    # service name (Al Jazeera English, i24 French, etc.) outranks provider or
    # orbital defaults.  Provider-country inference is shared with the hard
    # programme-feed routing firewall.
    if not language_hint:
        language_hint = str(programme_feed_policy.service_language(service) or "").lower()
    if not country_hint:
        provider_codes = set(programme_feed_policy.provider_countries(service.get("provider_name") or ""))
        if len(provider_codes) == 1:
            country_hint = next(iter(provider_codes))
    try:
        orbital = float(service.get("orbital"))
    except Exception:
        orbital = None
    sat_zone = str(service.get("satellite_zone") or "").upper()
    if not sat_zone and orbital is not None:
        sat_zone = programme_feed_policy.satellite_zone(orbital)
    return {
        "raw_name": raw_name,
        "clean_name": clean,
        "tag": tag,
        "country_hint": country_hint,
        "language_hint": language_hint,
        "sat_zone": sat_zone,
        "orbital": orbital,
        "provider": str(service.get("provider_name") or ""),
        "service_type": str(service.get("service_type") or "").upper(),
    }


def source_language(source):
    return str((source or {}).get("language") or "").lower().split("-", 1)[0].split("_", 1)[0]


def source_countries(source):
    return set(str(x or "").upper() for x in ((source or {}).get("countries") or []) if x)


def source_region(source):
    countries = source_countries(source)
    region = str((source or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return region or ""


def source_label(source):
    source = source or {}
    name = str(source.get("source_name") or source.get("name") or source.get("source_id") or source.get("id") or "EPG")
    lang = source_language(source)
    if lang in ("ar", "en", "fr", "it", "es", "de", "pt"):
        return "%s [%s]" % (name, lang.upper())
    return name


def _rule_for_service(name):
    n = norm(name)
    c = _compact(name)
    for rule in _RULES:
        for alias in rule.get("service") or ():
            a = norm(alias)
            if n == a or c == _compact(alias):
                return rule
    return None


def _rule_candidate_match(rule, epg):
    if not rule:
        return False
    name = str((epg or {}).get("display_name") or "")
    cid = str((epg or {}).get("channel_id") or "")
    values = (norm(name), norm(cid), _compact(name), _compact(cid))
    for alias in rule.get("candidate") or ():
        a = norm(alias); ac = _compact(alias)
        if a in values or ac in values:
            return True
    return False


def _rule_source_preference(rule, source, profile):
    if not rule:
        return 0
    sid = str((source or {}).get("source_id") or (source or {}).get("id") or "").lower()
    if sid in tuple(str(x).lower() for x in (rule.get("preferred_sources") or ())):
        return 320
    if sid in tuple(str(x).lower() for x in (rule.get("fallback_sources") or ())):
        return 130
    if profile.get("sat_zone") == "MENA":
        pref = tuple(str(x).lower() for x in (rule.get("preferred_sources_mena") or ()))
        if sid in pref:
            # earlier item has higher priority
            return 250 - (pref.index(sid) * 25)
    for token in rule.get("preferred_source_contains") or ():
        if str(token).lower() in sid:
            return 220
    return 0


def _source_context_allowed(profile, source, rule=None):
    countries = source_countries(source)
    sregion = source_region(source)
    shint = str(profile.get("country_hint") or "").upper()
    lhint = str(profile.get("language_hint") or "").lower()
    service_name = norm(profile.get("clean_name"))

    if shint:
        aliases = {shint}
        if shint == "UK": aliases.add("GB")
        if shint == "GB": aliases.add("UK")
        # IPTV country tags are strong. A source with explicit country metadata
        # that disagrees is not a safe automatic suggestion.
        if countries and not (countries & aliases):
            return False, "IPTV tag conflicts with source country"
    if lhint == "ar" and sregion and sregion not in ("MENA", "INTERNATIONAL", "INT"):
        return False, "Arabic IPTV tag requires an Arabic/MENA source"

    # MENA satellite services carrying global brands must not drift to Spain or
    # another European guide simply because the display name is exact.
    if profile.get("sat_zone") == "MENA" and sregion == "EUROPE":
        provider = norm(profile.get("provider"))
        strong_europe = bool(str(profile.get("country_hint") or "").upper() in _EUROPE) or any(
            x in provider for x in ("sky uk", "freesat", "movistar", "polsat", "tivusat", "sky italia", "sky deutschland", "rai", "canalsat", "canal france"))
        global_or_mena = any(x in service_name for x in ("al jazeera", "aljazeera", "bbc", "bein", "sky news arabia", "discovery", "national geographic", "cartoon network", "nickelodeon", "travelxp", "animal planet", "mbc", "star action"))
        if global_or_mena and not strong_europe:
            return False, "MENA satellite context rejects European regional EPG"
        # For a plain DVB service on a MENA orbital position, a European guide
        # must not become an automatic exact-name answer merely because no MENA
        # catalogue has finished preparing yet. Explicit IPTV country tags and
        # strong European DVB provider signatures still allow Europe.
        if not profile.get("country_hint") and not strong_europe:
            return False, "MENA satellite requires MENA/Arabic source unless receiver context explicitly says Europe"

    if rule:
        wanted_region = str(rule.get("region") or "").upper()
        if wanted_region and sregion and wanted_region != sregion:
            return False, "Validated channel identity conflicts with source region"
        wanted = set(str(x).upper() for x in (rule.get("countries") or ()))
        if wanted and countries and wanted.isdisjoint(countries):
            return False, "Validated channel identity conflicts with source country"
    return True, ""


def _aljazeera_variant(value):
    """Return a strict Al Jazeera family variant or an empty string.

    The normal Arabic channel, English, Documentary and Mubasher are different
    services even though fuzzy text similarity between them is very high.
    """
    n = norm(value)
    c = _compact(value)
    if "aljazeera" not in c and "al jazeera" not in n:
        return ""
    if "english" in n or c.endswith("englishnet"):
        return "en"
    if "documentary" in n or "doc" in n:
        return "doc"
    if "mubasher" in n or "mobasher" in n:
        return "mubasher"
    return "ar"


def _semantic_guard(service_name, epg_name, channel_id, alias_ok=False):
    svc_aj = _aljazeera_variant(service_name)
    epg_aj = _aljazeera_variant(epg_name or channel_id)
    if svc_aj and epg_aj and svc_aj != epg_aj:
        return False, "Al Jazeera family variant mismatch (%s/%s)" % (svc_aj, epg_aj)
    if alias_ok:
        return True, ""
    a = _strip_quality_words(service_name)
    b = _strip_quality_words(epg_name or channel_id)
    ta = set(a.split()); tb = set(b.split())
    if not ta or not tb:
        return False, "empty identity"
    # If a receiver explicitly states a semantic channel family, the candidate
    # must retain it.  This blocks beIN Movies -> beIN 4K and Sky Sports -> RTS1.
    required = ta & _SEMANTIC
    if required:
        # normalise simple singular/plural pairs
        pairs = (("movies", "movie"), ("sports", "sport"), ("premiere", "premier"))
        expanded = set(tb)
        for x,y in pairs:
            if x in tb: expanded.add(y)
            if y in tb: expanded.add(x)
        missing = set(required) - expanded
        # 'arabia' may be absent only for a validated alias; otherwise it is
        # identity-critical for Sky News Arabia.
        if missing:
            return False, "candidate drops semantic token(s): %s" % ", ".join(sorted(missing))
    # Never allow a fuzzy match supported only by a broad brand token.
    shared = ta & tb
    informative = set(x for x in shared if x not in _BROAD_BRAND and x not in _QUALITY and x not in _PRESENTATION)
    if not informative and ta != tb:
        return False, "only broad brand tokens overlap"
    return True, ""


def language_rank(profile, source, rule=None):
    lang = source_language(source)
    countries = source_countries(source)
    hint = str(profile.get("language_hint") or "").lower()
    country_hint = str(profile.get("country_hint") or "").upper()
    if hint:
        if lang == hint:
            return 0
        # country-specific guides often omit language metadata
        if country_hint and (country_hint in countries or (country_hint == "UK" and "GB" in countries)) and not lang:
            return 1
        if not lang:
            return 2
        return 6
    if rule and rule.get("prefer_language"):
        wanted = str(rule.get("prefer_language")).lower()
        if lang == wanted: return 0
        if not lang: return 2
        return 5
    # Default MENA preference requested by the operator: AR -> bilingual/blank
    # -> FR -> EN -> other. Outside MENA, source geography/identity ranks first.
    if profile.get("sat_zone") == "MENA" or (rule and str(rule.get("region") or "").upper() == "MENA"):
        if lang == "ar": return 0
        if not lang: return 1
        if lang == "fr": return 2
        if lang == "en": return 3
        return 4
    return 0 if not lang else 1



def variant_rank(profile, source):
    """Language-shard rank for parallel provider feeds (AR beats EN on MENA)."""
    sid = str((source or {}).get("source_id") or (source or {}).get("id") or "")
    prefer_ar = bool(profile.get("sat_zone") == "MENA" or profile.get("language_hint") == "ar")
    return source_variant_policy.preferred_variant_rank(sid, prefer_arabic=prefer_ar)


def evaluate(service, source, epg, name_score):
    """Return a conservative context decision for one UI suggestion candidate.

    ``name_score`` is supplied by the existing Unicode name scorer.  The result
    keeps the visible confidence grounded in channel identity; source/context
    only gates or tie-breaks and never inflates a weak fuzzy name to 94%.
    """
    profile = receiver_profile(service)
    service_name = profile.get("clean_name") or str((service or {}).get("name") or "")
    epg_name = str((epg or {}).get("display_name") or (epg or {}).get("channel_id") or "")
    rule = _rule_for_service(service_name)
    alias_ok = _rule_candidate_match(rule, epg)

    allowed, reason = _source_context_allowed(profile, source, rule=rule if alias_ok else None)
    if not allowed:
        return {"allowed": False, "verified": False, "score": 0, "context": -999, "language_rank": 99, "reason": reason}
    route_ok, route_score, route_reason = programme_feed_policy.route(service, source, epg)
    if not route_ok:
        return {"allowed": False, "verified": False, "score": 0, "context": -999, "language_rank": 99, "reason": route_reason}

    # beta121: deterministic curated identity is stronger than lexical overlap.
    # Resolve it *before* the generic semantic-name guard so a valid cross-script
    # alias such as ``2M MAROC HD`` <-> ``الثانية`` is not rejected simply
    # because Latin/Arabic tokens do not overlap.  A deterministic mismatch is
    # still a hard failure before any fuzzy scorer can participate.
    try:
        svc_identity = channel_identity_resolver.resolve(service_name) or {}
        epg_identity = (channel_identity_resolver.resolve(epg_name) or
                        channel_identity_resolver.resolve((epg or {}).get("channel_id") or "") or {})
    except Exception:
        svc_identity, epg_identity = {}, {}
    svc_iid = str(svc_identity.get("id") or "")
    epg_iid = str(epg_identity.get("id") or "")
    same_identity = bool(svc_iid and epg_iid and svc_iid == epg_iid)
    if svc_iid and epg_iid and svc_iid != epg_iid and not alias_ok:
        return {"allowed": False, "verified": False, "score": 0, "context": -700, "language_rank": 99,
                "reason": "deterministic channel identity mismatch (%s != %s)" % (svc_iid, epg_iid)}

    if not (same_identity or alias_ok):
        semantic_ok, semantic_reason = _semantic_guard(
            service_name, epg_name, (epg or {}).get("channel_id"), alias_ok=False)
        if not semantic_ok:
            return {"allowed": False, "verified": False, "score": 0, "context": -500,
                    "language_rank": 99, "reason": semantic_reason}

    score = int(name_score or 0)
    verified = False
    context = int(route_score or 0)
    if alias_ok:
        score = 100
        verified = True
        context += 500 + _rule_source_preference(rule, source, profile)
    elif same_identity:
        score = max(score, 99)
        verified = True
        context += 420
    else:
        clean_a = _strip_quality_words(service_name)
        clean_b = _strip_quality_words(epg_name)
        if clean_a and clean_a == clean_b:
            score = max(score, 99)
            verified = True
            context += 260
        elif score >= 96:
            # High-confidence text still needs useful semantic overlap.
            verified = True
            context += 120
        elif score >= 88:
            context += 30

    # Strong receiver country/region context is a rank, never confidence inflation.
    countries = source_countries(source)
    country_hint = str(profile.get("country_hint") or "").upper()
    if country_hint and (country_hint in countries or (country_hint == "UK" and "GB" in countries)):
        context += 180
    if profile.get("sat_zone") and source_region(source) == profile.get("sat_zone"):
        context += 90
    context += _rule_source_preference(rule, source, profile) if rule else 0

    return {
        "allowed": bool(score >= 78),
        "verified": bool(verified),
        "score": max(0, min(100, score)),
        "context": int(context),
        "language_rank": int(language_rank(profile, source, rule=rule)),
        "variant_rank": int(variant_rank(profile, source)),
        "reason": "Validated identity" if alias_ok else ("Exact channel identity" if verified else "Review candidate"),
        "profile": profile,
        "alias": bool(alias_ok),
    }

def candidate_aliases_for(service):
    profile = receiver_profile(service or {})
    rule = _rule_for_service(profile.get("clean_name") or (service or {}).get("name") or "")
    return list(rule.get("candidate") or ()) if rule else []


def preferred_source_ids_for(service):
    profile = receiver_profile(service or {})
    rule = _rule_for_service(profile.get("clean_name") or (service or {}).get("name") or "")
    if not rule:
        return []
    out = []
    for key in ("preferred_sources", "fallback_sources"):
        for sid in rule.get(key) or ():
            if sid not in out:
                out.append(sid)
    if profile.get("sat_zone") == "MENA":
        for sid in rule.get("preferred_sources_mena") or ():
            if sid not in out:
                out.append(sid)
    return out
