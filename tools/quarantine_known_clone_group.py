#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted receiver-facing quarantine for one proven false clone group.

These six different national sports channels currently carry the exact same
33-event AE timeline. We remove their programme groups only while at least three
of the known targets still share an identical fingerprint. Importing the pruned
production finalizer preserves every existing strict integrity/language rule and
ensures quarantined zero-EPG IDs are omitted from receiver-facing XML.
"""
from __future__ import annotations

import mena_cloud_finalize_pruned as pruned  # noqa: F401 - installs production layers
import mena_cloud_finalize as base
import mena_integrity_guard as guard

TARGETS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}

# After importing the pruned finalizer, programme_groups points at the strict
# integrity wrapper and build_feed points at the receiver-facing zero-EPG prune.
_original_groups = base.programme_groups


def targeted_groups(root, now, end):
    groups = _original_groups(root, now, end)
    present = {cid: groups.get(cid, []) for cid in TARGETS if groups.get(cid)}
    if len(present) < 3:
        return groups

    fps = {cid: guard.timeline_fingerprint(rows) for cid, rows in present.items()}
    vals = [fp for fp in fps.values() if fp is not None]

    # Fail-safe: quarantine only while at least three target services remain
    # exactly identical. A future corrected upstream schedule automatically
    # escapes this rule without code changes.
    if len(vals) >= 3 and len(set(vals)) == 1:
        for cid in present:
            groups.pop(cid, None)
        print(
            "Target clone quarantine: removed programmes for %d national-sports IDs: %s"
            % (len(present), ", ".join(sorted(present)))
        )
    return groups


base.programme_groups = targeted_groups


if __name__ == "__main__":
    raise SystemExit(base.main())
