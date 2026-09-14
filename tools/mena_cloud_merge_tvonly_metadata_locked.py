#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow-only TV service filter layered on top of the metadata-locked MENA merge.

The MENA Cloud is a television EPG source.  Known radio/audio-only identities
must not stay in provider/country TV shards merely because an upstream XMLTV
feed labels them like ordinary channels.

This wrapper intentionally starts with a tiny exact-ID denylist backed by an
external service-identity audit.  No fuzzy/name-only exclusion is allowed.
"""
from __future__ import annotations

import mena_cloud_merge as base
import mena_cloud_merge_metadata_locked as locked

_previous_load_candidates = base.load_candidates

# Verified non-TV identities.  Keep this exact-ID only: a similarly named TV
# service must never be removed by substring matching.
NON_TV_EXACT_IDS = {
    "Rotana.Tarab.Jordan.ae",  # Rotana/Radio Tarab Jordan 107.5 FM + satellite audio service
}

_dropped = set()


def tv_only_load_candidates(root, origin, source_name, site_by_id, now, end):
    rows = _previous_load_candidates(root, origin, source_name, site_by_id, now, end)
    kept = []
    for row in rows:
        if row.cid in NON_TV_EXACT_IDS:
            _dropped.add(row.cid)
            continue
        kept.append(row)
    return kept


base.load_candidates = tv_only_load_candidates


def main():
    rc = locked.main()
    if _dropped:
        print("TV-only exact-ID filter: dropped %d non-TV identity(s): %s" % (
            len(_dropped), ", ".join(sorted(_dropped, key=str.casefold))))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
