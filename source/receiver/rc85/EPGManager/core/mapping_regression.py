# -*- coding: utf-8 -*-
"""Tiny offline regression pack for Smart Mapping resolver policy.

These cases encode real failure classes reported during beta74-beta77. They are
synthetic and deterministic: no receiver database, network, source cache or XMLTV
programme file is required.  The purpose is to stop future UI/resolver changes
from silently reintroducing already-fixed source/language mistakes.
"""
from __future__ import print_function

from . import smart_context_match, source_variant_policy, smart_context_engine, unified_channel_identity, programme_feed_policy, canonical_bridge


def _source(source_id, name, countries, language, region="", provider="OPENEPG"):
    return {
        "source_id": source_id,
        "source_name": name,
        "countries": list(countries or []),
        "language": language,
        "region": region,
        "provider": provider,
    }


def _epg(source_id, name, channel_id=None):
    return {"source_id": source_id, "display_name": name, "channel_id": channel_id or name}


def _winner(service, candidates):
    ranked = []
    for source, epg, text_score in candidates:
        result = smart_context_match.evaluate(service, source, epg, text_score)
        if not result.get("allowed"):
            continue
        ranked.append((
            0 if result.get("verified") else 1,
            int(result.get("variant_rank") or 0),
            int(result.get("language_rank") or 0),
            -int(result.get("context") or 0),
            -int(result.get("score") or 0),
            source.get("source_id"),
            result,
        ))
    ranked.sort()
    return ranked[0] if ranked else None


