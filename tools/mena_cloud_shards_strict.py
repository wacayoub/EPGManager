#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict identity + compatibility layer on top of mena_cloud_shards_safe.

Known beIN XMLTV aliases are kept in BOTH XML and the lightweight catalogue so
an existing receiver mapping never becomes invalid.  Their programme timelines
are copied from audited canonical IDs.  New-mapping preference for canonical IDs
can be handled by the receiver suggestion score without deleting legacy IDs.
"""
from __future__ import annotations

import mena_cloud_shards_safe as safe

_original_provider_group = safe.safe_provider_group
_original_write_shard = safe.base.write_shard


# Only mappings verified as the same logical linear service are included here.
# Real EN/FR beIN linear variants are deliberately NOT collapsed into Arabic.
BEIN_COMPAT_ALIASES = {
    # Main sports / news / 4K
    "4k_DIGITAL_Mono_AR.bein": "beIN4K.qa@SD",
    "beINSports2.qa@MENA": "beIN SPORTS2 DIGITAL.qa",
    "beINSports4.qa@MENA": "beIN SPORTS4 DIGITAL.qa",
    "beINSports6.qa@MENA": "beIN SPORTS6 DIGITAL -d-1.qa",
    "beIN SPORTS7 DIGITAL.qa": "beINSports7.qa@MENA",
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

# IDs that should receive a negative recommendation score in a future receiver
# build.  They remain present here so an old manual mapping is not silently lost.
BEIN_NON_RECOMMENDED_IDS = {
    "beIN SPORTS66 DIGITAL -01.qa",
    "beIN_SPORTS66_DIGITAL_Mono-01_EN.bein",
    "beIN SPORTS-boxoffice-bein.com.qa",
    "bein.com-05.qa",
    "bein.com-06.qa",
    "bein.com-07.qa",
    "bein.com-08.qa",
    "bein SPORTS FTA DIGITAL.qa",
}


def strict_provider_group(cid, name, meta):
    probe = safe.compact("%s %s %s" % (cid or "", name or "", (meta or {}).get("name") or ""))

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


def strict_write_shard(out_dir, stem, ids, channels, programmes, label):
    ids_set = set(ids)
    compat_applied = {}

    if stem == "provider-bein":
        # Preserve old IDs and force their schedules to the audited canonical
        # timeline. This is intentionally done BEFORE writing both XML and TXT.
        for alias, canonical in BEIN_COMPAT_ALIASES.items():
            if alias in ids_set and canonical in ids_set and canonical in programmes:
                count = _copy_programmes_to_alias(alias, canonical, programmes)
                compat_applied[alias] = {"canonical": canonical, "programmes": count}

    result = _original_write_shard(out_dir, stem, ids_set, channels, programmes, label)

    if stem == "provider-bein":
        # Do not remove aliases from the .txt catalogue: current receiver mappings
        # may validate their saved XMLTV ID against that list.
        result["compat_aliases"] = compat_applied
        result["non_recommended_ids"] = sorted(
            [cid for cid in BEIN_NON_RECOMMENDED_IDS if cid in ids_set], key=str.casefold
        )
        result["catalog_preserves_legacy_ids"] = True
        print("beIN compatibility: %d aliases refreshed from canonical; legacy catalogue IDs preserved" %
              len(compat_applied))

    return result


safe.safe_provider_group = strict_provider_group
# safe.main() eventually calls base.main(), which resolves base.write_shard at runtime.
safe.base.write_shard = strict_write_shard


if __name__ == "__main__":
    raise SystemExit(safe.main())
