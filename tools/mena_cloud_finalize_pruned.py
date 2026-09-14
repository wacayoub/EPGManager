#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production finalizer for compact, integrity-safe receiver-facing MENA XMLTV.

The full internal catalogue is intentionally retained for audits/source recovery.
Only published XMLTV feeds are pruned: a channel is emitted iff final fresh/LKG
selection has at least one programme. Event channels therefore disappear while
idle and automatically return when real EPG appears.

A proven contaminated six-channel national-sports clone is also quarantined only
while at least three of those services still carry the exact same timeline. This
makes the guard self-releasing when upstream data is corrected.

beIN SPORTS NEWS is Arabic-first at the final receiver-facing boundary: known
recurring editorial titles are rendered in Arabic while IDs, dates, start/stop
slots and programme counts remain untouched. Unknown titles are preserved rather
than mistranslated.
"""
from __future__ import annotations

import re

import mena_cloud_finalize as base
import mena_cloud_finalize_strict as strict
import mena_integrity_guard as guard

KNOWN_FALSE_SPORT_CLONE_IDS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}

# Importing strict has already installed the standard integrity/LKG wrapper.
_strict_programme_groups = base.programme_groups

_NEWS_EXACT_TITLES = {
    "al hassila": "الحصيلة",
    "the issue of the day": "الشوط الثالث",
    "news bulletin": "نشرة الأخبار",
    "special interview": "مقابلة خاصة",
    "super monday": "سوبر الإثنين",
    "al hassad": "الحصاد",
    "the big interview": "المقابلة الكبرى",
    "sports news": "الأخبار الرياضية",
    "football news": "أخبار كرة القدم",
    "news summary": "موجز الأخبار",
    "breaking news": "أخبار عاجلة",
    "morning news": "أخبار الصباح",
    "evening news": "أخبار المساء",
    "world news": "أخبار العالم",
    "international news": "الأخبار الدولية",
    "press conference": "مؤتمر صحفي",
    "sports today": "رياضة اليوم",
}


def quarantine_known_false_sport_clone(root, now, end):
    groups = _strict_programme_groups(root, now, end)
    present = {
        cid: groups.get(cid, [])
        for cid in KNOWN_FALSE_SPORT_CLONE_IDS
        if groups.get(cid)
    }
    if len(present) < 3:
        return groups

    fingerprints = [
        guard.timeline_fingerprint(rows)
        for rows in present.values()
    ]
    fingerprints = [fp for fp in fingerprints if fp is not None]

    # Conservative fail-safe: quarantine only while at least three known target
    # services are exactly identical. A corrected source automatically escapes.
    if len(fingerprints) >= 3 and len(set(fingerprints)) == 1:
        for cid in present:
            groups.pop(cid, None)
        print(
            "Target clone quarantine: removed programmes for %d national-sports IDs: %s"
            % (len(present), ", ".join(sorted(present)))
        )
    return groups


def _is_bein_news_id(cid):
    probe = (cid or "").casefold()
    return "bein" in probe and "news" in probe


def _news_competition(title):
    # Reuse the Qatar1-style competition dictionary from the strict beIN layer.
    for rx, arabic in strict._BEIN_TITLE_COMPETITIONS:
        if rx.search(title or ""):
            return arabic
    if re.search(r"\bUEFA\s+UCL\b|\bUCL\b", title or "", re.I):
        return "دوري أبطال أوروبا"
    if re.search(r"\bUEL\b", title or "", re.I):
        return "الدوري الأوروبي"
    return ""


def _arabic_bein_news_title(title):
    """Translate safe recurring beIN SPORTS NEWS labels to Arabic.

    This is deliberately deterministic and conservative. It translates known
    editorial labels and competition-programme patterns, keeps date suffixes,
    and leaves an unknown title unchanged instead of guessing.
    """
    original = re.sub(r"\s+", " ", title or "").strip()
    if not original:
        return original

    # Preserve a trailing date used by beIN News editorial programmes.
    suffix = ""
    m = re.search(r"\s*[-–—|]\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$", original)
    if m:
        suffix = " - " + m.group(1)
        core = original[:m.start()].strip()
    else:
        core = original

    # Hybrid Qatar1 formatter may already have appended the Arabic equivalent.
    for arabic in (
        "نشرة الأخبار", "المقابلة الكبرى", "مقابلة خاصة", "الحصيلة",
        "الحصاد", "الشوط الثالث", "الأخبار الرياضية", "موجز الأخبار",
    ):
        if arabic in core:
            return arabic + suffix

    norm = re.sub(r"[^a-z0-9]+", " ", core.casefold()).strip()
    if norm in _NEWS_EXACT_TITLES:
        return _NEWS_EXACT_TITLES[norm] + suffix

    # Common branded UEFA magazine spelling seen in the Qatar1/beIN feeds.
    if re.search(r"\bUEFA\s+(?:UCL|Champions\s+League)\s+Magazine\b", core, re.I):
        return "مجلة دوري أبطال أوروبا" + suffix
    if re.search(r"\bUEFA\s+(?:UEL|Europa\s+League)\s+Magazine\b", core, re.I):
        return "مجلة الدوري الأوروبي" + suffix

    comp = _news_competition(core)
    if comp:
        if re.search(r"\bhighlights?\b", core, re.I):
            return "ملخص " + comp + suffix
        if re.search(r"\breview\b", core, re.I):
            return "مراجعة " + comp + suffix
        if re.search(r"\bpreview\b", core, re.I):
            return "تقديم " + comp + suffix
        if re.search(r"\bmagazine\b", core, re.I):
            return "مجلة " + comp + suffix

    # Other safe editorial programme classes.
    patterns = (
        (r"\bnews\s+bulletin\b", "نشرة الأخبار"),
        (r"\bsports?\s+bulletin\b", "النشرة الرياضية"),
        (r"\b(?:special|exclusive)\s+interview\b", "مقابلة خاصة"),
        (r"\binterview\b", "مقابلة"),
        (r"\bpress\s+conference\b", "مؤتمر صحفي"),
        (r"\bhighlights?\b", "ملخص رياضي"),
        (r"\breview\b", "مراجعة رياضية"),
        (r"\bpreview\b", "تقديم رياضي"),
        (r"\bmagazine\b", "مجلة رياضية"),
        (r"\bnews\s+summary\b", "موجز الأخبار"),
    )
    for pattern, arabic in patterns:
        if re.search(pattern, core, re.I):
            return arabic + suffix

    return original


def _translate_bein_news_programmes(programmes):
    """Translate NEWS rows before build_feed serializes XML/GZ/TXT outputs.

    base.build_feed returns a tuple of serialized artefacts, not only an XML root.
    Therefore translation has to happen on copied programme elements before the
    original builder runs; mutating the returned root would leave the gz/txt
    payload stale and previously caused a tuple/findall crash.
    """
    translated = dict(programmes)
    changed = 0
    unresolved = 0

    for cid, rows in programmes.items():
        if not _is_bein_news_id(cid) or not rows:
            continue
        out = []
        for programme in rows:
            cp = base.copy_element(programme)
            title = cp.find("title")
            if title is not None:
                old = (title.text or "").strip()
                if old:
                    new = _arabic_bein_news_title(old)
                    if new != old:
                        title.text = new
                        title.set("lang", "ar")
                        changed += 1
                    elif re.search(r"[A-Za-z]", old) and not re.search(r"[\u0600-\u06ff]", old):
                        unresolved += 1
            out.append(cp)
        translated[cid] = out

    print("beIN SPORTS NEWS Arabic titles: translated=%d unresolved_safe_keep=%d" % (changed, unresolved))
    return translated


def pruned_build_feed(ids, selected_programmes, cand_channels, prev_channels, source_by_id, generator_name):
    active_ids = {
        cid for cid in (ids or [])
        if selected_programmes.get(cid)
    }
    translated_programmes = _translate_bein_news_programmes(selected_programmes)
    return strict._original_build_feed(
        sorted(active_ids, key=str.casefold),
        translated_programmes,
        cand_channels,
        prev_channels,
        source_by_id,
        generator_name,
    )


# Preserve all strict protections, then layer the targeted clone quarantine,
# receiver-facing zero-EPG prune and beIN SPORTS NEWS Arabic-title policy.
base.programme_groups = quarantine_known_false_sport_clone
base.build_feed = pruned_build_feed


if __name__ == "__main__":
    raise SystemExit(base.main())
