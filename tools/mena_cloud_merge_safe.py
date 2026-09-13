#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accuracy wrapper for the MENA merge.

Hard rules added on top of mena_cloud_merge:
- TV only (including concatenated Radio aliases);
- normalize common MBC/beIN/ADM/Al Kass aliases without collapsing real variants;
- ONE programme timeline per logical channel: alternate feeds are metadata-only;
- reject invalid/placeholder/absurd-duration events before publication;
- resolve residual overlaps conservatively;
- premium beIN/OSN keeps English title + Arabic description when available;
- Arabic providers prefer Arabic title + Arabic description;
- never let an alternate feed replace the chosen timeline timestamps.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import re

import mena_cloud_merge as base

_original_is_radio = base.is_radio
_original_logical_key = base.logical_key


def is_non_tv(cid, name):
    probe = "%s %s" % (cid or "", name or "")
    folded = probe.casefold()
    if _original_is_radio(cid, name):
        return True
    if "radio" in folded:
        return True
    if re.search(r"(?:^|[\W_])(?:pulse\s*95|ofm)(?:[\W_]|$)", folded):
        return True
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()
    if cleaned in {"sat tv"}:
        return True
    return False


base.is_radio = is_non_tv


def _compact(value):
    value = (value or "").casefold()
    value = re.sub(r"\.(?:ae|sa|qa|eg|bh|kw|om|jo|lb|iq|ps|ye|mena|bein)(?:@.*)?$", "", value, flags=re.I)
    value = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|digital|mono)\b", " ", value, flags=re.I)
    value = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


_NUM_WORD = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def _number_words(value):
    bits = value.split()
    return " ".join(_NUM_WORD.get(x, x) for x in bits)


def safe_logical_key(cid, name):
    """Normalize only identities that are known aliases of the same linear channel."""
    raw_id = _compact(cid)
    raw_name = _compact(name)
    probe = "%s %s" % (raw_id, raw_name)

    # MBC: collapse formatting/country-pack aliases, but preserve real variants.
    if "mbc" in probe:
        src = raw_id or raw_name
        src = re.sub(r"\bmbc\s*(\d+)\b", r"mbc \1", src)
        # Preserve explicit variants encoded in the ID even when display-name is generic.
        modifiers = []
        for token in ("egypt", "masr", "usa", "iraq", "persia", "action", "bollywood",
                      "drama", "max", "plus", "mood", "variety"):
            if token in src:
                modifiers.append(token)
        m = re.search(r"\bmbc\s*(\d+)\b", src)
        if m and not modifiers:
            return "mbc %s" % m.group(1)
        if m and modifiers:
            return "mbc %s %s" % (m.group(1), " ".join(sorted(set(modifiers))))
        # Named MBC channels.
        for token, key in (
            ("action", "mbc action"), ("bollywood", "mbc bollywood"),
            ("max", "mbc max"), ("iraq", "mbc iraq"), ("persia", "mbc persia"),
        ):
            if token in src:
                return key

    # beIN: normalize compact SPORTS1/MOVIES1/etc while preserving English/French variants.
    if "bein" in probe or raw_id.startswith("beinsports"):
        src = raw_id or raw_name
        src = re.sub(r"^logos\s+", "", src)
        src = re.sub(r"\bbein\s*sports\s*(\d+)\b", r"bein sports \1", src)
        src = re.sub(r"\bbeinsports\s*(\d+)\b", r"bein sports \1", src)
        src = re.sub(r"\bbein\s*movies\s*(\d+)\b", r"bein movies \1", src)
        src = re.sub(r"\bbeinmovies\s*(\d+)\b", r"bein movies \1", src)
        src = re.sub(r"\bbein\s*series\s*(\d+)\b", r"bein series \1", src)
        src = re.sub(r"\bbeinseries\s*(\d+)\b", r"bein series \1", src)
        src = re.sub(r"\b(en|eng)\b", " english ", src)
        src = re.sub(r"\b(fr|fra)\b", " french ", src)
        src = re.sub(r"\s+", " ", src).strip()
        if re.search(r"\bbein sports \d+\b", src):
            m = re.search(r"\bbein sports (\d+)\b", src)
            lang = ""
            if "english" in src:
                lang = " english"
            elif "french" in src:
                lang = " french"
            return "bein sports %s%s" % (m.group(1), lang)
        # Let the base premium normalizer handle movies/series/max/xtra names.
        value = base.premium_key(cid if (cid or "").casefold().endswith(".bein") else (name or cid), cid)
        value = re.sub(r"\bbeinsports\s*(\d+)\b", r"bein sports \1", value)
        return re.sub(r"\s+", " ", value).strip()

    # Abu Dhabi Sports / AD Sports are the same identity.
    if ("abu dhabi sports" in probe or "abudhabi sports" in probe or "adsports" in probe
            or "ad sports" in probe):
        src = raw_id + " " + raw_name
        premium = "premium" in src
        m = re.search(r"(?:abu\s*dhabi|ad)\s*sports\s*(\d+)", src)
        if not m:
            m = re.search(r"abudhabi\s*sports\s*(\d+)", src)
        if not m:
            m = re.search(r"adsports\s*(\d+)", src)
        if m:
            return "abu dhabi sports%s %s" % (" premium" if premium else "", m.group(1))

    # Al Kass: One/1, Two/2, compact forms and regional-pack aliases are equivalent.
    if "alkass" in probe or "al kass" in probe:
        src = _number_words(raw_id + " " + raw_name)
        src = re.sub(r"\b2023\b", " ", src)
        m = re.search(r"(?:al\s*kass|alkass)\s*(\d+)\b", src)
        if m:
            return "alkass %s" % m.group(1)

    return _original_logical_key(cid, name)


