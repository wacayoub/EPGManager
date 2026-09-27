# -*- coding: utf-8 -*-
"""
sources/snrt.py

Ported from 3.SNRT.py. Preserves: 5 SNRT channels, the synthetic Aflam TV
filler schedule, detailed-description fetching per programme, news-keyword
retitling, and TNT/بث أرضي tagging.

Known limitation (documented, not silently "fixed" - see spec section 45):
the SNRT grid pages do not take a date parameter the way Medi1TV's do; the
site simply shows whatever window of the schedule is currently live on the
page (usually just the surrounding day or two, encoded in a per-block CSS
class like "...20260809..."). This script cannot force multiple days out of
a page that doesn't offer them, so it ingests everything the page currently
returns rather than looping `days` times like the other sources do.

Verification note (2026-08-09): a live fetch of snrt.ma returned a
"JavaScript is required" interstitial to our verification tool. This may be
specific to that tool's fetcher (no cookies/JS) rather than a real
anti-bot wall against plain `requests` + a browser User-Agent (the original
script was apparently working against this site with just `requests`).
To be safe, this source tries a plain request first and automatically
falls back to `cloudscraper` (if installed) on failure - see
`use_cloudscraper` below and core/downloader.py.
"""

import re
import html as html_lib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import EPGSource
from ..core.downloader import Downloader
from ..core.parser import get_lxml_tree, SourceWarning
from ..core.utils import clean_text
from ..core.xmltv import Programme

SNRT_CHANNELS = {
    "AlAoula": "https://www.snrt.ma/ar/node/1208",
    "Arrabiaa": "https://www.snrt.ma/ar/node/4071",
    "AlMaghribiya": "https://www.snrt.ma/ar/node/4072",
    "Assadisa": "https://www.snrt.ma/ar/node/4073",
    "Tamazight": "https://www.snrt.ma/ar/node/4075",
}

GENERIC_DESCRIPTIONS = {
    "أحوال الطقس": "نشرة جوية مفصلة حول حالة الطقس في المملكة المغربية.",
}

NEWS_KEYWORDS = {
    "الظهيرة": "أخبار الظهيرة",
    "الأمازيغية": "الأخبار الأمازيغية",
    "الفرنسية": "الأخبار الفرنسية",
    "الإسبانية": "الأخبار الإسبانية",
    "الرئيسية": "الأخبار الرئيسية",
    "الأخيرة": "الأخبار الأخيرة",
    "الرياضية": "أخبار الرياضة",
}


