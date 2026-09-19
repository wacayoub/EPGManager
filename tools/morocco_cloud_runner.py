#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resilient Morocco EPG cloud runner.

Design goals:
- one public XMLTV endpoint for the receiver;
- no single upstream source may prevent publication of the other sources;
- source-by-source Last Known Good fallback;
- fast bounded network operations;
- 2M fallback parser that does not depend on old CSS classes;
- Chada fallback via TeleNews when chada.ma blocks datacenter requests;
- all channel IDs are always present in the XML, even if one provider is temporarily down.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

# Reuse the already-tested SNRT / Arryadia / Medi1 engines and data model.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import morocco_epg as base

TZ = ZoneInfo("Africa/Casablanca")
PARIS = ZoneInfo("Europe/Paris")
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")

T2M_AR = dict(base.T2M)
T2M_AR.update({
    "ahsane patissier": "أحسن باتيسييه",
    "ahsan patissier celebrites 2m": "أحسن باتيسييه المشاهير",
    "ch hiwat bladi": "شهيوات بلادي",
    "alhane 3chaqnaha": "ألحان عشقناها",
    "3ayne libra": "عين ليبرا",
    "al wassit": "الوسيط",
    "kif al hal": "كيف الحال",
    "al khobarae": "الخبراء",
    "addahira": "الظهيرة",
    "info soir": "أخبار المساء",
    "al massaiya": "المسائية",
    "zor bladk": "زور بلادك",
    "lecture du coran": "تلاوة القرآن الكريم",
    "coran avec laureats": "القرآن الكريم مع الفائزين",
})

CHADA_META = {
    "dandana": ("دندنة", "برنامج موسيقي على شدى تي في يستضيف فنانين ويتابع جديد أعمالهم، مع حوار وفقرات وأداءات موسيقية."),
    "100 var": ("100% VAR", "مجلة رياضية على شدى تي في لتحليل أخبار كرة القدم والمباريات، مع نقاشات فنية وتكتيكية وإحصائية وتركيز على الكرة المغربية."),
    "assahm": ("السهم", "برنامج حواري على شدى تي في يتناول مواضيع الساعة وقضايا المجتمع مع ضيوف ونقاش وتحليل."),
    "al sahm": ("السهم", "برنامج حواري على شدى تي في يتناول مواضيع الساعة وقضايا المجتمع مع ضيوف ونقاش وتحليل."),
    "polemique": ("بوليميك", "برنامج حواري على شدى تي في يناقش قضايا الساعة والمواضيع التي تثير اهتمام الجمهور."),
    "gossip": ("غوسيب", "برنامج فني وترفيهي على شدى تي في يتابع أخبار الفن والمشاهير وأبرز المستجدات."),
    "kehiwa wla atay": ("قهيوة ولا أتاي", "برنامج ترفيهي وحواري على شدى تي في يجمع مواضيع الحياة اليومية والضيوف في أجواء مغربية خفيفة."),
    "9hiwa ola atay": ("قهيوة ولا أتاي", "برنامج ترفيهي وحواري على شدى تي في يجمع مواضيع الحياة اليومية والضيوف في أجواء مغربية خفيفة."),
    "massae el kheir ya maghreb": ("مساء الخير يا مغرب", "موعد مسائي على شدى تي في يتابع مواضيع المجتمع والمستجدات مع فقرات وضيوف متنوعين."),
    "masae el kheir ya maghreb": ("مساء الخير يا مغرب", "موعد مسائي على شدى تي في يتابع مواضيع المجتمع والمستجدات مع فقرات وضيوف متنوعين."),
    "musique chada tv": ("موسيقى شدى تي في", "فقرات موسيقية مختارة على شدى تي في تضم أعمالاً مغربية وعربية متنوعة."),
    "fan tyab maa damti": ("فن الطياب مع ضامتي", "برنامج طبخ وترفيه على شدى تي في يقدم وصفات وأفكاراً عملية في أجواء خفيفة."),
    "nissae ideal": ("نساء إيديال", "برنامج اجتماعي وأسري على شدى تي في يهتم بالمرأة والأسرة ومواضيع الحياة اليومية."),
    "hbab rabab": ("حباب رباب", "برنامج فني وموسيقي على شدى تي في يستضيف فنانين ومواهب ويقدم حوارات وفقرات موسيقية متنوعة."),
    "chada cover": ("شدى كوفر", "برنامج موسيقي على شدى تي في يسلط الضوء على الأصوات والمواهب والأداءات الجديدة."),
    "natla9aw f4": ("نتلاقاو فـ4", "برنامج على شدى تي في يقدم فقرات متنوعة ومواضيع قريبة من اهتمامات الجمهور."),
    "info sport": ("أخبار الرياضة", "موعد رياضي على شدى تي في يقدم الأخبار والنتائج وأبرز المستجدات الرياضية."),
}