base.logical_key = safe_logical_key


_PLACEHOLDER_RE = re.compile(
    r"^(?:schedule unavailable|programme schedule unavailable|program schedule unavailable|"
    r"no information|no info|tba|جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$",
    re.I,
)


def _first_text(programme, role):
    items = base.text_items(programme, role)
    return items[0] if items else ("", "other")


def _norm_title(value):
    value = (value or "").casefold()
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def _raw_offset(value):
    m = re.search(r"([+-]\d{4}|Z)\s*$", (value or "").strip())
    return m.group(1) if m else "implicit"


def _candidate_stats(candidate, premium):
    valid = []
    invalid = long_events = placeholders = empty_desc = 0
    ar_title = en_title = ar_desc = en_desc = 0
    offsets = defaultdict(set)

    for p in candidate.programmes:
        start = base.parse_xmltv_dt(p.get("start") or "")
        stop = base.parse_xmltv_dt(p.get("stop") or "")
        title, tlang = _first_text(p, "title")
        desc, dlang = _first_text(p, "desc")
        if base.language_of(title) == "ar" or tlang == "ar":
            ar_title += 1
        if base.language_of(title) == "en" or tlang == "en":
            en_title += 1
        if desc:
            if base.language_of(desc) == "ar" or dlang == "ar":
                ar_desc += 1
            if base.language_of(desc) == "en" or dlang == "en":
                en_desc += 1
        else:
            empty_desc += 1
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        duration = (stop - start).total_seconds()
        if duration > 12 * 3600:
            long_events += 1
        if _PLACEHOLDER_RE.match((title or "").strip()):
            placeholders += 1
        offsets[start.strftime("%Y-%m-%d")].add(_raw_offset(p.get("start") or ""))
        offsets[start.strftime("%Y-%m-%d")].add(_raw_offset(p.get("stop") or ""))
        valid.append((start, stop, p))

    valid.sort(key=lambda x: (x[0], x[1]))
    overlaps = 0
    prev = None
    for row in valid:
        if prev is not None and row[0] < prev[1]:
            overlaps += 1
            if row[1] <= prev[1]:
                continue
        prev = row

    n = max(1, len(candidate.programmes))
    nonempty = max(1, len(candidate.programmes) - empty_desc)
    title_ar = ar_title / n
    title_en = en_title / n
    desc_ar = ar_desc / nonempty if len(candidate.programmes) - empty_desc else 0.0
    mixed_tz = sum(1 for vals in offsets.values() if len(vals) > 1)

    score = float(base.source_base(candidate.origin, candidate.site))
    if candidate.origin == "primary":
        score += 10.0
    score += min(len(valid), 30) * 0.8
    if premium:
        score += title_en * 24.0 + desc_ar * 22.0
    else:
        score += title_ar * 24.0 + desc_ar * 22.0
    score -= invalid * 35.0
    score -= overlaps * 10.0
    score -= long_events * 20.0
    score -= placeholders * 12.0
    score -= mixed_tz * 18.0
    score -= (empty_desc / n) * 10.0
    return {
        "score": score, "valid": len(valid), "invalid": invalid, "overlaps": overlaps,
        "long": long_events, "placeholders": placeholders, "mixed_tz": mixed_tz,
        "title_ar": title_ar, "title_en": title_en, "desc_ar": desc_ar,
    }


