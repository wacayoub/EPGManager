# -*- coding: utf-8 -*-
"""
sources/chada_2m.py

Ported from 2.2M_CHADA.py. Uses translators/darija.py for all title/desc
translation (dictionaries preserved verbatim - see that module).

KNOWN RISK (preserved, not silently changed - spec section 45): the 2M grid
is scraped from tv-programme.telecablesat.fr, a French listings aggregator,
and the original script localizes its displayed times as Europe/Paris, not
Africa/Casablanca. Paris observes DST (UTC+2 in summer) while Morocco holds
a fixed UTC+1 outside Ramadan, so if telecablesat's displayed times are
actually meant to represent Moroccan broadcast wall-clock time (rather than
genuine Paris time), this would be off by one hour during the European
summer. The original script's exact behavior is preserved here because
verifying which is correct requires comparing against 2M's own real
broadcast schedule, which is outside what these source files can confirm -
flagged here and in the README rather than guessed at.
"""

import html as html_lib
from datetime import datetime, timedelta

from .base import EPGSource
from ..core.parser import get_lxml_tree, SourceWarning
from ..core.xmltv import Programme
from ..core.timezone_manager import TimezoneManager
from ..translators.darija import DarijaTranslator

try:
    import pytz
    SOURCE_TZ = pytz.timezone("Europe/Paris")
except ImportError:
    pytz = None
    SOURCE_TZ = None

CHADA_HOST_PROFILES = {
    "imane bououlid": "Imane Bououlid Idrissi - Journaliste et animatrice talentueuse sur Chada TV, connue pour ses émissions sociétales et ses débats interactifs chaleureux.",
    "fakherddine": "Fakherddine Rajhi - Animateur et consultant sportif éminent, apportant des analyses tactiques pointues et une expertise footballistique inégalée.",
    "houssine chahb": "Houssine Chahb - Animateur vedette sur Chada, réputé pour son charisme, son ton convivial et ses interviews exclusives avec les plus grandes stars de la musique.",
    "idrissi": "Idrissi - Animateur dynamique et engagé, gérant des sessions de débats et de partages d'actualités au quotidien sur Chada TV.",
    "fakhri": "Fakhri - Présentateur et journaliste sportif passionné, dynamique dans le suivi de la Botola et de l'actualité sportive nationale.",
    "jamila ouyoub": "Jamila Ouyoub - Animatrice phare de l'émission familiale et sociale 'Chada Al Ousra'.",
    "sarah azmi": "Sarah Azmi - Présentatrice de 'Chada cover', dédiée aux talents musicaux.",
    "imad ntifi": "Imad Ntifi - Animateur célèbre de l'émission musicale 'Dandana'.",
    "majda kilani": "Majda Kilani - Animatrice talentueuse, présente notamment sur l'émission 'Assahm'.",
}

HOST_KEYWORDS = sorted([
    "imane bououlid idrissi", "imane bououlid", "fakherddine rajhi",
    "houssine chahb", "fakherddine", "fakhri", "houssine", "chahb",
    "imane", "bououlid", "idrissi", "jamila ouyoub", "sarah azmi",
    "imad ntifi", "majda kilani",
], key=len, reverse=True)

import re
_REMOVAL_PATTERN = re.compile(
    r"(?i)(?:[-:|]\s*)?(?:\b(?:avec|by|présenté par|animé par)\b\s*)?"
    r"(?:%s)\b\s*(?:[-:|])?" % "|".join(re.escape(k) for k in HOST_KEYWORDS)
)


