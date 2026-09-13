#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arabic/Darija quality layer for the Morocco Cloud 2M scraper.

This wrapper keeps the resilient cloud runner but restores the translation
behaviour of the original 2M script: known Moroccan/Darija programme names
are normalized from a curated dictionary, unknown French/romanized titles
fall back to Arabic translation, and real descriptions are translated rather
than being replaced by a generic sentence.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import morocco_epg as base
import morocco_cloud_runner as runner

TZ = runner.TZ
PARIS = runner.PARIS
AUDIT = []

# Start from every correction already present in the historical 2M script,
# then keep the extra aliases added by the resilient cloud runner.
T2M_AR = dict(base.T2M)
T2M_AR.update(runner.T2M_AR)
T2M_AR.update({
    "charqi gharbi": "شرقي أو غربي",
    "charqi ou lgharbi": "شرقي أو غربي",
    "soiree chaabi": "سهرة شعبية",
    "soiree cha3bi": "سهرة شعبية",
    "attahssina": "التحصينة",
    "at tahssina": "التحصينة",
    "priere du vendredi": "صلاة الجمعة",
    "priere vendredi": "صلاة الجمعة",
    "ayne lkebrite": "عين الكبريت",
    "ayn lkebrite": "عين الكبريت",
    "sabahiyat 2m": "صباحيات 2M",
    "sabahiyat": "صباحيات 2M",
    "jt arabe": "الأخبار بالعربية",
    "journal amazigh": "الأخبار بالأمازيغية",
    "info soir": "أخبار المساء",
    "al akhbar": "الأخبار",
    "addahira": "الظهيرة",
    "al dahira": "الظهيرة",
    "al massaiya": "المسائية",
    "rachid show": "رشيد شو",
    "moudawala": "مداولة",
    "lmktoub": "المكتوب",
    "dar nsa": "دار النسا",
    "najm chaabi": "النجم الشعبي",
    "ahsane patissier": "أحسن باتيسييه",
    "ahsan patissier celebrites 2m": "أحسن باتيسييه المشاهير",
    "ch hiwat bladi": "شهيوات بلادي",
    "chhiwat bladi": "شهيوات بلادي",
    "alhane 3chaqnaha": "ألحان عشقناها",
    "3ayne libra": "عين ليبرا",
    "al wassit": "الوسيط",
    "kif al hal": "كيف الحال",
    "al khobarae": "الخبراء",
    "zor bladk": "زور بلادك",
    "lecture du coran": "تلاوة القرآن الكريم",
    "coran avec laureats": "القرآن الكريم مع الفائزين",
})

_AR_RE = re.compile(r"[\u0600-\u06ff]")
_title_cache = {}
_desc_cache = {}
_translate_http = base.Http()


def has_arabic(text):
    return bool(_AR_RE.search(str(text or "")))


def clean(text):
    return runner.clean(text)


def norm(text):
    return runner.norm(text)


def semantic_desc(title_ar):
    n = clean(title_ar)
    if any(x in n for x in ("الأخبار", "الظهيرة", "المسائية")):
        return "موعد إخباري على القناة الثانية 2M يقدم أبرز الأخبار والمستجدات الوطنية والدولية."
    if "صباحيات" in n:
        return "برنامج صباحي مغربي على 2M يجمع مواضيع المجتمع والصحة والثقافة والمطبخ مع فقرات وضيوف متنوعين."
    if any(x in n for x in ("شهيوات", "باتيسييه", "الطياب")):
        return "برنامج مغربي للطبخ على 2M يقدم وصفات ونصائح وأفكاراً من المطبخ المغربي."
    if "رشيد شو" in n:
        return "برنامج حواري وترفيهي مغربي على 2M يستضيف فنانين وشخصيات مع فقرات متنوعة."
    if "صلاة الجمعة" in n or "القرآن" in n:
        return "موعد ديني على القناة الثانية 2M."
    if "النجم الشعبي" in n or "سهرة شعبية" in n or "ألحان" in n:
        return "برنامج فني وموسيقي مغربي على 2M يهتم بالأغنية والفنانين المغاربة."
    return "برنامج مغربي يُعرض على القناة الثانية 2M."


def translate_title(title):
    raw = clean(title)
    if not raw:
        return "برنامج على 2M"
    if raw in _title_cache:
        return _title_cache[raw]
    if has_arabic(raw):
        out = raw
    else:
        n = norm(raw)
        out = T2M_AR.get(n)
        if not out:
            # Prefer the longest curated Darija/2M alias before machine translation.
            matches = [(len(k), v) for k, v in T2M_AR.items() if k and len(k) >= 5 and k in n]
            if matches:
                out = max(matches)[1]
        if not out:
            out = base.tr2m_title(_translate_http, raw)
        if not has_arabic(out):
            translated = base.google_ar(_translate_http, raw)
            if has_arabic(translated):
                out = translated
        # Never publish a raw French/romanized title as lang=ar.
        if not has_arabic(out):
            out = "برنامج على 2M"
    _title_cache[raw] = clean(out)
    return _title_cache[raw]


