#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict identity + compatibility layer on top of mena_cloud_shards_safe.

Known beIN XMLTV aliases are kept in BOTH XML and the lightweight catalogue so
an existing receiver mapping never becomes invalid. Their programme timelines
are copied from audited canonical IDs. New mappings prefer canonical IDs.

beIN MAX/XTRA are event-channel sources and are deliberately preserved as source
feeds even when the current guide is generic/repetitive outside a live event.
They may stay REVIEW for mapping quality, but their source programmes must not be
pruned merely because the guide is a placeholder between events. The integrity
guard now preserves those event feeds before this shard layer, and this layer
restores their untouched source timeline after beIN metadata repair as a second
safety net.
"""
from __future__ import annotations

import mena_cloud_shards_safe as safe
import bein_provider_repair as bein_repair

_original_provider_group = safe.safe_provider_group
_original_write_shard = safe.base.write_shard


# Only mappings verified as the same logical linear service are included here.
# Real EN/FR beIN linear variants are deliberately NOT collapsed into Arabic.
BEIN_COMPAT_ALIASES = {
    # Main sports / 4K
    "4k_DIGITAL_Mono_AR.bein": "beIN4K.qa@SD",
    "beINSports2.qa@MENA": "beIN SPORTS2 DIGITAL.qa",
    "beINSports4.qa@MENA": "beIN SPORTS4 DIGITAL.qa",
    "beINSports6.qa@MENA": "beIN SPORTS6 DIGITAL -d-1.qa",
    "beIN SPORTS7 DIGITAL.qa": "beINSports7.qa@MENA",

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


def strict_write_shard(out_dir, stem, ids, channels, programmes, label):
    ids_set = set(ids)
    compat_applied = {}
    repair_report = None
    shard_programmes = programmes
    event_sources_preserved = []

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
        result["repair"] = (repair_report or {}).get("summary", {})
        result["repair_long_events"] = (repair_report or {}).get("long_event_repairs", [])
        print(
            "beIN repair: ArabicDesc=%d replayTitle=%d long=%d genericXTRA=%d; eventSourcesPreserved=%d aliases=%d" % (
                result["repair"].get("arabic_desc_fills", 0),
                result["repair"].get("known_replay_title_fixes", 0),
                result["repair"].get("long_event_repairs", 0),
                result["repair"].get("generic_xtra_removed", 0),
                len(event_sources_preserved),
                len(compat_applied),
            )
        )

    return result


safe.safe_provider_group = strict_provider_group
# safe.main() eventually calls base.main(), which resolves base.write_shard at runtime.
safe.base.write_shard = strict_write_shard


if __name__ == "__main__":
    raise SystemExit(safe.main())
