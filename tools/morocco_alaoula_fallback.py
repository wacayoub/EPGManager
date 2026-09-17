#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Current-date Al Aoula fallback for Morocco Cloud.

Primary authority remains SNRT. This fallback is called only when the SNRT
scraper has no useful AlAoula event in the receiver horizon.

Safety rules:
- parse only the dedicated AL AOULA channel page;
- accept only links to dated /programmes/ pages;
- derive the event date from the programme URL, never from a guessed request;
- stop at the channel page TIMELINE/REPLAYS sections so unrelated channels are
  not imported;
- never repeat today's schedule into future days.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, time as dtime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import morocco_epg as base
import morocco_cloud_runner as runner

TZ = runner.TZ
CHANNEL_URL = "https://tele-news.net/fr/maroc/television/NGc1Q0ZoNVEzSGROVXJzVEF5amgvUT09"
STOP_HEADINGS = {
    "timeline",
    "replays",
    "sélection du jour",
    "selection du jour",
    "programmes connexes",
}


def _heading_text(node):
    return runner.clean(node.get_text(" ", strip=True)).casefold()


def _schedule_anchor_rows(html, today, horizon_days):
    """Return dated (day, HH:MM, title) rows from AL AOULA's main schedule."""
    soup = BeautifulSoup(html, "lxml")
    headings = [
        h for h in soup.find_all(re.compile(r"^h[1-6]$"))
        if _heading_text(h) == "al aoula"
    ]
    if not headings:
        return [], []

    # The last AL AOULA heading is the channel content heading; earlier matches
    # may belong to navigation/search widgets.
    start = headings[-1]
    rows = []
    navigation = []
    seen = set()
    end_day = today + timedelta(days=max(1, int(horizon_days)))

    for node in start.find_all_next():
        if node.name and re.fullmatch(r"h[1-6]", node.name):
            txt = _heading_text(node)
            if txt in STOP_HEADINGS:
                break

        if node.name != "a" or not node.get("href"):
            continue

        href = urljoin(CHANNEL_URL, node.get("href"))
        dm = runner.DATE_RE.search(href)
        if dm and "/programmes/" not in href and "television" in href:
            navigation.append(href)

        if "/programmes/" not in href or not dm:
            continue
        try:
            day = datetime.strptime(dm.group(0), "%Y-%m-%d").date()
        except Exception:
            continue
        if day < today or day >= end_day:
            continue

        title = runner.clean(node.get_text(" ", strip=True))
        if not title or len(title) < 2 or len(title) > 160:
            continue
        if title.casefold() in runner.SKIP_TEXT:
            continue

        block = runner.closest_program_block(node)
        if not block:
            continue
        _parent, _parts, times = block
        if len(times) != 1:
            continue
        tm = times[0]
        key = (day, tm, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append((day, tm, title))

    return rows, navigation


def _events(rows):
    out = []
    for day, tm, title in rows:
        try:
            hh, mm = map(int, tm.split(":"))
        except Exception:
            continue
        start = datetime.combine(day, dtime(hh, mm), TZ)
        # Keep the provider's programme title verbatim. Description stays Arabic
        # and deterministic; no online machine translation is injected here.
        desc = "برنامج ضمن شبكة قناة الأولى المغربية."
        out.append(base.Event(
            "AlAoula", start, title, desc, None,
            base.lang(title, "fr"), "ar", "telenews-alaoula",
        ))

    # One linear channel cannot have two different programmes at one instant.
    # Prefer the richer/longer title if the source exposes duplicate cards.
    exact = {}
    for e in out:
        old = exact.get(e.start)
        if old is None or len(e.title) > len(old.title):
            exact[e.start] = e
    clean = sorted(exact.values(), key=lambda e: e.start)
    base.infer(clean)
    return clean


def scrape(days=3):
    s = runner.session()
    today = datetime.now(TZ).date()
    horizon = min(max(1, int(days)), 3)
    all_rows = []
    fetched = set()
    discovered = []

    # The site has changed its date selector several times. Try both known query
    # names, but trust only the YYYY-MM-DD embedded in each programme URL.
    attempts = [(CHANNEL_URL, None)]
    for i in range(1, horizon):
        day = today + timedelta(days=i)
        attempts.append((CHANNEL_URL, {"date": day.isoformat()}))
        attempts.append((CHANNEL_URL, {"dt": day.isoformat()}))

    for url, params in attempts:
        marker = (url, tuple(sorted((params or {}).items())))
        if marker in fetched:
            continue
        fetched.add(marker)
        try:
            r = runner.fetch(s, url, params=params, referer="https://tele-news.net/fr/maroc/television")
            rows, links = _schedule_anchor_rows(r.text, today, horizon)
            all_rows.extend(rows)
            discovered.extend(links)
        except Exception as exc:
            runner.log("AlAoula TeleNews attempt failed: %s" % exc)

    # Follow only dated links that still point to the same encoded channel page.
    token = CHANNEL_URL.rsplit("/", 1)[-1]
    for href in dict.fromkeys(discovered):
        if token not in href:
            continue
        dm = runner.DATE_RE.search(href)
        if not dm:
            continue
        try:
            day = datetime.strptime(dm.group(0), "%Y-%m-%d").date()
        except Exception:
            continue
        if day < today or day >= today + timedelta(days=horizon):
            continue
        marker = (href, ())
        if marker in fetched:
            continue
        fetched.add(marker)
        try:
            r = runner.fetch(s, href, referer=CHANNEL_URL)
            rows, _links = _schedule_anchor_rows(r.text, today, horizon)
            all_rows.extend(rows)
        except Exception as exc:
            runner.log("AlAoula TeleNews dated page failed: %s" % exc)

    events = _events(all_rows)
    runner.log(
        "AlAoula TeleNews guarded fallback: %d events / dates=%s"
        % (len(events), sorted({e.start.date().isoformat() for e in events}))
    )
    return events
