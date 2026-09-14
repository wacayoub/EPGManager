#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted receiver-facing quarantine for one proven false clone group.

These six different national sports channels currently carry the exact same
33-event AE timeline. We remove their programme groups only when ALL present
members still share an identical fingerprint, so a future corrected upstream
schedule automatically escapes quarantine.
"""
from __future__ import annotations

import mena_cloud_finalize_strict as strict
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

_original_groups = base.programme_groups


def targeted_groups(root, now, end):
    groups = _original_groups(root, now, end)
    present = {cid: groups.get(cid, []) for cid in TARGETS if groups.get(cid)}
    if len(present) < 3:
        return groups
    fps = {cid: guard.timeline_fingerprint(rows) for cid, rows in present.items()}
    vals = [fp for fp in fps.values() if fp is not None]
    # Fail-safe: quarantine only if at least 3 target services remain byte-equivalent.
    if len(vals) >= 3 and len(set(vals)) == 1:
        for cid in present:
            groups.pop(cid, None)
        print("Target clone quarantine: removed programmes for %d national-sports IDs: %s" %
              (len(present), ", ".join(sorted(present))))
    return groups


# strict imported first and has already installed its integrity wrapper. Layer
# this targeted check before the normal final/LKG selection path used by base.main.
base.programme_groups = targeted_groups

if __name__ == "__main__":
    raise SystemExit(base.main())
