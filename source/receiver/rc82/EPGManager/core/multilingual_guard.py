# -*- coding: utf-8 -*-
"""Cheap language/provider firewall for Smart Mapping.

This is intentionally UI/search-only.  It never writes SRP maps and never
changes the frozen deterministic SAT mapping engine.  The goal is to reject the
most dangerous class of false positive before fuzzy ranking: same brand/number,
wrong language/region (for example beIN SPORTS 1 MENA vs FR 1 / EN 1).
"""
from __future__ import print_function
import re

from . import programme_feed_policy


def _norm(value):
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold())).strip()


def _source_lang(source):
    text = str((source or {}).get("language") or "").lower().replace("_", "-")
    return text.split("-", 1)[0]


def _receiver_lang(service):
    service = service or {}
    name = _norm(service.get("clean_name") or service.get("name") or "")
    explicit = str(service.get("explicit_region") or "").upper()
    if re.search(r"\bbein\s+sports\s+fr\b", name) or re.search(r"\bfr\s+\d+\b", name):
        return "fr"
    if re.search(r"\bbein\s+sports\s+en\b", name) or re.search(r"\ben\s+\d+\b", name):
        return "en"
    if explicit == "MENA":
        return "ar"
    raw = str(service.get("name") or "")
    m = re.search(r"(?:^|[|\[(_ -])(AR|ARA|MENA|FR|FRA|EN|ENG)(?:$|[|\])_ -])", raw, re.I)
    if m:
        tag = m.group(1).lower()
        return {"ara":"ar", "mena":"ar", "fra":"fr", "eng":"en"}.get(tag, tag)
    return ""


def _row_lang(source, epg):
    source = source or {}; epg = epg or {}
    lang = _source_lang(source)
    text = _norm("%s %s %s %s" % (
        source.get("source_id") or source.get("id") or "",
        source.get("source_name") or source.get("name") or "",
        epg.get("display_name") or "", epg.get("channel_id") or ""))
    # Explicit row/source markers beat generic source metadata.
    if re.search(r"\bbein\s+sports\s+fr\b", text) or re.search(r"\bfr\s+\d+\b", text):
        return "fr"
    if re.search(r"\bbein\s+sports\s+en\b", text) or re.search(r"\ben\s+\d+\b", text):
        return "en"
    # rc44 direct catalogues can mix channel languages inside one provider.
    # Only explicit language words are used here, so an ordinary Arabic MBC
    # row is not silently reclassified.
    if re.search(r"\b(?:english|eng)\b", text):
        return "en"
    if re.search(r"\b(?:french|francais|fra)\b", text):
        return "fr"
    if re.search(r"\b(?:arabic|arabe|ara)\b", text):
        return "ar"
    return lang




def _mbc_identity(value):
    """Return a stable MBC family identity for exact cross-language guide rows.

    OpenEPG's Saudi shards may expose the same MBC channel in parallel AR/EN
    programme-language feeds.  The language flag describes guide text, not a
    different MBC broadcast.  We therefore allow an exact MBC identity across
    those Saudi/MENA sibling shards, while still blocking MBC 2 -> MBC 3 etc.
    """
    text = _norm(value)
    if not text:
        return ""
    # Strip common XMLTV country/quality suffixes after normalisation.
    text = re.sub(r"\b(?:sa|mena|ar|en|hd|fhd|uhd|4k|sd)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"\bmbc\s*([1-5])\b", text)
    if m:
        return "mbc_%s" % m.group(1)
    for token, ident in (("action", "mbc_action"), ("bollywood", "mbc_bollywood"),
                         ("drama", "mbc_drama"), ("max", "mbc_max"),
                         ("mood", "mbc_mood"), ("iraq", "mbc_iraq"),
                         ("masr 2", "mbc_masr_2"), ("masr", "mbc_masr")):
        if re.search(r"\bmbc\s*%s\b" % re.escape(token), text):
            return ident
    return ""


def _same_mbc_sa_identity(service, source, epg):
    countries = set(str(x or "").upper() for x in ((source or {}).get("countries") or []))
    if not countries.intersection(("SA", "MENA")):
        return False
    receiver = _mbc_identity((service or {}).get("clean_name") or (service or {}).get("name") or "")
    if not receiver:
        return False
    candidate = _mbc_identity("%s %s" % ((epg or {}).get("display_name") or "", (epg or {}).get("channel_id") or ""))
    return bool(candidate and candidate == receiver)

def _is_bein_sports(service):
    name = _norm((service or {}).get("clean_name") or (service or {}).get("name") or "")
    return "bein" in name and "sport" in name


def check(service, source, epg=None, receiver_context=None):
    """Return ``(allowed, reason)`` using only cheap metadata.

    The guard is conservative: an unknown language is not rejected.  It rejects
    only explicit contradictions, so legitimate mixed-language catalogues remain
    available for manual review.
    """
    service = service or {}; source = source or {}; epg = epg or {}
    rlang = _receiver_lang(service)
    slang = _row_lang(source, epg)

    if rlang in ("ar", "fr", "en") and slang in ("ar", "fr", "en") and rlang != slang:
        # MBC Saudi/MENA parallel shards are programme-language variants of the
        # same broadcast. Exact MBC identity may safely cross AR/EN here; this
        # exception is deliberately narrow and does not apply to beIN/CNN/etc.
        if not _same_mbc_sa_identity(service, source, epg):
            return False, "language mismatch %s→%s" % (rlang.upper(), slang.upper())

    if _is_bein_sports(service):
        name = _norm(service.get("clean_name") or service.get("name") or "")
        src_text = _norm("%s %s %s %s" % (
            source.get("source_id") or source.get("id") or "",
            source.get("source_name") or source.get("name") or "",
            epg.get("display_name") or "", epg.get("channel_id") or ""))
        receiver_fr = bool(re.search(r"\bbein\s+sports\s+fr\b", name))
        receiver_en = bool(re.search(r"\bbein\s+sports\s+en\b", name))
        candidate_fr = bool(re.search(r"\bbein\s+sports\s+fr\b", src_text) or "france" in src_text)
        candidate_en = bool(re.search(r"\bbein\s+sports\s+en\b", src_text) or "english" in src_text)
        if receiver_fr and candidate_en:
            return False, "beIN FR/EN collision"
        if receiver_en and candidate_fr:
            return False, "beIN EN/FR collision"
        if not receiver_fr and not receiver_en:
            # Untagged beIN SPORTS on a MENA receiver must never auto-follow an
            # explicitly France/English candidate.  Unknown/mixed rows can still
            # be shown for manual review by higher layers.
            ctx = receiver_context or {}
            mena = str(ctx.get("region") or "").upper() == "MENA" or str(ctx.get("language") or "").lower() == "ar"
            if mena and (candidate_fr or candidate_en or slang in ("fr", "en")):
                return False, "beIN MENA language firewall"

    route_ok, _route_score, route_reason = programme_feed_policy.route(service, source, epg)
    if not route_ok:
        return False, route_reason
    return True, "language/provider compatible"
