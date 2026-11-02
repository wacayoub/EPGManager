#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ElCinema hybrid language builder.

Hybrid channels: English title/sub-title + Arabic description.
Arabic-native channels: Arabic title/sub-title + Arabic description.

The Arabic scrape remains authoritative for schedules and descriptions.
The English ElCinema guide is used only for titles of explicitly hybrid IDs.
No programme rows or translations are invented.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06FF]")
LATIN = re.compile(r"[A-Za-z]")
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


class Translator:
    def __init__(self, cache_path: str):
        self.path = Path(cache_path)
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            self.cache = {}
        self.new = 0
        self.failures = []

    def translate(self, text: str, target: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return text
        key = target + "|" + text
        cached = self.cache.get(key)
        if cached:
            return cached
        params = urlencode({"client":"gtx","sl":"auto","tl":target,"dt":"t","q":text})
        req = Request(TRANSLATE_URL + "?" + params, headers={"User-Agent":"Mozilla/5.0 EPGManager/ElCinema"})
        for attempt in range(3):
            try:
                with urlopen(req, timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8"))
                out = "".join(x[0] for x in data[0] if x and x[0]).strip()
                if out:
                    self.cache[key] = out
                    self.new += 1
                    time.sleep(0.12)
                    return out
            except Exception as e:
                if attempt == 2:
                    self.failures.append({"target":target,"text":text[:160],"error":str(e)})
                else:
                    time.sleep(1.5 * (attempt + 1))
        return text

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_root(path: str) -> ET.Element:
    return ET.parse(path).getroot()


def text_of(node: ET.Element, tag: str) -> str:
    for child in node.findall(tag):
        txt = (child.text or "").strip()
        if txt:
            return txt
    return ""


def is_arabic(text: str) -> bool:
    return bool(AR.search(text or ""))


def copy(node: ET.Element) -> ET.Element:
    return deepcopy(node)


def replace_tag(dst: ET.Element, src: ET.Element, tag: str, lang: str) -> bool:
    candidate = None
    for child in src.findall(tag):
        if (child.text or "").strip():
            candidate = deepcopy(child)
            break
    if candidate is None:
        return False
    for child in list(dst.findall(tag)):
        dst.remove(child)
    candidate.set("lang", lang)
    dst.insert(0 if tag == "title" else min(1, len(dst)), candidate)
    return True


def ensure_lang(node: ET.Element, tag: str, lang: str) -> None:
    for child in node.findall(tag):
        if (child.text or "").strip():
            child.set("lang", lang)


def ensure_arabic_description(node: ET.Element, tr: Translator) -> tuple[int, int]:
    translated = 0
    remaining = 0
    for child in node.findall("desc"):
        txt = (child.text or "").strip()
        if not txt:
            continue
        if not is_arabic(txt):
            new = tr.translate(txt, "ar")
            if new != txt and is_arabic(new):
                child.text = new
                child.set("lang", "ar")
                translated += 1
            else:
                remaining += 1
        else:
            child.set("lang", "ar")
    return translated, remaining


def normalize_title_language(node: ET.Element, profile: str, tr: Translator) -> tuple[int, int]:
    changed = 0
    remaining = 0
    for child in node.findall("title"):
        txt = (child.text or "").strip()
        if not txt:
            continue
        if profile == "hybrid":
            # English-title policy: translate residual Arabic-only/mixed Arabic titles
            # from the English ElCinema guide to English.
            if is_arabic(txt):
                new = tr.translate(txt, "en")
                if new != txt and not is_arabic(new):
                    child.text = new
                    child.set("lang", "en")
                    changed += 1
                else:
                    remaining += 1
            else:
                child.set("lang", "en")
        else:
            # Arabic-native policy: translate Latin-only titles from the Arabic
            # ElCinema page into Arabic instead of leaking English into the feed.
            if not is_arabic(txt) and LATIN.search(txt):
                new = tr.translate(txt, "ar")
                if new != txt and is_arabic(new):
                    child.text = new
                    child.set("lang", "ar")
                    changed += 1
                else:
                    remaining += 1
            else:
                child.set("lang", "ar")
    return changed, remaining


def event_key(p: ET.Element):
    return (
        (p.get("channel") or "").strip(),
        (p.get("start") or "").strip(),
        (p.get("stop") or "").strip(),
    )


def start_key(p: ET.Element):
    return ((p.get("channel") or "").strip(), (p.get("start") or "").strip())


def build_catalogue(fallback_catalogue: str, policy_path: str, output: str) -> int:
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    wanted = set(policy.get("hybrid_ids", []))
    root = read_root(fallback_catalogue)
    out = ET.Element("channels")
    found = set()
    for ch in root.findall("channel"):
        cid = (ch.get("xmltv_id") or ch.get("id") or "").strip()
        if cid in wanted:
            out.append(copy(ch))
            found.add(cid)
    ET.indent(out, space="  ")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    missing = sorted(wanted - found)
    print(json.dumps({
        "hybrid_requested": len(wanted),
        "hybrid_catalogue": len(out),
        "missing": missing,
    }, ensure_ascii=False))
    return 0 if len(out) and not missing else 3


def merge(arabic: str, english: str, policy_path: str, output: str, report: str, cache: str) -> int:
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    hybrid = set(policy.get("hybrid_ids", []))
    ar_root = read_root(arabic)
    en_root = read_root(english)
    tr = Translator(cache)

    en_exact = {}
    en_start = defaultdict(list)
    for p in en_root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if not cid:
            continue
        en_exact[event_key(p)] = p
        en_start[start_key(p)].append(p)

    out = ET.Element("tv", {
        "generator-info-name": "EPGManager ElCinema hybrid EN-title AR-description",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    for ch in ar_root.findall("channel"):
        out.append(copy(ch))

    stats = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "programmes": 0,
        "hybrid_programmes": 0,
        "arabic_native_programmes": 0,
        "english_title_applied": 0,
        "english_title_missing": 0,
        "arabic_description_present": 0,
        "arabic_description_missing": 0,
        "titles_translated_to_ar": 0,
        "titles_translated_to_en": 0,
        "title_language_remaining_mismatch": 0,
        "descriptions_translated_to_ar": 0,
        "description_language_remaining_mismatch": 0,
        "samples": {},
    }

    for p in ar_root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        node = copy(p)
        if cid in hybrid:
            stats["hybrid_programmes"] += 1
            en = en_exact.get(event_key(p))
            if en is None:
                candidates = en_start.get(start_key(p), [])
                en = candidates[0] if candidates else None
            applied = False
            if en is not None:
                applied = replace_tag(node, en, "title", "en")
                replace_tag(node, en, "sub-title", "en")
            if applied:
                stats["english_title_applied"] += 1
            else:
                stats["english_title_missing"] += 1
            ensure_lang(node, "desc", "ar")
        else:
            stats["arabic_native_programmes"] += 1
            ensure_lang(node, "title", "ar")
            ensure_lang(node, "sub-title", "ar")
            ensure_lang(node, "desc", "ar")

        title_changed, title_remaining = normalize_title_language(node, "hybrid" if cid in hybrid else "arabic_native", tr)
        if cid in hybrid:
            stats["titles_translated_to_en"] += title_changed
        else:
            stats["titles_translated_to_ar"] += title_changed
        stats["title_language_remaining_mismatch"] += title_remaining
        desc_changed, desc_remaining = ensure_arabic_description(node, tr)
        stats["descriptions_translated_to_ar"] += desc_changed
        stats["description_language_remaining_mismatch"] += desc_remaining
        title = text_of(node, "title")
        desc = text_of(node, "desc")
        if desc and is_arabic(desc):
            stats["arabic_description_present"] += 1
        else:
            stats["arabic_description_missing"] += 1

        if cid and cid not in stats["samples"]:
            stats["samples"][cid] = {
                "profile": "hybrid" if cid in hybrid else "arabic_native",
                "title": title,
                "description": desc[:240],
                "title_has_arabic": is_arabic(title),
                "description_has_arabic": is_arabic(desc),
            }
        out.append(node)
        stats["programmes"] += 1

    ET.indent(out, space="  ")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    tr.save()
    stats["translation_cache_new"] = tr.new
    stats["translation_failures"] = tr.failures[:20]
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k:v for k,v in stats.items() if k!="samples"}, ensure_ascii=False))
    if stats["hybrid_programmes"] and stats["english_title_applied"] == 0:
        return 4
    return 0 if stats["programmes"] else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("catalogue")
    c.add_argument("--fallback-catalogue", required=True)
    c.add_argument("--policy", required=True)
    c.add_argument("--output", required=True)

    m = sub.add_parser("merge")
    m.add_argument("--arabic", required=True)
    m.add_argument("--english", required=True)
    m.add_argument("--policy", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--report", required=True)
    m.add_argument("--cache", default="data/elcinema_translation_cache.json")

    args = ap.parse_args()
    if args.cmd == "catalogue":
        return build_catalogue(args.fallback_catalogue, args.policy, args.output)
    return merge(args.arabic, args.english, args.policy, args.output, args.report, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
