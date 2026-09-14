#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production finalizer for compact, integrity-safe receiver-facing MENA XMLTV.

The full internal catalogue is intentionally retained for audits/source recovery.
Only published XMLTV feeds are pruned: a channel is emitted iff final fresh/LKG
selection has at least one programme. Event channels therefore disappear while
idle and automatically return when real EPG appears.

A proven contaminated six-channel national-sports clone is also quarantined only
while at least three of those services still carry the exact same timeline. This
makes the guard self-releasing when upstream data is corrected.
"""
from __future__ import annotations

import mena_cloud_finalize as base
import mena_cloud_finalize_strict as strict
import mena_integrity_guard as guard

KNOWN_FALSE_SPORT_CLONE_IDS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}

# Importing strict has already installed the standard integrity/LKG wrapper.
_strict_programme_groups = base.programme_groups


def quarantine_known_false_sport_clone(root, now, end):
    groups = _strict_programme_groups(root, now, end)
    present = {
        cid: groups.get(cid, [])
        for cid in KNOWN_FALSE_SPORT_CLONE_IDS
        if groups.get(cid)
    }
    if len(present) < 3:
        return groups

    fingerprints = [
        guard.timeline_fingerprint(rows)
        for rows in present.values()
    ]
    fingerprints = [fp for fp in fingerprints if fp is not None]

    # Conservative fail-safe: quarantine only while at least three known target
    # services are exactly identical. A corrected source automatically escapes.
    if len(fingerprints) >= 3 and len(set(fingerprints)) == 1:
        for cid in present:
            groups.pop(cid, None)
        print(
            "Target clone quarantine: removed programmes for %d national-sports IDs: %s"
            % (len(present), ", ".join(sorted(present)))
        )
    return groups


def pruned_build_feed(ids, selected_programmes, cand_channels, prev_channels, source_by_id, generator_name):
    active_ids = {
        cid for cid in (ids or [])
        if selected_programmes.get(cid)
    }
    return strict._original_build_feed(
        sorted(active_ids, key=str.casefold),
        selected_programmes,
        cand_channels,
        prev_channels,
        source_by_id,
        generator_name,
    )


# Preserve all strict protections, then layer the targeted clone quarantine and
# the receiver-facing zero-EPG prune.
base.programme_groups = quarantine_known_false_sport_clone
base.build_feed = pruned_build_feed


if __name__ == "__main__":
    raise SystemExit(base.main())
