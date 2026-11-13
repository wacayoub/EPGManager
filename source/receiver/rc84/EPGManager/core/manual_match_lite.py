# -*- coding: utf-8 -*-
"""Low-CPU candidate discovery for interactive Smart Mapping.

This module is deliberately smaller than the automatic mapping stack.  It borrows
three rules that worked well in the IPTV->SAT matching project:

1. normalise once, then use indexed exact/canonical keys;
2. preserve family/number/language discriminators so siblings never collapse;
3. use provider/country/language as ranking context, not as permission to turn a
   wrong channel into a match.

It never performs network I/O and never parses programme XML.  It consumes the
persistent Smart-name index already prepared by EPGManager and returns a very
small candidate set for human-in-the-loop Smart Mapping.
"""
from __future__ import print_function

import re
import unicodedata

from . import id_mapping_engine, unified_channel_identity, smart_context_engine, smartmatch_ai, fast_name_key, source_variant_policy

_NOISE = set((
    "hd", "sd", "fhd", "uhd", "4k", "hdr", "tv", "television", "channel",
    "backup", "feed", "test", "hevc", "h265", "h264", "1080p", "2160p",
))
_GENERIC = set((
    "tv", "channel", "sport", "sports", "news", "movie", "movies", "cinema",
    "kids", "music", "plus", "one", "two", "three", "world", "international",
))
_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_EQUIV_WORDS = {
    "arabic": "arabi", "arabe": "arabi", "kurdish": "kurdi", "kurde": "kurdi",
    "sports": "sport", "national": "nat", "international": "inter",
    "francais": "french", "english": "en", "french": "fr",
}

# Safe brand/presentation expansions learned from real manual corrections.
# They are discovery-only.  Canonical identity + Mapping V6 must still approve
# the candidate, so these cannot turn a sibling channel into an auto-match.
_PHRASE_VARIANTS = (
    (r"^ad\s+sports?\b", "abu dhabi sport"),
    (r"^bbc\s+arabic\s+news$", "bbc news arabic"),
    (r"^bbc\s+news\s+arabic$", "bbc arabic news"),
    (r"^cnn(?:\s+hd)?$", "cnn international"),
    (r"^blomberg(?:\s*1)?$", "bloomberg"),
)


def _fold(value):
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        pass
    text = text.replace("&", " and ").replace("+", " plus ")
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def manual_norm(value):
    # One shared key is used by source-index build and receiver-registry build.
    return fast_name_key.normalize(value)


def manual_compact(value):
    return fast_name_key.compact(value)


def _variants(name):
    out = []
    seen = set()

    def add(value):
        value = str(value or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)

    add(name)
    try:
        for value in unified_channel_identity.search_variants(name):
            add(value)
        row = unified_channel_identity.resolve(name) or {}
        for value in row.get("aliases") or []:
            add(value)
    except Exception:
        pass

    # Manual discovery may broaden presentation synonyms, but only to FIND a
    # candidate.  id_mapping_engine remains the authority that decides whether
    # the candidate is SAFE/PROVEN/REVIEW/BLOCKED.
    base = list(out)
    for value in base:
        n = manual_norm(value)
        if n:
            add(n)
            for old, new in _EQUIV_WORDS.items():
                if re.search(r"(?:^| )%s(?: |$)" % re.escape(old), n):
                    add(re.sub(r"(?:^| )%s(?: |$)" % re.escape(old), " %s " % new, n).strip())
            for pattern, repl in _PHRASE_VARIANTS:
                if re.search(pattern, n):
                    add(re.sub(pattern, repl, n).strip())
    return out[:40]


