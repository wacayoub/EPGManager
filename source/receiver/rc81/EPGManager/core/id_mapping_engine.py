# -*- coding: utf-8 -*-
"""EPG Manager Mapping V2 - direct receiver-name -> XMLTV channel-ID matcher.

This module intentionally replaces the layered Smart Mapping decision chain for
interactive Mapping Repair / Find Matches only.  It does not alter the native
EPG import pipeline or the receiver's DVB service references.

Design rules:
  * XMLTV channel identity is primary. Country/provider are ranking context,
    never allowed to turn a wrong channel name into a match.
  * Curated aliases are deterministic proof (e.g. Sharjah HD == Sharjah TV,
    2M National == 2M, Al Aoula Inter == Al Aoula).
  * Presentation tokens (HD/SD/TV/UHD) are ignored for identity.
  * Number/feed/family conflicts are fail-closed.
  * Exact/canonical matches may auto-map; fuzzy candidates are REVIEW only.
  * MENA receiver services never auto-map to a European catalogue merely
    because the same text exists there.

The API mirrors the tiny subset of precision_match_engine consumed by the UI:
``evaluate`` and ``choose_best``.
"""
from __future__ import print_function

import re
import unicodedata
from difflib import SequenceMatcher

from . import channel_identity_resolver, unified_channel_identity
from . import smart_context_match, smart_context_engine, source_quality
from . import canonical_bridge, precision_match_engine

AUTO_MIN = 930
AUTO_MARGIN = 20
REVIEW_MIN = 720

_MENA = set("MA DZ TN LY EG SA QA AE PS LB JO IQ KW BH OM YE SY MENA".split())
_EUROPE = set("FR ES IT DE PT UK GB IE BE NL CH AT GR TR RO BG HR RS SI SK CZ DK SE NO FI PL UA HU LU MT CY".split())

# Presentation only - never channel identity.
_DROP = set((
    "hd", "sd", "fhd", "uhd", "4k", "hdr", "hdr10", "hevc", "h265", "h264",
    "1080p", "2160p", "720p", "tv", "television", "channel", "digital", "feed",
    "backup", "test",
))

# These words often identify a *different* sibling in the same brand family.
# If one side has a semantic discriminator and the other does not, text-only
# matching is REVIEW/BLOCKED rather than automatic. Curated canonical identity
# is allowed to override this list because the operator explicitly declared the
# two names equivalent in srp_aliases.json.
_SEMANTIC = set((
    "english", "arabic", "french", "francais", "sports", "sport", "news",
    "quran", "koran", "sunnah", "documentary", "docs", "kids", "drama",
    "action", "bollywood", "max", "mood", "iraq", "masr", "egypt", "mena",
    "world", "monde", "national", "international", "inter", "plus", "one",
    "two", "three", "premier", "premiere", "racing", "movies", "movie",
    "cinema", "family", "music",
))

_GENERIC_SINGLE = set((
    "one", "two", "max", "news", "sport", "sports", "movies", "movie",
    "cinema", "music", "kids", "family", "action", "drama", "world",
))


def _fold(value):
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        pass
    text = text.replace("&", " and ")
    text = text.replace("+", " plus ")
    text = re.sub(r"[._/\\|:\-]+", " ", text)
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize(value):
    words = []
    for raw in _fold(value).split():
        if not raw or raw in _DROP:
            continue
        word = raw
        # XMLTV IDs frequently glue presentation suffixes to the brand
        # (SharjahTV.ae, AbuDhabiTV.ae, MBC2HD.sa). Strip only long obvious
        # suffixes; short real brands such as ITV/CTV stay untouched.
        low = word.casefold()
        if low == "osntv":
            word = "osn"; low = "osn"
        for suffix in ("uhd", "fhd", "hd"):
            if low.endswith(suffix) and len(low) >= len(suffix) + 4:
                word = word[:-len(suffix)]; low = word.casefold(); break
        if low.endswith("tv") and len(low) >= 7:
            word = word[:-2]
        if word and word not in _DROP:
            words.append(word)
    return " ".join(words)


def compact(value):
    return normalize(value).replace(" ", "")


