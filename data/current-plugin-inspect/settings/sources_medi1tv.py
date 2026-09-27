# -*- coding: utf-8 -*-
"""
sources/medi1tv.py

Ported from the original 1.MEDI1TV.py, with these changes:

  * BUG FIX (verified against the live site 2026-08-09): the grid URL is
    actually https://www.medi1tv.com/ar/grille/arabic/<dd-mm-yyyy>, not
    https://www.medi1tv.com/ar/grille/<dd-mm-yyyy> as the original script
    used. Re-confirmed again on 2026-08-09 by fetching a real day's page -
    the site's own day-navigation links (Lundi/Mardi/.../Dimanche) follow
    exactly this /grille/<channel>/<dd-mm-yyyy> pattern.
  * PARSER REWRITE (2026-08-09): fetched and read the actual live markup.
    The original/previous "primary" selectors (`card-line`, `program-item`,
    `grille-item` CSS classes) do not exist anywhere on the real page -
    every single day was silently falling through to the old "last resort"
    branch, which only grabbed the time+title and never a description.
    The real markup is a flat, ordered sequence of plain <a> tags per
    programme: one link with text "HH:MM Title", usually followed by an
    empty link, then a link containing the description text, then a
    "Page de l'émission" / "Voir les JTs" link, then a thumbnail image.
    The parser below is now written directly against that structure -
    walking all <a> tags in document order, starting a new programme
    whenever a link's text matches the "HH:MM " prefix, and taking the
    next sufficiently-long non-boilerplate link text as that programme's
    description - instead of guessing at CSS classes that were never real.
  * Retry/backoff goes through core.downloader instead of a single
    unretried request.
  * Output path, channel id/name, XMLTV generation semantics unchanged.

Known limitation (not silently hidden): TV listing sites commonly only
publish schedules a day or two in advance. If Medi1TV hasn't published a
given future day's grid yet, that day will legitimately return zero or
very few programmes - this is a real gap in the SOURCE DATA, not a parser
bug, and cannot be fixed by scraping harder. min_programs is deliberately
kept low (3) so a thin day doesn't get discarded outright, but the
per-source day/programme counts on the dashboard are an honest reflection
of what the site actually had published at fetch time.
"""

import datetime as dt
import re
from datetime import timedelta

from .base import EPGSource
from ..core import dependencies
from ..core.parser import get_soup, SourceWarning
from ..core.xmltv import Programme

BASE_URL = "https://www.medi1tv.com/ar/grille/arabic"
CHANNEL_ID = "MEDI1TV_AR.ma"
CHANNEL_DISPLAY_NAME = "Medi1 TV Arabic"

_TIME_PREFIX_RE = re.compile(r"^([0-2]?\d)[:hH]([0-5]\d)\s*(.*)$")

# Link text that is boilerplate, never a real description - skipped when
# looking for the description that follows a programme's time+title link.
_BOILERPLATE_LINK_TEXTS = {
    "page de l'émission", "voir les jts", "en direct", "", "-",
}


class Medi1TVSource(EPGSource):
    id = "medi1tv"
    name = "Medi1 TV"
    output_filename = "medi1tv_ar.xml"
    default_days = 7
    min_programs = 3

    def fetch(self, downloader, tzm, days, config, builder):
        builder.add_channel(CHANNEL_ID, [(CHANNEL_DISPLAY_NAME, "ar")])

        today = tzm.today_midnight()
        all_raw = []

        for i in range(days):
            day = today + timedelta(days=i)
            date_str = day.strftime("%d-%m-%Y")
            url = "%s/%s" % (BASE_URL, date_str)

            resp = downloader.get_or_none(
                url, extra_headers={"Referer": "https://www.medi1tv.com/ar/"})
            if resp is None:
                self.log.warning("Medi1TV: could not fetch %s (day %d/%d)", url, i + 1, days)
                continue

            day_progs = self._parse_day(resp.text, day.date())
            self.log.info("Medi1TV %s: found %d programmes", date_str, len(day_progs))
            if not day_progs:
                self.log.info("Medi1TV %s: zero programmes - the site may not have "
                              "published this day's grid yet (normal for far-future days)",
                              date_str)
            all_raw.extend(day_progs)

        if not all_raw:
            raise SourceWarning("Medi1TV: no programmes found across %d day(s)" % days)

        programmes = []
        for p in all_raw:
            aware_start = tzm.localize(p["start"])
            programmes.append(Programme(
                channel_id=CHANNEL_ID,
                start=aware_start,
                title=p["title"] or "برنامج",
                desc=p["desc"] or "لا توجد تفاصيل",
                lang="ar",
            ))
        return programmes

    # -- parsing --------------------------------------------------------
    def _parse_day(self, html_content, current_date):
        if not html_content:
            return []

        soup = get_soup(html_content)
        if soup is None:
            self.log.warning("Medi1TV: bs4 not available, cannot parse HTML for %s", current_date)
            return []

        links = soup.find_all("a")
        programs = []
        i = 0
        while i < len(links):
            txt = links[i].get_text(" ", strip=True)
            m = _TIME_PREFIX_RE.match(txt)
            if not m:
                i += 1
                continue

            hour, minute, title_text = m.group(1), m.group(2), m.group(3).strip()
            try:
                time_obj = dt.datetime.strptime("%s:%s" % (hour, minute), "%H:%M").time()
            except ValueError:
                i += 1
                continue

            start_datetime = dt.datetime.combine(current_date, time_obj)
            if programs and start_datetime < programs[-1]["start"]:
                start_datetime += timedelta(days=1)

            # Look ahead (bounded) for the description: the next link with
            # substantial, non-boilerplate text, stopping as soon as we hit
            # the NEXT time-prefixed link (that's the following programme).
            description = ""
            j = i + 1
            lookahead_limit = min(len(links), i + 8)
            while j < lookahead_limit:
                candidate = links[j].get_text(" ", strip=True)
                if _TIME_PREFIX_RE.match(candidate):
                    break
                normalized = candidate.strip().lower()
                if len(candidate) > 12 and normalized not in _BOILERPLATE_LINK_TEXTS:
                    description = candidate
                    break
                j += 1

            programs.append({
                "start": start_datetime,
                "title": title_text or "برنامج",
                "desc": description or "لا توجد تفاصيل",
            })
            i += 1

        return programs

