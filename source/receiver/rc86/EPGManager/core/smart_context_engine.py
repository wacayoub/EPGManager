# -*- coding: utf-8 -*-
"""Provider-first receiver context and source ranking for Smart Mapping beta85.

This module is deliberately cheap: no network, no XML parsing and no lamedb
scan.  It consumes fields already present in the receiver snapshot and source
catalogue.  Manual mappings remain authoritative elsewhere.
"""
from __future__ import print_function
import re

from . import smart_context_match, programme_feed_policy, mapping_strategy

_MENA = set("MA DZ TN LY EG SA QA AE PS LB JO IQ KW BH OM YE SY MENA".split())
_EUROPE = set("FR ES IT DE PT UK GB IE BE NL CH AT GR TR RO BG HR RS SI SK CZ DK SE NO FI PL UA HU LU MT CY".split())

# Provider rules are intentionally stronger than orbital position.  Mixed
# satellites such as Astra 19.2E, Hot Bird 13E and Hispasat 30W must never be
# reduced to one country.
_PROVIDER_RULES = (
    (("snrt",), "MA", "MENA", "ar", "SNRT"),
    (("2m",), "MA", "MENA", "ar", "2M"),
    (("bein", "be in"), "", "MENA", "ar", "beIN"),
    (("osn",), "", "MENA", "ar", "OSN"),
    (("mbc",), "SA", "MENA", "ar", "MBC"),
    (("rotana",), "SA", "MENA", "ar", "Rotana"),
    (("al jazeera", "aljazeera", "alkass", "al kass"), "QA", "MENA", "ar", "Qatar"),
    (("abu dhabi", "ad sports", "dubai", "sharjah"), "AE", "MENA", "ar", "UAE"),
    (("canalsat", "canal satellite", "tntsat", "bis tv", "canal+ france"), "FR", "EUROPE", "fr", "France"),
    (("movistar", "digital+", "digital plus", "canal+ esp"), "ES", "EUROPE", "es", "Spain"),
    (("sky deutschland", "hd+", "austriasat"), "DE", "EUROPE", "de", "Germany"),
    (("sky italia", "tivusat", "tivùsat", "mediaset"), "IT", "EUROPE", "it", "Italy"),
    (("polsat", "canal+ polska", "platforma canal", "nc+", "cyfrowy"), "PL", "EUROPE", "pl", "Poland"),
    (("sky uk", "freesat", "freeview", "bbc sat"), "UK", "EUROPE", "en", "UK"),
    (("meo", "nos"), "PT", "EUROPE", "pt", "Portugal"),
)


def _norm(value):
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z+]+", " ", str(value or "").casefold())).strip()


def _orbital(service):
    try:
        return float((service or {}).get("orbital"))
    except Exception:
        return None


def _provider_route(provider):
    text = _norm(provider)
    if not text:
        return None
    for tokens, country, region, language, family in _PROVIDER_RULES:
        if any(_norm(token) in text for token in tokens):
            return {"country": country, "region": region, "language": language,
                    "provider_family": family, "basis": "provider"}
    return None


