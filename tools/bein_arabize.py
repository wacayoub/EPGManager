#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arabic post-processing for the published beIN MENA feed.

Goals:
- keep club/team names in Latin script in match titles;
- translate league/competition names and programme titles to Arabic;
- translate English descriptions to Arabic;
- move season/week/round/day metadata out of the title into the description;
- translate recurring beIN Sports News programme names;
- cache translations so future daily runs only translate new text.

This script never scrapes beIN. It only transforms an existing XMLTV feed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
DATE_TOKEN_RE = re.compile(r"(?:\s*[-–—]\s*)?(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
TIME_TOKEN_RE = re.compile(r"(?:\s*[-–—]\s*)?@?\b\d{1,2}:\d{2}\b")

YEAR_SEASON_RE = re.compile(r"\b(?:Season\s*)?(20\d{2})\s*[/\-]\s*(20\d{2})\b", re.I)
META_PATTERNS = [
    (re.compile(r"\bWeek\s*(\d+)\b", re.I), lambda m: f"الأسبوع {m.group(1)}"),
    (re.compile(r"\bRound\s*(\d+)\b", re.I), lambda m: f"الجولة {m.group(1)}"),
    (re.compile(r"\bMatchday\s*(\d+)\b", re.I), lambda m: f"الجولة {m.group(1)}"),
    (re.compile(r"\bDay\s*(\d+)\b", re.I), lambda m: f"اليوم {m.group(1)}"),
]

LEAGUE_GLOSSARY = {
    "English Premier League": "الدوري الإنجليزي الممتاز",
    "Premier League": "الدوري الإنجليزي الممتاز",
    "Ligue 1": "الدوري الفرنسي",
    "UEFA Champions League": "دوري أبطال أوروبا",
    "UEFA Europa League": "الدوري الأوروبي",
    "UEFA Conference League": "دوري المؤتمر الأوروبي",
    "LaLiga": "الدوري الإسباني",
    "La Liga": "الدوري الإسباني",
    "Serie A": "الدوري الإيطالي",
    "Bundesliga": "الدوري الألماني",
    "Saudi Pro League": "الدوري السعودي للمحترفين",
    "AFC Champions League Elite": "دوري أبطال آسيا للنخبة",
    "Asian Games Aichi-Nagoya 2026": "دورة الألعاب الآسيوية آيتشي-ناغويا 2026",
    "Asian Games": "دورة الألعاب الآسيوية",
}

SPORT_GLOSSARY = {
    "Football Men": "كرة القدم للرجال",
    "Football Women": "كرة القدم للسيدات",
    "Basketball": "كرة السلة",
    "Handball Men's": "كرة اليد للرجال",
    "Handball Women's": "كرة اليد للسيدات",
    "Volleyball Women's": "الكرة الطائرة للسيدات",
    "Volleyball Men's": "الكرة الطائرة للرجال",
    "Swimming": "السباحة",
    "Sports": "الرياضة",
    "Finals": "النهائيات",
    "Gold Medal": "الميدالية الذهبية",
    "Bronze Medal": "الميدالية البرونزية",
    "Quarter Final": "ربع النهائي",
    "Quarter-Final": "ربع النهائي",
    "QF": "ربع النهائي",
    "SF": "نصف النهائي",
}

NEWS_TITLES = {
    "ATP Tour": "ATP Tennis",
    "ATP": "ATP Tennis",
    "Three O'Clock bulletin": "نشرة الثالثة",
    "Three O’Clock bulletin": "نشرة الثالثة",
    "Al Jawla": "الجولة",
    "Al Jawla - Live Studio": "الجولة - الاستوديو المباشر",
    "All - Sports": "جميع الرياضات",
    "Arab Participation In Asian Games": "المشاركة العربية في دورة الألعاب الآسيوية",
    "Ligue 1 Show": "ملخص الدوري الفرنسي",
}

TITLE_CANONICAL_RULES = [
    # User-approved beIN title normalization.
    (re.compile(r"^مجلة الدوري الفرنسي$", re.I), "ملخص الدوري الفرنسي"),
    (re.compile(r"^الدوري الفرنسي\s+Weekly Review$", re.I), "الملخص الأسبوعي للدوري الفرنسي"),
    (re.compile(r"^قصص الدوري الإنجليزي(?:\s+EP\.\d+)?\s*-\s*طريق إبسويتش$", re.I), "قصص الدوري الإنجليزي - Ipswich Town"),
    (re.compile(r"^الدوري الإنجليزي\s+نتبوسترز(?:\s*-\s*الحلقة\s*\d+)?$", re.I), "ملخص الدوري الإنجليزي الممتاز"),
]

def canonicalize_title(text: str) -> str:
    text = strip_title_datetime(text)
    # Remove hybrid language labels left by machine translation.
    text = re.sub(r"\bSpanish\s+(?=الدوري الإسباني)", "", text, flags=re.I)
    text = re.sub(r"\bFrench\s+(?=الدوري الفرنسي)", "", text, flags=re.I)
    text = re.sub(r"\s*\(\s*Elderbi De Madrid\s*\)\s*", " ", text, flags=re.I)
    text = re.sub(r"\s*-\s*MD\d+\b", "", text, flags=re.I)
    # User preference: simplify AFC Elite title.
    text = re.sub(r"دوري أبطال آسيا للنخبة", "دوري أبطال آسيا", text)
    text = re.sub(r"دوري ابطال آسيا للنخبة", "دوري أبطال آسيا", text)
    for pat, repl in TITLE_CANONICAL_RULES:
        if pat.search(text):
            text = pat.sub(repl, text)
            break
    # Keep ATP naming in Latin script.
    text = re.sub(r"جولة\s+ATP(?:\s*-\s*مجلة)?", "ATP Tennis", text, flags=re.I)
    return normalize_spaces(text)

TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


def read_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def write_root(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    else:
        path.write_bytes(data)


def mostly_english(text: str) -> bool:
    if not text or ARABIC_RE.search(text):
        return False
    letters = re.findall(r"[A-Za-z]", text)
    return len(letters) >= 3


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" -–—|")

def strip_title_datetime(text: str) -> str:
    text = DATE_TOKEN_RE.sub("", text or "")
    text = TIME_TOKEN_RE.sub("", text)
    return normalize_spaces(text)


def apply_glossary(text: str) -> str:
    out = text
    for en, ar in sorted({**LEAGUE_GLOSSARY, **SPORT_GLOSSARY}.items(), key=lambda x: -len(x[0])):
        out = re.sub(re.escape(en), ar, out, flags=re.I)
    return normalize_spaces(out)


def extract_metadata(title: str):
    meta = []
    text = title
    m = YEAR_SEASON_RE.search(text)
    if m:
        meta.append(f"الموسم {m.group(1)}/{m.group(2)}")
        text = YEAR_SEASON_RE.sub("", text)
    for pat, render in META_PATTERNS:
        while True:
            m = pat.search(text)
            if not m:
                break
            meta.append(render(m))
            text = pat.sub("", text, count=1)
    text = re.sub(r"\s*-\s*-+", " - ", text)
    text = re.sub(r"(?:\s*-\s*)+$", "", text)
    text = re.sub(r"^\s*-\s*", "", text)
    return normalize_spaces(text), meta


def protect_match_participants(title: str):
    """Keep the participant span exactly as supplied when a title contains ' vs '."""
    if " vs " not in title.lower():
        return None, title
    # beIN titles are generally: TEAM A vs TEAM B - competition details
    parts = re.split(r"\s+-\s+", title, maxsplit=1)
    match = parts[0].strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    return match, tail


class Translator:
    def __init__(self, cache_path: Path, delay: float = 0.75):
        self.path = cache_path
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 EPGManager/1.0"})
        try:
            self.cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        except Exception:
            self.cache = {}
        self.new_count = 0
        self.failures = []
        self.rate_limited = False
        self.skipped_after_rate_limit = 0

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def translate(self, text: str) -> str:
        text = normalize_spaces(text)
        if not mostly_english(text):
            return text
        k = self.key(text)
        cached = self.cache.get(k)
        if cached and isinstance(cached, dict) and cached.get("source") == text and cached.get("ar"):
            return cached["ar"]

        # Google Translate is a best-effort enrichment only. A public 429 must
        # never invalidate an otherwise healthy beIN XMLTV feed.
        if self.rate_limited:
            self.skipped_after_rate_limit += 1
            return text

        params = {"client": "gtx", "sl": "en", "tl": "ar", "dt": "t", "q": text}
        saw_429 = False
        for attempt in range(4):
            try:
                r = self.session.get(TRANSLATE_URL, params=params, timeout=20)
                if r.status_code == 429:
                    saw_429 = True
                    retry_after = r.headers.get("Retry-After", "").strip()
                    try:
                        wait = max(1.0, min(60.0, float(retry_after)))
                    except Exception:
                        wait = (5.0, 12.0, 25.0, 45.0)[attempt]
                    if attempt < 3:
                        time.sleep(wait)
                        continue
                    self.rate_limited = True
                    self.failures.append({
                        "text": text[:180],
                        "error": "HTTP 429 after backoff; translation paused for remainder of run",
                    })
                    return text
                r.raise_for_status()
                data = r.json()
                ar = "".join(x[0] for x in data[0] if x and x[0])
                ar = normalize_spaces(ar)
                if ar and ARABIC_RE.search(ar):
                    self.cache[k] = {"source": text, "ar": ar}
                    self.new_count += 1
                    time.sleep(self.delay)
                    return ar
            except requests.RequestException as e:
                if attempt == 3:
                    self.failures.append({"text": text[:180], "error": str(e)})
                    return text
                time.sleep(2.0 * (2 ** attempt))
            except Exception as e:
                self.failures.append({"text": text[:180], "error": str(e)})
                return text
        if saw_429:
            self.rate_limited = True
        return text

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def translate_title(title: str, channel: str, tr: Translator):
    original = normalize_spaces(title)
    if not original:
        return original, []

    cleaned, meta = extract_metadata(strip_title_datetime(original))

    # Exact/recurring beIN News programmes first.
    for en, ar in sorted(NEWS_TITLES.items(), key=lambda x: -len(x[0])):
        if cleaned.lower() == en.lower():
            return canonicalize_title(ar), meta
        if cleaned.lower().startswith(en.lower() + " " ) or cleaned.lower().startswith(en.lower() + " -"):
            suffix = cleaned[len(en):].strip(" -")
            suffix = apply_glossary(suffix)
            if mostly_english(suffix):
                suffix = tr.translate(suffix)
            return canonicalize_title(normalize_spaces(f"{ar} - {suffix}" if suffix else ar)), meta

    match_part, tail = protect_match_participants(cleaned)
    if match_part is not None:
        # Team/club names remain exactly in English/Latin form.
        translated_tail = apply_glossary(tail)
        if mostly_english(translated_tail):
            translated_tail = tr.translate(translated_tail)
        return canonicalize_title(normalize_spaces(f"{match_part} - {translated_tail}" if translated_tail else match_part)), meta

    glossed = apply_glossary(cleaned)
    if mostly_english(glossed):
        glossed = tr.translate(glossed)
    return canonicalize_title(normalize_spaces(glossed)), meta


def append_metadata(desc: str, meta):
    if not meta:
        return desc
    marker = " • ".join(meta)
    if desc:
        return f"{desc}\n{marker}"
    return marker


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--cache", default="data/bein_translation_cache.json")
    ap.add_argument("--report", default="reports/bein-translation.json")
    args = ap.parse_args()

    root = read_root(Path(args.input))
    tr = Translator(Path(args.cache))
    desc_translated = 0
    programmes = root.findall("programme")

    # New production policy: NEVER machine-translate or rewrite beIN titles.
    # Titles are replaced from the full English guide in bein_hybrid.py.
    for p in programmes:
        desc=p.find("desc")
        if desc is None:
            continue
        old=normalize_spaces(desc.text or "")
        if mostly_english(old):
            new=tr.translate(old)
            if new!=old:
                desc.text=new
                desc.set("lang","ar")
                desc_translated += 1

    tr.save()
    write_root(Path(args.output),root)

    remaining_english_desc=0
    desc_total=0
    for p in programmes:
        d=p.find("desc")
        if d is not None and normalize_spaces(d.text or ""):
            desc_total += 1
            if mostly_english(normalize_spaces(d.text or "")):
                remaining_english_desc += 1

    report={
        "programmes":len(programmes),
        "title_policy":"preserve source here; full English titles applied in hybrid pass",
        "titles_changed":0,
        "descriptions_translated":desc_translated,
        "description_total":desc_total,
        "remaining_english_descriptions":remaining_english_desc,
        "new_cache_entries":tr.new_count,
        "translation_failures":tr.failures[:20],
        "rate_limited":tr.rate_limited,
        "skipped_after_rate_limit":tr.skipped_after_rate_limit,
        "translation_policy":"descriptions only; source text preserved on rate limit/error",
    }
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("BEIN_DESC_AR",json.dumps(report,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