class Chada2MSource(EPGSource):
    id = "chada_2m"
    name = "2M / Chada"
    output_filename = "2M_Chada.xml"
    default_days = 7
    min_programs = 5

    def fetch(self, downloader, tzm, days, config, builder):
        translator = DarijaTranslator()  # builds its own fast, single-attempt
                                          # downloader internally - see
                                          # translators/darija.py docstring

        builder.add_channel("2M", [("2M", "ar")])
        builder.add_channel("Chada TV", [("Chada TV", "fr")])

        programmes = []
        programmes.extend(self._fetch_2m(downloader, translator, days))
        programmes.extend(self._fetch_chada(downloader, translator, tzm, days))

        translator.flush()

        if not programmes:
            raise SourceWarning("2M/Chada: no programmes found for either channel")

        return programmes

    # -- 2M ---------------------------------------------------------------
    def _fetch_2m_day(self, downloader, translator, target_date, shift_days=0):
        programs = []
        base_url = "https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"
        periods = ["morning", "noon", "afternoon", "evening", "night"]
        target_str = target_date.strftime("%Y-%m-%d")

        after_massaiya = False
        after_infosoir = False

        for period in periods:
            resp = downloader.get_or_none(
                base_url, params={"date": target_str, "period": period})
            if resp is None:
                continue

            tree = get_lxml_tree(resp.text)
            if tree is None:
                continue

            blocks = (tree.xpath("//div[contains(@class,'item-programme')]")
                      or tree.xpath("//div[contains(@class,'news')]"))

            for block in blocks:
                time_el = block.xpath(".//div[contains(@class,'schedule-hour')]/text()")
                title_els = (block.xpath(".//div[contains(@class,'item-content')]//h3//strong")
                             or block.xpath(".//div[contains(@class,'item-content')]//h3"))
                desc_el = block.xpath(".//div[contains(@class,'item-content')]//p/text()")

                if not time_el or not title_els:
                    continue

                from lxml import etree
                time_str = time_el[0].strip()
                title_raw = html_lib.unescape(
                    etree.tostring(title_els[0], encoding="unicode", method="text").strip())
                desc_raw = " ".join(d.strip() for d in desc_el if d.strip()) if desc_el else title_raw

                clean_title = title_raw.lower()
                if "massaiya" in clean_title or "المسائية" in title_raw:
                    after_massaiya, after_infosoir = True, False
                if "info soir" in clean_title or "infosoir" in clean_title:
                    after_infosoir, after_massaiya = True, False

                try:
                    time_obj = datetime.strptime(time_str, "%H:%M").time()
                except ValueError:
                    continue

                actual_date = target_date
                if time_obj.hour < 5 and period in ("evening", "night"):
                    actual_date += timedelta(days=1)

                dt_naive = datetime.combine(actual_date, time_obj)
                if shift_days:
                    dt_naive += timedelta(days=shift_days)

                if SOURCE_TZ is not None:
                    dt_aware = SOURCE_TZ.localize(dt_naive)
                else:
                    from datetime import timezone as _tzfixed
                    dt_aware = dt_naive.replace(tzinfo=_tzfixed(timedelta(hours=1)))

                is_whitelisted = translator.is_whitelisted_french(title_raw)

                programs.append({
                    "start": dt_aware,
                    "title": translator.translate(title_raw, after_massaiya, after_infosoir,
                                                    is_desc=False, keep_french=is_whitelisted),
                    "desc": translator.translate(desc_raw, after_massaiya, after_infosoir,
                                                   is_desc=True, keep_french=is_whitelisted),
                })
        return programs

    def _fetch_2m(self, downloader, translator, days):
        self.log.info("2M: fetching %d day(s)...", days)
        today = (SOURCE_TZ.localize(datetime.utcnow()) if SOURCE_TZ else datetime.utcnow()).date() \
            if SOURCE_TZ is None else datetime.now(SOURCE_TZ).date()

        target_dates = [today + timedelta(days=i) for i in range(days)]

        # Each day's 5 periods must run IN ORDER (after_massaiya/
        # after_infosoir tracking carries state from one period to the
        # next), but the days themselves are fully independent, so we run
        # up to 4 days concurrently to cut wall-clock time - this was
        # previously fully sequential (7 days x 5 periods = 35 requests
        # back-to-back), a large chunk of the reported 309s runtime.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        raw = []
        with ThreadPoolExecutor(max_workers=min(4, len(target_dates))) as executor:
            futures = {
                executor.submit(self._fetch_2m_day_with_fallback, downloader, translator,
                                 target_date, today): target_date
                for target_date in target_dates
            }
            for future in as_completed(futures):
                try:
                    raw.extend(future.result())
                except Exception as e:
                    self.log.warning("2M: day fetch failed: %s", e)

        seen_times = set()
        unique = []
        for p in raw:
            if p["start"] not in seen_times:
                unique.append(p)
                seen_times.add(p["start"])

        return [Programme(channel_id="2M", start=p["start"], title=p["title"],
                           desc=p["desc"], lang="ar") for p in unique]

    def _fetch_2m_day_with_fallback(self, downloader, translator, target_date, today):
        day_progs = self._fetch_2m_day(downloader, translator, target_date)

        if not day_progs:
            # Original fallback: telecablesat recycles a weekly grid
            # pattern, so if a given day is empty, look back N*7 days
            # and shift the result forward - preserved verbatim.
            diff = (target_date - today).days
            weeks_back = max(1, (diff // 7) + 1)
            past_date = target_date - timedelta(days=7 * weeks_back)
            day_progs = self._fetch_2m_day(downloader, translator, past_date, shift_days=7 * weeks_back)
            if not day_progs:
                weeks_back += 1
                past_date = target_date - timedelta(days=7 * weeks_back)
                day_progs = self._fetch_2m_day(downloader, translator, past_date, shift_days=7 * weeks_back)

        return day_progs

    # -- Chada TV -----------------------------------------------------------
    def _fetch_chada(self, downloader, translator, tzm, days):
        self.log.info("Chada TV: fetching %d day(s)...", days)
        resp = downloader.get_or_none("https://chada.ma/fr/chada-tv/grille-tv/")
        if resp is None:
            return []

        tree = get_lxml_tree(resp.text)
        if tree is None:
            return []

        today = tzm.now().date()
        raw_data = []

        for bad in tree.xpath("//script | //style | //nav | //footer | //header"):
            parent = bad.getparent()
            if parent is not None:
                parent.remove(bad)

        main_area = (tree.xpath("//div[contains(@class, 'elementor-text-editor')]")
                     or tree.xpath("//div[contains(@class, 'posts-area')]")
                     or [tree.find("body")])

        if main_area and main_area[0] is not None:
            text_blocks = main_area[0].xpath(".//text()")
            full_text = "  ".join(t.strip() for t in text_blocks if t.strip())

            matches = re.finditer(
                r"(\d{2}:\d{2})(?:\s*(?:à|-)\s*\d{2}:\d{2})?\s*[\.\-]?\s*(.*?)(?=\s*(?:\d{2}:\d{2})|$)",
                full_text)
            for match in matches:
                start_time_str = match.group(1)
                title = match.group(2).strip(" .\t\r\n-|")
                title = re.sub(r"\s{2,}", " ", title)
                if len(title) > 120:
                    title = title[:120].rsplit(" ", 1)[0] + "..."

                if title and len(title) > 2:
                    try:
                        start_t = datetime.strptime(start_time_str, "%H:%M").time()
                        if not raw_data or raw_data[-1]["time"] != start_t:
                            raw_data.append({"time": start_t, "title": title})
                    except ValueError:
                        pass

        sanitized_data = self._sanitize_chada_sequence(raw_data)
        if not sanitized_data:
            return []

        processed_data = self._extract_hosts(sanitized_data)

        programmes = []
        for i in range(days):
            base_day = today + timedelta(days=i)
            current_day = base_day
            prev_time = processed_data[0]["time"]

            for item in processed_data:
                if item["time"] < prev_time:
                    current_day += timedelta(days=1)
                prev_time = item["time"]

                dt_naive = datetime.combine(current_day, item["time"])
                dt_final = tzm.localize(dt_naive)

                programmes.append(Programme(
                    channel_id="Chada TV", start=dt_final,
                    title=item["title"], desc=item["desc"], lang="fr",
                ))
        return programmes

    def _sanitize_chada_sequence(self, raw_data):
        sanitized = []
        last_t = None
        for item in raw_data:
            if last_t is None:
                sanitized.append(item)
                last_t = item["time"]
            elif item["time"] > last_t:
                sanitized.append(item)
                last_t = item["time"]
            elif item["time"] < last_t and last_t.hour >= 18 and item["time"].hour <= 5:
                sanitized.append(item)
                last_t = item["time"]
        return sanitized

    def _extract_hosts(self, sanitized_data):
        processed = []
        for item in sanitized_data:
            orig_title = item["title"]
            lower_title = orig_title.lower()
            final_desc = orig_title

            found_host = False
            for host_key, host_profile in CHADA_HOST_PROFILES.items():
                if host_key in lower_title:
                    final_desc = "Émission présentée par %s" % host_profile
                    found_host = True
                    break

            if not found_host:
                for ind in ["imane", "houssine", "idrissi", "fakher", "chahb",
                            "bououlid", "jamila", "sarah", "imad", "majda"]:
                    if ind in lower_title:
                        final_desc = "Émission animée par l'équipe de Chada TV (Réf: %s)" % orig_title
                        break

            final_title = _REMOVAL_PATTERN.sub("", orig_title)
            final_title = re.sub(r"\s{2,}", " ", final_title).strip(" .\t\r\n-|:")
            if not final_title or len(final_title) < 3:
                final_title = "Programme Chada TV"

            processed.append({"time": item["time"], "title": final_title, "desc": final_desc})
        return processed
