#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow finalizer: remove every exact placeholder programme row.

This layers on the full strict production finalizer. It only removes programme
rows whose title exactly matches the existing conservative placeholder regex.
Channel identities with no remaining useful programme rows are then naturally
omitted by the receiver-facing zero-EPG prune policy.
"""
from __future__ import annotations

import mena_cloud_finalize_strict as strict
import mena_cloud_finalize_safe as safe

_original_clean = safe._clean_channel_rows


def placeholder_pruned_clean(cid, name, rows):
    filtered = []
    for row in rows:
        title = safe._text(row, "title")
        if safe._PLACEHOLDER_RE.match(title or ""):
            continue
        filtered.append(row)
    return _original_clean(cid, name, filtered)


safe._clean_channel_rows = placeholder_pruned_clean


if __name__ == "__main__":
    raise SystemExit(strict.base.main())