def _index_parts(index):
    """Return resident index containers without copying them.

    8.1.3 hot-path invariant: this helper is called once per visible receiver
    row while painting suggestion state. Copying the complete ``rows`` list and
    ``buckets`` dict here turns an O(1) membership test into an O(N) allocation
    for every channel and was the cause of the 8.1.2 satellite-switch regression.
    The Smart index is published as an immutable snapshot, so read-only callers
    can safely keep direct references.
    """
    if not isinstance(index, dict) or int(index.get("_format") or 0) != 2:
        return (), {}
    rows = index.get("rows") or ()
    buckets = index.get("buckets") or {}
    return rows, buckets


def candidate_rows(service, index, limit=32):
    """Return a tiny candidate pool without scanning the whole catalogue."""
    service = service or {}
    name = str(service.get("clean_name") or service.get("name") or "").strip()
    rows, buckets = _index_parts(index)
    if not name or not rows or not buckets:
        return []

    positions = []
    seen = set()

    def add_bucket(key, cap=64):
        for pos in (buckets.get(key) or [])[:cap]:
            try:
                pos = int(pos)
            except Exception:
                continue
            if 0 <= pos < len(rows) and pos not in seen:
                seen.add(pos)
                positions.append(pos)

    variants = _variants(name)
    identity_ids = []
    for value in variants:
        try:
            iid = str(unified_channel_identity.canonical_id(value) or "")
        except Exception:
            iid = ""
        if iid and iid not in identity_ids:
            identity_ids.append(iid)
    for iid in identity_ids[:8]:
        add_bucket("@" + iid, 64)

    # ID-first exact/canonical buckets already used by Mapping V6.
    for value in variants[:24]:
        try:
            vn = id_mapping_engine.normalize(value)
            vc = id_mapping_engine.compact(value)
        except Exception:
            vn = vc = ""
        if vn:
            add_bucket("V=" + vn, 64)
        if vc:
            add_bucket("V~" + vc, 64)
        mn = manual_norm(value)
        if mn:
            add_bucket("=" + mn, 64)

    # IPTV->SAT style token-vote fallback: no global fuzzy scan.  Only a few
    # discriminating tokens are intersected against bounded index buckets.
    if len(positions) < 6:
        tokens = []
        for value in variants[:12]:
            for token in manual_norm(value).split():
                if token in _GENERIC:
                    continue
                if (len(token) >= 3 or token.isdigit()) and token not in tokens:
                    tokens.append(token)
        votes = {}
        for token in tokens[:6]:
            for prefix in ("V#", "#"):
                for pos in (buckets.get(prefix + token) or [])[:96]:
                    try:
                        pos = int(pos)
                    except Exception:
                        continue
                    votes[pos] = votes.get(pos, 0) + 1
        # Require stronger support when the receiver name has several useful
        # tokens; this avoids turning MBC/Abu Dhabi/beIN siblings into noise.
        min_votes = 2 if len(tokens) >= 2 else 1
        for pos, count in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
            if count < min_votes:
                continue
            if 0 <= pos < len(rows) and pos not in seen:
                seen.add(pos)
                positions.append(pos)
            if len(positions) >= int(limit or 32):
                break

    # RC14 multilingual sibling recovery.  Once one prepared AR/EN variant is
    # discovered, include the exact same XMLTV-ID row from its declared parallel
    # language shard.  This restores the rich Arabic IDs that Source Override
    # used to expose, while remaining index-only and O(1).  Only curated
    # source_variant_policy siblings are eligible, so unrelated providers that
    # happen to reuse an ID are never pulled in.
    seed_positions = list(positions[:max(1, int(limit or 32))])
    for pos in seed_positions[:32]:
        try:
            base = rows[pos] or {}
            sid = str(base.get("source_id") or "")
            cid = str(base.get("channel_id") or "").strip()
            sibling = source_variant_policy.sibling(sid)
            if not sibling or not cid:
                continue
            for other_pos in (buckets.get("C=" + cid.casefold()) or [])[:16]:
                other_pos = int(other_pos)
                if other_pos in seen or not (0 <= other_pos < len(rows)):
                    continue
                other = rows[other_pos] or {}
                if str(other.get("source_id") or "") != sibling:
                    continue
                seen.add(other_pos)
                positions.append(other_pos)
        except Exception:
            continue

    return [dict(rows[pos] or {}) for pos in positions[:max(1, int(limit or 32))]]


