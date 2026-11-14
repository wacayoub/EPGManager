# -*- coding: utf-8 -*-
"""
sources/chada_2m.py

Ported from 2.2M_CHADA.py. Uses translators/darija.py for 2M title/description Arabic enrichment.
beta43 keeps the operator-provided scraper as the source baseline but replaces
the unsafe word-substitution Darija fallback with the safer MSA-first engine.

rc45 timezone correction: 2M listing times are treated as the channel's
Morocco wall-clock schedule.  Morocco is legal GMT from 2026-09-20, so the
shared TimezoneManager pins current/future 2M events to +0000 even on images
with stale Africa/Casablanca tzdata.
"""

import html as html_lib
import os
from datetime import datetime, timedelta

from .base import EPGSource
from ..core.parser import get_lxml_tree, SourceWarning
from ..core.xmltv import Programme
from ..core.timezone_manager import TimezoneManager
from ..translators.darija import DarijaTranslator
from ..translators.arabic import contains_arabic

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


# RC23: curated Chada TV metadata used both by the receiver fallback scraper and
# by the GitHub Morocco Cloud generator.  The schedule page often publishes a
# short Latin title with the presenter appended; keep the canonical programme
# title in Arabic when known and move presenter/context into a useful Arabic
# synopsis. Unknown programmes are preserved rather than guessed.
CHADA_SHOW_METADATA = {
    "dandana": {
        "title": "دندنة",
        "desc": "برنامج موسيقي على شدى تي في يستضيف فنانين ويتابع جديد أعمالهم، مع حوار وفقرات موسيقية.",
    },
    "dandanah": {
        "title": "دندنة",
        "desc": "برنامج موسيقي على شدى تي في يستضيف فنانين ويتابع جديد أعمالهم، مع حوار وفقرات موسيقية.",
    },
    "l botola": {
        "title": "البطولة",
        "desc": "برنامج رياضي يتابع أخبار كرة القدم المغربية وأبرز مباريات البطولة، مع التحليل والنقاش الرياضي.",
    },
    "lbotola": {
        "title": "البطولة",
        "desc": "برنامج رياضي يتابع أخبار كرة القدم المغربية وأبرز مباريات البطولة، مع التحليل والنقاش الرياضي.",
    },
    "botola": {
        "title": "البطولة",
        "desc": "برنامج رياضي يتابع أخبار كرة القدم المغربية وأبرز مباريات البطولة، مع التحليل والنقاش الرياضي.",
    },
    "100 var": {
        "title": "100% VAR",
        "desc": "مجلة رياضية لتحليل أخبار كرة القدم والمباريات، مع نقاشات فنية وتكتيكية وتركيز على الكرة المغربية.",
    },
    "var": {
        "title": "100% VAR",
        "desc": "مجلة رياضية لتحليل أخبار كرة القدم والمباريات، مع نقاشات فنية وتكتيكية وتركيز على الكرة المغربية.",
    },
    "chada al ousra": {
        "title": "شدى الأسرة",
        "desc": "برنامج اجتماعي وأسري على شدى تي في يناقش قضايا الأسرة والمجتمع ويستضيف مختصين وضيوفاً من مجالات مختلفة.",
    },
    "chada al osra": {
        "title": "شدى الأسرة",
        "desc": "برنامج اجتماعي وأسري على شدى تي في يناقش قضايا الأسرة والمجتمع ويستضيف مختصين وضيوفاً من مجالات مختلفة.",
    },
    "assahm": {
        "title": "السهم",
        "desc": "برنامج حواري على شدى تي في يتناول مواضيع الساعة وقضايا المجتمع مع ضيوف ونقاش مباشر.",
    },
    "sahm": {
        "title": "السهم",
        "desc": "برنامج حواري على شدى تي في يتناول مواضيع الساعة وقضايا المجتمع مع ضيوف ونقاش مباشر.",
    },
    "9hiwa ola atay": {
        "title": "قهيوة ولا أتاي",
        "desc": "موعد خفيف على شدى تي في يجمع الحوار والترفيه ومواضيع الحياة اليومية في أجواء مغربية.",
    },
    "qhiwa ola atay": {
        "title": "قهيوة ولا أتاي",
        "desc": "موعد خفيف على شدى تي في يجمع الحوار والترفيه ومواضيع الحياة اليومية في أجواء مغربية.",
    },
    "hbab rabab": {
        "title": "حباب رباب",
        "desc": "برنامج ترفيهي وفني على شدى تي في مع فقرات متنوعة وضيوف من الساحة الفنية والإعلامية.",
    },
    "hbab rabab": {
        "title": "حباب رباب",
        "desc": "برنامج ترفيهي وفني على شدى تي في مع فقرات متنوعة وضيوف من الساحة الفنية والإعلامية.",
    },
    "funny m3a nani": {
        "title": "فاني مع ناني",
        "desc": "برنامج ترفيهي على شدى تي في يقدم فقرات خفيفة ومواقف مرحة وضيوفاً متنوعين.",
    },
    "din wa dounia": {
        "title": "دين ودنيا",
        "desc": "برنامج ديني واجتماعي يتناول قضايا الحياة اليومية من منظور توعوي مبسط.",
    },
    "dine wa dounia": {
        "title": "دين ودنيا",
        "desc": "برنامج ديني واجتماعي يتناول قضايا الحياة اليومية من منظور توعوي مبسط.",
    },
    "mag info": {
        "title": "MAG INFO - الأخبار",
        "desc": "موعد إخباري على شدى تي في يقدم أبرز الأخبار والمستجدات الوطنية والدولية.",
    },
    "chada cover": {
        "title": "شدى كوفر",
        "desc": "برنامج موسيقي يسلط الضوء على المواهب والأصوات الجديدة من خلال أداءات فنية وحوارات قصيرة.",
    },
}

