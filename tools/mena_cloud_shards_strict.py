#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict identity + compatibility layer on top of mena_cloud_shards_safe.

Known beIN and OSN XMLTV aliases are kept in BOTH XML and the lightweight
catalogue so an existing receiver mapping never becomes invalid. Their
programme timelines are copied from audited canonical IDs. New mappings prefer
canonical IDs.

beIN MAX/XTRA are event-channel sources and are deliberately preserved as source
feeds even when the current guide is generic/repetitive outside a live event.
They may stay REVIEW for mapping quality, but their source programmes must not be
pruned merely because the guide is a placeholder between events. The integrity
guard now preserves those event feeds before this shard layer, and this layer
restores their untouched source timeline after beIN metadata repair as a second
safety net.

beIN SPORTS NEWS is Arabic-first for all receiver-facing provider IDs, including
legacy source IDs that only appear during provider sharding. Only known recurring
editorial labels are translated; unknown labels remain untouched.
"""
from __future__ import annotations

import re

import mena_cloud_shards_safe as safe
import bein_provider_repair as bein_repair

_original_provider_group = safe.safe_provider_group
_original_write_shard = safe.base.write_shard


# Only mappings verified as the same logical linear service are included here.
# Real dedicated EN/FR channels are deliberately NOT collapsed into Arabic.
# In the current beIN MENA lineup only SPORTS EN 1/2 are separate English
# services. The old Mono_EN IDs for numbered 3/4/5/7 are guide-language variants
# of the same SPORTS 3/4/5/7 linear services, so they inherit the numbered MENA
# canonical timeline instead of publishing a shorter/incomplete schedule.
BEIN_COMPAT_ALIASES = {
    # Main sports / 4K
    "4k_DIGITAL_Mono_AR.bein": "beIN4K.qa@SD",
    "beIN SPORTS2 DIGITAL.qa": "beINSports2.qa@MENA",
    "beIN_SPORTS3_DIGITAL_Mono_EN.bein": "beINSports3.qa@MENA",
    "beIN SPORTS4 DIGITAL.qa": "beINSports4.qa@MENA",
    "beIN_SPORTS4_DIGITAL_Mono_EN.bein": "beINSports4.qa@MENA",
    "beIN_SPORTS5_DIGITAL_Mono_EN.bein": "beINSports5.qa@MENA",
    "beIN SPORTS6 DIGITAL -d-1.qa": "beINSports6.qa@MENA",
    "beIN SPORTS7 DIGITAL.qa": "beINSports7.qa@MENA",
    "beIN_SPORTS7_DIGITAL_Mono_EN.bein": "beINSports7.qa@MENA",

    # Older NEWS canonical IDs may reappear in upstream feeds.
    "NEWS_DIGITAL_Mono_AR.bein": "beIN.Sports.News.ae",
    "NEWS_DIGITAL_Mono_EN.bein": "beIN.Sports.News.ae",

    # XTRA aliases / guide-language twins. Leading-zero forms normalize to the
    # same linear XTRA number so an existing mapping keeps working.
    "beIN SPORTS XTRA 04 bein.com.qa": "beIN SPORTS XTRA 4.qa",
    "beIN SPORTS XTRA 05 bein.com.qa": "beIN SPORTS XTRA 5.qa",
    "beIN_SPORTS_XTRA_06_bein.com_EN.bein": "beIN SPORTS XTRA 6.qa",
    "beIN_SPORTS_XTRA_07_bein.com_EN.bein": "beIN SPORTS XTRA 7.qa",
    "beIN_SPORTS_XTRA_08_Bein.com_EN.bein": "beIN SPORTS XTRA 8.qa",
    "beIN_SPORTS_XTRA_Digital_09_Bein.com_EN.bein": "beIN SPORTS XTRA 9.qa",
    "beIN SPORTS XTRA 3.qa": "beINSPORTSXTRA3.qa",
    "logos-_beINSPORTSXTRA3_EN.bein": "beINSPORTSXTRA3.qa",
}
# Add current verified Arabic/Latin guide twins and current NEWS twin.
BEIN_COMPAT_ALIASES.update(bein_repair.RECOMMENDED_COMPAT_ALIASES)

# Verified same-service compatibility bridges. The legacy Egypt IDs are retained
# for saved receiver mappings, but their poorer metadata/timing is replaced by
# the corresponding official OSN UAE guide. Verification used matching programme
# sequence/times, not name similarity alone.
OSN_COMPAT_ALIASES = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}

# IDs that should receive a negative recommendation score. They remain present
# so an old manual mapping is not silently lost. Valid FTA and Box Office IDs are
# no longer penalized merely because they are not ordinary sports-linear services.
BEIN_NON_RECOMMENDED_IDS = {
    "beIN SPORTS66 DIGITAL -01.qa",
    "beIN_SPORTS66_DIGITAL_Mono-01_EN.bein",
    "bein.com-05.qa",
    "bein.com-06.qa",
    "bein.com-07.qa",
    "bein.com-08.qa",
}

_NEWS_EXACT_TITLES = {
    "al hassila": "الحصيلة",
    "the issue of the day": "الشوط الثالث",
    "news bulletin": "نشرة الأخبار",
    "special interview": "مقابلة خاصة",
    "super monday": "سوبر الإثنين",
    "al hassad": "الحصاد",
    "the big interview": "المقابلة الكبرى",
    "sports news": "الأخبار الرياضية",
    "football news": "أخبار كرة القدم",
    "news summary": "موجز الأخبار",
    "breaking news": "أخبار عاجلة",
    "morning news": "أخبار الصباح",
    "evening news": "أخبار المساء",
    "world news": "أخبار العالم",
    "international news": "الأخبار الدولية",
    "press conference": "مؤتمر صحفي",
    "sports today": "رياضة اليوم",
}
_NEWS_DATE_SUFFIX_RE = re.compile(r"\s*[-–—|]\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$")


def strict_provider_group(cid, name, meta):
    raw = "%s %s %s" % (cid or "", name or "", (meta or {}).get("name") or "")
    # base.norm()/safe.compact intentionally strips non-Latin text. Detect the
    # Arabic beIN identity first so rich Arabic guides do not fall into mena-eg.
    if bein_repair.ARABIC_BEIN_RE.search(raw):
        return "bein"

    probe = safe.compact(raw)

    # Majid Al Mohandis is an artist/music service, not Majid Kids / Abu Dhabi Media.
    if "majidalmohandis" in probe:
        return None

    return _original_provider_group(cid, name, meta)


def _copy_programmes_to_alias(alias, canonical, programmes):
    source = list(programmes.get(canonical, []))
    copied = []
    for programme in source:
        cp = safe.base.copy_element(programme)
        cp.set("channel", alias)
        copied.append(cp)
    programmes[alias] = copied
    return len(copied)


def _is_bein_event_source(cid):
    low = (cid or "").casefold()
    return "xtra" in low or "max" in low


def _is_bein_news_source(cid):
    low = (cid or "").casefold()
    return "news" in low and "bein" in low


def _translate_known_news_title(title):
    original = re.sub(r"\s+", " ", title or "").strip()
    if not original:
        return original

    suffix = ""
    m = _NEWS_DATE_SUFFIX_RE.search(original)
    if m:
        suffix = " - " + m.group(1)
        core = original[:m.start()].strip()
    else:
        core = original

    # Already-Arabic normalized labels stay stable on repeated rebuilds.
    for arabic in _NEWS_EXACT_TITLES.values():
        if arabic in core:
            return arabic + suffix

    # Strip Arabic portions only for lookup so Qatar1-style hybrid forms such as
    # "News Bulletin - نشرة الأخبار" collapse to the same Arabic display label.
    latin_core = re.sub(r"[\u0600-\u06ff]+", " ", core)
    norm = re.sub(r"[^a-z0-9]+", " ", latin_core.casefold()).strip()
    return _NEWS_EXACT_TITLES.get(norm, original) + (suffix if norm in _NEWS_EXACT_TITLES else "")


def _translate_bein_news_sources(ids_set, programmes):
    changed = 0
    for cid in ids_set:
        if not _is_bein_news_source(cid) or not programmes.get(cid):
            continue
        copied = []
        for programme in programmes.get(cid, []):
            cp = safe.base.copy_element(programme)
            title = cp.find("title")
            if title is not None:
                old = (title.text or "").strip()
                new = _translate_known_news_title(old)
                if new and new != old:
                    title.text = new
                    title.set("lang", "ar")
                    changed += 1
            copied.append(cp)
        programmes[cid] = copied
    return changed


def strict_write_shard(out_dir, stem, ids, channels, programmes, label):
    ids_set = set(ids)
    compat_applied = {}
    repair_report = None
    shard_programmes = programmes
    event_sources_preserved = []
    news_titles_translated = 0

    if stem == "provider-bein":
        # Work on copies. Repairs are metadata/schedule corrections limited to the
        # provider-beIN shard; the combined source and all other providers stay untouched.
        repaired, repair_report = bein_repair.repair_programme_map(
            ids_set, programmes, safe.base.copy_element
        )

        # MAX/XTRA are event-channel sources. Keep the upstream source timeline in
        # full, including generic/off-event guide rows. Generic rows may be marked
        # REVIEW by diagnostics, but they must not cause the source itself to be
        # stripped. This also means a future real event can appear without waiting
        # for the ID to be rediscovered/recreated.
        for cid in ids_set:
            if _is_bein_event_source(cid) and programmes.get(cid):
                repaired[cid] = [safe.base.copy_element(p) for p in programmes.get(cid, [])]
                event_sources_preserved.append(cid)

        # Future-proof zero guard: never let cleanup accidentally publish a channel
        # with zero programmes. MAX/XTRA are already restored above; this protects
        # every other beIN ID as well.
        restored = []
        for cid in ids_set:
            if programmes.get(cid) and not repaired.get(cid):
                repaired[cid] = [safe.base.copy_element(p) for p in programmes.get(cid, [])]
                restored.append(cid)
        if restored:
            repair_report["zero_guard_restored_ids"] = sorted(restored, key=str.casefold)
            repair_report["summary"]["zero_guard_restored"] = len(restored)

        shard_programmes = dict(programmes)
        shard_programmes.update(repaired)

        # Preserve aliases and force each to the audited canonical timeline AFTER
        # the canonical schedule has received safe metadata repairs. XTRA aliases
        # therefore inherit the preserved canonical event-source timeline.
        for alias, canonical in BEIN_COMPAT_ALIASES.items():
            if alias in ids_set and canonical in ids_set and canonical in shard_programmes:
                count = _copy_programmes_to_alias(alias, canonical, shard_programmes)
                compat_applied[alias] = {"canonical": canonical, "programmes": count}

        # Some legacy/source NEWS IDs are not compatibility copies because their
        # source timeline is retained independently. Apply the same deterministic
        # Arabic exact-label policy to every receiver-facing beIN NEWS ID here.
        news_titles_translated = _translate_bein_news_sources(ids_set, shard_programmes)

    elif stem == "provider-osn":
        # Keep legacy Ya Hala mapping targets but always serve the richer official
        # canonical OSN timeline. Work on a shallow map copy so other shards and
        # the combined feed remain untouched.
        shard_programmes = dict(programmes)
        for alias, canonical in OSN_COMPAT_ALIASES.items():
            if alias in ids_set and canonical in ids_set and canonical in shard_programmes:
                count = _copy_programmes_to_alias(alias, canonical, shard_programmes)
                compat_applied[alias] = {"canonical": canonical, "programmes": count}

    result = _original_write_shard(out_dir, stem, ids_set, channels, shard_programmes, label)

    if stem == "provider-bein":
        # Do not remove aliases from the .txt catalogue: current receiver mappings
        # may validate their saved XMLTV ID against that list.
        result["compat_aliases"] = compat_applied
        result["non_recommended_ids"] = sorted(
            [cid for cid in BEIN_NON_RECOMMENDED_IDS if cid in ids_set], key=str.casefold
        )
        result["catalog_preserves_legacy_ids"] = True
        result["event_source_policy"] = "MAX/XTRA source timelines preserved; generic/off-event rows are not pruned"
        result["event_sources_preserved"] = sorted(event_sources_preserved, key=str.casefold)
        result["event_source_count"] = len(event_sources_preserved)
        result["news_title_policy"] = "known beIN SPORTS NEWS editorial labels rendered Arabic-first; unknown labels preserved"
        result["news_titles_translated"] = news_titles_translated
        result["repair"] = (repair_report or {}).get("summary", {})
        result["repair_long_events"] = (repair_report or {}).get("long_event_repairs", [])
        print(
            "beIN repair: ArabicDesc=%d replayTitle=%d long=%d genericXTRA=%d; eventSourcesPreserved=%d aliases=%d newsArabic=%d" % (
                result["repair"].get("arabic_desc_fills", 0),
                result["repair"].get("known_replay_title_fixes", 0),
                result["repair"].get("long_event_repairs", 0),
                result["repair"].get("generic_xtra_removed", 0),
                len(event_sources_preserved),
                len(compat_applied),
                news_titles_translated,
            )
        )

    elif stem == "provider-osn":
        result["compat_aliases"] = compat_applied
        result["catalog_preserves_legacy_ids"] = True
        result["source_policy"] = (
            "official OSN canonical timelines preferred; verified legacy Ya Hala IDs copy canonicals"
        )
        print("OSN compatibility: aliases=%d" % len(compat_applied))

    return result


safe.safe_provider_group = strict_provider_group
# safe.main() eventually calls base.main(), which resolves base.write_shard at runtime.
safe.base.write_shard = strict_write_shard


if __name__ == "__main__":
    raise SystemExit(safe.main())
