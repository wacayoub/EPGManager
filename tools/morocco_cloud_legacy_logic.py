#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Historical SNRT + Arryadia logic for Morocco EPG Cloud.

This module ports the receiver-side EPGManager source behaviour into the
GitHub-side generator so moving scraping off the Vu+ does not simplify or
change the Moroccan EPG result.

SNRT preserved behaviour:
- Al Aoula / Arrabiaa / Al Maghribia / Assadissa / Tamazight
- synthetic AFLAM schedule
- broadcaster-day rollover at 07:00
- continuous stop times using the next real programme
- detailed description pages, best effort and deduplicated by URL
- news bulletin retitling
- exact weather description
- TNT / terrestrial tagging

Arryadia preserved behaviour:
- SAT / TNT / HD1 / HD2 / HD3 routing
- Moroccan club abbreviation expansion
- football competition detection
- matchday / round extraction
- LIVE detection
- Team A vs Team B reconstruction
- forced LIVE on HD1/HD2/HD3 event feeds
- three-hour filler and gap filling
- Africa/Casablanca timezone through zoneinfo
"""
from __future__ import annotations

import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dtime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import morocco_epg as base
import morocco_cloud_runner as runner

TZ = runner.TZ

SNRT_CHANNELS = {
    "AlAoula": "https://www.snrt.ma/ar/node/1208",
    "Arrabiaa": "https://www.snrt.ma/ar/node/4071",
    "AlMaghribiya": "https://www.snrt.ma/ar/node/4072",
    "Assadisa": "https://www.snrt.ma/ar/node/4073",
    "Tamazight": "https://www.snrt.ma/ar/node/4075",
}

SNRT_NEWS = {
    "الظهيرة": "أخبار الظهيرة",
    "الأمازيغية": "الأخبار الأمازيغية",
    "الفرنسية": "الأخبار الفرنسية",
    "الإسبانية": "الأخبار الإسبانية",
    "الرئيسية": "الأخبار الرئيسية",
    "الأخيرة": "الأخبار الأخيرة",
    "الرياضية": "أخبار الرياضة",
}

WEATHER_DESC = "نشرة جوية مفصلة حول حالة الطقس في المملكة المغربية."

ARR_MAIN_URL = "https://www.snrt.ma/ar/node/4070"
ARR_IDS = ["Arryadia_HD", "Arryadia_TNT", "Arryadia_HD1", "Arryadia_HD2", "Arryadia_HD3"]
ARR_NAMES = {
    "Arryadia_HD": "Arryadia HD",
    "Arryadia_TNT": "Arryadia TNT",
    "Arryadia_HD1": "Arryadia HD1",
    "Arryadia_HD2": "Arryadia HD2",
    "Arryadia_HD3": "Arryadia HD3",
}
ARR_TAGS = {
    r"\btnt\b": "Arryadia_TNT",
    r"\bsat\b": "Arryadia_HD",
    r"\bhd1\b": "Arryadia_HD1",
    r"\bhd2\b": "Arryadia_HD2",
    r"\bhd3\b": "Arryadia_HD3",
}

CLUBS = {
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


def _clean(value):
    return runner.clean(value)


def _fetch(session, url, timeout=(5, 10), referer=None):
    headers = {"Referer": referer} if referer else None
    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r


def _snrt_parse_channel(channel_id, url):
    s = runner.session()
    try:
        r = runner.fetch(s, url)
    except Exception as exc:
        runner.log("SNRT %s grid failed: %s" % (channel_id, exc))
        return []

    soup = BeautifulSoup(r.text, "lxml")
    extracted = []
    for block in soup.find_all("div", class_=lambda x: x and "grille-line" in x.split()):
        classes = block.get("class", [])
        date_class = next((x for x in classes if re.fullmatch(r"\d{8}", str(x))), None)
        time_tag = block.find("div", class_=lambda x: x and "grille-time" in x.split())
        title_tag = block.find("h2", class_=lambda x: x and "program-title-sm" in x.split())
        if not date_class or not time_tag or not title_tag:
            continue

        mt = re.search(r"(\d{1,2})[H:](\d{2})", time_tag.get_text(" ", strip=True), re.I)
        if not mt:
            continue
        try:
            naive = datetime.strptime("%s %02d:%s" % (date_class, int(mt.group(1)), mt.group(2)), "%Y%m%d %H:%M")
        except Exception:
            continue

        title = _clean(title_tag.get_text(" ", strip=True))
        raw_text = _clean(block.get_text(" ", strip=True))
        content = block.find("div", class_=lambda x: x and "grille-content" in x.split())
        desc_short = ""
        if content:
            # direct text nodes reproduce the receiver-side XPath behaviour
            pieces = []
            for child in content.contents:
                if isinstance(child, str) and child.strip():
                    pieces.append(child.strip())
            desc_short = _clean(" ".join(pieces))
        if "الرئيسية" in raw_text:
            desc_short = _clean(desc_short + " الرئيسية")
        elif "الأخيرة" in raw_text:
            desc_short = _clean(desc_short + " الأخيرة")

        link = block.find("a", class_=lambda x: x and "program-title-slide-over" in x.split()) or block.find("a", href=True)
        programme_url = urljoin("https://www.snrt.ma", link.get("href")) if link and link.get("href") else ""
        extracted.append({
            "start_naive": naive,
            "title": title,
            "channel": channel_id,
            "programme_url": programme_url,
            "raw_text": raw_text,
            "desc_short": desc_short or title,
        })

    # SNRT broadcaster day starts at 07:00. Rows 00:00-06:59 printed with
    # the same CSS date belong to the following civil day.
    has_daytime = {}
    for p in extracted:
        d = p["start_naive"].date()
        if p["start_naive"].hour >= 7:
            has_daytime[d] = True
        else:
            has_daytime.setdefault(d, False)

    result = []
    seen = set()
    for p in extracted:
        dt = p["start_naive"]
        if dt.hour < 7 and has_daytime.get(dt.date(), False):
            dt += timedelta(days=1)
        start = dt.replace(tzinfo=TZ)
        key = (start, p["title"])
        if key in seen:
            continue
        seen.add(key)
        p["start"] = start
        result.append(p)
    result.sort(key=lambda x: x["start"])
    return result


def _snrt_detail(url, title, short_desc):
    if not url:
        return short_desc
    s = runner.session()
    try:
        r = _fetch(s, url, timeout=(4, 6))
    except Exception:
        return short_desc
    soup = BeautifulSoup(r.text, "lxml")
    candidates = []
    for selector in ("div.content p", "div.field-items", "div.content div"):
        for node in soup.select(selector):
            txt = _clean(node.get_text(" ", strip=True))
            if txt:
                candidates.append(txt)
    long_desc = _clean(" ".join(candidates))
    return long_desc if len(long_desc) > len(short_desc or "") else short_desc


def _snrt_title(row):
    title = row["title"]
    desc = row.get("desc_final") or row.get("desc_short") or ""
    raw = row.get("raw_text") or ""
    if "الأخبار" in title or title == "نشرة":
        context = "%s %s %s" % (title, desc, raw)
        for key, replacement in SNRT_NEWS.items():
            if key in context:
                title = replacement
                break
    tnt_context = "%s %s %s" % (title, desc, raw)
    if "بث ارضي" in tnt_context or "بث أرضي" in tnt_context or "TNT" in tnt_context.upper():
        if "TNT" not in title.upper() and "أرضي" not in title and "ارضي" not in title:
            title = "%s [TNT / بث أرضي]" % title
    return title


def scrape_snrt_historical(days):
    rows = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_snrt_parse_channel, cid, url): cid for cid, url in SNRT_CHANNELS.items()}
        for f in as_completed(futures):
            cid = futures[f]
            try:
                rows.extend(f.result())
            except Exception as exc:
                runner.log("SNRT %s parse crashed: %s" % (cid, exc))

    # Preserve the original synthetic Aflam TV schedule: 7 days x eight 3h slots.
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = max(1, int(days)) * 8
    for i in range(slots):
        rows.append({
            "start": start + timedelta(hours=i * 3),
            "title": "برامج قناة السابعة AFLAM",
            "channel": "AFLAM.ma",
            "programme_url": "",
            "raw_text": "",
            "desc_short": "أفضل الأفلام والبرامج السينمائية على القناة السابعة المغربية",
        })

    if not rows:
        return []

    # Receiver script fetched every UNIQUE detail URL. Preserve that result,
    # but deduplicate network work and bound it with eight cloud workers.
    groups = defaultdict(list)
    for row in rows:
        if row.get("programme_url"):
            groups[row["programme_url"]].append(row)
    if groups:
        runner.log("SNRT: enriching %d unique detail pages" % len(groups))
        with ThreadPoolExecutor(max_workers=min(8, len(groups))) as ex:
            fmap = {}
            for url, grp in groups.items():
                sample = grp[0]
                fmap[ex.submit(_snrt_detail, url, sample.get("title", ""), sample.get("desc_short", ""))] = grp
            for f in as_completed(fmap):
                grp = fmap[f]
                try:
                    desc = f.result()
                except Exception:
                    desc = grp[0].get("desc_short", "")
                for row in grp:
                    row["desc_final"] = desc or row.get("desc_short", "")

    by_channel = defaultdict(list)
    for row in rows:
        if row.get("channel") and row.get("start"):
            by_channel[row["channel"]].append(row)
    for channel_rows in by_channel.values():
        channel_rows.sort(key=lambda x: x["start"])

    out = []
    for cid, channel_rows in by_channel.items():
        for idx, row in enumerate(channel_rows):
            title = _snrt_title(row)
            desc = row.get("desc_final") or row.get("desc_short") or title
            if title == "أحوال الطقس":
                desc = WEATHER_DESC
            stop = channel_rows[idx + 1]["start"] if idx + 1 < len(channel_rows) else None
            if stop is None and row["start"].hour < 7:
                stop = row["start"].replace(hour=7, minute=0, second=0, microsecond=0)
                if stop <= row["start"]:
                    stop += timedelta(days=1)
            out.append(base.Event(cid, row["start"], title, desc, stop, "ar", "ar", "snrt"))

    runner.log("SNRT historical logic: %d events / detail-pages=%d" % (len(out), len(groups)))
    return out


def normalize_club(name):
    return CLUBS.get(_clean(name).upper(), _clean(name).upper().title())


def competition_name(code):
    mapping = {
        "BOTOLA PRO": "Botola Pro",
        "BOTOLA PRO 2": "Botola Pro 2",
        "COUPE DU TRÔNE": "Coupe du Trône",
        "MATCH AMICAL": "Match Amical",
        "CAF CL": "Ligue des Champions CAF",
        "CAF CC": "Coupe de la Confédération CAF",
        "CAN": "Coupe d'Afrique des Nations",
        "MAROC": "Match International (Maroc)",
        "FOOT": "Match de Football",
    }
    return mapping.get(code, code)


def format_football_title(title, desc):
    original = re.sub(r"[\s|]+$", "", "%s | %s" % (title, desc))
    year_re = r'(\s*["\'\u2019\(\[]?\s*\d{4}[-/]\d{4}\s*["\'\u2019\)\]]?\s*|\s*\b20\d{2}\b\s*)'
    tc = re.sub(year_re, " ", title).strip()
    dc = re.sub(year_re, " ", desc).strip()
    lower = ("%s %s" % (tc, dc)).lower()
    is_live = any(x in lower for x in ("مباشر", "direct", "live"))
    for pattern in (r"\(?Direct\)?", r"\(?مباشر\)?"):
        tc = re.sub(pattern, "", tc, flags=re.I).strip()
        dc = re.sub(pattern, "", dc, flags=re.I).strip()
    tc = re.sub(r"^[\s|]+|[\s|]+$", "", tc)
    dc = re.sub(r"^[\s|]+|[\s|]+$", "", dc)

    round_re = r"(\d+\s*(?:J\.|Journée|Round|الدورة|الجولة)\s*|\s*(?:J\.|Journée|Round|الدورة|الجولة)\s*\d+)"
    round_text = ""
    mr = re.search(round_re, "%s %s" % (tc, dc), re.I)
    if mr:
        round_text = re.sub(r"الدورة|الجولة", "Journée", mr.group(0).strip())
        tc = re.sub(re.escape(mr.group(0)), "", tc, flags=re.I).strip()
        dc = re.sub(re.escape(mr.group(0)), "", dc, flags=re.I).strip()

    comp = ""
    if any(x in lower for x in ("ودية", "amical")):
        comp = "MATCH AMICAL"
    elif any(x in lower for x in ("أبطال إفريقيا", "champions league", "caf cl")):
        comp = "CAF CL"
    elif any(x in lower for x in ("الكونفدرالية", "caf cc")):
        comp = "CAF CC"
    elif any(x in lower for x in ("المنتخب الوطني", "maroc", "أسود الأطلس")):
        comp = "MAROC"
    elif any(x in lower for x in ("القسم الثاني", "الوطني الثاني")):
        comp = "BOTOLA PRO 2"
    elif "كأس العرش" in lower or "coupe du trone" in lower:
        comp = "COUPE DU TRÔNE"
    elif any(x in lower for x in ("الأمم الإفريقية", "can")):
        comp = "CAN"
    elif any(x in lower for x in ("القسم الوطني الأول", "البطولة الإحترافية", "botola")):
        comp = "BOTOLA PRO"

    teams = re.search(r"([A-Z0-9]{2,}(?:\s+[A-Z0-9]{2,})*)\s*-\s*([A-Z0-9]{2,}(?:\s+[A-Z0-9]{2,})*)", dc)
    if teams:
        a = normalize_club(teams.group(1))
        b = normalize_club(teams.group(2))
        new_title = "%s vs %s - %s" % (a, b, competition_name(comp) if comp else "Football")
    else:
        new_title = tc
        if new_title.strip() == "كرة القدم" and comp:
            new_title = "%s - %s" % (new_title, competition_name(comp))
    new_title = re.sub(r"\s+", " ", new_title).strip()
    if is_live and not new_title.lower().startswith("live:"):
        new_title = "Live: %s" % new_title
    final_desc = "[%s] %s" % (round_text, original) if round_text else original
    return new_title, final_desc, is_live


def _arryadia_filler(start, end):
    out = defaultdict(list)
    cur = start
    while cur < end:
        nxt = min(end, cur + timedelta(hours=3))
        for cid in ARR_IDS:
            name = ARR_NAMES[cid]
            out[cid].append((cur, nxt, "Programmes %s" % name, "Suivez le meilleur du sport sur %s." % name))
        cur = nxt
    return out


def _arryadia_fill_gaps(scraped, start, end):
    result = defaultdict(list)
    for cid in ARR_IDS:
        progs = sorted(scraped.get(cid, []), key=lambda x: x[0])
        name = ARR_NAMES[cid]
        cur = start
        for p in progs:
            while cur < p[0] - timedelta(minutes=1):
                gap_end = min(p[0], cur + timedelta(hours=3))
                result[cid].append((cur, gap_end, "Programmes %s" % name,
                                    "Suivez le meilleur du sport marocain et international sur %s." % name))
                cur = gap_end
            result[cid].append(p)
            cur = max(cur, p[1])
        while cur < end:
            nxt = min(end, cur + timedelta(hours=3))
            result[cid].append((cur, nxt, "Programmes %s" % name,
                                "Suivez le meilleur du sport marocain et international sur %s." % name))
            cur = nxt
    return result


def scrape_arryadia_historical(days):
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(1, min(int(days), 3)))
    s = runner.session()
    try:
        r = runner.fetch(s, ARR_MAIN_URL)
        soup = BeautifulSoup(r.text, "lxml")
        program_rows = soup.find_all("div", class_=lambda x: x and "grille-line" in x.split())
    except Exception as exc:
        runner.log("Arryadia grid failed: %s" % exc)
        program_rows = []

    details = []
    for row in program_rows:
        try:
            date_class = next((x for x in row.get("class", []) if re.fullmatch(r"\d{8}", str(x))), None)
            time_tag = row.find("div", class_=lambda x: x and "grille-time" in x.split())
            if not date_class or not time_tag:
                continue
            time_text = time_tag.get_text(" ", strip=True).replace("H", ":")
            naive = datetime.strptime("%s %s" % (date_class, time_text), "%Y%m%d %H:%M")
            event_start = naive.replace(tzinfo=TZ)
            title_tag = row.find("h2", class_=lambda x: x and "program-title-sm" in x.split())
            title = _clean(title_tag.get_text(" ", strip=True)) if title_tag else "Programme"
            desc = ""
            content = row.find("div", class_=lambda x: x and "grille-content" in x.split())
            if content:
                a = content.find("a", attrs={"tabindex": "0"})
                if a:
                    sib = a.next_sibling
                    while sib:
                        if isinstance(sib, str) and sib.strip():
                            desc = _clean(sib)
                            break
                        text = getattr(sib, "string", None)
                        if text and str(text).strip():
                            desc = _clean(text)
                            break
                        sib = getattr(sib, "next_sibling", None)
            details.append({"start": event_start, "title": title, "desc": desc})
        except Exception:
            continue

    if not details:
        filler = _arryadia_filler(start, end)
        out = []
        for cid in ARR_IDS:
            for st, sp, ti, de in filler[cid]:
                out.append(base.Event(cid, st, ti, de, sp, "fr", "ar", "arryadia"))
        runner.log("Arryadia historical logic: no grid, filler=%d" % len(out))
        return out

    details.sort(key=lambda x: x["start"])
    unique_starts = sorted({x["start"] for x in details})
    scraped = defaultdict(list)
    for p in details:
        later = [x for x in unique_starts if x > p["start"]]
        stop = later[0] if later else p["start"] + timedelta(hours=2)
        full = ("%s %s" % (p["title"], p["desc"])).lower()
        intended = [cid for pattern, cid in ARR_TAGS.items() if re.search(pattern, full)]
        if not intended:
            intended = ["Arryadia_HD", "Arryadia_TNT"]
        for cid in intended:
            title, desc, _live = format_football_title(p["title"], p["desc"])
            if cid in ("Arryadia_HD1", "Arryadia_HD2", "Arryadia_HD3") and not title.upper().startswith("LIVE:"):
                title = "Live: %s" % title
            scraped[cid].append((p["start"], stop, title, desc))

    filled = _arryadia_fill_gaps(scraped, start, end)
    out = []
    for cid in ARR_IDS:
        for st, sp, ti, de in filled[cid]:
            out.append(base.Event(cid, st, ti, de, sp, "fr", "ar", "arryadia"))
    runner.log("Arryadia historical logic: %d raw / %d final events" % (len(details), len(out)))
    return out


def install():
    """Install historical source functions into the cloud runner's base module."""
    base.scrape_snrt = scrape_snrt_historical
    base.scrape_arryadia = scrape_arryadia_historical
    runner.log("Historical SNRT + Arryadia logic installed")