CHADA_HOST_NAMES_AR = {
    "imane bououlid": "إيمان بوعوليد الإدريسي",
    "fakherddine": "فخر الدين الرجحي",
    "houssine chahb": "حسين شهب",
    "jamila ouyoub": "جميلة أيوب",
    "sarah azmi": "سارة عزمي",
    "imad ntifi": "عماد النتيفي",
    "majda kilani": "ماجدة الكيلاني",
}


def _chada_key(value):
    value = str(value or "").casefold().replace("’", "'")
    value = re.sub(r"[^a-z0-9%]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _chada_metadata(title):
    key = _chada_key(title)
    # Longest alias first so "100% VAR" is not reduced to generic VAR.
    for alias in sorted(CHADA_SHOW_METADATA, key=len, reverse=True):
        if alias in key:
            return dict(CHADA_SHOW_METADATA[alias])
    return None

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
    use_cloudscraper = True

    def fetch(self, downloader, tzm, days, config, builder):
        translator = DarijaTranslator()  # builds its own fast, single-attempt
                                          # downloader internally - see
                                          # translators/darija.py docstring

        builder.add_channel("2M", [("2M", "ar")])
        builder.add_channel("Chada TV", [("Chada TV", "fr")])

        programmes = []
        two_m = self._fetch_2m(downloader, translator, tzm, days)
        programmes.extend(two_m)
        programmes.extend(self._fetch_chada(downloader, translator, tzm, days))

        translator.flush()

        if not programmes:
            raise SourceWarning("2M/Chada: no programmes found for either channel")

        return programmes

    # -- 2M ---------------------------------------------------------------
    def _extract_2m_page(self, response, translator, tzm, target_date, shift_days=0, period_hint=""):
        """Parse one Telecablesat period page defensively.

        beta110 no longer chooses *either* the legacy parser *or* the generic
        parser.  Telecablesat can keep an ``item-programme`` wrapper while
        changing the nested time/title classes; the old exclusive branch then
        found a container but produced zero useful rows.  We now collect from
        both layouts and de-duplicate in document order.
        """
        programs = []
        if response is None:
            return programs
        tree = get_lxml_tree(response.text)
        if tree is None:
            return programs

        from lxml import etree
        raw_rows = []
        seen_pairs = set()

        def add_row(time_str, title_raw, desc_raw):
            time_str = str(time_str or "").strip()
            m = re.fullmatch(r"(\d{1,2}:\d{2})", time_str)
            if not m:
                return
            title_raw = html_lib.unescape(str(title_raw or "").strip())
            desc_raw = html_lib.unescape(str(desc_raw or title_raw).strip())
            if not title_raw:
                return
            key = (m.group(1), re.sub(r"\s+", " ", title_raw).strip().lower())
            if key in seen_pairs:
                return
            seen_pairs.add(key)
            raw_rows.append((m.group(1), title_raw, desc_raw or title_raw))

        # Legacy/current card layouts.  Be liberal about the element carrying
        # schedule-hour and about where the H3 is nested.
        blocks = (tree.xpath("//div[contains(@class,'item-programme')]")
                  or tree.xpath("//*[contains(@class,'item-programme')]")
                  or tree.xpath("//div[contains(@class,'news')]") )
        for block in blocks:
            time_nodes = (block.xpath(".//*[contains(@class,'schedule-hour')]//text()")
                          or block.xpath(".//time//text()"))
            time_str = ""
            for txt in time_nodes:
                mm = re.search(r"\b(\d{1,2}:\d{2})\b", str(txt))
                if mm:
                    time_str = mm.group(1)
                    break
            if not time_str:
                # Last resort: only inspect the first short chunk of card text
                # so a duration/description later in the card is not mistaken
                # for the programme start.
                card_text = " ".join(x.strip() for x in block.itertext() if x and x.strip())[:120]
                mm = re.search(r"\b(\d{1,2}:\d{2})\b", card_text)
                if mm:
                    time_str = mm.group(1)

            title_els = (block.xpath(".//*[contains(@class,'item-content')]//h3//strong")
                         or block.xpath(".//*[contains(@class,'item-content')]//h3")
                         or block.xpath(".//h3//strong")
                         or block.xpath(".//h3")
                         or block.xpath(".//a[contains(@href,'/emission/')][1]"))
            if not time_str or not title_els:
                continue
            title_raw = etree.tostring(title_els[0], encoding="unicode", method="text").strip()
            desc_nodes = (block.xpath(".//*[contains(@class,'item-content')]//p//text()")
                          or block.xpath(".//p//text()"))
            desc_raw = " ".join(str(d).strip() for d in desc_nodes if str(d).strip()) or title_raw
            add_row(time_str, title_raw, desc_raw)

        # Generic document-order parser ALWAYS runs as an augmentation/fallback.
        # This is the key beta110 fix for partial morning/noon pages.
        candidates = tree.xpath("//*[self::div or self::span or self::p or self::time or self::li]")
        for node in candidates:
            direct = " ".join(str(x).strip() for x in node.xpath("./text()") if str(x).strip())
            m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*", direct or "")
            if not m:
                continue
            title_nodes = (node.xpath("following::h3[1]")
                           or node.xpath("following::a[contains(@href,'/emission/')][1]"))
            if not title_nodes:
                continue
            title_node = title_nodes[0]
            title_raw = " ".join(x.strip() for x in title_node.itertext() if x and x.strip())
            if not title_raw:
                continue
            desc_raw = ""
            following = title_node.xpath("following::p[1] | following::h3[1]")
            if following and str(getattr(following[0], "tag", "")).lower() == "p":
                desc_raw = " ".join(x.strip() for x in following[0].itertext() if x and x.strip())
            add_row(m.group(1), title_raw, desc_raw or title_raw)

        # Convert times in page order.  The evening page includes the 00:xx-
        # 05:xx continuation, which belongs to the following calendar day.
        rolled = False
        previous_hour = None
        for time_str, title_raw, desc_raw in raw_rows:
            try:
                time_obj = datetime.strptime(time_str, "%H:%M").time()
            except ValueError:
                continue
            if previous_hour is not None and previous_hour >= 18 and time_obj.hour < 6:
                rolled = True
            previous_hour = time_obj.hour
            actual_date = target_date + (timedelta(days=1) if rolled else timedelta(0))
            if period_hint in ("afternoon", "evening", "night") and time_obj.hour < 6:
                actual_date = target_date + timedelta(days=1)
            dt_naive = datetime.combine(actual_date, time_obj)
            if shift_days:
                dt_naive += timedelta(days=shift_days)
            # 2M guide rows publish the channel/Morocco wall-clock time, not
            # the listing site's browser timezone.  Treat 21:00 as 21:00 in
            # Morocco; TimezoneManager applies the permanent GMT rule from
            # 2026-09-20 and keeps historical dates compatible.
            dt_aware = tzm.localize(dt_naive)

            low = title_raw.lower()
            after_massaiya = ("massaiya" in low or "المسائية" in title_raw)
            after_infosoir = ("info soir" in low or "infosoir" in low)
            is_whitelisted = translator.is_whitelisted_french(title_raw)
            programs.append({
                "start": dt_aware,
                "title": translator.translate(title_raw, after_massaiya, after_infosoir,
                                               is_desc=False, keep_french=is_whitelisted),
                "desc": translator.translate(desc_raw, after_massaiya, after_infosoir,
                                              is_desc=True, keep_french=is_whitelisted),
            })
        return programs

    @staticmethod
    def _period_has_expected_coverage(rows, period):
        """Reject a period response that only contains the wrong day-part.

        A partial/cached Telecablesat response can otherwise make the guide look
        like it only has the post-midnight continuation.
        """
        hours = [r.get("start").hour for r in rows if r.get("start") is not None]
        if not hours:
            return False
        if period == "morning":
            return sum(1 for h in hours if 5 <= h < 12) >= 2
        if period == "noon":
            return sum(1 for h in hours if 11 <= h < 20) >= 2
        if period == "afternoon":
            return sum(1 for h in hours if h >= 18 or h < 6) >= 2
        return True

    def _get_2m_rows(self, downloader, translator, tzm, base_url, target_date, period, shift_days=0):
        params = {"date": target_date.strftime("%Y-%m-%d"), "period": period}
        resp = downloader.get_or_none(base_url, params=params)
        rows = self._extract_2m_page(resp, translator, tzm, target_date, shift_days=shift_days, period_hint=period)
        if rows and self._period_has_expected_coverage(rows, period):
            return rows
        if rows:
            self.log.warning("2M %s page parsed %d row(s) but day-part coverage is abnormal; retrying plain HTTP", period, len(rows))

        # Some receiver images behave differently with cloudscraper.  A plain
        # requests Session is a cheap second path used only when the primary
        # response was missing/unparseable/incomplete.
        try:
            from ..core.downloader import Downloader
            fallback = Downloader(retries=1, timeout=9, use_cloudscraper=False,
                                  headers={"Referer": "https://tv-programme.telecablesat.fr/"})
            try:
                resp = fallback.get_or_none(base_url, params=params)
                fallback_rows = self._extract_2m_page(resp, translator, tzm, target_date, shift_days=shift_days, period_hint=period)
                if fallback_rows and self._period_has_expected_coverage(fallback_rows, period):
                    return fallback_rows
                if fallback_rows:
                    self.log.warning("2M %s plain HTTP fallback still has abnormal coverage (%d rows)", period, len(fallback_rows))
            finally:
                fallback.close()
        except Exception as exc:
            self.log.debug("2M plain HTTP fallback failed for %s: %s", period, exc)
        return []

    def _fetch_2m_day(self, downloader, translator, tzm, target_date, shift_days=0):
        """Fetch the three real Telecablesat day tabs.

        The site exposes Matin=morning, Midi=noon and Soir=afternoon.  beta52
        also requested evening/night, which are redundant and cost two extra
        network round-trips per day.  beta53 drops those duplicate requests,
        making a seven-day refresh ~40% lighter before translation/cache hits.
        """
        base_url = "https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"
        target_str = target_date.strftime("%Y-%m-%d")
        programs = []
        seen = set()
        for period in ("morning", "noon", "afternoon"):
            rows = self._get_2m_rows(downloader, translator, tzm, base_url, target_date, period, shift_days=shift_days)
            for row in rows:
                key = (row.get("start"), row.get("title"))
                if key in seen:
                    continue
                seen.add(key)
                programs.append(row)
        return programs

    @staticmethod
    def _parse_xmltv_dt(value):
        value = str(value or "").strip()
        for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M %z", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                pass
        return None

    def _load_cached_2m(self, valid_from=None, valid_to=None):
        """Recover only date-valid 2M events from the combined XMLTV cache.

        beta110 deliberately refuses stale schedules.  A cached event is useful
        only when its actual local calendar date overlaps the requested window.
        """
        try:
            from xml.etree import ElementTree as ET
        except Exception:
            return []
        paths = []
        if self.output_path:
            paths = [self.output_path, self.output_path + ".bak"]
        best = []
        for path in paths:
            if not path or not os.path.isfile(path):
                continue
            try:
                root = ET.parse(path).getroot()
                rows = []
                for el in root.findall("programme"):
                    if str(el.get("channel") or "") != "2M":
                        continue
                    start = self._parse_xmltv_dt(el.get("start"))
                    stop = self._parse_xmltv_dt(el.get("stop"))
                    if start is None:
                        continue
                    # XMLTV stores the programme's local wall clock together
                    # with its explicit offset; the calendar date is therefore
                    # already the Morocco broadcast date we need here.
                    start_day = start.date()
                    if valid_from is not None and start_day < valid_from:
                        continue
                    if valid_to is not None and start_day > valid_to:
                        continue
                    title_el = el.find("title")
                    desc_el = el.find("desc")
                    title = (title_el.text or "").strip() if title_el is not None else ""
                    desc = (desc_el.text or "").strip() if desc_el is not None else title
                    if not title:
                        continue
                    title_lang = title_el.get("lang") if title_el is not None else ("ar" if contains_arabic(title) else "fr")
                    desc_lang = desc_el.get("lang") if desc_el is not None else ("ar" if contains_arabic(desc) else "fr")
                    rows.append(Programme(channel_id="2M", start=start, stop=stop,
                                          title=title, desc=desc,
                                          lang=title_lang or "ar",
                                          title_lang=title_lang or "ar",
                                          desc_lang=desc_lang or "ar"))
                if len(rows) > len(best):
                    best = rows
            except Exception as exc:
                self.log.warning("Could not recover cached 2M from %s: %s", path, exc)
        return best

    @staticmethod
    def _programme_local_hour(programme):
        dt = getattr(programme, "start", None)
        if dt is None:
            return None
        return dt.hour

    def _today_2m_is_complete(self, programmes, today):
        today_rows = []
        for p in programmes:
            dt = getattr(p, "start", None)
            if dt is None:
                continue
            if dt.date() == today:
                today_rows.append(p)
        hours = [self._programme_local_hour(p) for p in today_rows]
        hours = [h for h in hours if h is not None]
        morning = sum(1 for h in hours if 5 <= h < 12)
        midday = sum(1 for h in hours if 12 <= h < 18)
        evening = sum(1 for h in hours if 18 <= h < 24)
        ok = len(today_rows) >= 8 and morning >= 2 and midday >= 2 and evening >= 2
        self.log.info("2M today coverage: programmes=%d morning=%d midday=%d evening=%d complete=%s",
                      len(today_rows), morning, midday, evening, "yes" if ok else "no")
        return ok

    def _fetch_2m(self, downloader, translator, tzm, days):
        self.log.info("2M: fetching %d day(s) from Telecablesat without synthetic week-shift fallback...", days)
        today = tzm.now().date()
        raw = []

        for i in range(days):
            target_date = today + timedelta(days=i)
            day_progs = self._fetch_2m_day(downloader, translator, tzm, target_date)
            if not day_progs:
                self.log.warning("2M: no trustworthy rows for %s; not fabricating schedule from a previous week", target_date.isoformat())
            raw.extend(day_progs)

        seen_times = set()
        result = []
        for p in sorted(raw, key=lambda x: x["start"]):
            key = (p["start"], p.get("title"))
            if key in seen_times:
                continue
            seen_times.add(key)
            title_lang = "ar" if contains_arabic(p["title"]) else "fr"
            desc_lang = "ar" if contains_arabic(p["desc"]) else "fr"
            result.append(Programme(
                channel_id="2M", start=p["start"], title=p["title"], desc=p["desc"],
                lang=title_lang, title_lang=title_lang, desc_lang=desc_lang))

        # If today's live scrape is incomplete, merge only cache rows that
        # actually belong to today/future in the requested horizon.  Never use
        # stale dates and never shift an old weekly schedule forward.
        if not self._today_2m_is_complete(result, today):
            valid_to = today + timedelta(days=max(0, int(days or 1) - 1))
            cached = self._load_cached_2m(valid_from=today, valid_to=valid_to)
            if cached:
                merged = {(getattr(p, "start", None), getattr(p, "title", None)): p for p in result}
                for p in cached:
                    merged.setdefault((getattr(p, "start", None), getattr(p, "title", None)), p)
                result = sorted(merged.values(), key=lambda p: getattr(p, "start", datetime.max))
                self.log.warning("2M: merged %d date-valid cached event(s) to repair partial live coverage", len(cached))

        if not self._today_2m_is_complete(result, today):
            raise SourceWarning("2M current-day guide is incomplete (morning/midday/evening coverage missing); keeping Last Known Good instead of publishing a partial guide")
        return result

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

                title_value = item["title"]
                desc_value = item["desc"]
                title_lang = "ar" if contains_arabic(title_value) else "fr"
                programmes.append(Programme(
                    channel_id="Chada TV", start=dt_final,
                    title=title_value, desc=desc_value, lang=title_lang,
                    title_lang=title_lang, desc_lang="ar",
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
        """Normalize Chada subjects and produce useful Arabic descriptions.

        RC23 deliberately avoids free-form machine invention.  Known recurring
        programmes use curated canonical Arabic metadata; unknown programmes
        keep the cleaned source title and receive only a neutral Arabic channel
        description. Presenter names are removed from the title and, when
        recognized, appended to the synopsis instead.
        """
        processed = []
        for item in sanitized_data:
            orig_title = str(item.get("title") or "").strip()
            lower_title = orig_title.casefold()

            presenter = ""
            for host_key, host_ar in CHADA_HOST_NAMES_AR.items():
                if host_key in lower_title:
                    presenter = host_ar
                    break

            cleaned_title = _REMOVAL_PATTERN.sub("", orig_title)
            cleaned_title = re.sub(r"\s{2,}", " ", cleaned_title).strip(" .\t\r\n-|:")
            if not cleaned_title or len(cleaned_title) < 3:
                cleaned_title = "برنامج شدى تي في"

            meta = _chada_metadata(cleaned_title) or _chada_metadata(orig_title)
            if meta:
                final_title = str(meta.get("title") or cleaned_title)
                final_desc = str(meta.get("desc") or "برنامج ضمن شبكة شدى تي في.")
            else:
                final_title = cleaned_title
                final_desc = "برنامج ضمن شبكة شدى تي في."

            if presenter and presenter not in final_desc:
                final_desc = "%s. تقديم %s." % (final_desc.rstrip(" ."), presenter)

            processed.append({"time": item["time"], "title": final_title, "desc": final_desc})
        return processed

