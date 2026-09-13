#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2M full-day Arabic/Darija quality layer.

Extends morocco_cloud_runner_ar with the full TeleCableSat day split used by
the historical 2M scraper (morning + noon + afternoon), so evening bulletins
such as Info Soir, Meteo and Eco News are not lost by a morning-only page.

Historical operator policy is preserved exactly for the evening bulletin trio:
for each broadcast day, the first evening Info Soir, Meteo and Eco News title
stays in French; later/replay occurrences are Arabic. Descriptions remain
Arabic for both the French first edition and Arabic repeats.
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
    "infosoir": "أخبار المساء",
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

# First evening edition is deliberately French. All later occurrences/replays
# are converted to the canonical Arabic title. This is per Morocco broadcast
# day and only applies from 18:00 local time onward.
_SPECIAL_FR = {
    "info_soir": "Info Soir",
    "meteo": "Météo",
    "eco_news": "Eco News",
}
_SPECIAL_AR = {
    "info_soir": "أخبار المساء",
    "meteo": "النشرة الجوية",
    "eco_news": "إيكو نيوز",
}


def _special_family(title):
    raw = ar1.clean(title)
    n = ar1.norm(raw)
    if raw == "أخبار المساء" or n in ("info soir", "info soir 2m", "infosoir"):
        return "info_soir"
    if raw == "النشرة الجوية" or n in ("meteo", "la meteo", "meteo 2m", "bulletin meteo"):
        return "meteo"
    if raw == "إيكو نيوز" or n in ("eco news", "econews", "eco news 2m"):
        return "eco_news"
    return None


def _apply_evening_language_policy(rows):
    """Keep the first evening bulletin trio in French, repeats in Arabic.

    The source translation pass runs first so every event already has a useful
    Arabic description. Only the title/language marker changes here.
    """
    seen_first = set()
    stats = defaultdict(lambda: {"fr_first": 0, "ar_other": 0})

    for e in sorted(rows, key=lambda x: x.start):
        family = _special_family(e.title)
        if not family:
            continue
        local = e.start.astimezone(TZ)
        key = (local.date().isoformat(), family)

        if local.hour >= 18 and key not in seen_first:
            seen_first.add(key)
            e.title = _SPECIAL_FR[family]
            e.tl = "fr"
            stats[family]["fr_first"] += 1
        else:
            e.title = _SPECIAL_AR[family]
            e.tl = "ar"
            stats[family]["ar_other"] += 1

        # Descriptions always stay Arabic, matching the historical 2M logic.
        e.dl = "ar"

    return dict(stats)


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

    # Exact dedupe across period boundaries while titles are still canonical
    # Arabic, then apply the first-evening-French rule once per broadcast day.
    ded = {}
    for e in out:
        ded[(e.start, e.title.casefold())] = e
    rows = sorted(ded.values(), key=lambda e: e.start)
    policy_stats = _apply_evening_language_policy(rows)
    base.infer(rows)

    special = {"info_soir": 0, "meteo": 0, "eco_news": 0}
    evening = 0
    french_first = 0
    for e in rows:
        family = _special_family(e.title)
        if e.start.astimezone(TZ).hour >= 18:
            evening += 1
        if family:
            special[family] += 1
            if e.tl == "fr":
                french_first += 1

    title_ar = sum(ar1.has_arabic(e.title) for e in rows)
    desc_ar = sum(ar1.has_arabic(e.desc) for e in rows)
    runner.log(
        "2M FULL-DAY audit: %d events; periods=%s; evening=%d; InfoSoir=%d; Meteo=%d; EcoNews=%d; FR-first=%d; AR-title=%d/%d; AR-desc=%d/%d; policy=%s"
        % (
            len(rows), dict(period_counts), evening,
            special["info_soir"], special["meteo"], special["eco_news"],
            french_first, title_ar, len(rows), desc_ar, len(rows), policy_stats,
        )
    )
    return rows


def main():
    # ar1.main() will patch runner.scrape_2m with this global function.
    ar1.scrape_2m_ar = scrape_2m_full_day
    return ar1.main()


if __name__ == "__main__":
    raise SystemExit(main())
