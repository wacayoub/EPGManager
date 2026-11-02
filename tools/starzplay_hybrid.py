#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge STARZPLAY Arabic and English XMLTV into the EPGManager hybrid policy.

Arabic feed owns channel coverage, schedule times and Arabic descriptions.
International/foreign channels take English title/sub-title when the same event
exists in the English feed. Arabic-native channels remain fully Arabic.
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

import requests

AR = re.compile(r"[\u0600-\u06ff]")


def text_of(node: ET.Element, tag: str) -> str:
    for child in node.findall(tag):
        txt = (child.text or "").strip()
        if txt:
            return txt
    return ""


def channel_name(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return text_of(node, "display-name") or (node.get("id") or "")


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def event_key(p: ET.Element) -> tuple[str, str, str]:
    return ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip())


def start_key(p: ET.Element) -> tuple[str, str]:
    return ((p.get("channel") or "").strip(), (p.get("start") or "").strip())


def replace_tag(dst: ET.Element, src: ET.Element, tag: str, lang: str) -> bool:
    candidate = next((deepcopy(x) for x in src.findall(tag) if (x.text or "").strip()), None)
    if candidate is None:
        return False
    for old in list(dst.findall(tag)):
        dst.remove(old)
    candidate.set("lang", lang)
    dst.insert(0 if tag == "title" else min(1, len(dst)), candidate)
    return True


def ensure_lang(node: ET.Element, tag: str, lang: str) -> None:
    for child in node.findall(tag):
        if (child.text or "").strip():
            child.set("lang", lang)


def is_ar(text: str) -> bool:
    return bool(AR.search(text or ""))


def starzplay_provider_id(node: ET.Element | None) -> str:
    """Actual immutable STARZPLAY provider channel ID from XMLTV metadata."""
    if node is None:
        return ""
    return next(((link.text or "").strip() for link in node.findall("url")
                 if link.get("system") == "starzplay-id" and (link.text or "").strip()), "")


def english_display_names(channels: dict[str, ET.Element],
                          en_channels: dict[str, ET.Element]) -> dict:
    """Replace *display-name* only; never alter XMLTV channel/programme IDs.

    The independent AR/EN catalogue builders sometimes assign different
    canonical XMLTV IDs to the SAME STARZPLAY numeric channel ID. Join by the
    provider ID only if it is unique on each side, never by fuzzy text.
    """
    by_provider = {}
    ambiguous = set()
    for en in en_channels.values():
        provider = starzplay_provider_id(en)
        if not provider:
            continue
        if provider in by_provider:
            ambiguous.add(provider)
        else:
            by_provider[provider] = en

    ar_providers = [starzplay_provider_id(ch) for ch in channels.values()]
    ar_duplicates = {provider for provider in ar_providers
                     if provider and ar_providers.count(provider) > 1}
    stats = {"applied": 0, "missing": 0, "by_provider_id": 0,
             "missing_ids": [], "by_provider_id_ids": [],
             "ambiguous_provider_ids": sorted(ambiguous | ar_duplicates)}
    for cid, node in channels.items():
        provider = starzplay_provider_id(node)
        chosen = en_channels.get(cid)
        if (chosen is not None and provider and starzplay_provider_id(chosen)
                and provider != starzplay_provider_id(chosen)):
            chosen = None
        matched_by_provider = False
        if chosen is None and provider and provider not in ambiguous | ar_duplicates:
            chosen = by_provider.get(provider)
            matched_by_provider = chosen is not None
        # IMPORTANT: do not accept channel_name()'s fallback to XMLTV ID,
        # and do not mark Arabic text from an "en" source as an English label.
        official = text_of(chosen, "display-name") if chosen is not None else ""
        if official and not is_ar(official):
            for old in list(node.findall("display-name")):
                node.remove(old)
            ET.SubElement(node, "display-name", {"lang": "en"}).text = official
            stats["applied"] += 1
            if matched_by_provider:
                stats["by_provider_id"] += 1
                stats["by_provider_id_ids"].append(cid)
        else:
            stats["missing"] += 1
            stats["missing_ids"].append(cid)
    return stats


