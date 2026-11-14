# -*- coding: utf-8 -*-
"""High-precision, fail-closed channel/EPG identity resolver.

This is intentionally *not* a cloud/LLM matcher.  A Vu+ receiver needs a
repeatable answer with no network dependency.  The engine combines independent
proofs (curated identity, programme-feed, DVB provider/country, satellite,
language, provider-native maps and exact names).  Fuzzy similarity is recall
only and can never by itself produce an automatic mapping.

The numeric ``confidence`` below is an evidence score, not a statistical
probability.  ``auto=True`` is the important contract: callers may write an
automatic map only for a PROVEN or SAFE EXACT result. Fuzzy-only evidence remains REVIEW or BLOCKED.
"""
from __future__ import print_function

import re

from . import channel_identity_resolver, deterministic_matcher, programme_feed_policy
from . import multilingual_guard, smart_context_engine

AUTO_MIN = 990
# beta126 Balanced Precision: a second automatic tier for truly exact/canonical
# names that pass every hard feed/provider/country/language/variant firewall.
# It is deliberately below PROVEN, but fuzzy text can never enter this tier.
BALANCED_AUTO_MIN = 910
AUTO_MARGIN = 18
SAFE_MARGIN = 14
REVIEW_MIN = 760

_BROAD = set(("MENA", "INT", "INTERNATIONAL", "GLOBAL", "WORLD"))
_MENA = set(("MA","DZ","TN","LY","EG","SA","AE","QA","KW","BH","OM","JO","LB","PS","IQ","SY","YE"))
_EUROPE = set(("FR","BE","NL","DE","AT","CH","IT","ES","PT","UK","GB","IE","GR","TR","RO","BG","HR","RS","SI","SK","CZ","DK","SE","NO","FI","PL","AL","BA","BY","CY","HU","LT","LU","LV","MT","ME","MK","UA"))

# Legacy source rows/caches do not always retain country metadata.  Infer only
# from unambiguous provider/source labels so old rows such as
# ext_epgshare_united_arab_emirates cannot escape the native-country firewall.
_SOURCE_COUNTRY_HINTS = (
    (("united_arab_emirates", "united arab emirates", "uae"), "AE"),
    (("saudi_arabia", "saudi arabia"), "SA"), (("morocco",), "MA"),
    (("egypt",), "EG"), (("qatar",), "QA"), (("palestine",), "PS"),
    (("lebanon",), "LB"), (("tunisia",), "TN"), (("algeria",), "DZ"),
    (("iraq",), "IQ"), (("jordan",), "JO"), (("kuwait",), "KW"),
    (("bahrain",), "BH"), (("oman",), "OM"), (("yemen",), "YE"),
    (("france",), "FR"), (("belgium",), "BE"), (("switzerland",), "CH"),
    (("germany",), "DE"), (("austria",), "AT"), (("italy",), "IT"),
    (("spain",), "ES"), (("portugal",), "PT"), (("united_kingdom", "united kingdom", "uk"), "UK"),
    (("ireland",), "IE"), (("poland",), "PL"), (("netherlands",), "NL"),
    (("greece",), "GR"), (("turkey",), "TR"), (("romania",), "RO"),
    (("bulgaria",), "BG"), (("croatia",), "HR"), (("serbia",), "RS"),
    (("czech",), "CZ"), (("slovakia",), "SK"), (("denmark",), "DK"),
    (("sweden",), "SE"), (("norway",), "NO"), (("finland",), "FI"),
)

_GENERIC_EXACT_WORDS = set((
    "tv","channel","network","hd","sd","fhd","uhd","4k","live","plus",
    "news","sport","sports","movie","movies","cinema","music","kids",
    "family","action","drama","classic","one","two","max","world","international"
))


def _source_id(source):
    source = source or {}
    return str(source.get("source_id") or source.get("manager_id") or source.get("id") or "")


def _source_name(source):
    source = source or {}
    return str(source.get("source_name") or source.get("name") or _source_id(source))