def translate_desc(desc, title_ar):
    raw = clean(desc)
    key = (raw, title_ar)
    if key in _desc_cache:
        return _desc_cache[key]
    if raw and has_arabic(raw):
        out = raw
    elif raw:
        out = base.tr2m_desc(_translate_http, raw)
        if not has_arabic(out):
            out = base.google_ar(_translate_http, raw)
    else:
        out = ""
    if not has_arabic(out):
        out = semantic_desc(title_ar)
    # Avoid descriptions that are only the title; make them useful for EPG.
    if norm(out) == norm(title_ar) or len(clean(out)) < 10:
        out = semantic_desc(title_ar)
    _desc_cache[key] = clean(out)
    return _desc_cache[key]


def scrape_2m_ar(days):
    s = runner.session()
    base_url = "https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"
    today = datetime.now(TZ).date()
    out = []

    for i in range(days):
        day = today + timedelta(days=i)
        candidates = []
        for params in ({"date": day.isoformat()}, None if i == 0 else {"date": day.isoformat(), "period": "morning"}):
            try:
                r = runner.fetch(s, base_url, params=params, referer="https://tv-programme.telecablesat.fr/")
                candidates = runner.generic_programme_cards(r.text)
                if len(candidates) >= 5:
                    break
            except Exception as exc:
                runner.log("2M %s fetch failed: %s" % (day, exc))

        # If generic parsing cannot understand the upstream page, use the old
        # 2M parser: it already contains the historical Arabic translation logic.
        if len(candidates) < 5:
            try:
                h = base.Http()
                r = h.get(base_url, params={"date": day.isoformat()})
                old = base.parse_2m(h, r.text, day, "morning")
                if old:
                    for e in old:
                        e.tl = "ar"
                        e.dl = "ar"
                        if not has_arabic(e.title):
                            e.title = translate_title(e.title)
                        if not has_arabic(e.desc):
                            e.desc = translate_desc(e.desc, e.title)
                        AUDIT.append({
                            "date": day.isoformat(), "source_title": "old-parser",
                            "title_ar": e.title, "desc_ar": e.desc,
                            "title_ok": has_arabic(e.title), "desc_ok": has_arabic(e.desc),
                        })
                    out.extend(old)
                    continue
            except Exception as exc:
                runner.log("2M old parser fallback failed: %s" % exc)

        for tm, source_title, source_desc in candidates:
            hh, mm = map(int, tm.split(":"))
            start = datetime.combine(day, dtime(hh, mm), PARIS).astimezone(TZ)
            title_ar = translate_title(source_title)
            desc_ar = translate_desc(source_desc, title_ar)
            out.append(base.Event("2M", start, title_ar, desc_ar, None, "ar", "ar", "2m"))
            AUDIT.append({
                "date": day.isoformat(),
                "time": tm,
                "source_title": clean(source_title),
                "title_ar": title_ar,
                "source_desc": clean(source_desc)[:500],
                "desc_ar": desc_ar,
                "title_ok": has_arabic(title_ar),
                "desc_ok": has_arabic(desc_ar),
            })

    ded = {}
    for e in out:
        ded[(e.start, e.title.casefold())] = e
    rows = sorted(ded.values(), key=lambda e: e.start)
    base.infer(rows)
    title_ok = sum(has_arabic(e.title) for e in rows)
    desc_ok = sum(has_arabic(e.desc) for e in rows)
    runner.log("2M Arabic audit: %d/%d titles AR, %d/%d descriptions AR" % (title_ok, len(rows), desc_ok, len(rows)))
    return rows


def _output_dir_from_argv():
    try:
        i = sys.argv.index("--output-dir")
        return Path(sys.argv[i + 1])
    except Exception:
        return Path("output")


def main():
    runner.scrape_2m = scrape_2m_ar
    rc = runner.main()
    outdir = _output_dir_from_argv()
    outdir.mkdir(parents=True, exist_ok=True)
    if AUDIT:
        titles_ok = sum(bool(x.get("title_ok")) for x in AUDIT)
        descs_ok = sum(bool(x.get("desc_ok")) for x in AUDIT)
        report = {
            "events": len(AUDIT),
            "title_arabic": titles_ok,
            "description_arabic": descs_ok,
            "title_ratio": round(titles_ok / float(len(AUDIT)), 4),
            "description_ratio": round(descs_ok / float(len(AUDIT)), 4),
            "samples": AUDIT[:40],
        }
        (outdir / "2m_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
