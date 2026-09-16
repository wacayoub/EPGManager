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

Known MBC legacy/foreign/operator aliases are internal-only. They remain in the
catalogue/merge reports for recovery and audits but are never emitted into any
receiver XML. The reviewed canonical MBC set is therefore the only MBC identity
surface exposed to EPGManager.

Rotana follows the same receiver-boundary principle after the 2026-09-14
ID-by-ID audit: official rotana.net IDs and unique useful MENA services stay
receiver-facing, while legacy Egypt/UAE duplicates, ambiguous generic cinema
aliases, the generic Clip holding-guide twin and the foreign Cinema+ US feed are
kept internal-only for audit/recovery.

Two canonical MBC services have a narrow evidence-backed Shahid donor policy.
OSN remains primary for Al Hadath and ElCinema remains primary for MBC Masr
Drama; Shahid may add only non-placeholder Arabic events that do not overlap the
selected primary timeline. This is applied before normal finalization so combined
and provider feeds stay coherent.

beIN SPORTS NEWS is Arabic-first at the final receiver-facing boundary: known
recurring editorial titles are rendered in Arabic while IDs, dates, start/stop
slots and programme counts remain untouched. Unknown titles are preserved rather
than mistranslated.
"""
from __future__ import annotations

import re
import sys

import mena_cloud_finalize as base
import mena_cloud_finalize_strict as strict
import mena_integrity_guard as guard
import mbc_gap_repair

KNOWN_FALSE_SPORT_CLONE_IDS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}

MBC_INTERNAL_ONLY_IDS = {
    "Al Arabiya.sa",
    "Al Arabiya Business.sa",
    "AlArabiyaBusiness.ae@SD",
    "AlarabiyaPortrait.ae@SD",
    "EN:.MBC1.Iraq.sa",
    "EN:.MBC1.Masr.sa",
    "MBC Egypt.eg",
    "MBC Maser 2.sa",
    "MBC Maser.sa",
    "MBC MASR 2.sa",
    "MBC Masr Drama.eg",
    "MBC.eg",
    "MBC1Egypt.eg@HD",
    "MBC1USA.us@SD",
    "MBC3USA.us@SD",
    "MBCDramaUSA.us@SD",
    "MBCMasrUSA.us@SD",
    "MBC Plus eLife HD.sa",
    "MBC Plus Variety HD.sa",
    "MBC VARIETY.sa",
    "MBCMood.sa@HD",
    "Wanasah.sa",
}

ROTANA_INTERNAL_ONLY_IDS = {
    "Rotana Cinema + US.sa",
    "Rotana Cinema HD.sa",
    "Rotana Cinema Masr.sa",
    "Rotana Classic.eg",
    "Rotana Classic HD.sa",
    "Rotana Clip.sa",
    "Rotana Comedy.eg",
    "Rotana Comedy.sa",
    "Rotana Drama.eg",
    "Rotana Drama HD.sa",
    "Rotana Khalejia.eg",
    "Rotana Khalijia HD.sa",
    "Rotana.Cinema.Egypt.ae",
    "Rotana.Cinema.KSA.ae",
}

ADM_INTERNAL_ONLY_IDS = {
    "Abu Dhabi.sa",
    "AbuDhabiSports1.ae@SD",
    "AbuDhabiTV.ae@SD",
    "AD Sports 2.sa",
    "AD Sports Premium 1.sa",
    "AD Sports Premium 2.sa",
    "Al Emarat.sa",
    "en:.AD.Sports.Extra.ae",
    "en:.YAS.TV.Extra.ae",
    "Majid.sa",
    "Nat.Geo.Abu.Dhabi.HD.ae",
    "Yas.TV.HD.ae",
    "Emarat.HD.ae",
}

INVALID_LEGACY_IDENTITY_IDS = {
    "AlSharqiya.eg",
    "الشرقية.eg",
}

RECEIVER_INTERNAL_ONLY_IDS = (
    MBC_INTERNAL_ONLY_IDS | ROTANA_INTERNAL_ONLY_IDS | ADM_INTERNAL_ONLY_IDS |
    INVALID_LEGACY_IDENTITY_IDS
)

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

    fingerprints = [guard.timeline_fingerprint(rows) for rows in present.values()]
    fingerprints = [fp for fp in fingerprints if fp is not None]
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
    for rx, arabic in strict._BEIN_TITLE_COMPETITIONS:
        if rx.search(title or ""):
            return arabic
    if re.search(r"\bUEFA\s+UCL\b|\bUCL\b", title or "", re.I):
        return "دوري أبطال أوروبا"
    if re.search(r"\bUEL\b", title or "", re.I):
        return "الدوري الأوروبي"
    return ""


def _arabic_bein_news_title(title):
    original = re.sub(r"\s+", " ", title or "").strip()
    if not original:
        return original

    suffix = ""
    m = re.search(r"\s*[-–—|]\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$", original)
    if m:
        suffix = " - " + m.group(1)
        core = original[:m.start()].strip()
    else:
        core = original

    for arabic in (
        "نشرة الأخبار", "المقابلة الكبرى", "مقابلة خاصة", "الحصيلة",
        "الحصاد", "الشوط الثالث", "الأخبار الرياضية", "موجز الأخبار",
    ):
        if arabic in core:
            return arabic + suffix

    norm = re.sub(r"[^a-z0-9]+", " ", core.casefold()).strip()
    if norm in _NEWS_EXACT_TITLES:
        return _NEWS_EXACT_TITLES[norm] + suffix

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


def _prune_titleless_programmes(programmes):
    """Drop receiver-facing XMLTV events that have no usable title.

    A programme without a title is not useful to EPGManager and is treated as a
    malformed source row.  We drop only that row, never invent metadata, and keep
    the channel whenever other real programmes remain.
    """
    cleaned = {}
    dropped = 0
    affected = []
    for cid, rows in programmes.items():
        kept = []
        local_dropped = 0
        for programme in rows or []:
            titles = programme.findall("title")
            if not any((title.text or "").strip() for title in titles):
                local_dropped += 1
                dropped += 1
                continue
            kept.append(programme)
        cleaned[cid] = kept
        if local_dropped:
            affected.append("%s:%d" % (cid, local_dropped))
    if dropped:
        print(
            "Receiver invalid-title prune: dropped=%d channels=%s" %
            (dropped, ", ".join(affected[:20]))
        )
    return cleaned


def pruned_build_feed(ids, selected_programmes, cand_channels, prev_channels, source_by_id, generator_name):
    requested_ids = set(ids or [])
    internal_mbc = requested_ids & MBC_INTERNAL_ONLY_IDS
    internal_rotana = requested_ids & ROTANA_INTERNAL_ONLY_IDS
    internal_adm = requested_ids & ADM_INTERNAL_ONLY_IDS

    cleaned_programmes = _prune_titleless_programmes(selected_programmes)
    active_ids = {
        cid for cid in requested_ids
        if cleaned_programmes.get(cid) and cid not in RECEIVER_INTERNAL_ONLY_IDS
    }
    if internal_mbc and "Legacy Combined" in generator_name:
        print(
            "MBC receiver prune: removed %d internal-only IDs from combined XML: %s" %
            (len(internal_mbc), ", ".join(sorted(internal_mbc, key=str.casefold)))
        )
    if internal_rotana and "Legacy Combined" in generator_name:
        print(
            "Rotana receiver prune: removed %d internal-only IDs from combined XML: %s" %
            (len(internal_rotana), ", ".join(sorted(internal_rotana, key=str.casefold)))
        )
    if internal_adm and "Legacy Combined" in generator_name:
        print(
            "ADM receiver prune: removed %d legacy/LKG IDs from combined XML: %s" %
            (len(internal_adm), ", ".join(sorted(internal_adm, key=str.casefold)))
        )
    translated_programmes = _translate_bein_news_programmes(cleaned_programmes)
    return strict._original_build_feed(
        sorted(active_ids, key=str.casefold),
        translated_programmes,
        cand_channels,
        prev_channels,
        source_by_id,
        generator_name,
    )


base.programme_groups = quarantine_known_false_sport_clone
base.build_feed = pruned_build_feed


def _arg_value(name, default=None):
    for idx, arg in enumerate(sys.argv[1:], 1):
        if arg == name and idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return default


def main():
    candidate = _arg_value("--candidate")
    window_hours = int(_arg_value("--window-hours", "48") or 48)
    if candidate:
        try:
            report_path = str((__import__("pathlib").Path(candidate).parent / "mbc-gap-repair.json"))
            mbc_gap_repair.repair_file(candidate, window_hours=window_hours, report_path=report_path)
        except Exception as exc:
            print("MBC Shahid donor repair unavailable; freeze gate will decide: %s" % str(exc)[:240])
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