def run():
    cases = []

    # Sky News Arabia: Arabic Saudi 5 must beat parallel English Saudi 6.
    svc = {"name": "SKY NEWS ARABIA HD", "orbital": -7.0, "satellite_zone": "MENA"}
    s5 = _source("ext_openepg_saudi_arabia_5", "Saudi Arabia 5", ["SA"], "ar", "MENA")
    s6 = _source("ext_openepg_saudi_arabia_6", "Saudi Arabia 6", ["SA"], "en", "MENA")
    win = _winner(svc, [(s6, _epg(s6["source_id"], "Sky News Arabia"), 100),
                        (s5, _epg(s5["source_id"], "Sky News Arabia"), 99)])
    cases.append(("Sky News Arabia prefers AR", bool(win and win[5] == s5["source_id"]), win[5] if win else "none"))

    # BBC Earth: Qatar Arabic shard must beat the parallel English shard in MENA.
    svc = {"name": "BBC EARTH", "orbital": -7.0, "satellite_zone": "MENA"}
    q4 = _source("ext_openepg_qatar_4", "Qatar 4", ["QA"], "ar", "MENA")
    q5 = _source("ext_openepg_qatar_5", "Qatar 5", ["QA"], "en", "MENA")
    win = _winner(svc, [(q5, _epg(q5["source_id"], "BBC Earth", "bbcearth-new"), 100),
                        (q4, _epg(q4["source_id"], "BBC Earth", "bbceahrt-new"), 99)])
    cases.append(("BBC Earth prefers AR Qatar", bool(win and win[5] == q4["source_id"]), win[5] if win else "none"))

    # IPTV |IT| is authoritative country/language context; Saudi is invalid.
    svc = {"name": "|IT| RAI 4K", "service_type": "IPTV"}
    italy = _source("ext_epgshare_italy", "Italy", ["IT"], "it", "EUROPE", "EPGSHARE")
    sa = _source("ext_openepg_saudi_arabia_1", "Saudi Arabia 1", ["SA"], "ar", "MENA")
    good = smart_context_match.evaluate(svc, italy, _epg(italy["source_id"], "RAI 4K"), 100)
    bad = smart_context_match.evaluate(svc, sa, _epg(sa["source_id"], "RAI 4K"), 100)
    cases.append(("IPTV IT accepts Italy", bool(good.get("allowed")), good.get("reason")))
    cases.append(("IPTV IT rejects Saudi", not bool(bad.get("allowed")), bad.get("reason")))

    # beIN Movies is a semantic alias, not beIN 4K.
    svc = {"name": "beIN MOVIES 1", "orbital": -7.0, "satellite_zone": "MENA"}
    q4 = _source("ext_openepg_qatar_4", "Qatar 4", ["QA"], "ar", "MENA")
    movie = smart_context_match.evaluate(svc, q4, _epg(q4["source_id"], "Movie1 Premier Digital"), 72)
    wrong = smart_context_match.evaluate(svc, q4, _epg(q4["source_id"], "beIN 4K"), 92)
    cases.append(("beIN Movies alias verified", bool(movie.get("allowed") and movie.get("verified")), movie.get("reason")))
    cases.append(("beIN Movies rejects beIN 4K", not bool(wrong.get("allowed")), wrong.get("reason")))

    # Parallel source policy must never keep both siblings in the same selection.
    normalized = source_variant_policy.normalize_selected([
        "ext_openepg_saudi_arabia_5", "ext_openepg_saudi_arabia_6"
    ])
    cases.append(("Parallel AR/EN selection collapses", normalized == ["ext_openepg_saudi_arabia_5"], repr(normalized)))


    # beta85 Provider-First Smart Context Engine.
    fr = _source("ext_rytec_france", "France", ["FR"], "fr", "EUROPE", "RYTEC")
    de = _source("ext_rytec_germany", "Germany", ["DE"], "de", "EUROPE", "RYTEC")
    it = _source("ext_rytec_italy", "Italy", ["IT"], "it", "EUROPE", "RYTEC")
    pl = _source("ext_rytec_poland", "Poland", ["PL"], "pl", "EUROPE", "RYTEC")
    pt = _source("ext_rytec_portugal", "Portugal", ["PT"], "pt", "EUROPE", "RYTEC")
    uk = _source("ext_epgshare_uk", "United Kingdom", ["UK"], "en", "EUROPE", "EPGSHARE")
    mena = _source("ext_openepg_qatar_4", "Qatar 4", ["QA"], "ar", "MENA")

    svc = {"name":"CANAL+ SPORT", "orbital":19.2, "provider_name":"CanalSat"}
    cases.append(("19.2 CanalSat routes France", smart_context_engine.rank_source(svc, fr)[0] < smart_context_engine.rank_source(svc, de)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"Sky Cinema", "orbital":19.2, "provider_name":"Sky Deutschland"}
    cases.append(("19.2 Sky DE routes Germany", smart_context_engine.rank_source(svc, de)[0] < smart_context_engine.rank_source(svc, fr)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"Sky Cinema", "orbital":13.0, "provider_name":"Sky Italia"}
    cases.append(("13E Sky Italia routes Italy", smart_context_engine.rank_source(svc, it)[0] < smart_context_engine.rank_source(svc, pl)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"CANAL+ Premium", "orbital":13.0, "provider_name":"Polsat Box"}
    cases.append(("13E Polsat routes Poland", smart_context_engine.rank_source(svc, pl)[0] < smart_context_engine.rank_source(svc, it)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"RTP 1", "orbital":-30.0, "provider_name":"NOS"}
    cases.append(("30W NOS routes Portugal", smart_context_engine.rank_source(svc, pt)[0] < smart_context_engine.rank_source(svc, fr)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"BBC One", "orbital":28.2, "provider_name":""}
    cases.append(("28.2E routes UK", smart_context_engine.rank_source(svc, uk)[0] < smart_context_engine.rank_source(svc, fr)[0], smart_context_engine.context_label(svc)))
    svc = {"name":"BBC Earth", "orbital":-7.0, "provider_name":"OSN"}
    cases.append(("Nilesat MENA beats Europe", smart_context_engine.rank_source(svc, mena, "bbc_earth")[0] < smart_context_engine.rank_source(svc, fr, "bbc_earth")[0], smart_context_engine.context_label(svc)))

    local2m = _source("chada_2m", "2M / Chada", ["MA"], "ar", "MENA", "EPG MANAGER")
    openma = _source("ext_openepg_morocco_1", "Morocco 1", ["MA"], "ar", "MENA")
    svc = {"name":"2M", "orbital":-7.0, "provider_name":"2M"}
    cases.append(("2M base dedicated source wins", smart_context_engine.rank_source(svc, local2m, "2m")[0] < smart_context_engine.rank_source(svc, openma, "2m")[0], str(smart_context_engine.rank_source(svc, local2m, "2m"))))
    cases.append(("2M National does not force base generator", "chada_2m" not in smart_context_engine.dedicated_sources("2m_national", "2M National"), repr(smart_context_engine.dedicated_sources("2m_national", "2M National"))))

    # beta86 real provider labels: suffix decorations and historical identity IDs
    # must not make well-known channels disappear from Find Matches.
    trt_id = unified_channel_identity.canonical_id("TRT Arabic.cz")
    cases.append(("TRT provider suffix resolves", trt_id == "trt_arabi", trt_id))
    m2a = unified_channel_identity.canonical_id("2M Nat")
    m2b = unified_channel_identity.canonical_id("2M National")
    cases.append(("2M National/Nat aliases collapse to base 2M", bool(m2a and m2a == m2b == "2m_maroc"), "%s/%s" % (m2a, m2b)))

    # beta119 SAT-first context unification. Explicit channel language must beat
    # provider defaults, and mixed European satellites must use DVB provider country.
    q_ar = _source("q_ar", "Qatar Arabic", ["QA"], "ar", "MENA")
    q_en = _source("q_en", "Qatar English", ["QA"], "en", "MENA")
    svc = {"name":"Al Jazeera English HD", "orbital":-7.0, "provider_name":"Al Jazeera"}
    ajctx = smart_context_engine.service_context(svc)
    cases.append(("Al Jazeera English context is EN", ajctx.get("country") == "QA" and ajctx.get("language") == "en", repr(ajctx)))
    cases.append(("Al Jazeera English ranks EN above AR", smart_context_engine.rank_source(svc, q_en)[0] < smart_context_engine.rank_source(svc, q_ar)[0], "%s/%s" % (smart_context_engine.rank_source(svc, q_en), smart_context_engine.rank_source(svc, q_ar))))

    svc = {"name":"RAI 1 HD", "orbital":13.0, "provider_name":"RAI"}
    raictx = smart_context_engine.service_context(svc)
    cases.append(("Hotbird RAI resolves Italy/IT", raictx.get("country") == "IT" and raictx.get("language") == "it", repr(raictx)))
    cases.append(("Hotbird RAI ranks Italy above Poland", smart_context_engine.rank_source(svc, it)[0] < smart_context_engine.rank_source(svc, pl)[0], "%s/%s" % (smart_context_engine.rank_source(svc, it), smart_context_engine.rank_source(svc, pl))))

    svc = {"name":"Movistar LaLiga", "orbital":-30.0, "provider_name":"Movistar"}
    es = _source("es", "Spain", ["ES"], "es", "EUROPE", "KOALA")
    cases.append(("Hispasat Movistar routes Spain", smart_context_engine.rank_source(svc, es)[0] < smart_context_engine.rank_source(svc, fr)[0], smart_context_engine.context_label(svc)))

    # beta121: native broadcaster country + programme-feed firewall.  These are
    # direct regressions for receiver screenshots where Morocco services were
    # auto-mapped to Egypt/Belgium because an exact display name outranked the
    # provider/country/feed context.
    ma_ar = _source("ext_openepg_morocco_1", "Morocco 1", ["MA"], "ar", "MENA", "OPENEPG")
    ma_fr = _source("ext_openepg_morocco_2", "Morocco 2", ["MA"], "fr", "MENA", "OPENEPG")
    eg_ar = _source("ext_openepg_egypt_1", "Egypt 1", ["EG"], "ar", "MENA", "OPENEPG")
    be_fr = _source("ext_epgshare_belgium", "Belgium", ["BE"], "fr", "EUROPE", "EPGSHARE")
    local_2m = _source("chada_2m", "2M / Chada", ["MA"], "ar", "MENA", "EPG MANAGER")

    svc = {"name":"2M MAROC HD", "orbital":25.5, "provider_name":"2M"}
    ok = smart_context_match.evaluate(svc, local_2m, _epg("chada_2m", "الثانية", "2M"), 35)
    bad = smart_context_match.evaluate(svc, eg_ar, _epg("ext_openepg_egypt_1", "الثانية", "الثانية.eg"), 100)
    cases.append(("2M Maroc Arabic alias is deterministic", bool(ok.get("allowed") and ok.get("verified")), ok.get("reason")))
    cases.append(("2M Maroc rejects Egypt source", not bool(bad.get("allowed")), bad.get("reason")))

    svc = {"name":"2M NATIONAL HD", "orbital":25.5, "provider_name":"2M"}
    feed_ok, feed_reason = programme_feed_policy.feed_compatible(svc, "2M", "2M")
    cases.append(("2M National accepts base 2M feed", bool(feed_ok), feed_reason))

    svc = {"name":"Al Aoula Inter HD", "orbital":-7.0, "provider_name":"SNRT"}
    bad = smart_context_match.evaluate(svc, be_fr, _epg("ext_epgshare_belgium", "Al Aoula International", "AlAoulaInter.be"), 100)
    good = smart_context_match.evaluate(svc, ma_ar, _epg("ext_openepg_morocco_1", "Al Aoula International", "AlAoulaInter.ma"), 96)
    base = smart_context_match.evaluate(svc, ma_ar, _epg("ext_openepg_morocco_1", "Al Aoula", "AlAoula.ma"), 100)
    cases.append(("Al Aoula Inter rejects Belgium source", not bool(bad.get("allowed")), bad.get("reason")))
    cases.append(("Al Aoula Inter accepts Morocco source", bool(good.get("allowed") and good.get("verified")), good.get("reason")))
    cases.append(("Al Aoula Inter accepts domestic Al Aoula feed", bool(base.get("allowed") and base.get("verified")), base.get("reason")))

    svc = {"name":"2M MAROC HD", "orbital":-7.0, "provider_name":"2M"}
    route_ar = programme_feed_policy.route(svc, ma_ar, {"display_name":"الثانية", "channel_id":"2M"})
    route_fr = programme_feed_policy.route(svc, ma_fr, {"display_name":"2M", "channel_id":"2M"})
    cases.append(("Nilesat 2M prefers Arabic source", bool(route_ar[0] and not route_fr[0]), "%s / %s" % (route_ar, route_fr)))

    svc = {"name":"2M MONDE HD", "orbital":13.0, "provider_name":"2M"}
    hotbird_fr = programme_feed_policy.route(svc, ma_fr, {"display_name":"2M Monde", "channel_id":"2MMonde.ma"})
    cases.append(("Hotbird 2M Monde may use Morocco FR", bool(hotbird_fr[0]), str(hotbird_fr)))

    cases.append(("2M base dedicated list starts local", smart_context_engine.dedicated_sources("2m_maroc", "2M MAROC HD")[:1] == ["chada_2m"], repr(smart_context_engine.dedicated_sources("2m_maroc", "2M MAROC HD"))))
    cases.append(("2M National dedicated list excludes local base", "chada_2m" not in smart_context_engine.dedicated_sources("2m_national", "2M NATIONAL HD"), repr(smart_context_engine.dedicated_sources("2m_national", "2M NATIONAL HD"))))

    # rc44: current direct-source IDs whose Arabic display labels are ambiguous
    # across countries must be resolved by the full XMLTV ID before generic aliases.
    althania_aliases = canonical_bridge.aliases("Althania.sy@SD", "الثانية")
    cases.append(("Al Thania Syria does not become Morocco 2M", althania_aliases == ["Al Thania TV"], repr(althania_aliases)))
    amman_aliases = canonical_bridge.aliases("AmmanTV.jo@SD", "عَمان")
    cases.append(("Amman TV Jordan does not become Oman TV", amman_aliases == ["Amman TV"], repr(amman_aliases)))

    failed = [row for row in cases if not row[1]]
    return {
        "total": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "cases": [{"name": x[0], "ok": bool(x[1]), "detail": str(x[2])} for x in cases],
    }
