#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2M full-day Arabic/Darija quality layer with multi-source failover.

Source order is deliberately conservative:
1. TeleCableSat (historical/current source)
2. Telerama
3. Sudinfo / Cine-Tele-Revue
4. TVMag / Le Figaro (today-only emergency web fallback)
5. The outer cloud runner's Last-Known-Good feed if fresh coverage is still bad.

Every accepted source is normalized through the same Arabic/Darija title and
Arabic-description layer. The first evening Info Soir, Meteo and Eco News title
stays French; later/replay occurrences are Arabic. Descriptions always remain
Arabic.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup

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
_TELERAMA_BASE = "https://television.telerama.fr/chaine/2m-maroc"
_SUDINFO_BASE = "https://programmestv.sudinfo.be/programme-tv/chaine/2m-maroc/606"
_TVMAG_BASE = "https://tvmag.lefigaro.fr/programme-tv/chaine/340/tous-les-programmes-de-2m-maroc"
_WEEKDAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MONTHS_FR = (
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
)
_TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
_DURATION_RE = re.compile(r"(?:(\d+)\s*h(?:\s*(\d+))?|(?:(\d+)\s*min))", re.I)

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
    """Keep the first evening bulletin trio in French, repeats in Arabic."""
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
        e.dl = "ar"

    return dict(stats)


def _append_audit(day, source, tm, source_title, source_desc, title_ar, desc_ar):
    ar1.AUDIT.append({
        "date": day.isoformat(),
        "source": source,
        "time": tm,
        "source_title": ar1.clean(source_title),
        "title_ar": title_ar,
        "source_desc": ar1.clean(source_desc)[:500],
        "desc_ar": desc_ar,
        "title_ok": ar1.has_arabic(title_ar),
        "desc_ok": ar1.has_arabic(desc_ar),
    })


def _useful_desc(desc):
    """Avoid translating tiny category labels; semantic Arabic is better."""
    raw = ar1.clean(desc)
    if not raw:
        return ""
    n = ar1.norm(raw)
    generic = (
        "magazine", "serie", "feuilleton", "journal", "meteo", "documentaire",
        "talk show", "film", "divertissement", "clips", "sport", "religieux",
        "actualite", "culinaire", "societe", "rediffusion",
    )
    if len(raw) < 38 and any(x in n for x in generic):
        return ""
    return raw


def _events_from_candidates(day, source, candidates, rollover=False):
    """Convert source-time candidates to Morocco XMLTV events.

    Candidate forms: (HH:MM, title, desc) or (HH:MM, title, desc, duration_min).
    Sudinfo is a broadcast-day list and may append 00:xx-05:xx after the evening;
    rollover=True detects that clock wrap and moves those rows to the next day.
    """
    rows = []
    add_day = 0
    prev_minute = None
    for item in candidates:
        if len(item) < 3:
            continue
        tm, source_title, source_desc = item[:3]
        duration = item[3] if len(item) > 3 else None
        try:
            hh, mm = map(int, tm.split(":"))
        except Exception:
            continue
        minute = hh * 60 + mm
        if rollover and prev_minute is not None and prev_minute >= 18 * 60 and minute < 6 * 60:
            add_day += 1
        prev_minute = minute
        event_day = day + timedelta(days=add_day)
        start = datetime.combine(event_day, dtime(hh, mm), PARIS).astimezone(TZ)
        stop = start + timedelta(minutes=int(duration)) if duration else None
        title_ar = ar1.translate_title(source_title)
        desc_ar = ar1.translate_desc(_useful_desc(source_desc), title_ar)
        rows.append(base.Event("2M", start, title_ar, desc_ar, stop, "ar", "ar", "2m-" + source))
        _append_audit(day, source, tm, source_title, source_desc, title_ar, desc_ar)
    return rows


def _from_generic(day, period, candidates):
    # TeleCableSat's afternoon block may include the after-midnight tail.
    converted = []
    for tm, source_title, source_desc in candidates:
        try:
            hh, mm = map(int, tm.split(":"))
        except Exception:
            continue
        event_day = day + (timedelta(days=1) if period == "afternoon" and hh < 6 else timedelta())
        start = datetime.combine(event_day, dtime(hh, mm), PARIS).astimezone(TZ)
        title_ar = ar1.translate_title(source_title)
        desc_ar = ar1.translate_desc(_useful_desc(source_desc), title_ar)
        converted.append(base.Event("2M", start, title_ar, desc_ar, None, "ar", "ar", "2m-telecablesat"))
        _append_audit(day, "telecablesat/" + period, tm, source_title, source_desc, title_ar, desc_ar)
    return converted


def _from_old_parser(day, period, html):
    """Fallback to the historical 2M parser when TeleCableSat markup changes."""
    rows = []
    try:
        parsed = base.parse_2m(ar1._translate_http, html, day, period)
    except Exception as exc:
        runner.log("2M old parser %s %s failed: %s" % (day, period, exc))
        parsed = []
    for e in parsed:
        title_ar = e.title if ar1.has_arabic(e.title) else ar1.translate_title(e.title)
        desc_ar = ar1.translate_desc(_useful_desc(e.desc), title_ar)
        e.title, e.desc, e.tl, e.dl, e.source = title_ar, desc_ar, "ar", "ar", "2m-telecablesat-old"
        rows.append(e)
        _append_audit(day, "telecablesat-old/" + period, e.start.strftime("%H:%M"), "old-parser", e.desc, title_ar, desc_ar)
    return rows