def _choose_timeline(candidates):
    premium = any(base.is_premium(c.cid, c.name) for c in candidates)
    ranked = []
    for c in candidates:
        st = _candidate_stats(c, premium)
        ranked.append((st["score"], st["valid"], base.source_base(c.origin, c.site), c.cid.casefold(), c, st))
    ranked.sort(reverse=True, key=lambda x: (x[0], x[1], x[2], x[3]))
    return ranked[0][4], ranked[0][5]


def _event_value(programme, premium):
    title, tlang = _first_text(programme, "title")
    desc, dlang = _first_text(programme, "desc")
    value = 0.0
    if title:
        value += 10.0
    if desc:
        value += 8.0 + min(len(desc), 300) / 100.0
    if premium:
        if tlang == "en" or base.language_of(title) == "en":
            value += 8.0
        if dlang == "ar" or base.language_of(desc) == "ar":
            value += 10.0
    else:
        if tlang == "ar" or base.language_of(title) == "ar":
            value += 8.0
        if dlang == "ar" or base.language_of(desc) == "ar":
            value += 10.0
    if _PLACEHOLDER_RE.match((title or "").strip()):
        value -= 30.0
    return value


def _clean_timeline(candidate, premium):
    rows = []
    for p in candidate.programmes:
        start = base.parse_xmltv_dt(p.get("start") or "")
        stop = base.parse_xmltv_dt(p.get("stop") or "")
        if start is None or stop is None or stop <= start:
            continue
        duration = (stop - start).total_seconds()
        title, _lang = _first_text(p, "title")
        if duration > 12 * 3600:
            continue
        if _PLACEHOLDER_RE.match((title or "").strip()) and duration > 60 * 60:
            continue
        rows.append([start, stop, p])
    rows.sort(key=lambda x: (x[0], x[1]))

    kept = []
    for row in rows:
        if not kept:
            kept.append(row)
            continue
        prev = kept[-1]
        if row[0] >= prev[1]:
            kept.append(row)
            continue

        ptitle = _norm_title(_first_text(prev[2], "title")[0])
        ctitle = _norm_title(_first_text(row[2], "title")[0])
        same = bool(ptitle and ptitle == ctitle)
        overlap = (prev[1] - row[0]).total_seconds()
        current_duration = (row[1] - row[0]).total_seconds()

        if same and (row[1] <= prev[1] or current_duration <= 10 * 60):
            # Contained/mini duplicate: retain the richer event only.
            if _event_value(row[2], premium) > _event_value(prev[2], premium) and row[1] >= prev[1]:
                kept[-1] = row
            continue

        if overlap <= 2 * 60 and row[0] > prev[0]:
            # Tiny boundary error: trim the previous stop to the next start.
            cp = base.copy_element(prev[2])
            cp.set("stop", row[2].get("start") or cp.get("stop") or "")
            kept[-1] = [prev[0], row[0], cp]
            kept.append(row)
            continue

        # Large conflict inside one feed. Keep the event with better metadata;
        # if tied, keep the earlier event to avoid timeline oscillation.
        if _event_value(row[2], premium) > _event_value(prev[2], premium) + 2.0:
            kept[-1] = row
        # otherwise drop current conflicting row.

    return kept