def _countries(source):
    source = source or {}
    out = set(str(x or "").upper() for x in (source.get("countries") or []) if str(x or "").strip())
    # Recover country from stale/legacy source IDs only when metadata does not
    # already provide a concrete country. Broad MENA/INT markers are not
    # concrete and may be supplemented by an unambiguous source label.
    concrete = set(x for x in out if x not in _BROAD)
    if not concrete:
        text = ("%s %s %s" % (_source_id(source), _source_name(source), source.get("region") or "")).casefold()
        text_norm = re.sub(r"[^a-z0-9]+", "_", text)
        for aliases, code in _SOURCE_COUNTRY_HINTS:
            hit = False
            for token in aliases:
                token_cf = str(token).casefold()
                token_norm = re.sub(r"[^a-z0-9]+", "_", token_cf).strip("_")
                if token_cf in text or (token_norm and token_norm in text_norm):
                    hit = True; break
            if hit:
                out.add(code); break
    return out


def _specific_countries(source):
    return set(x for x in _countries(source) if x not in _BROAD)


def _languages(source):
    out = set()
    for value in ((source or {}).get("languages") or []):
        if value:
            out.add(str(value).lower())
    value = str((source or {}).get("language") or "").lower().strip()
    if value:
        out.add(value)
    return out


def _provider(source):
    return str((source or {}).get("provider") or "").upper().strip()


def _source_trust(source):
    """Small deterministic tie-break only; never enough to create identity."""
    source = source or {}
    if bool(source.get("native_srp")):
        return 6
    provider = _provider(source)
    if source.get("kind") == "local" or provider == "EPG MANAGER":
        return 5
    if provider in ("RYTEC", "AZMAN", "KOALA"):
        return 5
    # rc42: GitHub EPG-Scrapers direct feeds are validated first-party runtime
    # sources for this plugin.  Treat them like other curated/direct catalogues
    # for PrecisionMatch trust, while retaining all identity/provider/feed guards.
    if provider == "EPG SCRAPERS" and bool(source.get("direct_feed")):
        return 5
    if provider == "OPENEPG":
        return 4
    if provider == "EPGSHARE":
        return 3
    return 1


def _identity(value):
    try:
        return channel_identity_resolver.resolve(value) or {}
    except Exception:
        return {}


def _identity_id(value):
    return str((_identity(value) or {}).get("id") or "")


def _identity_countries(value):
    row = _identity(value)
    out = set(str(x or "").upper() for x in (row.get("countries") or []) if x)
    country = str(row.get("country") or "").upper().strip()
    if country:
        out.add(country)
    return out


def _home_country(service):
    """Native identity country used only for AUTO proof, never for manual choice."""
    try:
        locked = str(programme_feed_policy.locked_home_country(service or {}) or "").upper().strip()
    except Exception:
        locked = ""
    if locked:
        return locked
    name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    countries = _identity_countries(name)
    return list(countries)[0] if len(countries) == 1 else ""


def _candidate_name(epg):
    epg = epg or {}
    return str(epg.get("match_name") or epg.get("display_name") or epg.get("channel_id") or "")


def _norm(value):
    try:
        return channel_identity_resolver.normalize(value)
    except Exception:
        return str(value or "").strip().casefold()


def _compact(value):
    try:
        return channel_identity_resolver.compact(value)
    except Exception:
        return re.sub(r"\W+", "", str(value or "").casefold())


