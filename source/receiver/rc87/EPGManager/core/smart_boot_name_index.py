# -*- coding: utf-8 -*-
"""Lean exact/identity index for Smart Mapping 7.1.0 Performance Core.

The previous global builder applied several normalization engines, fuzzy-token
indexes and the merged identity resolver to every EPG row. On a Vu+ Zero 4K,
that could consume tens of seconds and was rebuilt repeatedly while remote
catalogues arrived.

7.1.0 deliberately indexes only what cursor navigation needs:
  * exact Unicode-safe normalized name/channel-id/aliases; and
  * curated deterministic identity IDs from the small SRP alias dictionary.

No token/fuzzy catalogue scan is built. Fuzzy/advanced matching remains an
explicit action elsewhere. Building is background-only and an unavailable index
returns an empty candidate list immediately -- NEVER a full RAM scan on the UI
thread.
"""
from __future__ import print_function

import threading
import time

from .logger import get_logger
from . import (
    source_catalog, smart_catalog_boot, smart_mapping_warm_cache,
    smart_name_index_cache, manual_match_lite, channel_identity_resolver,
    unified_channel_identity,
)

log = get_logger(__name__)

_LOCK = threading.RLock()
_BUSY = False
_READY = False
_RERUN = False
_INDEX = {}
_SOURCE_IDS = []
_STARTED_AT = 0.0
_FINISHED_AT = 0.0
_GENERATION = 0


def _add(bucket_map, key, pos, cap=96):
    if not key:
        return
    bucket = bucket_map.setdefault(key, [])
    if len(bucket) < int(cap or 96):
        bucket.append(int(pos))


def _row_values(raw):
    raw = raw or {}
    cid = str(raw.get("channel_id") or "").strip()
    name = str(raw.get("display_name") or cid).strip() or cid
    aliases = []
    seen = set()
    for value in list(raw.get("aliases") or []) + list(raw.get("search_aliases") or []):
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen and key not in (cid.casefold(), name.casefold()):
            seen.add(key)
            aliases.append(text)
    return cid, name, aliases[:8]


def _prepared_rows_snapshot():
    """Snapshot already-resident rows only. No disk/network calls."""
    rows = []
    source_ids = []
    seen = set()
    try:
        items = list(source_catalog.all_sources() or [])
    except Exception:
        items = []
    for item in items:
        sid = str(source_catalog.mapping_source_id(item) or "")
        if not sid:
            continue
        name = str(item.get("name") or sid)
        try:
            prepared = smart_catalog_boot.get(sid) or ()
        except Exception:
            prepared = ()
        if prepared:
            source_ids.append(sid)
        for raw in prepared:
            cid, display, aliases = _row_values(raw)
            if not cid:
                continue
            dedupe = (sid.casefold(), cid.casefold())
            if dedupe in seen:
                continue
            seen.add(dedupe)
            row = {
                "source_id": sid,
                "source_name": name,
                "channel_id": cid,
                "display_name": display,
            }
            if aliases:
                row["search_aliases"] = aliases
            rows.append(row)
    return rows, smart_name_index_cache.normalize_source_ids(source_ids)


def _identity_id_fast(value):
    """Deterministic SRP alias lookup only; O(1) after tiny alias cache load."""
    try:
        row = channel_identity_resolver.resolve(value) or {}
        iid = str(row.get("id") or "")
        return unified_channel_identity.canonicalize_identity_id(iid) if iid else ""
    except Exception:
        return ""


