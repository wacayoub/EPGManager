#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accuracy wrapper for the MENA merge: harden TV-only filtering."""
from __future__ import annotations

import re
import mena_cloud_merge as base

_original_is_radio = base.is_radio


def is_non_tv(cid, name):
    probe = "%s %s" % (cid or "", name or "")
    folded = probe.casefold()
    if _original_is_radio(cid, name):
        return True
    # Some upstream IDs concatenate Radio with the brand, bypassing word-boundary rules.
    if "radio" in folded:
        return True
    # Known radio-only aliases seen in MENA aggregator packs.
    if re.search(r"(?:^|[\W_])(?:pulse\s*95|ofm)(?:[\W_]|$)", folded):
        return True
    # Aggregator/service placeholder, not a linear TV channel.
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()
    if cleaned in {"sat tv"}:
        return True
    return False


base.is_radio = is_non_tv

if __name__ == "__main__":
    raise SystemExit(base.main())