def _duration_minutes(text):
    m = _DURATION_RE.search(ar1.clean(text))
    if not m:
        return None
    if m.group(3):
        return int(m.group(3))
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _heading_cards(html):
    """Fallback parser for pages such as Sudinfo where programmes are H3 cards."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    seen = set()
    for heading in soup.find_all(["h3", "h4"]):
        title = ar1.clean(heading.get_text(" ", strip=True))
        if not title or len(title) > 160:
            continue
        low = title.casefold()
        if "programme tv" in low or "coups de" in low or low in ("chaine", "chaîne"):
            continue
        cur = heading.parent
        best = None
        for _ in range(6):
            if cur is None:
                break
            parts = [ar1.clean(x) for x in cur.stripped_strings if ar1.clean(x)]
            times = [x for x in parts if _TIME_RE.fullmatch(x)]
            if len(times) == 1 and title in parts and len(parts) <= 24:
                best = parts
                if len(parts) <= 10:
                    break
            cur = cur.parent
        if not best:
            continue
        tm = next((x for x in best if _TIME_RE.fullmatch(x)), None)
        if not tm:
            continue
        duration = None
        desc_parts = []
        try:
            idx = best.index(title)
        except ValueError:
            idx = -1
        tail = best[idx + 1:] if idx >= 0 else best
        for x in tail:
            if _TIME_RE.fullmatch(x) or x.casefold() in ("voir plus", "la suite sous cette pub"):
                continue
            dm = _duration_minutes(x)
            if dm and duration is None:
                duration = dm
                continue
            if x != title and len(x) > 2 and not x.lower().startswith("image:"):
                desc_parts.append(x)
        key = (tm, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append((tm, title, ar1.clean(" ".join(desc_parts[:2])), duration))
    return rows


def _colonize_french_times(html):
    """Normalize French TV times (e.g. 21h00) to 21:00 for shared parser."""
    return re.sub(
        r"(?<!\d)([0-2]?\d)\s*[hH]\s*([0-5]\d)(?!\d)",
        lambda m: "%02d:%s" % (int(m.group(1)), m.group(2)),
        str(html or ""),
    )


def _page_matches_day(html, day):
    """Reject cached/wrong weekday pages before they can poison the cloud feed."""
    text = ar1.clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True)).casefold()
    # normalize French accents to the same lightweight key used elsewhere
    n = ar1.norm(text)
    month = _MONTHS_FR[day.month - 1]
    # ar1.norm strips accents, so août => aout, février => fevrier.
    token1 = "%d %s" % (day.day, month)
    token2 = "%02d %s" % (day.day, month)
    return token1 in n or token2 in n


def _telerama_url(day, today):
    delta = (day - today).days
    if delta == 0:
        return _TELERAMA_BASE
    weekday = _WEEKDAYS_FR[day.weekday()]
    suffix = "-prochain" if delta >= 7 else ""
    return "https://television.telerama.fr/programme-tv-%s%s/2m-maroc" % (weekday, suffix)


def _sudinfo_url(day, today):
    delta = (day - today).days
    if delta == 0:
        return _SUDINFO_BASE
    if delta == 1:
        return _SUDINFO_BASE + "/demain"
    weekday = _WEEKDAYS_FR[day.weekday()]
    suffix = "prochain" if delta >= 7 else ""
    return _SUDINFO_BASE + "/" + weekday + suffix


def _dedupe_exact(rows):
    by_start = {}
    for e in rows:
        key = e.start.replace(second=0, microsecond=0)
        old = by_start.get(key)
        if old is None:
            by_start[key] = e
            continue
        # Same linear channel/start cannot have two shows. Prefer richer metadata,
        # then explicit duration/stop.
        old_score = len(ar1.clean(old.desc)) * 2 + len(ar1.clean(old.title)) + (20 if old.stop else 0)
        new_score = len(ar1.clean(e.desc)) * 2 + len(ar1.clean(e.title)) + (20 if e.stop else 0)
        if new_score > old_score:
            by_start[key] = e
    return sorted(by_start.values(), key=lambda x: x.start)


def _day_complete(rows, source_day):
    rr = [e for e in rows if e.start.astimezone(PARIS).date() == source_day]
    if len(rr) < 14:
        return False
    hours = [e.start.astimezone(PARIS).hour for e in rr]
    return any(h < 12 for h in hours) and any(12 <= h < 18 for h in hours) and any(h >= 18 for h in hours) and any(h >= 20 for h in hours)


def _fetch_telecablesat_day(s, day):
    day_rows = []
    for period in _PERIODS:
        try:
            r = runner.fetch(
                s, _BASE_URL,
                params={"date": day.isoformat(), "period": period},
                referer="https://tv-programme.telecablesat.fr/",
            )
            candidates = runner.generic_programme_cards(r.text)
            rows = _from_generic(day, period, candidates) if candidates else []
            if len(rows) < 2:
                rows = _from_old_parser(day, period, r.text)
            day_rows.extend(rows)
        except Exception as exc:
            runner.log("2M TeleCableSat %s %s failed: %s" % (day, period, exc))
    return _dedupe_exact(day_rows)


def _fetch_telerama_day(s, day, today):
    url = _telerama_url(day, today)
    r = runner.fetch(s, url, referer="https://television.telerama.fr/")
    if not _page_matches_day(r.text, day):
        raise ValueError("Telerama returned a page for another date")
    candidates = runner.generic_programme_cards(_colonize_french_times(r.text))
    rows = _events_from_candidates(day, "telerama", candidates)
    return _dedupe_exact(rows)


def _fetch_sudinfo_day(s, day, today):
    url = _sudinfo_url(day, today)
    r = runner.fetch(s, url, referer="https://programmestv.sudinfo.be/")
    if not _page_matches_day(r.text, day):
        raise ValueError("Sudinfo returned a page for another date")
    candidates = _heading_cards(r.text)
    if len(candidates) < 8:
        candidates = runner.generic_programme_cards(r.text)
    rows = _events_from_candidates(day, "sudinfo", candidates, rollover=True)
    return _dedupe_exact(rows)


def _fetch_tvmag_day(s, day, today):
    # TVMag is intentionally today-only. Its archive/cache is less predictable,
    # so it must never be trusted for future-day URLs.
    if day != today:
        return []
    r = runner.fetch(s, _TVMAG_BASE, referer="https://tvmag.lefigaro.fr/")
    candidates = runner.generic_programme_cards(_colonize_french_times(r.text))
    rows = _events_from_candidates(day, "tvmag", candidates)
    return _dedupe_exact(rows)


def _merge_prefer(primary, backup):
    """Fill missing start slots from a backup without rewriting good primary rows."""
    merged = {e.start.replace(second=0, microsecond=0): e for e in primary}
    for e in backup:
        key = e.start.replace(second=0, microsecond=0)
        if key not in merged:
            merged[key] = e
        else:
            old = merged[key]
            # Keep primary title/timing, but an explicit stop from Sudinfo can safely
            # fill a missing stop and a richer Arabic description can replace a weak one.
            if old.stop is None and e.stop is not None:
                old.stop = e.stop
            if len(ar1.clean(e.desc)) > len(ar1.clean(old.desc)) + 20 and ar1.has_arabic(e.desc):
                old.desc = e.desc
                old.dl = "ar"
    return sorted(merged.values(), key=lambda x: x.start)


def scrape_2m_full_day(days):
    s = runner.session()
    today = datetime.now(TZ).date()
    out = []
    source_stats = defaultdict(int)
    good_days = 0

    for i in range(days):
        day = today + timedelta(days=i)
        selected = _fetch_telecablesat_day(s, day)
        source_stats["telecablesat"] += len(selected)

        if not _day_complete(selected, day):
            for name, fetcher in (
                ("telerama", _fetch_telerama_day),
                ("sudinfo", _fetch_sudinfo_day),
                ("tvmag", _fetch_tvmag_day),
            ):
                try:
                    backup = fetcher(s, day, today)
                    source_stats[name] += len(backup)
                    if backup:
                        selected = _merge_prefer(selected, backup)
                    if _day_complete(selected, day):
                        runner.log("2M %s recovered fresh via %s (%d events)" % (day, name, len(selected)))
                        break
                except Exception as exc:
                    runner.log("2M %s %s backup failed: %s" % (day, name, exc))

        selected = _dedupe_exact(selected)
        if _day_complete(selected, day):
            good_days += 1
        else:
            runner.log("2M %s remains incomplete after all fresh sources (%d events)" % (day, len(selected)))
        out.extend(selected)

    # If most of the requested horizon is not complete, deliberately return no
    # fresh 2M rows so runner.choose() falls back to the proven Last-Known-Good
    # provider feed instead of publishing a misleading partial schedule.
    required_good_days = max(1, min(days, max(2, days - 2)))
    if good_days < required_good_days:
        runner.log("2M fresh horizon rejected: good-days=%d/%d required=%d; sources=%s" % (
            good_days, days, required_good_days, dict(source_stats)))
        return []

    rows = _dedupe_exact(out)
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

    title_ar = sum(ar1.has_arabic(e.title) or e.tl == "fr" for e in rows)
    desc_ar = sum(ar1.has_arabic(e.desc) for e in rows)
    runner.log(
        "2M MULTI-SOURCE audit: %d events; good-days=%d/%d; sources=%s; evening=%d; InfoSoir=%d; Meteo=%d; EcoNews=%d; FR-first=%d; title-policy=%d/%d; AR-desc=%d/%d; policy=%s"
        % (
            len(rows), good_days, days, dict(source_stats), evening,
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