def rank(service, index, source_lookup, active_source_ids=None, limit=8, include_review=True, learned_target=None):
    """Rank only indexed candidates; never read disk/network or programme XML."""
    active = set(str(x) for x in (active_source_ids or []) if x)
    ranked = []
    for epg in candidate_rows(service, index, limit=40):
        sid = str(epg.get("source_id") or "")
        try:
            src = source_lookup(sid) or {}
        except Exception:
            src = {}
        if not src:
            continue
        learned = False
        if learned_target:
            learned = (str(learned_target.get("source_id") or "").casefold() == sid.casefold() and
                       str(learned_target.get("channel_id") or "").casefold() == str(epg.get("channel_id") or "").casefold())
        try:
            verdict = id_mapping_engine.evaluate(service, src, epg, learned=learned)
        except Exception:
            continue
        if not verdict.get("allowed"):
            continue
        if not verdict.get("auto") and not include_review:
            continue
        try:
            context_rank, _label = smart_context_engine.rank_source(service, src,
                str((unified_channel_identity.resolve(service.get("name") or "") or {}).get("id") or ""))
        except Exception:
            context_rank = 9
        ranked.append({
            "source_id": sid,
            "source": src,
            "epg": epg,
            "grade": str(verdict.get("grade") or "REVIEW"),
            "auto": bool(verdict.get("auto")),
            "confidence": int(verdict.get("confidence") or 0),
            "reason": str(verdict.get("reason") or ""),
            "target_key": str(verdict.get("target_key") or ""),
            "context_rank": int(context_rank),
            "active": bool(not active or sid in active),
        })
    ranked.sort(key=lambda x: (
        0 if x.get("auto") else 1,
        0 if x.get("active") else 1,
        -int(x.get("confidence") or 0),
        int(x.get("context_rank") or 9),
        str((x.get("epg") or {}).get("display_name") or (x.get("epg") or {}).get("channel_id") or "").casefold(),
    ))
    return ranked[:max(1, int(limit or 8))]


def has_suggestion(service, index, source_lookup, active_source_ids=None):
    """Cheap boolean used only to colour the receiver list yellow.

    This hot path deliberately skips operator-learning deep copies and source
    re-ranking.  Any ID-first SAFE/PROVEN/REVIEW candidate is enough to paint a
    yellow hint; the exact ordering is calculated only when the user presses
    GREEN/RIGHT.
    """
    active = set(str(x) for x in (active_source_ids or []) if x)
    fallback = False
    for epg in candidate_rows(service, index, limit=20):
        sid = str(epg.get("source_id") or "")
        try:
            src = source_lookup(sid) or {}
        except Exception:
            src = {}
        if not src:
            continue
        try:
            verdict = id_mapping_engine.evaluate(service, src, epg, learned=False)
        except Exception:
            continue
        if not verdict.get("allowed"):
            continue
        if not active or sid in active:
            return True
        fallback = True
    return fallback


def has_prepared_hint_fast(service, index):
    """O(1) paint hint for Reception/Satellite channel colouring.

    8.1.2: receiver rows already carry ``fast_match_key`` from the persistent
    channel registry. The Smart index uses the exact same key in its ``=``
    bucket, so changing satellites does not run normalize/compact/identity on
    every channel. If an old/non-registry row lacks the key, one lightweight
    normalization is used as a compatibility fallback.
    """
    service = service or {}
    _rows, buckets = _index_parts(index)
    if not buckets:
        return False
    key = str(service.get("fast_match_key") or "").strip()
    if not key:
        name = str(service.get("clean_name") or service.get("name") or "").strip()
        if not name:
            return False
        key = fast_name_key.normalize(name)
    return bool(key and buckets.get("=" + key))
