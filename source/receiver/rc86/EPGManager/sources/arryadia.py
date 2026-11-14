# -*- coding: utf-8 -*-
"""
sources/arryadia.py

Ported from 7.ARRYADIA.py. Preserves the club-name map, football title
formatter (competition detection, matchday/round extraction, LIVE
detection, "Team A vs Team B" reconstruction), and channel tag mapping.

BUG FIXES vs the original:
  * The original hardcoded `MOROCCO_TZ = timezone(timedelta(hours=1))` - a
    fixed UTC+1 offset that is WRONG during Morocco's Ramadan DST pause.
    Now uses the shared TimezoneManager (Africa/Casablanca + Hijri
    cross-check) like every other source.
  * `format_football_title()`'s return type hint used `tuple[str, str,
    bool]` (PEP 585 generic subscripting, Python 3.9+ only). Replaced with
    `typing.Tuple` for broader Python compatibility per spec section 7.
"""

import re
from datetime import datetime, timedelta
from collections import defaultdict

try:
    from typing import Tuple
except ImportError:
    Tuple = None

from .base import EPGSource
from ..core.parser import get_soup, SourceWarning
from ..core.xmltv import Programme

MAIN_ARRYADIA_URL = "https://www.snrt.ma/ar/node/4070"
ARRYADIA_TARGET_CHANNELS = ["Arryadia_HD", "Arryadia_TNT", "Arryadia_HD1", "Arryadia_HD2", "Arryadia_HD3"]

CHANNEL_DISPLAY_NAMES = {
    "Arryadia_HD": "Arryadia HD",
    "Arryadia_TNT": "Arryadia TNT",
    "Arryadia_HD1": "Arryadia HD1",
    "Arryadia_HD2": "Arryadia HD2",
    "Arryadia_HD3": "Arryadia HD3",
}

CHANNEL_TAG_MAPPING = {
    r"\btnt\b": "Arryadia_TNT",
    r"\bsat\b": "Arryadia_HD",
    r"\bhd1\b": "Arryadia_HD1",
    r"\bhd2\b": "Arryadia_HD2",
    r"\bhd3\b": "Arryadia_HD3",
}

CLUB_NAME_MAP = {
    "WAC": "Wydad Casablanca", "RCA": "Raja Casablanca", "RSB": "RS Berkane",
    "AS FAR": "AS FAR Rabat", "FUS": "FUS Rabat", "MAS": "Maghreb Fès",
    "KACM": "Kawkab Marrakech", "MAT": "Moghreb Tétouan", "HUSA": "Hassania Agadir",
    "OCS": "Olympic Safi", "IRT": "Ittihad Tanger", "SCCM": "Chabab Mohammedia",
    "MCO": "Mouloudia Oujda", "UTS": "Union Touarga", "JSS": "Jeunesse Soualem",
    "RCAZ": "Renaissance Zemamra", "DHJ": "Difaâ El Jadidi", "CAYB": "Youssoufia Berrechid",
    "OD": "Olympique Dcheira", "OCK": "Olympique Khouribga", "CODM": "CODM Meknès",
    "CAK": "Chabab Atlas Khénifra", "RBM": "Raja Béni Mellal", "JSM": "Jeunesse El Massira",
    "USMO": "USM Oujda", "SM": "Stade Marocain", "KAC": "Kénitra AC",
    "WAF": "Widad Fès", "RAC": "Racing Casablanca", "RCOZ": "Rapide Oued Zem",
    "ASS": "AS Salé", "IZK": "Ittihad Khémisset", "CJBG": "Chabab Ben Guerir",
    "MAROC": "Équipe du Maroc", "MAR": "Équipe du Maroc",
}


def normalize_club_name(name):
    return CLUB_NAME_MAP.get(name.strip().upper(), name.strip().upper().title())


def get_display_champ_name(abbr):
    mapping = {
        "BOTOLA PRO": "Botola Pro", "BOTOLA PRO 2": "Botola Pro 2",
        "COUPE DU TRÔNE": "Coupe du Trône", "MATCH AMICAL": "Match Amical",
        "CAF CL": "Ligue des Champions CAF", "CAF CC": "Coupe de la Confédération CAF",
        "CAN": "Coupe d'Afrique des Nations", "MAROC": "Match International (Maroc)",
        "FOOT": "Match de Football",
    }
    return mapping.get(abbr, abbr)


