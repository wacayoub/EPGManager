#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safety wrapper around mena_cloud_finalize.

The merge already chooses one timeline per logical channel.  This final gate
also sanitizes LKG channels before they are written so an older polluted feed
cannot reintroduce overlaps or invalid slots.

For beIN SPORTS only, when no Arabic description exists, a conservative Arabic
summary is generated from the English programme title/competition metadata.
It never invents scores, winners or match facts.
"""
from __future__ import annotations

from collections import defaultdict
import re

import mena_cloud_finalize as base

_original_build_feed = base.build_feed

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")

_PLACEHOLDER_RE = re.compile(
    r"^(?:schedule unavailable|programme schedule unavailable|program schedule unavailable|"
    r"no information|no info|tba|جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$",
    re.I,
)

_COMPETITIONS = [
    (re.compile(r"english premier league|\bpremier league\b|\bepl\b", re.I), "الدوري الإنجليزي الممتاز"),
    (re.compile(r"french (?:league - )?ligue 1|\bligue 1\b", re.I), "الدوري الفرنسي"),
    (re.compile(r"spanish (?:la ?liga|league)|\bla ?liga\b", re.I), "الدوري الإسباني"),
    (re.compile(r"uefa champions league|\bucl\b", re.I), "دوري أبطال أوروبا"),
    (re.compile(r"uefa europa league|\buel\b", re.I), "الدوري الأوروبي"),
    (re.compile(r"uefa (?:conference league|europa conference league)|\buecl\b", re.I), "دوري المؤتمر الأوروبي"),
    (re.compile(r"afc champions league elite", re.I), "دوري أبطال آسيا للنخبة"),
    (re.compile(r"afc champions league two", re.I), "دوري أبطال آسيا 2"),
    (re.compile(r"afc champions league", re.I), "دوري أبطال آسيا"),
    (re.compile(r"turkish super league|süper lig|super lig", re.I), "الدوري التركي الممتاز"),
    (re.compile(r"english football league.*championship|efl.*championship", re.I), "دوري البطولة الإنجليزية"),
    (re.compile(r"bundesliga", re.I), "الدوري الألماني"),
    (re.compile(r"serie a", re.I), "الدوري الإيطالي"),
    (re.compile(r"saudi pro league", re.I), "الدوري السعودي للمحترفين"),
    (re.compile(r"uae pro league|adnoc pro league", re.I), "دوري أدنوك للمحترفين"),
    (re.compile(r"qatar stars league", re.I), "الدوري القطري"),
    (re.compile(r"formula 1|formula one|\bf1\b", re.I), "بطولة فورمولا 1"),
    (re.compile(r"nba", re.I), "دوري كرة السلة الأمريكي"),
    (re.compile(r"wimbledon", re.I), "بطولة ويمبلدون"),
]


def _text(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _lang(text):
    ar = len(AR_RE.findall(text or ""))
    lat = len(LAT_RE.findall(text or ""))
    if ar >= 3 and ar >= lat:
        return "ar"
    if lat >= 3:
        return "en"
    return "other"


def _norm(text):
    text = (text or "").casefold()
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return " ".join(text.split())


def _is_bein_sports(cid, name):
    probe = ("%s %s" % (cid or "", name or "")).casefold()
    if "bein" not in probe:
        return False
    if any(x in probe for x in ("movie", "movies", "series", "drama", "gourmet", "junior")):
        return False
    return any(x in probe for x in ("sport", "sports", "xtra", "max", "4k", "news"))


def _competition(title):
    for rx, ar in _COMPETITIONS:
        if rx.search(title or ""):
            return ar
    return ""


def _season(title):
    m = re.search(r"\b(20\d{2}[/-]20\d{2}|20\d{2})\b", title or "")
    return m.group(1) if m else ""


def _round(title):
    m = re.search(r"(?:week|matchday|md|round)\s*[-.:]?\s*(\d{1,2})\b", title or "", re.I)
    return m.group(1) if m else ""


def _episode(title):
    m = re.search(r"(?:episode|ep\.?|e)\s*[-.:]?\s*(\d{1,3})\b", title or "", re.I)
    return m.group(1) if m else ""


def arabic_bein_description(title):
    """Generate factual Arabic metadata from a sports listing title only."""
    title = (title or "").strip()
    if not title:
        return ""
    comp = _competition(title)
    season = _season(title)
    rnd = _round(title)
    ep = _episode(title)
    low = title.casefold()

    bits = []
    if " vs " in low or " v " in low:
        bits.append("مباراة")
        if comp:
            bits.append("ضمن منافسات %s" % comp)
    elif "highlights" in low:
        bits.append("ملخص لأبرز أحداث ومباريات%s" % ((" " + comp) if comp else " البطولة"))
    elif "review" in low:
        bits.append("مراجعة لأبرز أحداث%s" % ((" " + comp) if comp else " البطولة"))
    elif "preview" in low:
        bits.append("تقديم وتحليل لأبرز أحداث%s" % ((" " + comp) if comp else " البطولة"))
    elif "magazine" in low:
        bits.append("مجلة رياضية تقدم الأخبار والتحليلات المتعلقة%s" % ((" بـ" + comp) if comp else " بالبطولة"))
    elif "big interview" in low or "interview" in low:
        bits.append("مقابلة رياضية خاصة مع إحدى الشخصيات البارزة في عالم الرياضة")
    elif "stories" in low:
        bits.append("برنامج رياضي يستعرض قصصاً وشخصيات بارزة%s" % ((" من " + comp) if comp else ""))
    elif "news" in low or "bulletin" in low:
        bits.append("نشرة رياضية تقدم أبرز الأخبار والتطورات")
    else:
        bits.append("برنامج رياضي ضمن تغطية قنوات بي إن سبورتس")

    tail = []
    if rnd:
        tail.append("الجولة %s" % rnd)
    if season:
        tail.append("موسم %s" % season.replace("-", "/"))
    if ep:
        tail.append("الحلقة %s" % ep)
    text = " ".join(bits).strip()
    if tail:
        text += "، " + "، ".join(tail)
    if text and not text.endswith("."):
        text += "."
    return text


def _set_desc(programme, text):
    for d in list(programme.findall("desc")):
        programme.remove(d)
    if text:
        d = base.ET.SubElement(programme, "desc", {"lang": "ar"})
        d.text = text


def _event_quality(p, prefer_ar_desc=False):
    title = _text(p, "title")
    desc = _text(p, "desc")
    score = 10.0 if title else 0.0
    if desc:
        score += 8.0 + min(len(desc), 300) / 100.0
    if prefer_ar_desc and _lang(desc) == "ar":
        score += 8.0
    if _PLACEHOLDER_RE.match(title):
        score -= 40.0
    if desc and _norm(desc) == _norm(title):
        score -= 5.0
    return score


def _clean_channel_rows(cid, name, rows):
    sports = _is_bein_sports(cid, name)
    prepared = []
    for row in rows:
        p = base.copy_element(row)
        start = base.parse_xmltv_dt(p.get("start") or "")
        stop = base.parse_xmltv_dt(p.get("stop") or "")
        if start is None or stop is None or stop <= start:
            continue
        duration = (stop - start).total_seconds()
        title = _text(p, "title")
        desc = _text(p, "desc")
        if duration > 12 * 3600:
            continue
        if _PLACEHOLDER_RE.match(title):
            continue
        if desc and _norm(desc) == _norm(title):
            _set_desc(p, "")
            desc = ""
        if sports and _lang(desc) != "ar":
            _set_desc(p, arabic_bein_description(title))
        prepared.append([start, stop, p])

    prepared.sort(key=lambda x: (x[0], x[1]))
    kept = []
    for row in prepared:
        # Re-check until current no longer conflicts with the latest kept row.
        current = row
        while current is not None and kept and current[0] < kept[-1][1]:
            prev = kept[-1]
            ptitle = _norm(_text(prev[2], "title"))
            ctitle = _norm(_text(current[2], "title"))
            same = bool(ptitle and ptitle == ctitle)
            overlap = (prev[1] - current[0]).total_seconds()
            cdur = (current[1] - current[0]).total_seconds()
            if same and (current[1] <= prev[1] or cdur <= 10 * 60):
                if _event_quality(current[2], sports) > _event_quality(prev[2], sports) + 1.0:
                    kept.pop()
                    continue
                current = None
                break
            if overlap <= 3 * 60 and current[0] > prev[0]:
                cp = base.copy_element(prev[2])
                cp.set("stop", current[2].get("start") or cp.get("stop") or "")
                kept[-1] = [prev[0], current[0], cp]
                break
            # Bigger contradiction: keep the richer event. If current wins,
            # pop previous and compare against the row before it as well.
            if _event_quality(current[2], sports) > _event_quality(prev[2], sports) + 2.0:
                kept.pop()
                continue
            current = None
            break
        if current is not None:
            kept.append(current)
    return [x[2] for x in kept]


def safe_build_feed(ids, selected_programmes, cand_channels, prev_channels, source_by_id, generator_name):
    cleaned = {}
    for cid in ids:
        c = cand_channels.get(cid) or prev_channels.get(cid)
        if c is None:
            name = cid
        else:
            name = base.display_name(c)
        cleaned[cid] = _clean_channel_rows(cid, name, selected_programmes.get(cid, []))
    return _original_build_feed(ids, cleaned, cand_channels, prev_channels, source_by_id, generator_name)


base.build_feed = safe_build_feed


if __name__ == "__main__":
    raise SystemExit(base.main())