def _matching_entries(base_row, candidates, timeline_candidate):
    start, stop, programme = base_row
    title = _norm_title(_first_text(programme, "title")[0])
    duration = (stop - start).total_seconds()
    entries = [(timeline_candidate, programme)]
    for candidate in candidates:
        if candidate is timeline_candidate:
            continue
        best = None
        for alt in candidate.programmes:
            astart = base.parse_xmltv_dt(alt.get("start") or "")
            astop = base.parse_xmltv_dt(alt.get("stop") or "")
            if astart is None or astop is None or astop <= astart:
                continue
            delta = abs((astart - start).total_seconds())
            if delta > 10 * 60:
                continue
            adur = (astop - astart).total_seconds()
            atitle = _norm_title(_first_text(alt, "title")[0])
            same_title = bool(title and atitle and title == atitle)
            close_slot = delta <= 2 * 60 and abs(adur - duration) <= 15 * 60
            if not (same_title or close_slot):
                continue
            quality = _event_value(alt, any(base.is_premium(c.cid, c.name) for c in candidates))
            rank = (1 if same_title else 0, -delta, -abs(adur - duration), quality)
            if best is None or rank > best[0]:
                best = (rank, alt)
        if best is not None:
            entries.append((candidate, best[1]))
    return entries


def safe_cluster_programmes(candidates):
    """Return clusters built from ONE selected timeline, never a union of schedules."""
    timeline, _stats = _choose_timeline(candidates)
    premium = any(base.is_premium(c.cid, c.name) for c in candidates)
    rows = _clean_timeline(timeline, premium)
    return [[int(start.timestamp()), _matching_entries(row, candidates, timeline)] for start, stop, p in rows for row in [(start, stop, p)]]


base.cluster_programmes = safe_cluster_programmes


def _remove_role(programme, role):
    for node in list(programme.findall(role)):
        programme.remove(node)


def safe_choose_event(entries, premium):
    """Keep timestamps from selected timeline; alternate feeds can enrich text only."""
    timeline_candidate, timeline_programme = entries[0]
    out = base.copy_element(timeline_programme)

    if premium:
        _tc, tp = base.choose_best_entry(entries, "en", "title")
        _dc, dp = base.choose_best_entry(entries, "ar", "desc")
        title, title_lang, _ = base.best_text(tp, "title", "en")
        desc, desc_lang, _ = base.best_text(dp, "desc", "ar")
        if title and title_lang == "en":
            base.replace_role(out, "title", title, "en")
        if desc and desc_lang == "ar":
            base.replace_role(out, "desc", desc, "ar")
        else:
            # User policy: never publish English premium prose as description.
            _remove_role(out, "desc")
    else:
        _tc, tp = base.choose_best_entry(entries, "ar", "title")
        _dc, dp = base.choose_best_entry(entries, "ar", "desc")
        title, title_lang, _ = base.best_text(tp, "title", "ar")
        desc, desc_lang, _ = base.best_text(dp, "desc", "ar")
        if title and title_lang == "ar":
            base.replace_role(out, "title", title, "ar")
        if desc and desc_lang == "ar":
            base.replace_role(out, "desc", desc, "ar")
        else:
            # Do not leak English descriptions into Arabic-provider shards.
            first_desc, first_lang = _first_text(out, "desc")
            if first_desc and first_lang == "en":
                _remove_role(out, "desc")

    # Defensive: timeline attributes must remain from the selected feed.
    out.set("start", timeline_programme.get("start") or "")
    if timeline_programme.get("stop"):
        out.set("stop", timeline_programme.get("stop") or "")
    return out


base.choose_event = safe_choose_event


if __name__ == "__main__":
    raise SystemExit(base.main())