def _candidate_values(epg):
    epg = epg or {}
    out = []
    seen = set()
    for value in [epg.get("display_name"), epg.get("match_name")]:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold()); out.append(text)
    cid = str(epg.get("channel_id") or "").strip()
    if cid:
        try:
            label = channel_identity_resolver.label_from_id(cid)
        except Exception:
            label = ""
        for value in (label, cid):
            text = str(value or "").strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold()); out.append(text)
    for field in ("search_aliases", "aliases"):
        for value in epg.get(field) or []:
            text = str(value or "").strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold()); out.append(text)
    return out


def _identity_ids(values):
    out = set()
    for value in values or []:
        try:
            iid = unified_channel_identity.canonical_id(value)
        except Exception:
            iid = ""
        if iid:
            out.add(str(iid).casefold())
    return out


def _source_countries(source):
    return set(str(x or "").upper() for x in ((source or {}).get("countries") or []) if str(x or "").strip())


def _source_region(source):
    countries = _source_countries(source)
    region = str((source or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region or "MIDDLE EAST" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return region




def _source_country_aliases(source):
    countries = _source_countries(source)
    out = set(countries)
    if "GB" in out:
        out.add("UK")
    if "UK" in out:
        out.add("GB")
    return out


def source_aware_id_label(channel_id, source=None):
    """Readable XMLTV ID with only the source's own country suffix removed.

    EPGShare IDs use endings such as ``.tr`` and ``.fr``. Globally stripping
    FR/EN is unsafe because those tokens can be feed identity, so removal is
    done only when the suffix is also the catalogue country.
    """
    try:
        label = str(channel_identity_resolver.label_from_id(channel_id) or channel_id).strip()
    except Exception:
        label = str(channel_id or "").strip()
    if not label:
        return label
    parts = label.split()
    countries = _source_country_aliases(source or {})
    if parts and parts[-1].upper() in countries:
        parts.pop()
    return " ".join(parts).strip() or label


def _bein_feed(value):
    """Return (language, number) for beIN SPORTS numbered feeds."""
    n = normalize(value)
    c = compact(value)
    if "bein" not in c or "sport" not in c:
        return ("", 0)
    lang = ""
    if re.search(r"(?:^| )(?:fr|french|francais)(?: |$)", n) or "sportsfr" in c or "sportsfrench" in c:
        lang = "fr"
    elif re.search(r"(?:^| )(?:en|eng|english)(?: |$)", n) or "sportsen" in c or "sportsenglish" in c:
        lang = "en"
    m = re.search(r"(?:sports|sport)(?:fr|french|francais|en|eng|english)?0?(\d{1,2})", c)
    if not m:
        m = re.search(r"(?:sports|sport)0?(\d{1,2})(?:fr|french|francais|en|eng|english)", c)
    try:
        num = int(m.group(1)) if m else 0
    except Exception:
        num = 0
    return (lang, num)


def _source_feed_language(source):
    lang = _source_language(source)
    if lang in ("fr", "en"):
        return lang
    countries = _source_countries(source)
    if "FR" in countries:
        return "fr"
    # Do not infer English merely from a UK/US country. Many international
    # catalogues carry multilingual variants; explicit source language is safer.
    return ""


def source_qualified_aliases(source, epg):
    """Synthetic exact aliases justified by source metadata, not fuzzy text.

    A French catalogue often publishes ``beIN.SPORTS.1.fr`` instead of
    ``beIN SPORTS FR 1``; an English Qatar shard may similarly publish generic
    ``beIN SPORTS 1``. These aliases let exact-only Auto Repair discover the
    correct language feed without making generic beIN 1 equivalent globally.
    """
    epg = epg or {}
    cid = str(epg.get("channel_id") or "")
    label = source_aware_id_label(cid, source)
    values = [label, epg.get("display_name"), epg.get("match_name")]
    feed_lang = _source_feed_language(source)
    out = []
    for value in values:
        lang, num = _bein_feed(value)
        if num and not lang and feed_lang in ("fr", "en"):
            alias = "beIN SPORTS %s %d" % (feed_lang.upper(), num)
            if alias not in out:
                out.append(alias)
    return out


def _receiver_context(service):
    try:
        ctx = smart_context_engine.service_context(service or {}) or {}
    except Exception:
        ctx = {}
    try:
        profile = smart_context_match.receiver_profile(service or {}) or {}
    except Exception:
        profile = {}
    region = str(ctx.get("region") or "").upper()
    country = str(ctx.get("country") or profile.get("country_hint") or "").upper()
    language = str(ctx.get("language") or profile.get("language_hint") or "").lower()
    return {"region": region, "country": country, "language": language}


def _source_language(source):
    value = str((source or {}).get("language") or "").lower().strip()
    return value.split("-", 1)[0].split("_", 1)[0]


def _numbers(value):
    return tuple(re.findall(r"(?<![a-z])\d+(?![a-z])", normalize(value)))


def _semantic(value):
    words = set(x for x in normalize(value).split() if x in _SEMANTIC)
    # XMLTV IDs often glue semantic feed words (SharjahQuran, AbuDhabiSports).
    # Recover only strong, sufficiently long discriminators from the compact
    # form; do not substring-match generic inter/world/national tokens.
    c = compact(value)
    for token in ("english", "arabic", "french", "sports", "sport", "news", "quran",
                  "sunnah", "documentary", "kids", "drama", "action", "bollywood",
                  "mood", "iraq", "masr", "egypt", "racing", "movies", "cinema"):
        if token in c:
            words.add(token)
    return words


def _distinctive_exact(value):
    words = normalize(value).split()
    if not words:
        return False
    c = "".join(words)
    if len(c) < 3:
        return False
    if len(words) == 1 and words[0] in _GENERIC_SINGLE:
        return False
    return True


def _best_text(receiver, values):
    rn = normalize(receiver)
    rc = compact(receiver)
    best = (0, "", False, False)
    for value in values or []:
        cn = normalize(value)
        cc = compact(value)
        if not cn:
            continue
        exact = bool(rn and rn == cn)
        compact_exact = bool(rc and rc == cc)
        if exact:
            score = 100
        elif compact_exact:
            score = 99
        else:
            ratio = int(round(100.0 * SequenceMatcher(None, rn, cn).ratio())) if rn and cn else 0
            a = set(rn.split()); b = set(cn.split())
            jacc = int(round(100.0 * len(a & b) / len(a | b))) if (a | b) else 0
            contains = 94 if rn and cn and (rn in cn or cn in rn) else 0
            score = max(ratio, jacc, contains)
        if score > best[0]:
            best = (score, value, exact, compact_exact)
    return best


def _result(status, confidence, reason, target_key="", auto=False, review=False, proofs=None, risks=None):
    return {
        "allowed": status != "BLOCKED",
        "auto": bool(auto),
        "review": bool(review),
        "grade": status,
        "precision_grade": status,
        "confidence": max(0, min(1000, int(confidence or 0))),
        "score": max(0, min(100, int(round(float(confidence or 0) / 10.0)))),
        "reason": str(reason or status),
        "target_key": str(target_key or ""),
        "proofs": list(proofs or []),
        "risks": list(risks or []),
        "verified": bool(auto),
    }


def evaluate(service, source, epg, text_score=None, learned=False, native_proof=False, balanced=True):
    """Evaluate receiver service -> XMLTV row with Mapping V2 rules.

    The XMLTV ``channel_id`` is the first identity signal.  Its readable label
    is checked before display-name aliases.  A display label can therefore
    never "rescue" an XMLTV ID that is known to belong to a different sibling.
    This is the core beta143 rule: ID proves identity; source metadata only
    ranks an identity that has already been proven.
    """
    service = service or {}; source = source or {}; epg = epg or {}
    receiver = str(service.get("clean_name") or service.get("name") or "").strip()
    cid = str(epg.get("channel_id") or "").strip()
    if not receiver or not cid:
        return _result("BLOCKED", 0, "missing receiver/XMLTV channel identity")

    # 8.1.8: an XMLTV <channel> declaration is not enough to make an EPG ID
    # safe. Only a COMPLETE cached quality profile may prove that this exact ID
    # has zero programme rows. Unknown/partially sampled feeds are never marked
    # empty. Keep the row manually reviewable, but never auto-map it.
    try:
        no_programmes = bool(source_quality.confirmed_no_programmes(
            epg.get("source_id") or source.get("source_id") or source.get("id"), cid))
    except Exception:
        no_programmes = False

    if learned:
        return _result("PROVEN", 1000, "operator-confirmed mapping", "learned:%s:%s" % (
            str((source or {}).get("source_id") or ""), cid), auto=True,
            proofs=["manual-learning"])

    # rc44: EPG-Scrapers direct catalogues are source-scoped, validated XMLTV
    # inventories. Reuse the exact same PrecisionMatch gate as SRP Master so
    # interactive Smart Mapping cannot reject an ID that the deterministic SRP
    # builder has already proven. The raw provider ID may contain transport/
    # scraper tokens (sport24.adsports.1, *Action, country suffixes) that are not
    # the receiver channel name; the source-scoped display/canonical bridge is
    # the authoritative match label. Zero-program IDs remain fail-closed.
    direct_validated = bool(source.get("direct_feed")) and str(source.get("provider") or "").upper() == "EPG SCRAPERS"
    if direct_validated:
        if no_programmes:
            return _result("NO EPG", 900, "direct XMLTV ID has no future programme rows",
                           "xmltv:%s" % (compact(cid) or normalize(cid) or cid.casefold()),
                           auto=False, review=True, proofs=["direct-source-id"],
                           risks=["no-programmes"])
        direct_epg = dict(epg)
        if not str(direct_epg.get("match_name") or "").strip():
            override = canonical_bridge.source_override(cid)
            aliases = canonical_bridge.aliases(cid, direct_epg.get("display_name") or "")
            direct_epg["match_name"] = override or (aliases[0] if aliases else
                                                       str(direct_epg.get("display_name") or cid))
        verdict = dict(precision_match_engine.evaluate(
            service, source, direct_epg, text_score=text_score,
            learned=False, native_proof=native_proof, balanced=balanced) or {})
        verdict.setdefault("allowed", verdict.get("grade") != "BLOCKED")
        verdict.setdefault("review", not bool(verdict.get("auto")))
        verdict["verified"] = bool(verdict.get("auto"))
        verdict.setdefault("precision_grade", verdict.get("grade") or "BLOCKED")
        return verdict

    id_label = source_aware_id_label(cid, source)

    # Keep XMLTV ID evidence separate from display-name evidence.  Older Smart
    # Mapping merged them into one alias bag, so a misleading display name could
    # hide the fact that the real ID belonged to another numbered/feed sibling.
    display_values = []
    seen_display = set()
    for value in [epg.get("display_name"), epg.get("match_name")]:
        text = str(value or "").strip()
        if text and text.casefold() not in seen_display:
            seen_display.add(text.casefold()); display_values.append(text)
    for field in ("search_aliases", "aliases"):
        for value in epg.get(field) or []:
            text = str(value or "").strip()
            if text and text.casefold() not in seen_display:
                seen_display.add(text.casefold()); display_values.append(text)

    receiver_ids = _identity_ids([receiver])
    id_ids = _identity_ids([id_label])
    display_ids = _identity_ids(display_values)
    common_id_ids = receiver_ids & id_ids
    common_display_ids = receiver_ids & display_ids

    # Source-qualified beIN language feed. The raw XMLTV ID may be generic
    # (beIN.SPORTS.1.fr / beIN SPORTS 1), while the source itself proves FR/EN.
    # This proof is deliberately one-way: a generic receiver beIN 1 never becomes
    # FR/EN merely because such a source exists.
    source_bein_proof = False
    r_bein_lang, r_bein_num = _bein_feed(receiver)
    c_bein_lang, c_bein_num = _bein_feed(id_label)
    if r_bein_lang in ("fr", "en") and r_bein_num and c_bein_num == r_bein_num:
        if not c_bein_lang and _source_feed_language(source) == r_bein_lang:
            source_bein_proof = True

    # A resolvable XMLTV ID is authoritative.  Example: an accidental row with
    # channel_id=MBC3.sa but display_name="MBC 2" must NEVER map to MBC 2.
    if receiver_ids and id_ids and not common_id_ids and not source_bein_proof:
        return _result("BLOCKED", 0, "XMLTV ID belongs to a different curated channel",
                       risks=["xmltv-id-conflict"])
    if receiver_ids and not id_ids and display_ids and not common_display_ids:
        return _result("BLOCKED", 0, "display identity belongs to a different curated channel",
                       risks=["identity-conflict"])

    id_score, _id_value, id_exact, id_compact_exact = _best_text(receiver, [id_label])
    disp_score, disp_value, disp_exact, disp_compact_exact = _best_text(receiver, display_values)

    # If the raw ID is readable but not curated, protect numbered/feed siblings
    # before looking at display text. This catches MBC2/MBC3 and similar IDs even
    # when the provider publishes a bad or generic display label.
    if not common_id_ids and not source_bein_proof:
        rnums, inums = _numbers(receiver), _numbers(id_label)
        if rnums and inums and rnums != inums:
            return _result("BLOCKED", 0, "XMLTV ID numbered sibling conflict",
                           risks=["xmltv-number-conflict"])
        rsem, isem = _semantic(receiver), _semantic(id_label)
        strong = set(("english","arabic","french","sports","sport","news","quran","sunnah",
                      "documentary","kids","drama","action","bollywood","max","mood","iraq",
                      "masr","egypt","racing","movies","movie","cinema"))
        # Only enforce semantic ID words when the label has useful alphabetic
        # content. Opaque numeric/hash IDs fall through to the display-name path.
        if any(ch.isalpha() for ch in normalize(id_label)) and (rsem & strong) != (isem & strong):
            # A readable ID feed word is authoritative even when most of the
            # brand text is shared (Sharjah vs SharjahQuran is ~94% by contains).
            return _result("BLOCKED", 0, "XMLTV ID feed/family conflict",
                           risks=["xmltv-semantic-conflict"])

    # Identity proof order: actual XMLTV ID -> curated display alias -> exact ID
    # text -> exact display text.  Fuzzy text never auto-maps.
    proof_kind = ""
    target_id = ""
    confidence = 0
    if common_id_ids:
        target_id = sorted(common_id_ids)[0]
        confidence = 995
        proof_kind = "same-xmltv-id-identity"
    elif source_bein_proof:
        target_id = "bein_sports_%s_%d" % (r_bein_lang, r_bein_num)
        confidence = 985
        proof_kind = "source-qualified-bein-feed"
    elif common_display_ids:
        target_id = sorted(common_display_ids)[0]
        confidence = 980
        proof_kind = "same-curated-display-identity"
    elif (id_exact or id_compact_exact) and _distinctive_exact(receiver):
        confidence = 970 if id_exact else 965
        proof_kind = "exact-xmltv-id-name"
    elif (disp_exact or disp_compact_exact) and _distinctive_exact(receiver):
        confidence = 950 if disp_exact else 945
        proof_kind = "exact-display-name"
    else:
        best_score = max(int(id_score or 0), int(disp_score or 0))
        best_value = id_label if int(id_score or 0) >= int(disp_score or 0) else disp_value
        # Discovery only. Guard siblings using whichever candidate text won.
        rnums, cnums = _numbers(receiver), _numbers(best_value)
        if rnums and cnums and rnums != cnums:
            return _result("BLOCKED", 0, "numbered channel sibling conflict",
                           risks=["number-conflict"])
        rsem, csem = _semantic(receiver), _semantic(best_value)
        strong = set(("english","arabic","french","sports","sport","news","quran","sunnah",
                      "documentary","kids","drama","action","bollywood","max","mood","iraq",
                      "masr","egypt","racing","movies","movie","cinema"))
        if (rsem & strong) != (csem & strong):
            return _result("BLOCKED", 0, "channel feed/family conflict",
                           risks=["semantic-conflict"])
        if best_score < 72:
            return _result("BLOCKED", best_score * 10, "name/ID evidence too weak",
                           risks=["weak-name"])
        conf = min(899, 690 + best_score * 2)
        if no_programmes:
            return _result("NO EPG", conf, "XMLTV ID exists but has no programme rows",
                           "name:%s" % (normalize(receiver) or compact(receiver)),
                           review=True, proofs=["text-similarity-%d" % best_score],
                           risks=["no-programmes"])
        return _result("REVIEW", conf, "close XMLTV ID/name - confirm manually",
                       "name:%s" % (normalize(receiver) or compact(receiver)),
                       review=True, proofs=["text-similarity-%d" % best_score])

    ctx = _receiver_context(service)
    src_region = _source_region(source)
    # Only a broad region mismatch is a hard source firewall. Country/provider
    # containers cannot veto a proven channel ID inside MENA (e.g. a UAE channel
    # carried in a Saudi catalogue), and they can never create a match either.
    if ctx.get("region") in ("MENA", "EUROPE") and src_region in ("MENA", "EUROPE") and ctx["region"] != src_region:
        if proof_kind != "source-qualified-bein-feed":
            return _result("BLOCKED", 0, "%s receiver rejects %s catalogue" % (ctx["region"], src_region),
                           risks=["region-conflict"])

    source_id = str((source or {}).get("source_id") or (source or {}).get("id") or "")
    # beta147: duplicate provider rows that prove the *same exact receiver name*
    # are one mapping identity even when providers use different raw XMLTV IDs.
    # The old xmltv:<raw-id> key made MBC/Abu Dhabi/etc. look ambiguous simply
    # because two valid catalogues named their ID differently. Number/feed
    # sibling conflicts have already failed closed above, so an exact name key is
    # safe here and lets source context choose the best provider.
    if target_id:
        target_key = "id:%s" % target_id
    elif proof_kind in ("exact-xmltv-id-name", "exact-display-name"):
        target_key = "name:%s" % (compact(receiver) or normalize(receiver) or receiver.casefold())
    else:
        target_key = "xmltv:%s" % (compact(id_label) or normalize(id_label) or cid.casefold())
    proofs = [proof_kind]
    bonus = 0

    countries = _source_countries(source)
    identity_country = ""
    if target_id:
        try:
            row = unified_channel_identity.resolve(receiver) or {}
            identity_country = str(row.get("country") or "").upper()
        except Exception:
            identity_country = ""
    if identity_country and identity_country in countries:
        bonus += 5; proofs.append("identity-country")
    elif countries and ctx.get("country") and str(ctx.get("country")) in countries:
        bonus += 4; proofs.append("receiver-country")
    elif ctx.get("region") and src_region and ctx.get("region") == src_region:
        bonus += 3; proofs.append("same-region")

    src_lang = _source_language(source)
    svc_lang = str(ctx.get("language") or "").lower()
    if svc_lang and src_lang and svc_lang == src_lang:
        bonus += 2; proofs.append("same-language")

    provider = str((source or {}).get("provider") or "").upper()
    if provider in ("EPG MANAGER", "OPENEPG", "RYTEC"):
        bonus += 2
    elif provider == "EPGSHARE":
        bonus += 1

    confidence = min(1000, confidence + bonus)
    grade = "PROVEN" if proof_kind.startswith("same-") else "SAFE"
    if no_programmes:
        return _result("NO EPG", confidence, "XMLTV ID exists but has no programme rows",
                       target_key, auto=False, review=True, proofs=proofs,
                       risks=["no-programmes"])
    return _result(grade, confidence,
                   "XMLTV channel ID proves same channel" if proof_kind.startswith("same-xmltv") else
                   ("same curated channel identity" if proof_kind.startswith("same-curated") else
                    "exact XMLTV channel identity"),
                   target_key, auto=True, proofs=proofs)

def choose_best(results, margin=AUTO_MARGIN):
    """Choose one automatic row; duplicate sources for the same identity compete by score.

    Ambiguity is only between *different* target identities. Multiple catalogues
    carrying the same canonical channel are not considered a mapping conflict.
    """
    rows = [dict(x or {}) for x in (results or []) if (x or {}).get("auto", True)]
    if not rows:
        return None
    rows.sort(key=lambda r: (-int(r.get("confidence") or 0), str(r.get("target_key") or ""), str(r.get("source_id") or "")))
    best = rows[0]
    best_target = str(best.get("target_key") or "")
    competitor = None
    for row in rows[1:]:
        if str(row.get("target_key") or "") != best_target:
            competitor = row; break
    if competitor is not None and int(best.get("confidence") or 0) - int(competitor.get("confidence") or 0) < int(margin or 0):
        return None
    return best