def service_context(service):
    """Resolve one SAT/provider/country/language context for Smart Mapping.

    beta119 deliberately reuses :mod:`programme_feed_policy` instead of keeping
    a second, slightly different interpretation of provider and language data.
    Explicit channel language (for example *Al Jazeera English*) is stronger
    than a provider's default language.  DVB provider country is stronger than
    a mixed European orbital position; only then do we fall back to satellite.
    """
    service = service or {}
    profile = smart_context_match.receiver_profile(service)
    provider = str(profile.get("provider") or service.get("provider_name") or "")
    explicit_language = str(programme_feed_policy.service_language(service) or "").lower()
    try:
        identity_countries = set(programme_feed_policy.identity_countries(
            profile.get("clean_name") or service.get("name") or ""))
    except Exception:
        identity_countries = set()
    identity_country = next(iter(identity_countries)) if len(identity_countries) == 1 else ""

    routed = _provider_route(provider)
    if routed:
        out = dict(routed)
        if explicit_language:
            out["language"] = explicit_language
            out["basis"] = "provider+channel-language"
    else:
        out = {"country": "", "region": "", "language": explicit_language,
               "provider_family": provider.strip(), "basis": "channel-language" if explicit_language else ""}

        # Use the same provider-country table as the hard routing firewall.
        countries = set(programme_feed_policy.provider_countries(provider))
        if len(countries) == 1:
            country = next(iter(countries))
            langs = set(programme_feed_policy.COUNTRY_LANGS.get(country, set()))
            out.update({
                "country": country,
                "region": "MENA" if country in _MENA else ("EUROPE" if country in _EUROPE else ""),
                "language": explicit_language or (next(iter(langs)) if len(langs) == 1 else ""),
                "basis": "provider-country+channel-language" if explicit_language else "provider-country",
            })

    # Curated channel identity outranks a conflicting DVB platform/acronym.
    # Example: Sharjah HD is a UAE identity while the short provider label SBA
    # is ambiguous in the wild.  This is ranking context only; PrecisionMatch
    # still enforces the source/candidate identity and feed guards.
    if identity_country:
        previous_country = str(out.get("country") or "").upper()
        langs = set(programme_feed_policy.COUNTRY_LANGS.get(identity_country, set()))
        out["country"] = identity_country
        out["region"] = "MENA" if identity_country in _MENA else ("EUROPE" if identity_country in _EUROPE else out.get("region") or "")
        if not explicit_language and len(langs) == 1:
            out["language"] = next(iter(langs))
        if previous_country and previous_country != identity_country:
            out["basis"] = "identity-over-provider"
        elif not out.get("basis"):
            out["basis"] = "channel-identity"

    # Explicit receiver tags remain authoritative when no recognised provider
    # route exists.  SAT Smart Mapping normally has no IPTV tags, but retaining
    # this makes the common context engine safe for legacy/manual callers.
    if not routed and not out.get("country"):
        country = str(profile.get("country_hint") or "").upper()
        language = str(profile.get("language_hint") or explicit_language or "").lower()
        if country or language:
            out.update({"country": country, "language": language,
                        "region": "MENA" if language == "ar" or country in _MENA else ("EUROPE" if country in _EUROPE else out.get("region") or ""),
                        "basis": "receiver-tag"})

    orbital = _orbital(service)
    if not out.get("region"):
        if orbital is not None and (-9.5 <= orbital <= -6.0):
            out.update({"region": "MENA", "language": out.get("language") or "ar", "basis": "satellite"})
        elif orbital is not None and 24.0 <= orbital <= 26.8:
            # 25.5E is strongly Qatar/beIN; 26E is wider Badr/MENA.
            country = "QA" if 25.2 <= orbital <= 25.8 else ""
            out.update({"country": out.get("country") or country, "region": "MENA",
                        "language": out.get("language") or "ar", "basis": "satellite"})
        elif orbital is not None and 27.5 <= orbital <= 29.0:
            out.update({"country": out.get("country") or "UK", "region": "EUROPE",
                        "language": out.get("language") or "en", "basis": "satellite"})
        elif orbital is not None and -30.8 <= orbital <= -29.2:
            out.update({"region": "EUROPE", "basis": "satellite-iberia"})
        elif orbital is not None and ((12.0 <= orbital <= 14.5) or (18.5 <= orbital <= 20.0) or (22.5 <= orbital <= 24.0)):
            out.update({"region": "EUROPE", "basis": "satellite-mixed"})

    out["orbital"] = orbital
    out["provider"] = provider
    out["channel"] = str(profile.get("clean_name") or service.get("name") or "")
    out["service_type"] = str(profile.get("service_type") or service.get("service_type") or "")
    return out