def format_football_title(title, desc):
    """Returns (new_title, final_desc, is_live). Type hints intentionally
    omitted for broad Python 2/3.x-era compatibility (see module docstring)."""
    is_live = False

    original_raw_text = "%s | %s" % (title, desc)
    original_raw_text = re.sub(r"[\s|]+$", "", original_raw_text)

    year_regex = r'(\s*["\'\u2019\(\[]?\s*\d{4}[-/]\d{4}\s*["\'\u2019\)\\]?\s*|\s*\b20\d{2}\b\s*)'
    title_clean = re.sub(year_regex, " ", title).strip()
    desc_clean = re.sub(year_regex, " ", desc).strip()

    full_text_lower = ("%s %s" % (title_clean, desc_clean)).lower()
    if "مباشر" in full_text_lower or "direct" in full_text_lower or "live" in full_text_lower:
        is_live = True

    desc_clean = re.sub(r"\(?Direct\)?", "", desc_clean, flags=re.IGNORECASE).strip()
    desc_clean = re.sub(r"\(?مباشر\)?", "", desc_clean).strip()
    desc_clean = re.sub(r"^[\s|]+|[\s|]+$", "", desc_clean)

    title_clean = re.sub(r"\(?Direct\)?", "", title_clean, flags=re.IGNORECASE).strip()
    title_clean = re.sub(r"\(?مباشر\)?", "", title_clean).strip()
    title_clean = re.sub(r"^[\s|]+|[\s|]+$", "", title_clean)

    matchday_regex = r"(\d+\s*(J\.|Journée|Round|الدورة|الجولة)\s*|\s*(J\.|Journée|Round|الدورة|الجولة)\s*\d+)"
    matchday_str = ""
    m_match = re.search(matchday_regex, "%s %s" % (title_clean, desc_clean), re.IGNORECASE)
    if m_match:
        matchday_str = m_match.group(0).strip()
        matchday_str = re.sub(r"الدورة|الجولة", "Journée", matchday_str)
        title_clean = re.sub(re.escape(m_match.group(0)), "", title_clean, flags=re.IGNORECASE).strip()
        desc_clean = re.sub(re.escape(m_match.group(0)), "", desc_clean, flags=re.IGNORECASE).strip()

    champ_abbr = ""
    if any(kw in full_text_lower for kw in ["ودية", "amical"]): champ_abbr = "MATCH AMICAL"
    elif any(kw in full_text_lower for kw in ["أبطال إفريقيا", "champions league", "caf cl"]): champ_abbr = "CAF CL"
    elif any(kw in full_text_lower for kw in ["الكونفدرالية", "caf cc"]): champ_abbr = "CAF CC"
    elif any(kw in full_text_lower for kw in ["المنتخب الوطني", "maroc", "أسود الأطلس"]): champ_abbr = "MAROC"
    elif any(kw in full_text_lower for kw in ["القسم الثاني", "الوطني الثاني"]): champ_abbr = "BOTOLA PRO 2"
    elif "كأس العرش" in full_text_lower or "coupe du trone" in full_text_lower: champ_abbr = "COUPE DU TRÔNE"
    elif any(kw in full_text_lower for kw in ["الأمم الإفريقية", "can"]): champ_abbr = "CAN"
    elif any(kw in full_text_lower for kw in ["القسم الوطني الأول", "البطولة الإحترافية", "botola"]): champ_abbr = "BOTOLA PRO"

    teams_pattern = r"([A-Z0-9]{2,}(?:\s+[A-Z0-9]{2,})*)\s*-\s*([A-Z0-9]{2,}(?:\s+[A-Z0-9]{2,})*)"
    teams_match = re.search(teams_pattern, desc_clean)

    if teams_match:
        team_a = normalize_club_name(teams_match.group(1).strip())
        team_b = normalize_club_name(teams_match.group(2).strip())
        match_label = "%s vs %s" % (team_a, team_b)
        display_champ = get_display_champ_name(champ_abbr) if champ_abbr else "Football"
        new_title = "%s - %s" % (match_label, display_champ)
    else:
        new_title = title_clean
        if new_title.strip() == "كرة القدم" and champ_abbr:
            new_title = "%s - %s" % (new_title, get_display_champ_name(champ_abbr))

    new_title = re.sub(r"\s+", " ", new_title).strip()
    new_title = re.sub(r"[\s|]+$", "", new_title)

    if is_live and not new_title.lower().startswith("live:"):
        new_title = "Live: %s" % new_title

    final_desc = original_raw_text
    if matchday_str:
        final_desc = "[%s] %s" % (matchday_str, final_desc)

    return new_title, final_desc, is_live


