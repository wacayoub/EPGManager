# -*- coding: utf-8 -*-
"""Medi1 TV local XMLTV source.

beta43 keeps the operator-provided Medi1 scraper semantics as the baseline, but
models the two real TV grids separately:
  * Medi1 TV Arabic
  * Medi1 TV Maghreb

Both channel IDs live in the same local XMLTV file so Smart Sources still shows
one simple "Medi1 TV" source.  The receiver's live lamedb decides which SRP(s)
exist for each channel identity.
"""

import datetime as dt
import re
from datetime import timedelta

from .base import EPGSource
from ..core.parser import get_soup, SourceWarning
from ..core.xmltv import Programme

# The .ma Maghreb path was supplied by the operator.  .com is kept as a
# fallback because the same Medi1TV site is served from both hostnames and one
# host can occasionally reject/break a request while the other still works.
FEEDS = (
    {
        "id": "MEDI1TV_AR.ma",
        "name": "Medi1 TV Arabic",
        "lang": "ar",
        "bases": (
            "https://www.medi1tv.com/ar/grille/arabic",
            "https://www.medi1tv.ma/ar/grille/arabic",
        ),
    },
    {
        "id": "MEDI1TV_MAGHREB.ma",
        "name": "Medi1 TV Maghreb",
        "lang": "ar",
        "bases": (
            "https://www.medi1tv.ma/ar/grille/maghreb",
            "https://www.medi1tv.com/ar/grille/maghreb",
        ),
    },
)

_TIME_PREFIX_RE = re.compile(r"^([0-2]?\d)[:hH]([0-5]\d)\s*(.*)$")
_BOILERPLATE_LINK_TEXTS = {
    "page de l'émission", "page de lemission", "صفحة البرنامج",
    "voir les jts", "شاهد النشرات", "en direct", "المباشر", "", "-",
}


