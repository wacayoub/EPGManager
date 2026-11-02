#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact content fingerprints for the 14 upstream XMLTV source feeds.

Track the actual compressed bytes consumed by the derived MENA builder.
A provider's calendar day is not proof of freshness; and one stale provider
must not block MENA from incorporating another correctly updated provider.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def get_source_snapshot(feed_dir, source_names):
    root=Path(feed_dir)
    names=list(source_names)
    if len(names)!=len(set(names)):
        raise ValueError("DUPLICATE_MENA_SOURCE_NAME")
    out={}
    for name in names:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("INVALID_MENA_SOURCE_NAME")
        feed=root/f"{name}.xml.gz"
        metadata=root/f"{name}.json"
        if not feed.is_file():
            out[name]=None
            continue
        data=feed.read_bytes()
        if not data.startswith(b"\x1f\x8b"):
            raise ValueError(f"INVALID_GZIP_SOURCE:{name}")
        digest=hashlib.sha256(data).hexdigest()
        if not metadata.is_file():
            raise ValueError(f"SOURCE_METADATA_MISSING:{name}")
        meta=json.loads(metadata.read_text(encoding="utf-8"))
        if meta.get("sha256")!=digest or meta.get("size_bytes")!=len(data):
            raise ValueError(f"SOURCE_MANIFEST_SHA_OR_SIZE_MISMATCH:{name}")
        out[name]=digest
    return out

def needs_rebuild(snapshot, derived_metadata):
    """Rebuild when any actual compressed upstream source has changed.

    A MENA feed generated yesterday may remain correct today if no upstream
    feed changed, but must be rebuilt today even if unrelated sources are stale
    when some other genuine source has published different bytes.
    """
    return not isinstance(derived_metadata,dict) or (
        derived_metadata.get("source_snapshot")!=snapshot
    )
