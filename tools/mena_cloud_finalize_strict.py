#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict final publication policy layered on mena_cloud_finalize_safe.

beIN SPORTS title policy mirrors the Qatar1 formatter. This layer also applies
an integrity gate to BOTH the fresh candidate and previous LKG programme maps,
so a polluted historical feed cannot reintroduce cloned/wrong schedules.
"""
from __future__ import annotations

import re

import mena_cloud_finalize as base
import mena_cloud_finalize_safe as safe
import mena_integrity_guard as guard

_original_clean = safe._clean_channel_rows
_original_programme_groups = base.programme_groups
_INTEGRITY_GROUP_CALL = 0


def strict_programme_groups(root, now, end):
    """Reject bad final/LKG channel timelines before fresh-vs-LKG selection."""
    global _INTEGRITY_GROUP_CALL
    groups = _original_programme_groups(root, now, end)
    clean, findings = guard.sanitize_programme_groups(groups)
    _INTEGRITY_GROUP_CALL += 1
    label = "fresh" if _INTEGRITY_GROUP_CALL == 1 else "lkg"
    if findings.get("blocked_channels"):
        print("Integrity final gate (%s): kept=%d blocked=%d reasons=%s" % (
            label,
            findings.get("kept_channels", 0),
            findings.get("blocked_channels", 0),
            findings.get("reason_counts", {}),
        ))
    return clean


base.programme_groups = strict_programme_groups


def _probe(cid, name):
    return ("%s %s" % (cid or "", name or "")).casefold()


def _is_alkass(cid, name):
    p = _probe(cid, name)
    return "alkass" in p or "al kass" in p


def _is_bein_sports(cid, name):
    return safe._is_bein_sports(cid, name)


def _is_arabic_provider(cid, name):
    p = _probe(cid, name)
    tokens = (
        "mbc", "alarabiya", "al arabiya", "alhadath", "wanasah",
        "abu dhabi", "abudhabi", "ad sports", "adsports", "yas tv", "majid",
        "dubai tv", "dubai sports", "dubai racing", "sama dubai", "dubai zaman",
        "rotana", "alkass", "al kass", "art ", "art.", "ssc",
    )
    return any(x in p for x in tokens)


def _alkass_desc(title):
    t = (title or "").strip()
    if not t:
        return ""
    if "دوري نجوم قطر" in t:
        return "تغطية لمباريات وأحداث دوري نجوم قطر."
    if "المجلس" in t:
        return "برنامج رياضي حواري يناقش أبرز القضايا والأحداث الرياضية."
    if "تواصل" in t:
        return "برنامج رياضي يتابع أبرز الأخبار والمواضيع الرياضية."
    if "الحكم" in t:
        return "برنامج رياضي يناقش أبرز الحالات والقرارات التحكيمية."
    if "90" in t or "٩٠" in t:
        return "برنامج رياضي يقدم التحليل والنقاش حول أبرز الأحداث والمباريات."
    return "برنامج رياضي على قنوات الكأس."


_BAD_ALKASS_RE = re.compile(
    r"hollywood weapons|wildlife heroes|farming the wild|backcountry rescue|"
    r"nordic wild hunter|dropped[_ ]|survival mode|american icons",
    re.I,
)

_BEIN_TITLE_COMPETITIONS = [
    (re.compile(r"\bUEFA\s+Champions\s+League\b", re.I), "دوري أبطال أوروبا"),
    (re.compile(r"\bUEFA\s+Europa\s+League\b", re.I), "الدوري الأوروبي"),
    (re.compile(r"\bUEFA\s+(?:Europa\s+)?Conference\s+League\b", re.I), "دوري المؤتمر الأوروبي"),
    (re.compile(r"\bAFC\s+Champions\s+League\s+Elite\b", re.I), "دوري أبطال آسيا للنخبة"),
    (re.compile(r"\bAFC\s+Champions\s+League\s+Two\b", re.I), "دوري أبطال آسيا 2"),
    (re.compile(r"\bAFC\s+Champions\s+League\b", re.I), "دوري أبطال آسيا"),
    (re.compile(r"\bEnglish\s+Premier\s+League\b|\bPremier\s+League\b", re.I), "الدوري الإنجليزي الممتاز"),
    (re.compile(r"\bFrench\s+(?:League\s*-\s*)?Ligue\s*1\b|\bLigue\s*1\b", re.I), "الدوري الفرنسي"),
    (re.compile(r"\bSpanish\s+(?:La\s*Liga|League)\b|\bLa\s*Liga\b", re.I), "الدوري الإسباني"),
    (re.compile(r"\bGerman\s+Bundesliga\b|\bBundesliga\b", re.I), "الدوري الألماني"),
    (re.compile(r"\bItalian\s+Serie\s*A\b|\bSerie\s*A\b", re.I), "الدوري الإيطالي"),
    (re.compile(r"\bSaudi\s+Pro\s+League\b", re.I), "الدوري السعودي للمحترفين"),
    (re.compile(r"\bUAE\s+Pro\s+League\b|\bADNOC\s+Pro\s+League\b", re.I), "دوري أدنوك للمحترفين"),
    (re.compile(r"\bQatar\s+Stars\s+League\b", re.I), "دوري نجوم قطر"),
    (re.compile(r"\bTurkish\s+Super\s+League\b|\bS[uü]per\s+Lig\b", re.I), "الدوري التركي الممتاز"),
    (re.compile(r"\bEFL\b.*?\bChampionship\b|\bEnglish\s+Football\s+League.*?Championship\b", re.I), "دوري البطولة الإنجليزية"),
]

_SEASON_ROUND_TAIL_RE = re.compile(
    r"(?:\s*[-–—|:]\s*)?(?:20\d{2}(?:[/\-]20\d{2})?)"
    r"(?:\s*[-–—|:]\s*(?:Week|Round|Matchday|MD)\s*\d{1,2})?\s*$",
    re.I,
)
_ROUND_TAIL_RE = re.compile(
    r"\s*[-–—|:]\s*(?:Week|Round|Matchday|MD)\s*\d{1,2}\s*$", re.I
)
_LIVE_PREFIX_RE = re.compile(r"^\s*Live\s*(?:[-:|])?\s*", re.I)


def _set_title(programme, text):
    if not text:
        return
    titles = list(programme.findall("title"))
    if titles:
        title = titles[0]
        for extra in titles[1:]:
            programme.remove(extra)
    else:
        title = base.ET.SubElement(programme, "title")
    title.text = text
    title.set("lang", "en")


def _cleanup_title_separators(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s*[-–—|:]\s*[-–—|:]\s*", " - ", text)
    text = re.sub(r"\s*[-–—|:]\s*$", "", text)
    text = re.sub(r"^\s*[-–—|:]\s*", "", text)
    return text.strip()


def _hybrid_bein_title(title):
    """Return Qatar1-style hybrid EN/AR display title for beIN SPORTS."""
    original = re.sub(r"\s+", " ", title or "").strip()
    if not original:
        return original

    had_live = bool(_LIVE_PREFIX_RE.match(original))
    work = _LIVE_PREFIX_RE.sub("", original).strip()

    if re.search(r"\bUEFA\s+Champions\s+League\s+Magazine\b", work, re.I):
        if re.search(r"\bpreview\b", work, re.I):
            work = "UEFA مجلة - تقديم دوري أبطال أوروبا"
        else:
            work = "UEFA مجلة - دوري أبطال أوروبا"
    elif re.search(r"\bUEFA\s+Europa\s+League\s+Magazine\b", work, re.I):
        if re.search(r"\bpreview\b", work, re.I):
            work = "UEFA مجلة - تقديم الدوري الأوروبي"
        else:
            work = "UEFA مجلة - الدوري الأوروبي"
    else:
        for rx, arabic in _BEIN_TITLE_COMPETITIONS:
            if rx.search(work):
                work = rx.sub(arabic, work, count=1)
                break

        old = None
        while old != work:
            old = work
            work = _ROUND_TAIL_RE.sub("", work)
            work = _SEASON_ROUND_TAIL_RE.sub("", work)

        work = re.sub(r"\bNews\s+Bulletin\b", "News Bulletin - نشرة الأخبار", work, flags=re.I)
        work = re.sub(r"\bThe\s+Big\s+Interview\b", "The Big Interview - المقابلة الكبرى", work, flags=re.I)
        work = re.sub(r"\bEPL\s+Stories\b", "EPL Stories - قصص الدوري الإنجليزي الممتاز", work, flags=re.I)
        if re.search(r"\bHighlights\b", work, re.I) and "ملخص" not in work:
            work = re.sub(r"\bHighlights\b", "Highlights - ملخص", work, count=1, flags=re.I)
        if re.search(r"\bPreview\b", work, re.I) and "تقديم" not in work:
            work = re.sub(r"\bPreview\b", "Preview - تقديم", work, count=1, flags=re.I)
        if re.search(r"\bReview\b", work, re.I) and "مراجعة" not in work:
            work = re.sub(r"\bReview\b", "Review - مراجعة", work, count=1, flags=re.I)
        if re.search(r"\bMagazine\b", work, re.I) and "مجلة" not in work:
            work = re.sub(r"\bMagazine\b", "Magazine - مجلة", work, count=1, flags=re.I)

    work = _cleanup_title_separators(work)
    if had_live:
        work = "Live : " + work
    return work


def strict_clean_channel_rows(cid, name, rows):
    cleaned = _original_clean(cid, name, rows)

    if _is_alkass(cid, name) and cleaned:
        titles = [safe._text(p, "title") for p in cleaned if safe._text(p, "title")]
        ar = sum(1 for t in titles if safe._lang(t) == "ar")
        bad_signature = sum(1 for t in titles if _BAD_ALKASS_RE.search(t or ""))
        ar_ratio = (ar / float(len(titles))) if titles else 0.0
        if bad_signature >= 2 or (len(titles) >= 5 and ar_ratio < 0.50):
            return []

        for p in cleaned:
            desc = safe._text(p, "desc")
            if not desc:
                safe._set_desc(p, _alkass_desc(safe._text(p, "title")))

    if _is_bein_sports(cid, name):
        for p in cleaned:
            old_title = safe._text(p, "title")
            new_title = _hybrid_bein_title(old_title)
            if new_title and new_title != old_title:
                _set_title(p, new_title)

    if _is_arabic_provider(cid, name):
        for p in cleaned:
            desc = safe._text(p, "desc")
            if desc and safe._lang(desc) == "en":
                safe._set_desc(p, "")

    return cleaned


safe._clean_channel_rows = strict_clean_channel_rows


if __name__ == "__main__":
    raise SystemExit(base.main())