class Medi1TVSource(EPGSource):
    id = "medi1tv"
    name = "Medi1 TV"
    output_filename = "medi1tv_ar.xml"
    default_days = 7
    min_programs = 3

    def fetch(self, downloader, tzm, days, config, builder):
        for feed in FEEDS:
            builder.add_channel(feed["id"], [(feed["name"], feed["lang"])])

        today = tzm.today_midnight()
        programmes = []
        per_feed_counts = {}

        for feed in FEEDS:
            raw = []
            for i in range(days):
                day = today + timedelta(days=i)
                date_str = day.strftime("%d-%m-%Y")
                resp, used_url = self._fetch_day_response(
                    downloader, feed, date_str, allow_undated=(i == 0))
                if resp is None:
                    self.log.warning("Medi1TV %s: could not fetch %s", feed["name"], date_str)
                    continue

                day_progs = self._parse_day(resp.text, day.date())
                self.log.info("Medi1TV %s %s: %d programmes via %s",
                              feed["name"], date_str, len(day_progs), used_url)
                raw.extend(day_progs)

            # Deduplicate a host-fallback/current-page overlap by start+title.
            seen = set()
            ordered = []
            for p in sorted(raw, key=lambda x: x["start"]):
                key = (p["start"], (p["title"] or "").strip().lower())
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(p)

            count = 0
            for idx, p in enumerate(ordered):
                start_aware = tzm.localize(p["start"])
                stop_aware = None

                # Medi1 frequently exposes only the current broadcaster day.
                # If the final fetched event begins at 23:xx, the next grid
                # starts at 05:00.  Do not leave the 00:00 -> 05:00 EPG hole.
                if idx == len(ordered) - 1 and p["start"].hour == 23:
                    stop_naive = dt.datetime.combine(
                        p["start"].date() + timedelta(days=1), dt.time(5, 0))
                    stop_aware = tzm.localize(stop_naive)

                programmes.append(Programme(
                    channel_id=feed["id"],
                    start=start_aware,
                    stop=stop_aware,
                    title=p["title"] or "برنامج",
                    desc=p["desc"] or "لا توجد تفاصيل",
                    title_lang=self._guess_lang(p["title"], feed["lang"]),
                    desc_lang=self._guess_lang(p["desc"], feed["lang"]),
                ))
                count += 1
            per_feed_counts[feed["id"]] = count

        if not programmes:
            raise SourceWarning("Medi1TV: no programmes found on Arabic or Maghreb grids")

        # Do not fail the whole Medi1 source when one of the two variants is
        # temporarily unavailable.  XMLTVBuilder keeps last-known-good output
        # protection at file level; the log makes the partial state visible.
        for feed in FEEDS:
            if per_feed_counts.get(feed["id"], 0) == 0:
                self.log.warning("Medi1TV: %s returned zero programmes this run", feed["name"])

        return programmes

    def _fetch_day_response(self, downloader, feed, date_str, allow_undated=False):
        referer = "https://www.medi1tv.ma/ar/"
        for base in feed["bases"]:
            url = "%s/%s" % (base, date_str)
            resp = downloader.get_or_none(url, extra_headers={"Referer": referer})
            if resp is not None:
                return resp, url

        # Some deployments expose the current grid only at the undated path.
        # Use this fallback for today only; never reuse today's grid for future
        # days, which would fabricate EPG data.
        if allow_undated:
            for base in feed["bases"]:
                resp = downloader.get_or_none(base, extra_headers={"Referer": referer})
                if resp is not None:
                    return resp, base
        return None, ""

    @staticmethod
    def _guess_lang(text, default="ar"):
        text = text or ""
        if re.search(r"[\u0600-\u06ff]", text):
            return "ar"
        if re.search(r"[A-Za-zÀ-ÿ]", text):
            return "fr"
        return default

    def _parse_day(self, html_content, current_date):
        """Parse one Medi1 grid without ever merging CTA/navigation into title.

        Medi1 cards may expose HH:MM/title, synopsis and a "شاهد النشرات"
        action as separate text nodes/anchors.  beta43 scanned following links,
        which could turn the CTA into the title and even use "MEDI1TV Maghreb"
        as description.  beta48 only reads the timed programme anchor/card.
        """
        if not html_content:
            return []
        soup = get_soup(html_content)
        if soup is None:
            return []

        cta_labels = set(x.lower() for x in (
            "صفحة البرنامج", "صفحة النشرة", "شاهد النشرات", "شاهد البرنامج",
            "التفاصيل", "المزيد", "voir les jts", "voir le programme",
            "voir plus", "details", "détails", "en direct", "المباشر",
            "MEDI1TV Maghreb", "MEDI1TV Arabic", "MEDI1TV Afrique",
        ))

        def clean(value):
            value = (value or "").replace("\xa0", " ")
            return re.sub(r"\s+", " ", value).strip()

        def useful(value):
            value = clean(value)
            return bool(value and value.lower() not in cta_labels)

        programs = []
        seen = set()
        previous_minutes = None
        programme_date = current_date

        for link in soup.find_all("a"):
            # Preserve child-node boundaries: on current Medi1 markup this is
            # what separates title from synopsis even though get_text() joins them.
            pieces = [clean(x) for x in link.stripped_strings if clean(x)]
            if not pieces:
                continue

            m = _TIME_PREFIX_RE.match(pieces[0])
            time_only = re.match(r"^([0-2]?\d)[:hH]([0-5]\d)$", pieces[0])
            payload = []
            if time_only:
                hour, minute = int(time_only.group(1)), int(time_only.group(2))
                payload.extend(pieces[1:])
            elif m:
                hour, minute = int(m.group(1)), int(m.group(2))
                remainder = clean(m.group(3))
                if remainder:
                    payload.append(remainder)
                payload.extend(pieces[1:])
            else:
                joined = clean(link.get_text(" ", strip=True))
                m = _TIME_PREFIX_RE.match(joined)
                if not m:
                    continue
                hour, minute = int(m.group(1)), int(m.group(2))
                remainder = clean(m.group(3))
                if remainder:
                    payload.append(remainder)

            # Remove CTA/navigation labels only; never borrow text from the
            # following unrelated anchors.
            payload = [x for x in payload if useful(x)]
            if not payload:
                continue

            title = payload[0]
            desc_parts = [x for x in payload[1:] if useful(x) and x != title]
            desc = clean(" ".join(desc_parts))

            # Defensive cleanup for markup variants that append the CTA to the
            # same node as the title.
            for label in sorted(cta_labels, key=len, reverse=True):
                if title.lower().endswith(" " + label):
                    title = clean(title[:-len(label)])
            if not title:
                title = "برنامج"
            if not desc:
                # No invented synopsis: a short title is safer than a channel
                # name/navigation string masquerading as programme description.
                desc = title

            minutes = hour * 60 + minute
            if previous_minutes is not None and minutes + 300 < previous_minutes:
                programme_date = programme_date + timedelta(days=1)
            previous_minutes = minutes
            start_datetime = dt.datetime.combine(programme_date, dt.time(hour, minute))

            key = (start_datetime, title.lower())
            if key in seen:
                continue
            seen.add(key)
            programs.append({"start": start_datetime, "title": title, "desc": desc})

        return programs
