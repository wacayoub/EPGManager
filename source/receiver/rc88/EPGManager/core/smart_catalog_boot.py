# -*- coding: utf-8 -*-
"""Ultra-light process-resident Channel-ID catalogue for Smart Mapping.

7.1.0 Performance Core rules:

* Receiver startup NEVER downloads a remote catalogue or XMLTV file.
* Smart Mapping cursor/RIGHT/OK NEVER reads XMLTV and NEVER performs HTTP.
* Local source hints are published immediately.
* Existing compact, source-specific Channel-ID shards are loaded once in a
  daemon worker and kept in RAM.
* Provider-wide mapping files (Rytec/Azman/Koala ``*.channels.xml``) are NOT
  treated as per-source channel membership.  Old shards created from those
  shared maps are ignored unless they explicitly declare ``scope=source``.
* The Smart name index is built at most twice: a tiny local-hint index first,
  then one final index after cached shards have been loaded.  There is no
  repeated rebuild-per-source loop.

This module deliberately owns no network code. Explicit YELLOW/source refresh
can update source_channel_cache; ``ensure_async(force=True)`` then reloads the
compact shards into RAM.
"""
from __future__ import print_function

import threading
import time

from .logger import get_logger
from . import source_catalog, source_channel_cache, smart_name_index_cache, fast_name_key

log = get_logger(__name__)

_LOCK = threading.RLock()
_RAM = {}
_META = {}
_SELECTED = set()
_BUSY = False
_READY = False
_RERUN = False
_GENERATION = 0
_STARTED_AT = 0.0
_FINISHED_AT = 0.0


def _compact_rows(sid, source_name, rows):
    out = []
    seen = set()
    for raw in rows or []:
        raw = raw or {}
        cid = str(raw.get("channel_id") or "").strip()
        if not cid:
            continue
        key = cid.casefold()
        if key in seen:
            continue
        seen.add(key)
        item = {
            "source_id": str(sid),
            "source_name": str(source_name or sid),
            "epg_xml_path": "",
            "channel_id": cid,
            "display_name": str(raw.get("display_name") or cid).strip() or cid,
        }
        aliases = []
        alias_seen = set()
        for value in (list(raw.get("aliases") or []) + list(raw.get("search_aliases") or []) +
                      list(fast_name_key.xmltv_id_aliases(cid) or [])):
            text = str(value or "").strip()
            akey = text.casefold()
            if text and akey not in alias_seen and akey not in (key, item["display_name"].casefold()):
                alias_seen.add(akey)
                aliases.append(text)
        if aliases:
            item["aliases"] = aliases[:8]
        out.append(item)
    return tuple(out)


def _publish(sid, source_name, rows, state="ready", method="cache", error=""):
    global _GENERATION
    sid = str(sid or "")
    if not sid:
        return
    compact = _compact_rows(sid, source_name, rows)
    with _LOCK:
        if compact:
            _RAM[sid] = compact
        elif state == "ready" and sid not in _RAM:
            _RAM[sid] = tuple()
        _META[sid] = {
            "state": str(state or "ready"),
            "rows": len(compact) if compact else len(_RAM.get(sid, ())),
            "method": str(method or "cache"),
            "error": str(error or ""),
            "updated_at": int(time.time()),
        }
        _GENERATION += 1


def get(source_id):
    """Return immutable resident rows. Zero disk/network I/O."""
    with _LOCK:
        return _RAM.get(str(source_id or ""), tuple())


def has(source_id):
    with _LOCK:
        return bool(_RAM.get(str(source_id or "")))


def is_selected(source_id):
    with _LOCK:
        return str(source_id or "") in _SELECTED


def is_loading(source_id=None):
    sid = str(source_id or "")
    with _LOCK:
        if sid:
            return str((_META.get(sid) or {}).get("state") or "") == "loading"
        return bool(_BUSY)


def generation():
    with _LOCK:
        return int(_GENERATION)


def status():
    with _LOCK:
        return {
            "busy": bool(_BUSY),
            "ready": bool(_READY),
            "sources": len([1 for rows in _RAM.values() if rows]),
            "rows": sum(len(rows or ()) for rows in _RAM.values()),
            "selected": len(_SELECTED),
            "started_at": float(_STARTED_AT or 0.0),
            "finished_at": float(_FINISHED_AT or 0.0),
            "generation": int(_GENERATION),
            "mode": "cache-only-no-network",
        }


def source_status(source_id):
    with _LOCK:
        return dict(_META.get(str(source_id or "")) or {})


def _cache_is_source_specific(item, sid):
    """Reject legacy provider-wide map shards masquerading as one feed.

    7.0.10 could save the same Rytec provider map under dozens of individual
    source IDs. New shards may opt back in by carrying ``scope=source``.
    Sources without a shared ``channels_urls`` mapping file are source-specific
    by construction and remain compatible with older cache versions.
    """
    if not (item or {}).get("channels_urls"):
        return True
    try:
        # Tiny sidecar only. Never decode a legacy huge Rytec shard merely to
        # discover that it came from the provider-wide shared map.
        return str(source_channel_cache.scope_fast(sid) or "").lower() == "source"
    except Exception:
        return False


