#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production finalizer that omits receiver-facing channel IDs with zero programmes.

The full internal catalogue is intentionally retained for audits/source recovery.
Only published XMLTV feeds are pruned: a channel is emitted iff fresh/LKG final
selection has at least one programme. Event channels (e.g. beIN MAX/XTRA) are
therefore absent while idle and automatically return when real EPG appears.
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


# Importing the strict layer keeps all existing integrity, provider and language
# protections. Only the old zero-programme identity retention is replaced here.
base.build_feed = pruned_build_feed


if __name__ == "__main__":
    raise SystemExit(base.main())
