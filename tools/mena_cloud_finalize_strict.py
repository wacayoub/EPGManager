#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict final publication policy layered on mena_cloud_finalize_safe."""
from __future__ import annotations

import re

import mena_cloud_finalize as base
import mena_cloud_finalize_safe as safe

_original_clean = safe._clean_channel_rows


def _probe(cid, name):
    return ("%s %s" % (cid or "", name or "")).casefold()


def _is_alkass(cid, name):
    p = _probe(cid, name)
    return "alkass" in p or "al kass" in p


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


def strict_clean_channel_rows(cid, name, rows):
    cleaned = _original_clean(cid, name, rows)

    if _is_alkass(cid, name) and cleaned:
        titles = [safe._text(p, "title") for p in cleaned if safe._text(p, "title")]
        ar = sum(1 for t in titles if safe._lang(t) == "ar")
        bad_signature = sum(1 for t in titles if _BAD_ALKASS_RE.search(t or ""))
        ar_ratio = (ar / float(len(titles))) if titles else 0.0
        # Wrong aggregator guide is worse than no guide. Keep the channel ID for
        # mapping, but publish no programmes until a trustworthy schedule exists.
        if bad_signature >= 2 or (len(titles) >= 5 and ar_ratio < 0.50):
            return []

        for p in cleaned:
            desc = safe._text(p, "desc")
            if not desc:
                safe._set_desc(p, _alkass_desc(safe._text(p, "title")))

    if _is_arabic_provider(cid, name):
        for p in cleaned:
            desc = safe._text(p, "desc")
            # Arabic-provider policy: never publish English prose as description.
            if desc and safe._lang(desc) == "en":
                safe._set_desc(p, "")

    return cleaned


safe._clean_channel_rows = strict_clean_channel_rows


if __name__ == "__main__":
    raise SystemExit(base.main())