class SNRTSource(EPGSource):
    id = "snrt"
    name = "SNRT"
    output_filename = "snrt.xml"
    default_days = 7   # requested; see class docstring re: real-world limits
    min_programs = 3
    use_cloudscraper = True  # opt-in fallback if plain requests gets blocked

    def fetch(self, downloader, tzm, days, config, builder):
        all_progs = []

        # A dedicated fast, single-attempt downloader for the detail-page
        # fetches below: SNRT's own /node/ pages can 404 or be slow for a
        # given programme fairly often, and this method is called once PER
        # PROGRAMME (potentially hundreds of times). Using the main
        # `downloader` (which retries 3x with backoff on failure, matching
        # the beIN/2M bug found and fixed elsewhere) would multiply any
        # unresponsive page's cost by ~3x across the whole batch. The
        # original script used a plain, single-attempt `requests.get(...,
        # timeout=5)` here with a silent fallback - restored below.
        detail_downloader = Downloader(retries=1, timeout=5)

        workers = max(1, min(len(SNRT_CHANNELS), config.get_parallel_workers() if config else 4))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._process_channel, downloader, tzm, name, url): name
                for name, url in SNRT_CHANNELS.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    all_progs.extend(future.result())
                except Exception as e:
                    self.log.warning("SNRT channel '%s' failed: %s", name, e)

        all_progs.extend(self._generate_aflam_epg(tzm))

        if not all_progs:
            raise SourceWarning("SNRT: no programmes found on any channel")

        # Detailed descriptions (best-effort, parallel, never fatal)
        detailed = [p for p in all_progs if p.get("programme_url")]
        if detailed:
            self.log.info("SNRT: fetching %d detailed descriptions...", len(detailed))
            with ThreadPoolExecutor(max_workers=min(10, len(detailed))) as executor:
                future_map = {
                    executor.submit(self._fetch_detailed_description, detail_downloader,
                                     p["programme_url"], p["description_short"]): p
                    for p in detailed
                }
                for future in as_completed(future_map):
                    prog = future_map[future]
                    try:
                        prog["description_final"] = future.result()
                    except Exception:
                        prog["description_final"] = prog["description_short"]

        for ch_id, url in SNRT_CHANNELS.items():
            builder.add_channel(ch_id, [(ch_id, None)])
        builder.add_channel("AFLAM.ma", [("AFLAM.ma", None)])

        programmes = []
        for p in all_progs:
            title = self._apply_news_and_tnt_rules(p)
            desc = p.get("description_final", p.get("description_short", title))
            if title == "أحوال الطقس":
                desc = GENERIC_DESCRIPTIONS["أحوال الطقس"]
            programmes.append(Programme(
                channel_id=p["channel_name"],
                start=p["start_dt"],
                title=title,
                desc=desc,
                lang="ar",
            ))
        return programmes

    # -- per-channel scraping --------------------------------------------
    def _process_channel(self, downloader, tzm, channel_name, url):
        resp = downloader.get_or_none(url)
        if resp is None:
            return []

        tree = get_lxml_tree(resp.text)
        if tree is None:
            self.log.warning("SNRT '%s': lxml unavailable or HTML unparsable", channel_name)
            return []

        raw_extracted = []
        for block in tree.xpath("//div[contains(@class, 'grille-line')]"):
            grille_class = block.get("class", "")
            match_date = re.search(r"\b(\d{8})\b", grille_class)
            if not match_date:
                continue

            date_str = match_date.group(1)
            start_time_nodes = block.xpath(".//div[contains(@class, 'grille-time')]/text()")
            title_els = block.xpath(".//h2[contains(@class, 'program-title-sm')]/text()")
            link_el = (block.xpath(".//a[contains(@class, 'program-title-slide-over')]")
                       or block.xpath(".//a"))

            if not (start_time_nodes and title_els):
                continue

            time_raw = " ".join(start_time_nodes).strip()
            match_t = re.search(r"(\d{1,2})[H:](\d{2})", time_raw, re.IGNORECASE)
            if not match_t:
                continue

            h, m = match_t.groups()
            clean_time = "%02d:%s" % (int(h), m)
            prog_url = link_el[0].get("href") if link_el else None
            if prog_url and prog_url.startswith("/"):
                prog_url = "https://www.snrt.ma" + prog_url

            title = clean_text(title_els[0])
            raw_node_text = " ".join(t for t in block.xpath(".//text()") if t.strip())
            desc_short = " ".join(
                d for d in block.xpath(".//div[contains(@class, 'grille-content')]/text()") if d.strip())

            if "الرئيسية" in raw_node_text:
                desc_short += " الرئيسية"
            elif "الأخيرة" in raw_node_text:
                desc_short += " الأخيرة"

            try:
                dt_obj = datetime.strptime("%s %s" % (date_str, clean_time), "%Y%m%d %H:%M")
                raw_extracted.append({
                    "start_dt_naive": dt_obj,
                    "title": title,
                    "channel_name": channel_name,
                    "programme_url": prog_url,
                    "raw_text": clean_text(raw_node_text),
                    "description_short": clean_text(desc_short) or title,
                })
            except Exception:
                continue

        raw_extracted.sort(key=lambda x: x["start_dt_naive"])

        final_progs = []
        last_dt = None
        for p in raw_extracted:
            curr_dt = p["start_dt_naive"]
            if last_dt and curr_dt < last_dt and curr_dt.hour < 7:
                curr_dt += timedelta(days=1)
                p["start_dt_naive"] = curr_dt

            p["start_dt"] = tzm.localize(curr_dt)

            if any(f["start_dt"] == p["start_dt"] and f["title"] == p["title"] for f in final_progs):
                continue
            final_progs.append(p)
            last_dt = curr_dt

        final_progs.sort(key=lambda x: x["start_dt"])
        return final_progs

    def _generate_aflam_epg(self, tzm):
        programs = []
        start_point = tzm.today_midnight()
        for i in range(56):  # 7 days * 8 slots (3h) - unchanged from original
            current_start = start_point.replace(tzinfo=None) + timedelta(hours=i * 3)
            programs.append({
                "start_dt": tzm.localize(current_start),
                "title": "برامج قناة السابعة AFLAM",
                "channel_name": "AFLAM.ma",
                "description_short": "أفضل الأفلام والبرامج السينمائية على القناة السابعة المغربية",
                "raw_text": "",
                "programme_url": None,
            })
        return programs

    def _fetch_detailed_description(self, downloader, programme_url, description_short):
        if not programme_url:
            return description_short
        resp = downloader.get_or_none(programme_url)
        if resp is None:
            return description_short
        tree = get_lxml_tree(resp.text)
        if tree is None:
            return description_short
        try:
            desc_elements = tree.xpath(
                "//div[contains(@class, 'content')]//p//text() | "
                "//div[contains(@class, 'field-items')]//text() | "
                "//div[contains(@class, 'content')]//div/text()"
            )
            if desc_elements:
                raw_description = " ".join(d for d in desc_elements if d.strip())
                description_long = clean_text(raw_description)
                if len(description_long) > len(description_short):
                    return description_long
        except Exception:
            pass
        return description_short

    def _apply_news_and_tnt_rules(self, p):
        title = p["title"]
        desc = p.get("description_final", p.get("description_short", ""))
        raw_text = p.get("raw_text", "")

        if "الأخبار" in title or title == "نشرة":
            context = "%s %s" % (desc, raw_text)
            for keyword, new_title in NEWS_KEYWORDS.items():
                if keyword in context:
                    title = new_title
                    break

        context_tnt = "%s %s %s" % (title, desc, raw_text)
        if ("بث ارضي" in context_tnt or "بث أرضي" in context_tnt
                or "TNT" in context_tnt.upper()):
            if "TNT" not in title.upper() and "أرضي" not in title and "ارضي" not in title:
                title = "%s [TNT / بث أرضي]" % title

        return title
