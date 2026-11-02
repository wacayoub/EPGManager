#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a hybrid OSN XMLTV guide.

Policy:
- Arabic-native channels: Arabic title/sub-title + Arabic description.
- International/foreign channels: English title/sub-title + Arabic description.

The Arabic scrape is authoritative for channel list, schedule times and Arabic
descriptions.  The English scrape is used only to replace title/sub-title on
channels classified as hybrid.  No programme rows are invented.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06FF]")


def read_root(path: str) -> ET.Element:
    return ET.parse(path).getroot()


def text_of(node: ET.Element, tag: str) -> str:
    for child in node.findall(tag):
        txt = (child.text or "").strip()
        if txt:
            return txt
    return ""


def channel_name(node: ET.Element) -> str:
    return text_of(node, "display-name") or (node.get("id") or "").strip()


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


def event_key(p: ET.Element) -> tuple[str, str, str]:
    return (
        (p.get("channel") or "").strip(),
        (p.get("start") or "").strip(),
        (p.get("stop") or "").strip(),
    )


def start_key(p: ET.Element) -> tuple[str, str]:
    return ((p.get("channel") or "").strip(), (p.get("start") or "").strip())


def is_arabic(text: str) -> bool:
    return bool(AR.search(text or ""))


def enforce_arabic_description(node: ET.Element) -> int:
    """Remove non-Arabic description placeholders/leakage.

    The hybrid policy must never emit an English description.  If the official
    Arabic guide has no real Arabic description (for example "." or ".."),
    omit the description rather than substituting English text.
    """
    removed = 0
    for child in list(node.findall("desc")):
        txt = (child.text or "").strip()
        if txt and not is_arabic(txt):
            node.remove(child)
            removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arabic", required=True)
    ap.add_argument("--english", required=True)
    ap.add_argument("--policy", default="config/osn-language-policy.json")
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--mode", choices=("hybrid", "ar", "en"))
    args = ap.parse_args()

    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    mode = args.mode or policy.get("mode", "hybrid")
    hybrid_ids = set(policy.get("hybrid_ids", []))
    arabic_native_ids = set(policy.get("arabic_native_ids", []))
    name_tokens = [str(x).casefold() for x in policy.get("hybrid_name_tokens", [])]
    default_profile = policy.get("default_profile", "arabic_native")

    ar_root = read_root(args.arabic)
    en_root = read_root(args.english)

    channels = {}
    for ch in ar_root.findall("channel"):
        cid = (ch.get("id") or ch.get("xmltv_id") or "").strip()
        if cid:
            channels[cid] = deepcopy(ch)

    def profile(cid: str) -> str:
        if cid in arabic_native_ids:
            return "arabic_native"
        if cid in hybrid_ids:
            return "hybrid"
        name = channel_name(channels.get(cid, ET.Element("channel"))).casefold()
        if any(tok in name for tok in name_tokens):
            return "hybrid"
        return default_profile

    en_exact = {}
    en_start = defaultdict(list)
    for p in en_root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if not cid:
            continue
        en_exact[event_key(p)] = p
        en_start[start_key(p)].append(p)

    out = ET.Element("tv", {
        "generator-info-name": "EPGManager OSN hybrid EN-title AR-description",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    for cid in sorted(channels, key=str.casefold):
        out.append(deepcopy(channels[cid]))

    stats = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "channels": len(channels),
        "programmes": 0,
        "hybrid_programmes": 0,
        "arabic_native_programmes": 0,
        "english_title_applied": 0,
        "english_title_missing": 0,
        "arabic_description_present": 0,
        "arabic_description_missing": 0,
        "non_arabic_description_removed": 0,
        "profiles": {},
        "samples": {},
    }

    for p in ar_root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in channels:
            continue
        node = deepcopy(p)
        prof = profile(cid)
        stats["profiles"][cid] = prof

        if mode == "en":
            en = en_exact.get(event_key(p))
            if en is None:
                candidates = en_start.get(start_key(p), [])
                en = candidates[0] if candidates else None
            if en is not None:
                replace_tag(node, en, "title", "en")
                replace_tag(node, en, "sub-title", "en")
                replace_tag(node, en, "desc", "en")
        elif mode == "ar":
            ensure_lang(node, "title", "ar")
            ensure_lang(node, "sub-title", "ar")
            ensure_lang(node, "desc", "ar")
        elif prof == "hybrid":
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

        stats["non_arabic_description_removed"] += enforce_arabic_description(node)
        desc = text_of(node, "desc")
        if desc and is_arabic(desc):
            stats["arabic_description_present"] += 1
        else:
            stats["arabic_description_missing"] += 1

        if cid not in stats["samples"]:
            stats["samples"][cid] = {
                "profile": prof,
                "title": text_of(node, "title"),
                "description": desc[:240],
                "title_has_arabic": is_arabic(text_of(node, "title")),
                "description_has_arabic": is_arabic(desc),
            }
        out.append(node)
        stats["programmes"] += 1

    ET.indent(out, space="  ")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        k: v for k, v in stats.items() if k not in {"profiles", "samples"}
    }, ensure_ascii=False))

    if mode == "hybrid" and stats["hybrid_programmes"] and not stats["english_title_applied"]:
        return 4
    return 0 if stats["programmes"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
