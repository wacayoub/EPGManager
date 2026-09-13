#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2M full-day Arabic/Darija quality layer.

Extends morocco_cloud_runner_ar with the full TeleCableSat day split used by
the historical 2M scraper (morning + noon + afternoon), so evening bulletins
such as Info Soir, Meteo and Eco News are not lost by a morning-only page.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import morocco_epg as base
import morocco_cloud_runner as runner
import morocco_cloud_runner_ar as ar1

TZ = runner.TZ
PARIS = runner.PARIS

# Preserve the branding/native meaning instead of trusting generic MT.
ar1.T2M_AR.update({
    "info soir": "أخبار المساء",
    "info soir 2m": "أخبار المساء",
    "meteo": "النشرة الجوية",
    "la meteo": "النشرة الجوية",
    "meteo 2m": "النشرة الجوية",
    "bulletin meteo": "النشرة الجوية",
    "eco news": "إيكو نيوز",
    "econews": "إيكو نيوز",
    "eco news 2m": "إيكو نيوز",
})
ar1.SHOW_DESC.update({
    "أخبار المساء": "نشرة إخبارية مسائية على القناة الثانية 2M تقدم أبرز الأخبار والمستجدات الوطنية والدولية.",
    "النشرة الجوية": "نشرة جوية تقدم توقعات الطقس ودرجات الحرارة والرياح والتساقطات بمختلف مناطق المغرب.",
    "إيكو نيوز": "فقرة اقتصادية على القناة الثانية 2M تقدم أبرز أخبار الاقتصاد والأسواق والمقاولات والمال والأعمال.",
})
ar1._title_cache.clear()
ar1._desc_cache.clear()

_PERIODS = ("morning", "noon", "afternoon")
_BASE_URL = "https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"


def _append_audit(day, period, tm, source_title, source_desc, title_ar, desc_ar):
    ar1.AUDIT.append({
        "date": day.isoformat(),
        "period": period,
        "time": tm,
        "source_title": ar1.clean(source_title),
        "title_ar": title_ar,
        "source_desc": ar1.clean(source_desc)[:500],
        "desc_ar": desc_ar,
        "title_ok": ar1.has_arabic(title_ar),
        "desc_ok": ar1.has_arabic(desc_ar),
    })


def _from_generic(day, period, candidates):
    rows = []
    for tm, source_title, source_desc in candidates:
        try:
            hh, mm = map(int, tm.split(":"))
        except Exception:
            continue
        # TeleCableSat's afternoon block may include the after-midnight tail.
        event_day = day + (timedelta(days=1) if period == "afternoon" and hh < 6 else timedelta())
        start = datetime.combine(event_day, dtime(hh, mm), PARIS).astimezone(TZ)
        title_ar = ar1.translate_title(source_title)
        desc_ar = ar1.translate_desc(source_desc, title_ar)
        rows.append(base.Event("2M", start, title_ar, desc_ar, None, "ar", "ar", "2m"))
        _append_audit(day, period, tm, source_title, source_desc, title_ar, desc_ar)
    return rows


def _from_old_parser(day, period, html):
    """Fallback to the historical 2M parser when generic card parsing changes."""
    rows = []
    try:
        parsed = base.parse_2m(ar1._translate_http, html, day, period)
    except Exception as exc:
        runner.log("2M old parser %s %s failed: %s" % (day, period, exc))
        parsed = []
    for e in parsed:
        # Historical parser already translated; re-apply our semantic desc layer.
        title_ar = e.title if ar1.has_arabic(e.title) else ar1.translate_title(e.title)
        desc_ar = ar1.translate_desc(e.desc, title_ar)
        e.title, e.desc, e.tl, e.dl = title_ar, desc_ar, "ar", "ar"
        rows.append(e)
        _append_audit(day, period, e.start.strftime("%H:%M"), "old-parser", e.desc, title_ar, desc_ar)
    return rows


def scrape_2m_full_day(days):
    s = runner.session()
    today = datetime.now(TZ).date()
    out = []
    period_counts = defaultdict(int)

    for i in range(days):
        day = today + timedelta(days=i)
        day_rows = []
        for period in _PERIODS:
            try:
                r = runner.fetch(
                    s,
                    _BASE_URL,
                    params={"date": day.isoformat(), "period": period},
                    referer="https://tv-programme.telecablesat.fr/",
                )
                candidates = runner.generic_programme_cards(r.text)
                # Generic cards retain the original source title, which gives better
                # Darija normalization than machine-translating an already altered title.
                rows = _from_generic(day, period, candidates) if candidates else []
                if len(rows) < 2:
                    rows = _from_old_parser(day, period, r.text)
                period_counts[period] += len(rows)
                day_rows.extend(rows)
            except Exception as exc:
                runner.log("2M %s %s fetch failed: %s" % (day, period, exc))

        if not day_rows:
            runner.log("2M %s: no full-day period rows" % day)
        out.extend(day_rows)

    # Exact dedupe across period boundaries.
    ded = {}
    for e in out:
        ded[(e.start, e.title.casefold())] = e
    rows = sorted(ded.values(), key=lambda e: e.start)
    base.infer(rows)

    special = {"info_soir": 0, "meteo": 0, "eco_news": 0}
    evening = 0
    for e in rows:
        n = ar1.norm(e.title)
        if e.start.astimezone(TZ).hour >= 18:
            evening += 1
        if "اخبار المساء" in n or e.title == "أخبار المساء":
            special["info_soir"] += 1
        if e.title == "النشرة الجوية":
            special["meteo"] += 1
        if e.title == "إيكو نيوز":
            special["eco_news"] += 1

    title_ok = sum(ar1.has_arabic(e.title) for e in rows)
    desc_ok = sum(ar1.has_arabic(e.desc) for e in rows)
    runner.log(
        "2M FULL-DAY audit: %d events; periods=%s; evening=%d; InfoSoir=%d; Meteo=%d; EcoNews=%d; AR title=%d/%d desc=%d/%d"
        % (
            len(rows), dict(period_counts), evening,
            special["info_soir"], special["meteo"], special["eco_news"],
            title_ok, len(rows), desc_ok, len(rows),
        )
    )
    return rows


def main():
    # ar1.main() will patch runner.scrape_2m with this global function.
    ar1.scrape_2m_ar = scrape_2m_full_day
    return ar1.main()


if __name__ == "__main__":
    raise SystemExit(main())