def dedicated_sources(identity_id, channel_name=""):
    """Preferred provider feeds for identities that have a native guide.

    beta121 keeps programme-feed semantics intact: the local 2M generator is
    base-only, while National/Monde/MENA are looked up in Morocco catalogues.
    Al Aoula International likewise prefers Morocco sources and never a Belgium
    catalogue merely because it contains an exact international display name.
    """
    iid = str(identity_id or "").casefold()
    name = _norm(channel_name)
    out = []

    if iid in ("2m", "2m_maroc") or name in ("2m", "2m maroc", "2m tv", "2m maroc hd"):
        out += ["chada_2m", "ext_openepg_morocco_1", "ext_openepg_morocco_2"]
    elif iid in ("2m_national", "2m_world", "2m_mena"):
        out += ["ext_openepg_morocco_1", "ext_openepg_morocco_2"]

    if iid == "al_aoula":
        out += ["snrt", "ext_openepg_morocco_1", "ext_openepg_morocco_2"]
    elif iid in ("al_aoula_inter", "al_aoula_laayoune"):
        out += ["ext_openepg_morocco_1", "ext_openepg_morocco_2"]
    elif iid in ("al_maghribia", "assadisa", "tamazight", "arrabiaa", "attaqafia"):
        out += ["snrt", "ext_openepg_morocco_1"]

    if iid == "arryadia" or "arryadia" in name:
        out += ["arryadia", "snrt", "ext_openepg_morocco_1"]
    if iid.startswith("medi1_") or name.startswith("medi1"):
        out += ["medi1tv", "ext_openepg_morocco_1"]
    if iid.startswith("bein_sports") or ("bein" in name and "sport" in name):
        out += ["bein_sports"]
    if iid == "al_jazeera_arabic" or name in ("al jazeera", "al jazeera hd", "aljazeera", "aljazeera hd"):
        out += ["ext_epgshare_al_jazeera"]

    # Mapping V4 strategic discovery: high-value broadcaster families can span
    # several country shards.  These are discovery preferences only; the ID
    # matcher still has to prove the same channel before anything is mapped.
    try:
        out += mapping_strategy.strategic_source_ids(channel_name, country="")
    except Exception:
        pass

    seen = set(); clean = []
    for sid in out:
        if sid and sid not in seen:
            seen.add(sid); clean.append(sid)
    return clean


def _source_countries(source):
    return set(str(x or "").upper() for x in ((source or {}).get("countries") or []) if x)


def _source_region(source):
    countries = _source_countries(source)
    region = str((source or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return region


def rank_source(service, source, identity_id=""):
    """Return a small deterministic context rank; lower is better.

    This never validates channel identity. Callers must first prove that the EPG
    row is the same/exact/alias channel. Context only orders already-valid rows.
    """
    source = source or {}
    sid = str(source.get("source_id") or source.get("id") or "")
    ctx = service_context(service)
    dedicated = dedicated_sources(identity_id, ctx.get("channel"))
    if sid in dedicated:
        # Preserve the explicit preference order. A local/base-native source is
        # rank 0; same-country fallbacks follow without becoming equivalent to
        # an unrelated exact-name catalogue.
        return (min(2, dedicated.index(sid)), "DEDICATED SOURCE")

    countries = _source_countries(source)
    region = _source_region(source)
    country = str(ctx.get("country") or "").upper()
    wanted_lang = str(ctx.get("language") or "").lower()
    source_lang = str((source or {}).get("language") or "").lower().split("-", 1)[0].split("_", 1)[0]
    if country:
        aliases = {country}
        if country == "UK": aliases.add("GB")
        if country == "GB": aliases.add("UK")
        if countries & aliases:
            if wanted_lang and source_lang:
                if source_lang == wanted_lang:
                    return (1, "PROVIDER COUNTRY + LANGUAGE")
                return (4, "PROVIDER COUNTRY / LANGUAGE MISMATCH")
            return (1, "PROVIDER COUNTRY")
    if wanted_lang and source_lang == wanted_lang and ctx.get("region") and region == ctx.get("region"):
        return (2, "RECEIVER LANGUAGE + REGION")
    if ctx.get("region") and region == ctx.get("region"):
        return (3, "RECEIVER REGION")
    if not region or region in ("INTERNATIONAL", "INT"):
        return (4, "INTERNATIONAL")
    return (5, "OTHER REGION")


def context_label(service):
    ctx = service_context(service)
    parts = []
    provider = str(ctx.get("provider") or "").strip()
    if provider:
        parts.append("Provider: %s" % provider)
    if ctx.get("country"):
        parts.append(str(ctx.get("country")))
    elif ctx.get("region"):
        parts.append(str(ctx.get("region")))
    if ctx.get("language"):
        parts.append("Lang %s" % str(ctx.get("language")).upper())
    orbital = ctx.get("orbital")
    if orbital is not None:
        parts.insert(0, ("%.1fE" % orbital) if orbital >= 0 else ("%.1fW" % abs(orbital)))
    return " • ".join(parts) if parts else "Context: receiver metadata"