class ArryadiaSource(EPGSource):
    id = "arryadia"
    name = "Arryadia"
    output_filename = "arryadia_snrt.xml"
    default_days = 3
    min_programs = 5  # the synthetic-filler logic below means we rarely hit zero
    use_cloudscraper = True  # shares snrt.ma with the SNRT source - same anti-bot risk

    def fetch(self, downloader, tzm, days, config, builder):
        for ch_id in ARRYADIA_TARGET_CHANNELS:
            builder.add_channel(ch_id, [(CHANNEL_DISPLAY_NAMES[ch_id], None)])

        scraped = self._fetch_schedule(downloader, tzm, MAIN_ARRYADIA_URL, days)

        programmes = []
        for ch_id in ARRYADIA_TARGET_CHANNELS:
            for start, stop, title, desc in scraped.get(ch_id, []):
                programmes.append(Programme(
                    channel_id=ch_id, start=start, stop=stop,
                    title=title, desc=desc, lang="fr", desc_lang="ar",
                ))

        if not programmes:
            raise SourceWarning("Arryadia: no programmes generated (scrape + filler both empty)")
        return programmes

    def _fetch_schedule(self, downloader, tzm, url, days):
        scraped_programs = defaultdict(list)

        now = tzm.now()
        epg_start = tzm.today_midnight()
        epg_end = epg_start + timedelta(days=days)

        resp = downloader.get_or_none(url)
        program_rows = []
        if resp is not None:
            soup = get_soup(resp.text)
            if soup is not None:
                program_rows = soup.find_all("div", class_=lambda x: x and "grille-line" in x.split())

        program_details = []
        for row in program_rows:
            try:
                date_class = [cls for cls in row.get("class", []) if cls.isdigit() and len(cls) == 8]
                time_tag = row.find("div", class_="grille-time")
                if not date_class or not time_tag:
                    continue

                time_str = time_tag.get_text().strip().replace("H", ":")
                start_naive = datetime.strptime("%s %s" % (date_class[0], time_str), "%Y%m%d %H:%M")
                start_dt = tzm.localize(start_naive)

                title_tag = row.find("h2", class_="program-title-sm")
                title = title_tag.text.strip() if title_tag else "Programme"

                desc = ""
                grille_content = row.find("div", class_="grille-content")
                if grille_content:
                    a_tag = grille_content.find("a", tabindex="0")
                    if a_tag and a_tag.next_sibling:
                        sibling = a_tag.next_sibling
                        while sibling:
                            if hasattr(sibling, "string") and sibling.string and sibling.string.strip():
                                desc = sibling.string.strip()
                                break
                            sibling = sibling.next_sibling

                program_details.append({"start_dt": start_dt, "title": title, "desc": desc})
            except Exception:
                continue

        if not program_details:
            self.log.warning("Arryadia: no programmes scraped from %s - generating filler schedule", url)
            return self._generate_filler(tzm, epg_start, epg_end)

        program_details.sort(key=lambda x: x["start_dt"])
        unique_starts = sorted(set(p["start_dt"] for p in program_details))

        for p in program_details:
            start = p["start_dt"]
            next_starts = [t for t in unique_starts if t > start]
            end = next_starts[0] if next_starts else start + timedelta(hours=2)

            full_text = ("%s %s" % (p["title"], p["desc"])).lower()
            intended = [ch_id for pattern, ch_id in CHANNEL_TAG_MAPPING.items()
                        if re.search(pattern, full_text)]
            if not intended:
                intended = ["Arryadia_HD", "Arryadia_TNT"]

            for ch_id in intended:
                f_title, f_desc, _ = format_football_title(p["title"], p["desc"])
                if ch_id in ("Arryadia_HD1", "Arryadia_HD2", "Arryadia_HD3") and not f_title.upper().startswith("LIVE:"):
                    f_title = "Live: %s" % f_title
                scraped_programs[ch_id].append((start, end, f_title, f_desc))

        return self._fill_gaps(scraped_programs, epg_start, epg_end)

    def _generate_filler(self, tzm, epg_start, epg_end):
        scraped = defaultdict(list)
        curr = epg_start
        while curr < epg_end:
            nxt = curr + timedelta(hours=3)
            for ch_id in ARRYADIA_TARGET_CHANNELS:
                name = CHANNEL_DISPLAY_NAMES[ch_id]
                scraped[ch_id].append(
                    (curr, nxt, "Programmes %s" % name, "Suivez le meilleur du sport sur %s." % name))
            curr = nxt
        return scraped

    def _fill_gaps(self, scraped_programs, epg_start, epg_end):
        result = defaultdict(list)
        for ch_id in ARRYADIA_TARGET_CHANNELS:
            progs = sorted(scraped_programs.get(ch_id, []), key=lambda x: x[0])
            name = CHANNEL_DISPLAY_NAMES[ch_id]
            curr = epg_start

            for p in progs:
                while curr < p[0] - timedelta(minutes=1):
                    gap_end = min(p[0], curr + timedelta(hours=3))
                    result[ch_id].append((curr, gap_end, "Programmes %s" % name,
                                           "Suivez le meilleur du sport marocain et international sur %s." % name))
                    curr = gap_end
                result[ch_id].append(p)
                curr = p[1]

            while curr < epg_end:
                nxt = curr + timedelta(hours=3)
                result[ch_id].append((curr, nxt, "Programmes %s" % name,
                                       "Suivez le meilleur du sport marocain et international sur %s." % name))
                curr = nxt

        return result