def _build(rows):
    started = time.time()
    buckets = {}
    compact_rows = []
    for raw in rows or []:
        row = dict(raw or {})
        cid = str(row.get("channel_id") or "").strip()
        name = str(row.get("display_name") or cid).strip() or cid
        if not cid:
            continue
        values = [name, cid] + list(row.get("search_aliases") or [])
        pos = len(compact_rows)
        compact_rows.append(row)
        # RC14: keep one exact XMLTV-ID bucket as well.  OpenEPG publishes
        # several parallel AR/EN shards with the same XMLTV channel ID.  The
        # suggestion browser can therefore recover the Arabic sibling in O(1)
        # without scanning a provider catalogue or touching disk/network.
        _add(buckets, "C=" + cid.casefold(), pos, 64)
        exact_seen = set()
        identity_seen = set()
        for value in values[:10]:
            text = str(value or "").strip()
            if not text:
                continue
            try:
                key = manual_match_lite.manual_norm(text)
            except Exception:
                key = ""
            if key and key not in exact_seen:
                exact_seen.add(key)
                _add(buckets, "=" + key, pos, 96)
            iid = _identity_id_fast(text)
            if iid and iid not in identity_seen:
                identity_seen.add(iid)
                _add(buckets, "@" + iid, pos, 64)
        if identity_seen:
            row["identity_ids"] = list(identity_seen)[:4]
    elapsed = time.time() - started
    log.info("Lean Smart index built: %d rows / %d keys in %.3fs", len(compact_rows), len(buckets), elapsed)
    return {"_format": 2, "_profile": "exact-lite-7.2-id-alias", "rows": compact_rows, "buckets": buckets}


def _publish(index, source_ids, persist=True):
    global _INDEX, _SOURCE_IDS, _READY, _FINISHED_AT, _GENERATION
    with _LOCK:
        _INDEX = index if isinstance(index, dict) else {}
        _SOURCE_IDS = list(source_ids or [])
        _READY = bool(_INDEX and int(_INDEX.get("_format") or 0) == 2)
        _FINISHED_AT = time.time()
        _GENERATION += 1
    try:
        smart_mapping_warm_cache.update_smart_index(_INDEX, _SOURCE_IDS)
    except Exception:
        pass
    if persist and _READY and _SOURCE_IDS:
        try:
            smart_name_index_cache.save(_INDEX, _SOURCE_IDS)
        except Exception:
            log.exception("Could not persist lean Smart Mapping index")


def ensure_async(force=False):
    """Build/rebuild one lean index. At most one rerun may be queued."""
    global _BUSY, _RERUN, _STARTED_AT
    with _LOCK:
        if _BUSY:
            if force:
                _RERUN = True
            return False
        if _READY and not force:
            return True
        _BUSY = True
        _STARTED_AT = time.time()

    def worker():
        global _BUSY, _RERUN
        try:
            rows, source_ids = _prepared_rows_snapshot()
            index = _build(rows)
            _publish(index, source_ids, persist=True)
        except Exception:
            log.exception("Lean Smart Mapping name index failed")
        finally:
            with _LOCK:
                _BUSY = False
                rerun = bool(_RERUN)
                _RERUN = False
            if rerun:
                ensure_async(force=True)

    threading.Thread(target=worker, daemon=True).start()
    return False


def snapshot():
    with _LOCK:
        return _INDEX if _READY else {}


def status():
    with _LOCK:
        return {
            "ready": bool(_READY),
            "busy": bool(_BUSY),
            "rows": len((_INDEX or {}).get("rows") or []),
            "sources": len(_SOURCE_IDS),
            "generation": int(_GENERATION),
            "started_at": float(_STARTED_AT or 0.0),
            "finished_at": float(_FINISHED_AT or 0.0),
            "profile": "exact-lite-7.2-id-alias",
        }


def ram_exact_candidates(service, limit=64):
    """Return indexed candidates or [] immediately.

    Critical latency invariant: when the index is not ready, NEVER scan every
    prepared catalogue row synchronously. This function is called from Enigma2's
    UI thread by older compatibility paths.
    """
    if not service:
        return []
    with _LOCK:
        index = _INDEX if _READY else {}
    if not index:
        return []
    try:
        return manual_match_lite.candidate_rows(service, index, limit=max(1, min(96, int(limit or 64))))
    except Exception:
        return []
