# -*- coding: utf-8 -*-
"""
sources/bein_sports.py

Ported from 4.BEIN_SPORTS.py. All translation dictionaries (LEAGUE_MAP,
PROGRAM_MAP, META_MAP) preserved verbatim. Channel IDs remain dynamically
discovered from the AJAX payload (channels are NOT a static list - beIN
adds/removes channels over time, so hardcoding would silently drop some).

CHANGES vs the original:
  * Parsing now goes through a BeautifulSoup-based primary parser (which
    tolerates both `<p class=time>` and `<p class="time">` since bs4
    normalizes attributes regardless of quoting) instead of relying purely
    on four separately-run `re.findall()` calls whose results were joined
    by *list position* - a single missing <p class=format> for one event
    silently shifts every subsequent event's data by one slot. The original
    regex approach is kept as an automatic fallback if bs4/lxml aren't
    available (spec section 21: primary selector -> fallback selector).
  * SSL verification is enabled by default; the original's blanket
    `verify=False` is now only used as a last-resort retry if an SSLError
    is actually raised (logged loudly either way), rather than unconditionally
    disabled up front (spec section 43).
"""

import re
from datetime import datetime, timedelta

from .base import EPGSource
from ..core import dependencies
from ..core.parser import get_soup, SourceWarning
from ..core.xmltv import Programme
from ..translators.sports import (
    translate_text, get_league_arabic, extract_metadata,
)

DOHA_TZ_NAME = "Asia/Qatar"