def _selected_mapping_ids():
    try:
        from .source_preferences import SourcePreferences
        prefs = SourcePreferences()
        selected_catalogue = set(str(x) for x in (prefs.selected_catalogue_ids() or []) if x)
        by_catalogue = source_catalog.by_catalogue_id()
        out = set()
        for cid in selected_catalogue:
            mid = str(source_catalog.mapping_source_id(by_catalogue.get(cid) or {}) or "")
            if mid:
                out.add(mid)
        return out
    except Exception:
        return set()


def _request_index_rebuild():
    try:
        from . import smart_boot_name_index
        smart_boot_name_index.ensure_async(force=True)
    except Exception:
        log.exception("Could not build lightweight Smart Mapping index")


def _run_once(force=False):
    global _SELECTED, _READY, _FINISHED_AT
    selected = _selected_mapping_ids()
    with _LOCK:
        _SELECTED = set(selected)
        # A forced reload follows an explicit source refresh. Drop only resident
        # catalogue rows; user mappings/settings are unrelated and untouched.
        if force:
            _RAM.clear()
            _META.clear()

    try:
        items = list(source_catalog.all_sources() or [])
    except Exception:
        items = []

    # Stage 1: publish local static hints immediately, before any JSON shard I/O.
    for item in items:
        if (item or {}).get("kind") != "local":
            continue
        sid = str(source_catalog.mapping_source_id(item) or "")
        if not sid:
            continue
        name = str(item.get("name") or sid)
        try:
            rows = source_catalog.local_channel_hints(sid) or []
        except Exception:
            rows = []
        _publish(sid, name, rows, state="ready", method="local-hints")

    # 8.1.0: do not build a temporary first index. A persisted snapshot can be
    # used immediately by the UI; one final rebuild follows the cache load.

    # Stage 2: load ONLY existing compact source-specific shards. This may touch
    # flash, so it stays entirely inside this daemon worker. No remote request is
    # queued here, even for selected sources.
    loaded = 0
    ignored_shared = 0
    for item in items:
        if (item or {}).get("kind") == "local":
            continue
        sid = str(source_catalog.mapping_source_id(item) or "")
        if not sid:
            continue
        name = str(item.get("name") or sid)
        with _LOCK:
            _META.setdefault(sid, {"state": "idle", "rows": 0, "method": "on-demand", "error": "", "updated_at": 0})
        try:
            # Check shared-map safety BEFORE source_channel_cache.has(); has()
            # JSON-decodes the shard, and legacy Rytec shards may be enormous.
            if not _cache_is_source_specific(item, sid):
                ignored_shared += 1
                with _LOCK:
                    _META[sid] = {
                        "state": "idle", "rows": 0,
                        "method": "legacy-shared-map-ignored",
                        "error": "", "updated_at": int(time.time()),
                    }
                continue
            if not source_channel_cache.has(sid):
                continue
            rows = source_channel_cache.get(sid) or []
        except Exception as exc:
            with _LOCK:
                _META[sid] = {"state": "error", "rows": 0, "method": "cache-error", "error": str(exc), "updated_at": int(time.time())}
            continue
        if rows:
            _publish(sid, name, rows, state="ready", method="compact-cache")
            loaded += 1
        # Cooperative yield: prevents a long run of JSON decode from starving
        # the Enigma2 main process on low-power MIPS/ARM receivers.
        if loaded and loaded % 12 == 0:
            time.sleep(0.01)

    # Rebuild only when the persisted compact index is absent/dirty or when this
    # run follows an explicit source refresh. A clean index is already consumed
    # by smart_mapping_warm_cache, so rebuilding it on every Enigma restart just
    # steals CPU from zapping/UI on small receivers.
    try:
        have_clean_index = bool(smart_name_index_cache.load_fast_snapshot())
    except Exception:
        have_clean_index = False
    if force or not have_clean_index:
        _request_index_rebuild()
    with _LOCK:
        _READY = True
        _FINISHED_AT = time.time()
    log.info("Smart catalogue cache-only boot ready: %d cached sources; %d legacy shared-map shards ignored; no network", loaded, ignored_shared)


def ensure_async(force=False):
    """Load compact cached IDs in one daemon worker; never perform HTTP."""
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
            _run_once(force=force)
        except Exception:
            log.exception("Smart catalogue cache-only boot failed")
        finally:
            with _LOCK:
                _BUSY = False
                rerun = bool(_RERUN)
                _RERUN = False
            if rerun:
                ensure_async(force=True)

    threading.Thread(target=worker, daemon=True).start()
    return False


def update_source(source_id, source_name, rows, method="explicit-refresh"):
    """Publish one already-fetched compact source to RAM and rebuild in background.

    The caller already owns ``rows`` (e.g. YELLOW refresh result), so this API
    performs no disk/network work and avoids reloading all catalogue shards.
    """
    sid = str(source_id or "")
    if not sid:
        return False
    _publish(sid, source_name or sid, rows or [], state="ready", method=method)
    _request_index_rebuild()
    return True