def translate_ar(text: str, cache: dict[str, str]) -> str:
    raw = (text or "").strip()
    if not raw or is_ar(raw):
        return raw
    if raw in cache:
        return cache[raw]

    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    # Primary translator: Google public endpoint. Retry once because GitHub
    # runners occasionally get a transient empty/429 response.
    for _ in range(2):
        try:
            r = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                headers=headers,
                params={"client": "gtx", "sl": "en", "tl": "ar", "dt": "t", "q": raw},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            out = "".join(x[0] for x in data[0] if x and x[0]).strip()
            if out and is_ar(out):
                cache[raw] = out
                return out
        except Exception:
            pass

    # Independent fallback translator. This is only used for the explicit
    # NatGeo Abu Dhabi Arabic-only exception, so request volume stays small.
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            headers=headers,
            params={"q": raw, "langpair": "en|ar"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        out = str(((data.get("responseData") or {}).get("translatedText")) or "").strip()
        if out and is_ar(out):
            cache[raw] = out
            return out
    except Exception:
        pass

    # Never replace a real programme title with the channel name. Keep the
    # original text if both translators fail; the audit will expose it.
    cache[raw] = raw
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arabic", required=True)
    ap.add_argument("--english", required=True)
    ap.add_argument("--policy", default="config/starzplay-language-policy.json")
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    ar_root = ET.parse(args.arabic).getroot()
    en_root = ET.parse(args.english).getroot()
    channels = {(c.get("id") or "").strip(): deepcopy(c) for c in ar_root.findall("channel") if (c.get("id") or "").strip()}
    en_channels = {(c.get("id") or "").strip(): c for c in en_root.findall("channel") if (c.get("id") or "").strip()}

    ar_native_ids = set(policy.get("arabic_native_ids", []))
    hybrid_ids = set(policy.get("hybrid_ids", []))
    arabic_display_name_ids = set(policy.get("arabic_display_name_ids", []))
    force_arabic_content_ids = set(policy.get("force_arabic_content_ids", []))
    translation_cache: dict[str, str] = {}

    # Display names should be English for ALL channels. NatGeo Abu Dhabi's
    # 100% Arabic rule applies to PROGRAMME metadata only, not channel labels.
    # Keep the provider's original Arabic display text for legacy profile
    # classification on the eight channels previously unlinked in the EN feed:
    # this request must not change actual programme titles/descriptions.
    previous_ar_names = {cid: channel_name(node) for cid, node in channels.items()}
    name_result = english_display_names(channels, en_channels)
    english_channel_names_applied = name_result["applied"]
    english_channel_names_missing = name_result["missing"]
    official_name_by_provider_ids = set(name_result["by_provider_id_ids"])
    ar_tokens = [norm(x) for x in policy.get("arabic_native_name_tokens", [])]
    hy_tokens = [norm(x) for x in policy.get("hybrid_name_tokens", [])]
    default_profile = policy.get("default_profile", "arabic_native")

    def profile(cid: str) -> str:
        if cid in ar_native_ids:
            return "arabic_native"
        if cid in hybrid_ids:
            return "hybrid"
        # Classification must not depend only on the Arabic localized
        # display-name. The English page is the reliable signal for international
        # channel brands such as National Geographic, CNN, Cartoon Network, Zee
        # and STARZPLAY thematic channels.
        name_ar = norm(previous_ar_names[cid] if cid in official_name_by_provider_ids
                       else channel_name(channels.get(cid)))
        name_en = norm(channel_name(en_channels.get(cid)))
        names = " | ".join(x for x in (name_ar, name_en) if x)
        if any(tok and tok in names for tok in ar_tokens):
            return "arabic_native"
        if any(tok and tok in names for tok in hy_tokens):
            return "hybrid"
        return default_profile

    en_exact = {}
    en_start = defaultdict(list)
    for p in en_root.findall("programme"):
        en_exact[event_key(p)] = p
        en_start[start_key(p)].append(p)

    out = ET.Element("tv", {
        "generator-info-name": "EPGManager STARZPLAY hybrid EN-title AR-description",
        "generator-info-url": "https://www.starzplay.com/",
    })
    for cid in sorted(channels, key=str.casefold):
        out.append(deepcopy(channels[cid]))

    stats = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "channels": len(channels),
        "programmes": 0,
        "hybrid_programmes": 0,
        "arabic_native_programmes": 0,
        "english_title_applied": 0,
        "english_title_missing": 0,
        "arabic_description_present": 0,
        "arabic_description_missing": 0,
        "english_channel_names_applied": english_channel_names_applied,
        "english_channel_names_missing": english_channel_names_missing,
        "english_channel_names_linked_by_provider_id": name_result["by_provider_id"],
        "english_channel_names_linked_by_provider_ids": name_result["by_provider_id_ids"],
        "english_channel_names_unresolved_ids": name_result["missing_ids"],
        "english_channel_names_ambiguous_provider_ids": name_result["ambiguous_provider_ids"],
        "arabic_display_name_ids": sorted(arabic_display_name_ids),
        "force_arabic_content_ids": sorted(force_arabic_content_ids),
        "translations_cached": 0,
        "forced_arabic_source_titles": [],
        "forced_arabic_translation_failures": [],
        "forced_arabic_description_untranslated": 0,
        "forced_arabic_description_missing_in_output": 0,
        "forced_arabic_title_language_review": 0,
        "forced_arabic_samples": [],
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
        if prof == "hybrid":
            stats["hybrid_programmes"] += 1
            en = en_exact.get(event_key(p))
            if en is None:
                candidates = en_start.get(start_key(p), [])
                en = candidates[0] if candidates else None
            applied = bool(en is not None and replace_tag(node, en, "title", "en"))
            if en is not None:
                replace_tag(node, en, "sub-title", "en")
            if applied:
                stats["english_title_applied"] += 1
            else:
                stats["english_title_missing"] += 1
            ensure_lang(node, "desc", "ar")
        else:
            stats["arabic_native_programmes"] += 1
            if cid in force_arabic_content_ids:
                source_title = text_of(node, "title")
                if source_title and source_title not in stats["forced_arabic_source_titles"]:
                    stats["forced_arabic_source_titles"].append(source_title)
                for tag in ("title", "sub-title", "desc"):
                    for child in node.findall(tag):
                        raw = (child.text or "").strip()
                        if raw and not is_ar(raw):
                            translated = translate_ar(raw, translation_cache)
                            if is_ar(translated):
                                child.text = translated
                            elif tag == "title":
                                # Preserve the real programme title rather than
                                # corrupting the guide with a repeated channel name.
                                child.text = raw
                                if raw not in stats["forced_arabic_translation_failures"]:
                                    stats["forced_arabic_translation_failures"].append(raw)
                            elif tag in ("desc", "sub-title"):
                                # A description/subtitle that the upstream
                                # supplies only in English MUST NOT be replaced
                                # with a generic Arabic filler. No trustworthy
                                # Arabic translation exists for this row.
                                node.remove(child)
                                if tag == "desc":
                                    stats["forced_arabic_description_untranslated"] += 1
                            else:
                                child.text = raw
                # The source may legitimately omit a programme description.
                # Record a missing description honestly: NEVER synthesize a
                # generic Nat Geo AD blurb or a fake documentary title.
                if not text_of(node, "desc"):
                    stats["forced_arabic_description_missing_in_output"] += 1
                if len(stats["forced_arabic_samples"]) < 24:
                    stats["forced_arabic_samples"].append({
                        "source_title": source_title,
                        "title": text_of(node, "title"),
                        "description": text_of(node, "desc")[:240],
                    })
            if cid in force_arabic_content_ids:
                # If both authenticated translation providers fail, keep the
                # provider's exact real title but label EN honestly and flag
                # a manual editorial review. Do not mislabel English as Arabic.
                for tag in ("title", "sub-title", "desc"):
                    for child in node.findall(tag):
                        val = (child.text or "").strip()
                        if not val:
                            continue
                        child.set("lang", "ar" if is_ar(val) else "en")
                        if tag == "title" and not is_ar(val):
                            stats["forced_arabic_title_language_review"] += 1
            else:
                ensure_lang(node, "title", "ar")
                ensure_lang(node, "sub-title", "ar")
                ensure_lang(node, "desc", "ar")

        for child in list(node.findall("desc")):
            txt = (child.text or "").strip()
            if txt and not is_ar(txt):
                node.remove(child)
        desc = text_of(node, "desc")
        if desc and is_ar(desc):
            stats["arabic_description_present"] += 1
        else:
            stats["arabic_description_missing"] += 1
        if cid not in stats["samples"]:
            stats["samples"][cid] = {
                "profile": prof,
                "title": text_of(node, "title"),
                "description": desc[:240],
            }
        out.append(node)
        stats["programmes"] += 1

    stats["translations_cached"] = len(translation_cache)
    ET.indent(out, space="  ")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k not in {"profiles", "samples"}}, ensure_ascii=False))
    if stats["hybrid_programmes"] and not stats["english_title_applied"]:
        return 4
    return 0 if stats["programmes"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
