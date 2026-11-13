# -*- coding: utf-8 -*-
"""Tiny persistent NOW/NEXT cache for Smart Mapping SAFE EPG previews.

This cache is deliberately separate from source-quality scoring.  A preview-only
sample must never make a channel look like a complete/empty quality profile or
influence mapping arbitration.  Smart Mapping reads this file only after it has
already been loaded once; provider work is always performed by a daemon worker.
"""
from __future__ import print_function

import json
import os
import re
import time

from .logger import get_logger

log = get_logger(__name__)
CACHE_DIR = "/etc/epgmanager_epg/.programme_preview"
VERSION = 2
_MEM = {}


def _sid(value):
    if isinstance(value, dict):
        value = value.get("source_id") or value.get("id") or value.get("manager_id") or ""
    return str(value or "").strip().lower()


def _slug(value):
    value = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_")
    return value or "unknown"


def path_for(source_id):
    return os.path.join(CACHE_DIR, _slug(_sid(source_id)) + ".json")


def _sig(path):
    try:
        st = os.stat(path)
        return (int(st.st_mtime), int(st.st_size))
    except Exception:
        return ()


def _load(source_id):
    path = path_for(source_id)
    sig = _sig(path)
    cached = _MEM.get(path)
    if cached and cached[0] == sig:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _MEM[path] = (sig, data)
    return data


def merge(source_id, previews):
    """Merge preview-only rows without touching source-quality profiles."""
    sid = _sid(source_id)
    if not sid or not isinstance(previews, dict):
        return False
    data = dict(_load(sid) or {})
    rows = dict(data.get("channels") or {})
    now = int(time.time())

    # Prune very old/expired rows while touching the shard.  This keeps the
    # cache bounded even when provider catalogues change IDs over time.
    kept = {}
    for cid, row in rows.items():
        row = dict(row or {})
        stop = max(int(row.get("now_stop") or 0), int(row.get("next_stop") or 0),
                   int(row.get("next_start") or 0))
        checked = int(row.get("checked") or data.get("checked") or 0)
        if (stop and stop >= now - 86400) or (checked and checked >= now - 2 * 86400):
            kept[str(cid).lower()] = row
    rows = kept

    for cid, preview in previews.items():
        key = str(cid or "").strip().lower()
        if not key or not isinstance(preview, dict):
            continue
        row = dict(rows.get(key) or {})
        for field in ("now_title", "now_start", "now_stop", "next_title", "next_start", "next_stop"):
            if field in preview:
                row[field] = preview.get(field)
        row["checked"] = now
        rows[key] = row

    # Hard cap: still a tiny UI helper, but large all-source suggestion sets
    # need more than the old 256-ID ceiling to avoid evicting previews while
    # the user browses several satellites.
    if len(rows) > 1024:
        ordered = sorted(rows.items(), key=lambda pair: int((pair[1] or {}).get("checked") or 0), reverse=True)
        rows = dict(ordered[:1024])

    payload = {"version": VERSION, "source_id": sid, "checked": now, "channels": rows}
    path = path_for(sid)
    try:
        if not os.path.isdir(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        _MEM[path] = (_sig(path), payload)
        return True
    except Exception:
        log.exception("Could not save programme preview cache for %s", sid)
        return False


def get(source_id, channel_id, now_epoch=None, max_future_hours=36):
    data = _load(source_id) or {}
    row = dict((data.get("channels") or {}).get(str(channel_id or "").strip().lower()) or {})
    if not row:
        return {}
    now_epoch = int(now_epoch or time.time())
    now_title = str(row.get("now_title") or "").strip()
    now_start = int(row.get("now_start") or 0)
    now_stop = int(row.get("now_stop") or 0)
    next_title = str(row.get("next_title") or "").strip()
    next_start = int(row.get("next_start") or 0)
    next_stop = int(row.get("next_stop") or 0)

    if next_title and next_start and next_start <= now_epoch and (not next_stop or now_epoch < next_stop):
        return {"kind": "NOW", "title": next_title, "start": next_start, "stop": next_stop}
    if now_title and now_start and now_stop and now_start <= now_epoch < now_stop:
        return {"kind": "NOW", "title": now_title, "start": now_start, "stop": now_stop}
    horizon = now_epoch + max(1, int(max_future_hours or 36)) * 3600
    if next_title and next_start and now_epoch < next_start <= horizon:
        return {"kind": "NEXT", "title": next_title, "start": next_start, "stop": next_stop}
    return {}