SKIP_TEXT = {
    "accueil", "chaînes", "chaines", "radio", "catégories", "categories", "replays",
    "aujourd'hui", "hier", "demain", "matin", "midi", "soir", "en ce moment",
    "vers la grille", "en prime time", "voir plus", "lire la suite", "télévision", "television",
}


def log(msg):
    print("[%s] %s" % (datetime.now(TZ).strftime("%F %T %Z"), msg), flush=True)


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def norm(s):
    return base.norm(s)


def session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152 Safari/537.36 EPGManagerCloud/rc24",
        "Accept-Language": "fr,ar;q=0.9,en;q=0.6",
    })
    return s


def fetch(s, url, params=None, referer=None):
    headers = {"Referer": referer} if referer else None
    last = None
    for attempt in range(2):
        try:
            r = s.get(url, params=params, headers=headers, timeout=(5, 12))
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
    raise last


def closest_program_block(node):
    """Return the smallest ancestor that contains a time and useful programme text."""
    cur = node.parent
    best = None
    for _ in range(6):
        if cur is None:
            break
        parts = [clean(x) for x in cur.stripped_strings if clean(x)]
        times = [x for x in parts if TIME_RE.fullmatch(x)]
        if times and len(parts) <= 24:
            best = (cur, parts, times)
            if len(times) == 1 and len(parts) <= 10:
                break
        cur = cur.parent
    return best


