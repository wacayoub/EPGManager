"""Read only authoritative Moroccan Al Aoula and Chada TV programme grids.

These readers use the provider's *current* HTML selectors. Missing sources
return [] rather than synthesising or copying an old, expired timetable.
"""
from datetime import datetime, timedelta, time
import re
from bs4 import BeautifulSoup
import morocco_epg as base
import morocco_cloud_runner as runner

TZ = runner.TZ
SNRT_LIVE_URL = "https://www.snrt.ma/fr/al-aoula"
CHADA_LIVE_URL = "https://chada.ma/fr/tv/grille/"
CLOCK = re.compile(r"^([01]?\d|2[0-3])\s*[Hh:]\s*([0-5]\d)$")


def _clock(value):
    value = (value or "").strip()
    if value == "24:00":
        return 24, 0
    match = CLOCK.fullmatch(value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_alaoula_html(html, today, now=None):
    """Al Aoula broadcasts its official daily grid from 07h into next dawn."""
    soup = BeautifulSoup(html, "lxml")
    grid = soup.select("#main-roller-content .grille-item")
    if len(grid) < 8:
        return []
    parsed = []
    for item in grid:
        when = item.select_one(".when")
        info = item.select_one(".info")
        if when is None or info is None:
            continue
        label = when.get_text(" ", strip=True)
        hm = _clock(label)
        if hm is None or hm[0] == 24:
            continue
        title = info.get_text(" ", strip=True)
        if title.startswith(label):
            title = title[len(label):].strip(" -–:")
        if not title or len(title) > 150:
            continue
        # SNRT broadcast-day invariant: 00:00–06:59 belong to the next day.
        date = today + timedelta(days=1 if hm[0] < 7 else 0)
        start = datetime.combine(date, time(*hm), TZ)
        parsed.append((start, title))
    parsed = sorted(set(parsed))
    if len(parsed) < 8:
        return []
    events = []
    for i, (start, title) in enumerate(parsed):
        stop = parsed[i+1][0] if i+1 < len(parsed) else start + timedelta(hours=1)
        if stop <= start:
            continue
        lang = base.lang(title)
        events.append(base.Event("AlAoula", start, title, title, stop, lang, lang, "snrt-official-live"))
    return events


def scrape_alaoula(days):
    try:
        response = runner.fetch(runner.session(), SNRT_LIVE_URL)
        today = datetime.now(TZ).date()
        rows = parse_alaoula_html(response.text, today)
        runner.log("AlAoula active official SNRT grid: %d real events" % len(rows))
        return rows
    except Exception as exc:
        runner.log("AlAoula active official SNRT page unavailable: %s" % exc)
        return []


def parse_chada_html(html, today, days=7):
    """The official seven day tabs use data-day-panel=1..7 (Mon..Sun)."""
    soup = BeautifulSoup(html, "lxml")
    if not soup.select(".chada-calendar-tab[data-day]"):
        return []
    out = []
    for panel in soup.select(".chada-day-panel[data-day-panel]"):
        try:
            day = int(panel["data-day-panel"])
        except (ValueError, KeyError):
            continue
        if not 1 <= day <= 7:
            continue
        date = today + timedelta(days=(day - 1 - today.weekday()) % 7)
        if (date - today).days >= days:
            continue
        for item in panel.select("article.chada-epg-row"):
            start_tag = item.select_one(".chada-epg-time__start")
            stop_tag = item.select_one(".chada-epg-time__end")
            if start_tag is None or stop_tag is None:
                continue
            a = _clock(start_tag.get_text(" ",strip=True))
            b = _clock(stop_tag.get_text(" ",strip=True))
            if a is None or b is None or a[0] == 24:
                continue
            heading = item.find(["h3", "h4", "h2"])
            if heading is None:
                heading = item.select_one("[class*=title]")
            if heading is None:
                continue
            title = heading.get_text(" ",strip=True)
            if not title or len(title) > 180:
                continue
            description = item.select_one("[class*=description]")
            if description is None:
                description = item.find("p")
            desc = description.get_text(" ",strip=True) if description is not None else title
            if not desc:
                desc = title
            start = datetime.combine(date, time(*a), TZ)
            stop_date = date + timedelta(days=1 if b[0] == 24 or b <= a else 0)
            stop = datetime.combine(stop_date, time(0, 0) if b[0] == 24 else time(*b), TZ)
            if stop <= start or stop - start > timedelta(hours=12):
                continue
            out.append(base.Event("Chada TV", start, title, desc, stop,
                                  base.lang(title), base.lang(desc), "chada-official-weekly"))
    # Trust only individual events the broadcaster has actually listed.
    unique = {(e.start, e.title.casefold()): e for e in out}
    return sorted(unique.values(), key=lambda e: e.start)


def scrape_chada(days):
    try:
        response = runner.fetch(runner.session(), CHADA_LIVE_URL)
        rows = parse_chada_html(response.text, datetime.now(TZ).date(), days)
        runner.log("Chada current official 7-day schedule: %d real events" % len(rows))
        return rows
    except Exception as exc:
        runner.log("Chada current official grid unavailable: %s" % exc)
        return []
