#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow-only finalizer that omits channel identities with zero selected programmes.

The internal catalogue remains unchanged.  This only tests receiver-facing XML
memory savings: a channel is published iff the final fresh/LKG selection contains
at least one programme.  Event channels such as beIN MAX/XTRA therefore disappear
while idle and automatically return when they have real schedule rows.
"""
from __future__ import annotations

import mena_cloud_finalize as base
import mena_cloud_finalize_strict as strict


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


# Importing mena_cloud_finalize_strict installs all existing integrity/language
# protections. Override only its identity-retention layer for this shadow test.
base.build_feed = pruned_build_feed


if __name__ == "__main__":
    raise SystemExit(base.main())