def generic_programme_cards(html, target_hint=None):
    """Extract (HH:MM, title, description) without depending on provider CSS classes."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    seen = set()
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if not title or len(title) < 2 or title.casefold() in SKIP_TEXT:
            continue
        block = closest_program_block(a)
        if not block:
            continue
        _, parts, times = block
        if len(times) != 1:
            continue
        tm = times[0]
        # reject menu/date navigation and obvious channel-selector blocks
        if len(title) > 140 or title.casefold() in {x.casefold() for x in base.CHANNELS.values()}:
            continue
        if target_hint and target_hint.casefold() not in " ".join(parts).casefold() and len(parts) > 12:
            continue
        try:
            idx = parts.index(title)
        except ValueError:
            idx = -1
        desc_parts = []
        if idx >= 0:
            for x in parts[idx + 1:]:
                if TIME_RE.fullmatch(x) or x.casefold() in SKIP_TEXT:
                    continue
                if x != title and len(x) > 5:
                    desc_parts.append(x)
        desc = clean(" ".join(desc_parts[:2]))
        k = (tm, title.casefold())
        if k not in seen:
            seen.add(k)
            rows.append((tm, title, desc))
    rows.sort(key=lambda x: int(x[0].split(":")[0]) * 60 + int(x[0].split(":")[1]))
    return rows


def translate_2m_title(title):
    n = norm(title)
    if n in T2M_AR:
        return T2M_AR[n]
    for key, value in T2M_AR.items():
        if key and key in n and len(key) >= 6:
            return value
    return clean(title)


def scrape_2m(days):
    s = session()
    base_url = "https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"
    today = datetime.now(TZ).date()
    out = []
    for i in range(days):
        day = today + timedelta(days=i)
        candidates = []
        # The site changed markup in 2026; request the date once and parse cards generically.
        for params in ({"date": day.isoformat()}, None if i == 0 else {"date": day.isoformat(), "period": "morning"}):
            try:
                r = fetch(s, base_url, params=params, referer="https://tv-programme.telecablesat.fr/")
                candidates = generic_programme_cards(r.text)
                if len(candidates) >= 5:
                    break
            except Exception as exc:
                log("2M %s fetch failed: %s" % (day, exc))
        # As a final fallback, use the old parser against the returned page if it still matches.
        if len(candidates) < 5:
            try:
                h = base.Http()
                r = h.get(base_url, params={"date": day.isoformat()})
                old = base.parse_2m(h, r.text, day, "morning")
                if old:
                    out.extend(old)
                    continue
            except Exception:
                pass
        for tm, title, desc in candidates:
            hh, mm = map(int, tm.split(":"))
            start = datetime.combine(day, dtime(hh, mm), PARIS).astimezone(TZ)
            at = translate_2m_title(title)
            ad = "برنامج يُعرض على قناة 2M."
            if desc:
                # Keep descriptions deterministic and lightweight: no per-event online translation call.
                nd = norm(desc)
                if "meteo" in nd or "météo" in desc.casefold():
                    ad = "نشرة تقدم توقعات الطقس ودرجات الحرارة والرياح والتساقطات."
                elif "journal" in nd or "info" in nd:
                    ad = "موعد إخباري على قناة 2M يقدم أبرز الأخبار والمستجدات."
            out.append(base.Event("2M", start, at, ad, None, base.lang(at, "fr"), "ar", "2m"))
    # dedupe and infer stops
    ded = {}
    for e in out:
        ded[(e.start, e.title.casefold())] = e
    rows = sorted(ded.values(), key=lambda e: e.start)
    base.infer(rows)
    log("2M parser: %d events" % len(rows))
    return rows


def chada_enrich(title):
    raw = clean(title)
    n = norm(raw)
    for key in sorted(CHADA_META, key=len, reverse=True):
        if key in n:
            return CHADA_META[key]
    return raw, "برنامج ضمن شبكة شدى تي في."


def parse_chada_page(html, day):
    rows = generic_programme_cards(html)
    out = []
    for tm, title, _desc in rows:
        # Keep only plausible programme names; TeleNews channel pages include unrelated timeline cards.
        n = norm(title)
        known = any(k in n for k in CHADA_META)
        if not known:
            continue
        hh, mm = map(int, tm.split(":"))
        at, ad = chada_enrich(title)
        out.append(base.Event("Chada TV", datetime.combine(day, dtime(hh, mm), TZ), at, ad, None, base.lang(at), "ar", "chada"))
    ded = {(e.start, e.title.casefold()): e for e in out}
    return sorted(ded.values(), key=lambda e: e.start)


def scrape_chada(days):
    s = session()
    today = datetime.now(TZ).date()
    out = []
    # Official page first. It may present a datacenter challenge, so failure is expected and harmless.
    try:
        r = fetch(s, "https://chada.ma/fr/chada-tv/grille-tv/")
        txt = clean(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))
        if "request is being verified" not in txt.casefold() and "just a moment" not in txt.casefold():
            # Reuse the legacy official parser if the page is actually available.
            try:
                legacy = base.scrape_2m_chada(days)
                official = [e for e in legacy if e.channel == "Chada TV"]
                if len(official) >= 3:
                    log("Chada official: %d events" % len(official))
                    return official
            except Exception:
                pass
    except Exception as exc:
        log("Chada official unavailable: %s" % exc)

    # Independent public fallback. This prevents Chada anti-bot from taking down 2M/SNRT/Medi1.
    url = "https://tele-news.net/fr/maroc/television/cm9WVVlDbkswYktGODVnYkFhWkpNZz09"
    try:
        r = fetch(s, url)
        first = parse_chada_page(r.text, today)
        if first:
            out.extend(first)
            # TeleNews exposes a rolling channel schedule. Preserve the visible pattern for the
            # configured horizon only when no per-date links are discoverable.
            soup = BeautifulSoup(r.text, "lxml")
            date_links = []
            for a in soup.find_all("a", href=True):
                m = DATE_RE.search(a.get("href", ""))
                if m:
                    date_links.append((m.group(0), urljoin(url, a["href"])))
            used_days = {today}
            for ds, href in date_links:
                try:
                    d = datetime.strptime(ds, "%Y-%m-%d").date()
                except Exception:
                    continue
                if d < today or d >= today + timedelta(days=days) or d in used_days:
                    continue
                try:
                    rr = parse_chada_page(fetch(s, href).text, d)
                    if rr:
                        out.extend(rr); used_days.add(d)
                except Exception:
                    pass
            # If only today's page is available, do not invent future schedules.
    except Exception as exc:
        log("Chada TeleNews fallback unavailable: %s" % exc)
    ded = {(e.start, e.title.casefold()): e for e in out}
    rows = sorted(ded.values(), key=lambda e: e.start)
    base.infer(rows)
    log("Chada fallback: %d events" % len(rows))
    return rows


def read_previous(path):
    return base.previous(path) if path and path.exists() else []


def choose(name, fresh, old, channel_ids, minimum):
    fresh = [e for e in fresh if e.channel in channel_ids]
    now = datetime.now(TZ)
    valid_fresh = [e for e in fresh if e.start < now + timedelta(days=8) and (e.stop or e.start + timedelta(hours=1)) > now - timedelta(hours=12)]
    if len(valid_fresh) >= minimum:
        return fresh, "fresh"
    previous = [e for e in old if e.channel in channel_ids]
    valid_old = [e for e in previous if e.start < now + timedelta(days=8) and (e.stop or e.start + timedelta(hours=1)) > now - timedelta(hours=12)]
    if len(valid_old) >= minimum:
        log("%s: fresh invalid (%d), using Last Known Good (%d)" % (name, len(valid_fresh), len(valid_old)))
        return previous, "last-known-good"
    log("%s: unavailable this run; source isolated, other providers will still publish" % name)
    return [], "unavailable"


def write_xml(rows, path):
    root = ET.Element("tv", {"generator-info-name": "EPGManager Morocco Cloud resilient"})
    # Stable IDs: always include every supported channel, even if one upstream is temporarily down.
    for cid in sorted(base.CHANNELS):
        ch = ET.SubElement(root, "channel", {"id": cid})
        ET.SubElement(ch, "display-name").text = base.CHANNELS[cid]
    counts = defaultdict(int)
    for e in sorted(rows, key=lambda e: (e.start, e.channel, e.title.casefold())):
        stop = e.stop or (e.start + timedelta(hours=1))
        p = ET.SubElement(root, "programme", {"start": base.xdt(e.start), "stop": base.xdt(stop), "channel": e.channel})
        ET.SubElement(p, "title", {"lang": e.tl or base.lang(e.title)}).text = e.title
        ET.SubElement(p, "desc", {"lang": e.dl or base.lang(e.desc)}).text = e.desc or e.title
        counts[e.channel] += 1
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return dict(counts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--previous", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--scheduled", action="store_true")
    a = ap.parse_args()
    now = datetime.now(TZ)
    # One controlled morning refresh per Casablanca day.  The workflow invokes
    # both possible UTC offsets to survive the Morocco DST/Ramadan transition.
    if a.scheduled and now.hour != 6:
        log("Schedule gate skip: local hour %02d" % now.hour)
        return 0

    outdir = Path(a.output_dir); outdir.mkdir(parents=True, exist_ok=True)
    old = read_previous(Path(a.previous)) if a.previous else []

    jobs = {
        "snrt": lambda: base.scrape_snrt(a.days),
        "arryadia": lambda: base.scrape_arryadia(min(a.days, 3)),
        "2m": lambda: scrape_2m(a.days),
        "chada": lambda: scrape_chada(a.days),
        "medi1": lambda: base.scrape_medi1(a.days),
    }
    # Run providers independently. A failure is data, not a global exception.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {k: [] for k in jobs}
    with ThreadPoolExecutor(max_workers=5) as ex:
        fut = {ex.submit(fn): name for name, fn in jobs.items()}
        for f in as_completed(fut):
            name = fut[f]
            try:
                results[name] = f.result()
                log("%s scraper returned %d events" % (name, len(results[name])))
            except Exception as exc:
                log("%s scraper crashed: %s" % (name, exc))

    selected = []
    status = {}
    config = {
        "snrt": (set(base.GROUPS["snrt"]), 10),
        "arryadia": (set(base.GROUPS["arryadia"]), 10),
        "2m": ({"2M"}, 5),
        "chada": ({"Chada TV"}, 3),
        "medi1": (set(base.GROUPS["medi1"]), 5),
    }
    for name, (ids, minimum) in config.items():
        rows, mode = choose(name, results.get(name, []), old, ids, minimum)
        selected.extend(rows)
        status[name] = {"mode": mode, "events": len(rows)}

    # Dedupe and infer missing stops independently by channel.
    ded = {}
    for e in selected:
        ded[(e.channel, base.xdt(e.start), e.title.casefold())] = e
    merged = list(ded.values())
    base.infer(merged)

    # Never publish an empty/near-empty candidate. SNRT alone normally exceeds this threshold.
    if len(merged) < 50:
        log("FATAL candidate too small: %d programmes" % len(merged))
        return 2

    xml = outdir / "morocco.xml"
    counts = write_xml(merged, xml)
    raw = xml.read_bytes()
    gz = outdir / "morocco.xml.gz"
    with gzip.GzipFile(filename="morocco.xml", mode="wb", fileobj=gz.open("wb"), compresslevel=9, mtime=0) as f:
        f.write(raw)
    (outdir / "morocco.txt").write_text("\n".join("%s | %s | %d" % (c, base.CHANNELS[c], counts.get(c, 0)) for c in sorted(base.CHANNELS)) + "\n", encoding="utf-8")
    manifest = {
        "version": now.strftime("%Y%m%d-%H%M%S"),
        "generated": now.isoformat(),
        "timezone": "Africa/Casablanca",
        "url": "morocco.xml.gz",
        "sha256": hashlib.sha256(gz.read_bytes()).hexdigest(),
        "size": gz.stat().st_size,
        "channels": len(base.CHANNELS),
        "programmes": sum(counts.values()),
        "channel_counts": {c: counts.get(c, 0) for c in sorted(base.CHANNELS)},
        "sources": status,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log("PASS: %d stable channels / %d programmes / %.1f KiB" % (manifest["channels"], manifest["programmes"], manifest["size"] / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