def _source_region(source):
    countries = _countries(source)
    region = str((source or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return region


def _distinctive_exact_name(value, identity_id=""):
    """Reject short/generic exact-name traps from the SAFE automatic tier."""
    if identity_id:
        return True
    n = _norm(value)
    compact = re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", n, flags=re.UNICODE)
    if len(compact) < 5:
        return False
    words = [w for w in n.split() if w]
    meaningful = [w for w in words if w not in _GENERIC_EXACT_WORDS and (len(w) >= 2 or w.isdigit())]
    if not meaningful:
        return False
    # A single generic-ish Latin token needs more entropy; Arabic names and
    # multi-token labels are naturally more discriminative.
    if len(words) == 1 and len(compact) < 7:
        return False
    return True


def _balanced_context(service, source, route_score, exact_like, identity_id=""):
    """Return (eligible, reason) for beta126 SAFE EXACT automatic mapping.

    Every hard guard has already passed before this function is called.  This
    tier only relaxes *proof quantity* for exact/canonical names; it never
    relaxes programme-feed, country, language, number, +1 or 4K boundaries.
    """
    source = source or {}
    # rc44: the seven EPG-Scrapers catalogues are prevalidated and source-scoped.
    # Once every hard feed/provider/country/language/variant guard above passed,
    # an exact/canonical name may be SAFE even when the label is short (H2, Ginx,
    # TLC, LTV). Fuzzy text still never enters this branch.
    if exact_like and _provider(source) == "EPG SCRAPERS" and bool(source.get("direct_feed")):
        return True, "validated direct catalogue + exact identity"
    if not exact_like or not _distinctive_exact_name((service or {}).get("clean_name") or (service or {}).get("name") or "", identity_id):
        return False, "name is not distinctive enough for SAFE EXACT"
    specific = _specific_countries(source)
    src_region = _source_region(source)
    try:
        ctx = smart_context_engine.service_context(service or {}) or {}
    except Exception:
        ctx = {}
    svc_region = str(ctx.get("region") or "").upper()
    svc_country = str(ctx.get("country") or "").upper()
    provider_country = _provider_country_match(service, source)
    home = _home_country(service)

    # Strong context remains preferred.
    if home and home in specific:
        return True, "native country + exact identity"
    if provider_country is True:
        return True, "DVB provider country + exact identity"
    if bool(source.get("native_srp")) or _dedicated_source(service, source):
        return True, "authoritative source + exact identity"
    # rc42: the seven EPG-Scrapers direct feeds are prevalidated catalogues.
    # Once all hard feed/variant/provider/satellite guards have passed, an exact
    # or curated identity is enough for SAFE automatic ownership even when the
    # feed is intentionally broad MENA (OSN/ElCinema). This does not relax fuzzy
    # matching and does not bypass the semantic guards above.
    if _provider(source) == "EPG SCRAPERS" and bool(source.get("direct_feed")):
        return True, "validated direct catalogue + exact identity"

    # Unknown channels may use the middle tier only when the satellite/region
    # and source metadata agree.  A concrete foreign region never qualifies.
    if svc_region and src_region and svc_region != src_region:
        return False, "receiver/source regions disagree"
    if svc_country and specific:
        aliases = set((svc_country,))
        if svc_country == "UK": aliases.add("GB")
        if svc_country == "GB": aliases.add("UK")
        if aliases.isdisjoint(specific):
            return False, "receiver/source countries disagree"

    # MENA routing contributes >=110 only when the source is Arabic/MENA
    # compatible; Europe needs either a provider/country hint or at least the
    # mixed-satellite region tie-break.  Broad MENA-only containers remain
    # REVIEW unless they are dedicated/native.
    if not specific:
        return False, "broad-region source is not enough for SAFE EXACT"
    min_route = 100 if svc_region == "MENA" else 25
    if int(route_score or 0) < min_route:
        return False, "not enough provider/satellite context for SAFE EXACT"
    return True, "exact distinctive name + compatible source context"


def _feed_key(value):
    try:
        family, variant = programme_feed_policy._known_family_variant(value)
    except Exception:
        family = variant = ""
    if family:
        return "%s:%s" % (family, variant or "base")
    return ""


def _provider_country_match(service, source):
    try:
        wanted = set(programme_feed_policy.provider_countries((service or {}).get("provider_name") or ""))
    except Exception:
        wanted = set()
    specific = _specific_countries(source)
    if not wanted or not specific:
        return None
    aliases = set(wanted)
    if "UK" in aliases:
        aliases.add("GB")
    if "GB" in aliases:
        aliases.add("UK")
    if not aliases.isdisjoint(specific):
        return True
    # A single curated channel country is stronger than a contradictory DVB
    # platform/acronym hint.  Returning None (not True) avoids awarding provider
    # proof while allowing the independent identity-country proof to decide.
    try:
        name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
        identity = _identity_countries(name)
    except Exception:
        identity = set()
    if len(identity) == 1 and not identity.isdisjoint(specific):
        return None
    return False


def _dedicated_source(service, source):
    """True only when the source itself visibly belongs to this broadcaster.

    Curated identities are preferred, but a few local generators (notably
    beIN) cover numbered services that are not all present in srp_aliases.json.
    For those, require an uncommon brand token to be shared by the receiver
    service and the source label/provider. Generic words never establish this
    proof on their own.
    """
    name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    row = _identity(name)
    provider = str(row.get("provider") or "").casefold().strip()
    text = ("%s %s %s" % (_source_name(source), _provider(source), _source_id(source))).casefold()
    if provider and provider in text:
        return True
    iid = str(row.get("id") or "").replace("_", " ").casefold().strip()
    tokens = [x for x in iid.split() if len(x) >= 3]
    if tokens and any(tok in text for tok in tokens[:2]):
        return True
    stop = set(("tv","hd","sd","fhd","uhd","4k","channel","channels","sports","sport",
                "news","mena","arabic","english","french","epg","manager","plus","live"))
    stoks = set(x for x in re.findall(r"[a-z0-9]+", name.casefold()) if len(x) >= 4 and x not in stop and not x.isdigit())
    ttoks = set(x for x in re.findall(r"[a-z0-9]+", text) if len(x) >= 4 and x not in stop and not x.isdigit())
    return bool(stoks.intersection(ttoks))


def _result(status, confidence, reason, proofs=None, risks=None, target_key="", auto=False, review=False):
    return {
        "allowed": status != "BLOCKED",
        "auto": bool(auto),
        "review": bool(review),
        "grade": status,
        "confidence": max(0, min(1000, int(confidence or 0))),
        "score": max(0, min(100, int(round(float(confidence or 0) / 10.0)))),
        "reason": str(reason or status),
        "proofs": list(proofs or []),
        "risks": list(risks or []),
        "target_key": str(target_key or ""),
    }


def evaluate(service, source, epg, text_score=None, learned=False, native_proof=False, balanced=False):
    """Evaluate one receiver-service -> XMLTV candidate.

    AUTO is fail-closed.  A fuzzy name can be shown for review, but automatic
    writes require deterministic identity or an exact name plus authoritative
    provider/country/native-SRP context.
    """
    service = service or {}; source = dict(source or {}); epg = epg or {}
    # Enrich stale source rows with unambiguous country metadata before any
    # routing guard. This closes old-cache holes without network I/O.
    inferred_countries = sorted(_countries(source))
    if inferred_countries:
        source["countries"] = inferred_countries
    sname = str(service.get("clean_name") or service.get("name") or "").strip()
    ename = _candidate_name(epg).strip()
    cid = str(epg.get("channel_id") or "").strip()
    if not sname or not (ename or cid):
        return _result("BLOCKED", 0, "missing receiver/candidate identity")

    proofs = []; risks = []

    # 1) Programme-feed: exact edition is preferred, but beta127 permits a
    # curated same-broadcaster fallback only from the native/local Arabic source.
    local_feed_fallback = False
    try:
        feed_ok, feed_reason, local_feed_fallback = programme_feed_policy.feed_compatible_with_source(
            service, cid, ename or cid, source=source, epg=epg)
    except Exception as exc:
        feed_ok, feed_reason, local_feed_fallback = False, "feed guard error: %s" % exc, False
    if not feed_ok:
        return _result("BLOCKED", 0, feed_reason, risks=["feed-conflict"])
    proofs.append("local-arabic-feed-fallback" if local_feed_fallback else "feed-compatible")
    if local_feed_fallback:
        risks.append("edition-fallback")

    # 2) Provider/country/satellite/language routing is an absolute safety gate.
    try:
        route_ok, route_score, route_reason = programme_feed_policy.route(service, source, epg)
    except Exception as exc:
        route_ok, route_score, route_reason = False, 0, "route guard error: %s" % exc
    if not route_ok:
        return _result("BLOCKED", 0, route_reason, risks=["route-conflict"])

    # 3) Explicit language variants cannot cross.
    try:
        lang_ok, lang_reason = multilingual_guard.check(
            service, source, epg, receiver_context=smart_context_engine.service_context(service))
    except Exception as exc:
        lang_ok, lang_reason = False, "language guard error: %s" % exc
    if not lang_ok:
        return _result("BLOCKED", 0, lang_reason, risks=["language-conflict"])

    # 4) Curated identity is stronger evidence than surface presentation.
    # srp_aliases.json deliberately joins names such as "Sharjah HD" and
    # "Sharjah TV", "Alkass 1" and "Alkass One", or Latin/Arabic aliases.
    # Once BOTH sides resolve to the same curated identity, presentation/number
    # heuristics must not veto that explicit proof.  Feed and language guards
    # have already run above, so true editions remain protected.
    sid = _identity_id(sname)
    eid = _identity_id(ename) or _identity_id(cid)
    same_identity = bool(sid and eid and sid == eid)
    if sid and eid and sid != eid and not local_feed_fallback:
        return _result("BLOCKED", 0, "deterministic channel identity mismatch (%s != %s)" % (sid, eid), risks=["identity-conflict"])

    # Numeric/+1/4K sibling heuristics are a fallback safety net only when the
    # curated identity database cannot prove that both labels are one channel.
    try:
        if local_feed_fallback:
            if not programme_feed_policy._same_family_presentation_safe(sname, ename or cid):
                return _result("BLOCKED", 0, "numeric/language/quality channel variant conflict", risks=["variant-conflict"])
        elif not same_identity and not deterministic_matcher.compatible_variant(sname, ename or cid):
            return _result("BLOCKED", 0, "numeric/language/quality channel variant conflict", risks=["variant-conflict"])
        elif same_identity:
            proofs.append("curated-identity-overrides-presentation")
    except Exception:
        return _result("BLOCKED", 0, "variant guard error", risks=["variant-guard-error"])

    sn = _norm(sname); en = _norm(ename or cid)
    exact_norm = bool(sn and en and sn == en)
    exact_compact = bool(_compact(sname) and _compact(sname) == _compact(ename or cid))
    if text_score is None:
        try:
            text_score = int(deterministic_matcher.score(sname, ename or cid) or 0)
        except Exception:
            text_score = 0
    text_score = max(0, min(100, int(text_score or 0)))

    try:
        strict_home = str(programme_feed_policy.locked_home_country(service or {}) or "").upper().strip()
    except Exception:
        strict_home = ""
    identity_countries = _identity_countries(sname)
    identity_home = next(iter(identity_countries)) if len(identity_countries) == 1 else ""
    home = strict_home or identity_home
    specific = _specific_countries(source)
    all_countries = _countries(source)
    native_country = bool(home and home in specific)
    broad_mena = bool(home in _MENA and not specific and "MENA" in all_countries)
    provider_country = _provider_country_match(service, source)
    native_srp = bool(native_proof or source.get("native_srp"))
    dedicated = _dedicated_source(service, source)

    # Country containers are routing metadata, not always channel ownership.
    # For a deterministic curated identity (e.g. Sharjah HD == Sharjah TV or
    # Al Ahly HD == Al Ahly TV), another Arabic/MENA catalogue may legitimately
    # carry the same programme feed.  Strict national broadcaster families
    # (2M/SNRT/etc.) remain country-locked via ``strict_home``.
    source_region = _source_region(source)
    cross_mena_identity = bool(
        not strict_home and same_identity and identity_home in _MENA and specific and
        identity_home not in specific and bool(specific & _MENA) and source_region == "MENA")

    if strict_home and specific and strict_home not in specific:
        return _result("BLOCKED", 0, "native broadcaster %s rejects foreign source country %s" % (
            strict_home, "/".join(sorted(specific))), risks=["foreign-country-container"])
    if identity_home and specific and identity_home not in specific and not cross_mena_identity:
        return _result("BLOCKED", 0, "native channel %s rejects foreign source country %s" % (
            identity_home, "/".join(sorted(specific))), risks=["foreign-country-container"])
    if provider_country is False and not cross_mena_identity:
        return _result("BLOCKED", 0, "DVB provider country conflicts with EPG source country", risks=["provider-country-conflict"])
    if cross_mena_identity:
        risks.append("cross-mena-catalogue")
        proofs.append("same-curated-identity-across-mena")

    # Learned corrections are only automatic when the caller proves an exact
    # receiver-context learning key (beta124 learning v2).
    if learned:
        target = "id:%s" % (sid or eid) if (sid or eid) else "name:%s" % sn
        return _result("PROVEN", 1000, "exact receiver-context learned correction",
                       proofs=["manual-learning-v2", "feed-compatible", "route-compatible"],
                       target_key=target, auto=True)

    confidence = 0
    if local_feed_fallback:
        # Deliberately below exact-edition PROVEN/SAFE.  It is an automatic
        # coverage fallback only because source country/language/broadcaster are local.
        confidence = 865 + int(programme_feed_policy.local_fallback_preference(service, cid, ename or cid) or 0)
        proofs.append("same-broadcaster-local-edition")
    elif same_identity:
        confidence = 885
        proofs.append("same-curated-identity")
    elif exact_norm:
        confidence = 845
        proofs.append("exact-normalized-name")
    elif exact_compact:
        confidence = 830
        proofs.append("exact-compact-name")
    elif text_score >= 98:
        confidence = 780
        proofs.append("near-exact-text")
    elif text_score >= 92:
        confidence = 730
        proofs.append("high-text-similarity")
    else:
        return _result("BLOCKED", 0, "identity evidence too weak", risks=["weak-name"])

    # Evidence bonuses.  They may confirm a strong identity but never rescue a
    # weak fuzzy name because non-deterministic cases are capped below AUTO.
    sf = _feed_key(sname); ef = _feed_key(ename or cid)
    if sf and ef and sf == ef:
        confidence += 35; proofs.append("exact-programme-feed")
    if native_country:
        confidence += 70; proofs.append("native-source-country")
        if same_identity:
            confidence += 10; proofs.append("identity-country-consensus")
    elif cross_mena_identity:
        confidence += 35; proofs.append("arabic-mena-catalogue-fallback")
    elif broad_mena:
        confidence += 20; proofs.append("broad-mena-source")
    if provider_country is True:
        confidence += 35; proofs.append("dvb-provider-country")
    if dedicated:
        confidence += 45; proofs.append("dedicated-broadcaster-source")
    if native_srp:
        confidence += 90; proofs.append("provider-native-service-ref")
    trust = _source_trust(source)
    confidence += trust * 5
    proofs.append("source-trust-%d" % trust)
    if exact_norm or exact_compact:
        confidence += 25
    if dedicated and trust >= 5 and (exact_norm or exact_compact):
        confidence += 25
        proofs.append("authoritative-dedicated-exact")
    if route_score and int(route_score) > 0:
        confidence += min(35, int(route_score) // 12)
    if _languages(source):
        confidence += 10; proofs.append("declared-source-language")

    target_id = sid or eid
    target = "id:%s" % target_id if target_id else "name:%s" % sn

    # A native/local Arabic broadcaster-edition fallback is an explicit SAFE
    # coverage tier; all foreign-country, language, number and +1/4K gates have
    # already passed above. Exact feed matches still outrank it.
    if local_feed_fallback:
        safe_conf = min(970, max(920, int(confidence)))
        family, _sv = programme_feed_policy._known_family_variant(sname)
        _cf, cv = programme_feed_policy._known_family_variant(ename or cid)
        return _result("SAFE", safe_conf, "SAFE LOCAL ARABIC: %s" % feed_reason,
                       proofs=proofs, risks=risks,
                       target_key="local:%s:%s" % (family or "family", cv or "base"), auto=True)

    # Deterministic same-channel identity may use a different Arabic/MENA
    # catalogue country as a SAFE fallback.  This is deliberately narrower than
    # generic cross-country exact-name matching: both sides must resolve to the
    # same curated identity, language/feed/variant guards already passed, and the
    # source must remain inside MENA.  Native-country sources still score higher.
    if cross_mena_identity:
        safe_conf = min(970, max(930, int(confidence)))
        return _result("SAFE", safe_conf, "SAFE MENA IDENTITY: same curated channel in Arabic/MENA catalogue",
                       proofs=proofs, risks=risks, target_key=target, auto=True)

    # Fuzzy-only evidence is never auto, regardless of arithmetic score.
    deterministic_identity = bool(same_identity or (native_srp and (exact_norm or exact_compact)))
    authoritative_exact = bool((exact_norm or exact_compact) and (native_country or provider_country is True or native_srp or dedicated))
    auto_eligible = bool(deterministic_identity or authoritative_exact)

    # A broad MENA container without native/dedicated proof can rank a candidate
    # but cannot establish a PROVEN automatic owner.
    if broad_mena and not (dedicated or native_srp):
        confidence = min(confidence, 975)
        risks.append("broad-region-only")
    if not same_identity and not native_srp and not (dedicated and trust >= 5 and (exact_norm or exact_compact)):
        confidence = min(confidence, 985)
    if not auto_eligible:
        confidence = min(confidence, 965)

    if auto_eligible and confidence >= AUTO_MIN:
        return _result("PROVEN", confidence, "multi-proof deterministic match",
                       proofs=proofs, risks=risks, target_key=target, auto=True)

    # beta126 middle ground: exact/canonical names can become SAFE automatic
    # matches after every hard semantic/context guard has passed.  The source
    # country is included in the competition key when receiver ownership is not
    # independently known, so two countries with the same channel label compete
    # rather than silently collapsing to one arbitrary source.
    exact_like = bool(exact_norm or exact_compact or same_identity)
    balanced_ok, balanced_reason = _balanced_context(service, source, route_score, exact_like, identity_id=(sid or eid))
    if bool(balanced) and balanced_ok:
        safe_conf = max(confidence, BALANCED_AUTO_MIN)
        owner = home
        if not owner:
            try:
                provider_codes = set(programme_feed_policy.provider_countries(service.get("provider_name") or ""))
            except Exception:
                provider_codes = set()
            if len(provider_codes) == 1:
                owner = next(iter(provider_codes))
        if not owner:
            specific_owner = sorted(_specific_countries(source))
            owner = specific_owner[0] if len(specific_owner) == 1 else ""
        competition_target = target if (home or provider_country is True or native_srp or dedicated) else (target + "@" + (owner or "UNKNOWN"))
        safe_proofs = list(proofs) + ["balanced-safe-exact", balanced_reason]
        return _result("SAFE", safe_conf, "SAFE EXACT: %s" % balanced_reason,
                       proofs=safe_proofs, risks=risks, target_key=competition_target, auto=True)

    if confidence >= REVIEW_MIN:
        return _result("REVIEW", confidence, "plausible match requires human confirmation",
                       proofs=proofs, risks=risks, target_key=target, review=True)
    return _result("BLOCKED", confidence, "insufficient independent proof", proofs=proofs, risks=risks, target_key=target)


def choose_best(results, margin=AUTO_MARGIN):
    """Return one PROVEN result only when no competing target is too close."""
    rows = [r for r in (results or []) if (r or {}).get("auto")]
    if not rows:
        return None
    rows.sort(key=lambda r: (-int(r.get("confidence") or 0), str(r.get("target_key") or ""), str(r.get("source_id") or "")))
    best = rows[0]
    best_target = str(best.get("target_key") or "")
    competitor = None
    for row in rows[1:]:
        if str(row.get("target_key") or "") != best_target:
            competitor = row
            break
    if competitor is not None and int(best.get("confidence") or 0) - int(competitor.get("confidence") or 0) < int(margin or 0):
        return None
    return best


def auto_allowed(service, source, epg, text_score=None, learned=False, native_proof=False, balanced=False):
    result = evaluate(service, source, epg, text_score=text_score, learned=learned, native_proof=native_proof, balanced=balanced)
    return bool(result.get("auto")), result