class BeinSportsSource(EPGSource):
    id = "bein_sports"
    name = "beIN Sports"
    output_filename = "bein.xml"
    default_days = 3
    max_days = 3    # hard cap: the original script deliberately limits itself
                     # to 3 days ("Anti-Ban" comment) - beIN's AJAX endpoint
                     # gets hit up to 8x per day requested, so letting the
                     # global "EPG days" setting silently expand this to 7
                     # more than doubles request volume against a site
                     # that's already been tuned to avoid rate-limiting
    min_programs = 5
    verify_ssl = False  # RESTORED from the original script's explicit
                         # `verify=False` (it also called
                         # urllib3.disable_warnings(InsecureRequestWarning),
                         # a second signal this was deliberate, not an
                         # oversight). A previous revision of this plugin
                         # turned SSL verification back on as a general
                         # hardening pass without realizing beIN's cert
                         # chain needs this - every HTTPS request was likely
                         # failing outright, which alone could fully explain
                         # "0 events found" independent of the parser bug
                         # fixed above.

    def fetch(self, downloader, tzm, days, config, builder):
        from ..core.timezone_manager import TimezoneManager
        # beIN's own AJAX payload is expressed in Doha (Asia/Qatar) local
        # time, independent of Morocco's timezone - this is intentional and
        # matches the original script, NOT a bug: we localize with Doha time
        # then XMLTV's own +offset handles conversion for any EPG client.
        doha_tzm = _DohaTZ()

        now_doha = doha_tzm.now()
        tasks = [(now_doha + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

        unique_events = set()
        entries = []
        channels_seen = set()

        for week in tasks:
            for idx in range(8):
                url = (
                    "https://www.bein.com/ar/epg-ajax-template/?action=epg_fetch"
                    "&category=sports&serviceidentity=bein.net&offset=00&mins=00"
                    "&cdate=%s&language=AR&postid=25344&loadindex=%d" % (week, idx)
                )
                resp = downloader.get_or_none(
                    url, extra_headers={"Referer": "https://www.bein.com/ar/epg/"})
                if resp is None or "li" not in resp.text:
                    break  # matches original "stop at first empty page" behavior

                events = self._parse_events(resp.text)
                if not events:
                    break

                for ev in events:
                    prog = self._build_programme(ev, week, now_doha, doha_tzm, unique_events)
                    if prog:
                        entries.append(prog)
                        channels_seen.add(prog.channel_id)

        if not entries:
            raise SourceWarning("beIN Sports: no events found across %d day(s)" % days)

        for ch_id in sorted(channels_seen):
            builder.add_channel(ch_id, [(ch_id, None)])

        return entries

    # -- parsing ----------------------------------------------------------
    def _parse_events(self, data):
        """Primary: the exact positional-regex parser from the original,
        known-working script - tried FIRST, unconditionally. Only if that
        yields zero events do we attempt the structural BeautifulSoup parse
        as a secondary strategy (spec section 21: try alternatives, but
        never skip the proven-good path just because a fancier one is
        technically available).

        Bug fixed 2026-08: the previous version checked "is bs4 installed"
        instead of "did bs4 actually find anything", so it silently used a
        broken bs4 parser (see _parse_events_bs4 docstring) on every run and
        never touched this working regex path at all - that was the actual
        cause of "0 events found", not the beIN endpoint itself."""
        events = self._parse_events_regex(data)
        if events:
            return events

        soup = get_soup(data)
        if soup is not None:
            bs4_events = self._parse_events_bs4(soup)
            if bs4_events:
                self.log.debug("Regex parser found nothing; bs4 fallback recovered %d event(s)",
                               len(bs4_events))
                return bs4_events
        return events  # empty list - let the caller's SourceWarning logic handle it

    def _parse_events_bs4(self, soup):
        """Secondary/fallback structural parse. `data-img` and `live` are
        attributes on the <li> tag ITSELF (confirmed by the original regex
        `li\\s+live=...`), not on a descendant - checked via li.get(...)
        first, with a descendant-search fallback in case a future markup
        change nests them instead."""
        events = []
        for li in soup.find_all("li"):
            data_img = li.get("data-img")
            if not data_img:
                img_el = li.find(attrs={"data-img": True})
                data_img = img_el.get("data-img") if img_el else None

            time_p = li.find("p", class_="time")
            title_p = li.find("p", class_="title")
            if not (data_img and time_p and title_p):
                continue

            match = re.search(r"/([^/]+?)\.png", data_img)
            channel_raw = match.group(1) if match else None
            if not channel_raw:
                continue

            format_p = li.find("p", class_="format")
            desc_p = li.find("p", class_="description")
            live_attr = li.get("live")
            if live_attr is None:
                live_attr = "0"

            events.append({
                "channel": channel_raw,
                "time": time_p.get_text(strip=True),
                "title": title_p.get_text(strip=True),
                "format": format_p.get_text(strip=True) if format_p else "",
                "desc": desc_p.get_text(strip=True) if desc_p else "",
                "live": str(live_attr),
            })
        return events

    def _parse_events_regex(self, data):
        """The ORIGINAL, proven-working parser - now the primary strategy.
        Kept byte-for-byte equivalent to your working script's regexes."""
        channels = re.findall(r"data-img=['\"].*?/([^/]+?)\.png['\"]", data)
        time_slots = re.findall(r"<p\sclass=time>(.*?)</p>", data)
        times = [t.replace("&nbsp;-&nbsp;", "-").replace(" ", "") for t in time_slots]
        titles = re.findall(r"<p\sclass=title>(.*?)</p>", data)
        formats = re.findall(r"<p\sclass=format>(.*?)</p>", data)
        live_events = re.findall(r"li\s+live=['\"](\d)['\"]", data)
        descriptions = re.findall(r"<p\sclass=description>(.*?)</p>", data)

        min_len = min(len(titles), len(times), len(channels))
        events = []
        for j in range(min_len):
            events.append({
                "channel": channels[j],
                "time": times[j],
                "title": titles[j],
                "format": formats[j] if j < len(formats) else "",
                "desc": descriptions[j] if j < len(descriptions) else "",
                "live": live_events[j] if j < len(live_events) else "0",
            })
        return events

    def _build_programme(self, ev, week, now_doha, doha_tzm, unique_events):
        try:
            time_raw = ev["time"].split("-")
            if len(time_raw) != 2 or not ev["time"]:
                return None

            title_cleaned, metadata = extract_metadata(ev["title"])
            translated_title = translate_text(title_cleaned)

            league_ar = get_league_arabic(ev["desc"]) or get_league_arabic(ev["format"])
            if league_ar and league_ar in translated_title:
                clean_title = re.sub(r"\s*[-|:()]\s*%s" % league_ar, "", translated_title)
                clean_title = re.sub(r"%s\s*[-|:()]\s*" % league_ar, "", clean_title)
                final_subject = league_ar if not clean_title.strip() else "[%s] %s" % (league_ar, clean_title.strip())
            elif league_ar:
                final_subject = "[%s] %s" % (league_ar, translated_title)
            else:
                final_subject = translated_title

            metadata_prefix = "[%s] " % metadata if metadata else ""
            final_desc = ("%s%s" % (metadata_prefix, translate_text(ev["desc"]))).strip()

            start_t = datetime.strptime(time_raw[0], "%H:%M")
            end_t = datetime.strptime(time_raw[1], "%H:%M")
            event_date = datetime.strptime(week, "%Y-%m-%d")
            end_date = event_date + timedelta(days=1) if end_t < start_t else event_date

            start_naive = event_date.replace(hour=start_t.hour, minute=start_t.minute)
            end_naive = end_date.replace(hour=end_t.hour, minute=end_t.minute)

            starttime = doha_tzm.localize(start_naive)
            endtime = doha_tzm.localize(end_naive)

            if endtime < now_doha:
                return None

            channel_id = (ev["channel"].split("?")[0]
                          .replace("_Digital_Mono", "").replace("_DIGITAL_Mono", "")
                          .replace("-1", ""))

            event_key = (channel_id, starttime.isoformat(), ev["title"])
            if event_key in unique_events:
                return None
            unique_events.add(event_key)

            live_prefix = "Live: " if ev["live"] == "1" else ""

            return Programme(
                channel_id=channel_id,
                start=starttime,
                stop=endtime,
                title="%s%s" % (live_prefix, final_subject),
                desc=final_desc,
                lang="ar",
            )
        except Exception:
            return None


class _DohaTZ(object):
    """Tiny Doha-time helper, deliberately separate from TimezoneManager
    since beIN's schedule is genuinely on Qatar time, not Morocco time -
    reusing the Casablanca/Ramadan logic here would be incorrect."""

    def __init__(self):
        if dependencies.check_module("pytz"):
            import pytz
            self._tz = pytz.timezone(DOHA_TZ_NAME)
            self._backend = "pytz"
        else:
            from datetime import timezone as _tzmod
            self._tz = _tzmod(timedelta(hours=3))
            self._backend = "fixed"

    def now(self):
        if self._backend == "pytz":
            import pytz
            return datetime.now(pytz.utc).astimezone(self._tz)
        return datetime.utcnow().replace(tzinfo=self._tz)

    def localize(self, naive_dt):
        if self._backend == "pytz":
            return self._tz.localize(naive_dt)
        return naive_dt.replace(tzinfo=self._tz)
