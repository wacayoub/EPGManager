# -*- coding: utf-8 -*-
"""Low-impact in-process warm cache for Smart Mapping.

The goal is simple: opening Smart Mapping must never wait for JSON disk reads.
The Home screen starts this cache in a daemon worker.  ChannelMappingScreen only
consumes already prepared Python objects; if the cache is not ready yet it paints
its UI immediately and keeps polling while the worker finishes.

No lamedb parse, XMLTV parse, network request or SRP rebuild is performed here.
"""
from __future__ import print_function

import os
import threading
import time

from . import channel_registry, channel_mapper, performance_cache, srp_master_engine, source_catalog, id_coverage_audit, smart_name_index_cache, source_variant_policy, mapping_view_cache, auto_repair_state, smart_mapping_view
from .mapping_store import MappingStore
from .source_preferences import SourcePreferences

_LOCK = threading.RLock()
_BUSY = False
_READY = False
_CACHE = {}
_STARTED_AT = 0.0
_FINISHED_AT = 0.0


def _load_fast_snapshot():
    """Publish only the data required to give the remote control back quickly.

    Crucially, do NOT call channel_registry.load(): on large receivers that
    compatibility helper can deserialize the complete IPTV registry (100k+
    services) before filtering SAT. Smart Mapping is SAT-first, so read the
    small SAT registry directly. Also do not deserialize the complete bouquet
    service catalogue; bouquet services are lazy-loaded one bouquet at a time.
    """
    try:
        sat, _rebuilt = channel_registry.load_satellite_registry(force=False)
        sat=list(sat or [])
    except Exception:
        sat=[]
    bouquet_dir=channel_mapper.BOUQUET_DIR
    all_records=performance_cache.load_cache_fast(
        os.path.join(bouquet_dir,'.epgmanager_bouquets_cache.json'),max_age=None) or []
    # beta125: the already-validated mapping ownership is a compact local cache.
    # Loading it here gives checkmarks/mapped IDs back immediately without
    # re-running PrecisionMatch for ~5k SRP rows on every screen open.
    try:
        mapping_view = mapping_view_cache.load()
    except Exception:
        mapping_view = None
    try:
        _store = MappingStore()
        ignored_refs = list(_store.ignored_refs() or [])
    except Exception:
        _store = None
        ignored_refs = []

    # 8.1.3: first Smart-Mapping paint must already know GREEN ownership.
    # If the compact validated mapping-view cache is absent, rebuild it here in
    # the warm-cache worker from the already-generated SRP Master state. SRP v8
    # rows were PrecisionMatch-gated when generated, so smart_mapping_view.build
    # is now a lightweight merge rather than a second expensive validation pass.
    try:
        state = srp_master_engine.load_state() or {}
    except Exception:
        state = {}
    if not mapping_view and state:
        try:
            prefs = SourcePreferences()
            store = _store or MappingStore()
            mapping_view = smart_mapping_view.build(
                sat, state, list(prefs.selected_mapping_ids() or []),
                store.all() or {}, store.blocked_refs() or [])
            if mapping_view:
                mapping_view_cache.save(mapping_view)
        except Exception:
            mapping_view = None

    # Load the persisted lean exact index in the same worker so yellow
    # SUGGESTION status is available on the very first channel paint. This is
    # disk/cache work only and never runs on the Enigma2 GUI thread.
    try:
        fast_index = smart_name_index_cache.load_fast_snapshot() or {}
        index = fast_index.get('index') or {}
        index_sources = list(fast_index.get('source_ids') or [])
    except Exception:
        index = {}
        index_sources = []

    return {
        'sat': sat,
        'bouquet_catalog': [],
        'all_records': list(all_records),
        'srp_state': state, 'srp_stats': dict((state or {}).get('stats') or {}), 'audit_summary': {},
        'mapping_view': mapping_view or {},
        'ignored_refs': ignored_refs,
        'repair_results': auto_repair_state.load_results(),
        'smart_name_index': index if isinstance(index, dict) else {},
        'smart_name_index_source_ids': index_sources,
        'fast_ready': True, 'loaded_at': time.time(),
    }


def _load_enrichment():
    """Optional background state, ordered by Auto Repair latency priority."""
    global _CACHE
    out={}
    # beta142: publish the clean compact channel-name index FIRST.  SRP/audit
    # state can be considerably larger and is not required by Auto Repair's
    # candidate lookup.  Loading the index first means the user can start an
    # automatic scan almost immediately after Home appears.
    try:
        with _LOCK:
            have_index = bool((_CACHE or {}).get('smart_name_index'))
            have_state = bool((_CACHE or {}).get('srp_state'))
        if not have_index:
            fast = smart_name_index_cache.load_fast_snapshot() or {}
            idx = fast.get('index') or {}
            if isinstance(idx, dict) and idx:
                out['smart_name_index'] = idx
                out['smart_name_index_source_ids'] = list(fast.get('source_ids') or [])
                out['smart_name_index_signature'] = str(fast.get('signature') or '')
                with _LOCK:
                    _CACHE.update(out)
    except Exception:
        pass
    try:
        if not have_state:
            state=srp_master_engine.load_state() or {}
            out['srp_state']=state; out['srp_stats']=dict(state.get('stats') or {})
    except Exception:
        pass
    try:
        out['audit_summary']=dict(id_coverage_audit.summary() or {})
    except Exception:
        pass
    try:
        source_catalog.all_sources(); SourcePreferences(); MappingStore()
    except Exception:
        pass
    return out


def ensure_async(force=False):
    global _BUSY, _READY, _STARTED_AT
    with _LOCK:
        if _BUSY:
            return False
        if _READY and not force:
            return True
        _BUSY = True
        if force:
            _READY = False
        _STARTED_AT = time.time()

    def worker():
        global _BUSY, _READY, _CACHE, _FINISHED_AT
        fast = {}
        try:
            fast = _load_fast_snapshot()
        except Exception:
            fast = {}
        # Publish the SAT/bouquet-metadata snapshot immediately. The screen may
        # now become interactive while enrichment continues in this worker.
        with _LOCK:
            _CACHE = fast
            _READY = bool(fast)
            _FINISHED_AT = time.time()
        extra = {}
        try:
            extra = _load_enrichment()
        except Exception:
            extra = {}
        with _LOCK:
            data = dict(_CACHE or {})
            data.update(extra or {})
            _CACHE = data
            _READY = bool(data)
            _BUSY = False
            _FINISHED_AT = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return False


def snapshot():
    """Return the current in-memory snapshot without touching disk."""
    with _LOCK:
        if not _READY:
            return None
        # A shallow dict is enough. ChannelMapping copies individual lists before
        # mutating its own view; large nested SRP data stays zero-copy.
        return dict(_CACHE)


def status():
    with _LOCK:
        return {
            "ready": bool(_READY),
            "busy": bool(_BUSY),
            "started_at": float(_STARTED_AT or 0.0),
            "finished_at": float(_FINISHED_AT or 0.0),
        }


def update_receiver(sat, bouquet_catalog, all_records, srp_state=None, audit_summary=None):
    """Refresh the resident snapshot after background lamedb verification."""
    global _CACHE, _READY, _FINISHED_AT
    with _LOCK:
        data = dict(_CACHE or {})
        # Preserve the last good Reception Lists snapshot on a transient empty
        # lamedb/API result.  A verified non-empty registry always replaces it.
        if sat or not data.get("sat"):
            data["sat"] = list(sat or [])
        data["bouquet_catalog"] = list(bouquet_catalog or [])
        data["all_records"] = list(all_records or [])
        if srp_state is not None:
            data["srp_state"] = srp_state
            data["srp_stats"] = dict((srp_state or {}).get("stats") or {})
        if audit_summary is not None:
            data["audit_summary"] = dict(audit_summary or {})
        data["loaded_at"] = time.time()
        _CACHE = data
        _READY = True
        _FINISHED_AT = time.time()


def update_mapping_view(view):
    """Publish a newly validated compact mapping view to the resident cache."""
    global _CACHE, _READY, _FINISHED_AT
    with _LOCK:
        data = dict(_CACHE or {})
        data["mapping_view"] = dict(view or {})
        _CACHE = data
        _READY = True
        _FINISHED_AT = time.time()




def update_repair_view(reception, view):
    """Publish one satellite-scoped ownership view in RAM only.

    Repair views are intentionally not persisted as the global mapping cache:
    they are partial by design. They let Mapping Repair show health immediately
    after one satellite has been validated without ever scanning every satellite.
    """
    global _CACHE, _READY, _FINISHED_AT
    key = str(reception or "").strip()
    if not key or not isinstance(view, dict):
        return
    with _LOCK:
        data = dict(_CACHE or {})
        views = dict(data.get("repair_views") or {})
        views[key] = dict(view or {})
        data["repair_views"] = views
        _CACHE = data
        _READY = True
        _FINISHED_AT = time.time()

def invalidate():
    global _READY
    with _LOCK:
        _READY = False


def update_smart_index(index, source_ids):
    """Publish a freshly rebuilt Smart name index to the resident snapshot."""
    global _CACHE
    with _LOCK:
        data = dict(_CACHE or {})
        data["smart_name_index"] = index if isinstance(index, dict) else {}
        data["smart_name_index_source_ids"] = list(source_ids or [])
        _CACHE = data


def update_ignored_refs(refs):
    """Publish the current NO EPG / IGNORE set without invalidating caches."""
    global _CACHE, _READY, _FINISHED_AT
    with _LOCK:
        data = dict(_CACHE or {})
        data["ignored_refs"] = list(refs or [])
        _CACHE = data
        _READY = True
        _FINISHED_AT = time.time()


def update_repair_result(reception, result):
    """Remember the lightweight exception queue for one satellite.

    beta138 persists this tiny state as well as publishing it in RAM.  This
    restores the previous Auto Mapping result after GUI/receptor restart without
    rescanning a satellite simply to recover red/yellow rows.
    """
    global _CACHE, _READY, _FINISHED_AT
    key = str(reception or "").strip()
    if not key or not isinstance(result, dict):
        return
    with _LOCK:
        data = dict(_CACHE or {})
        results = dict(data.get("repair_results") or {})
        results[key] = dict(result or {})
        data["repair_results"] = results
        _CACHE = data
        _READY = True
        _FINISHED_AT = time.time()
    try:
        auto_repair_state.save_result(key, result)
    except Exception:
        pass


def clear_repair_result(reception=None):
    """Drop stale exception state after undo/manual structural changes."""
    global _CACHE, _FINISHED_AT
    with _LOCK:
        data = dict(_CACHE or {})
        if reception is None:
            data["repair_results"] = {}
        else:
            results = dict(data.get("repair_results") or {})
            results.pop(str(reception or "").strip(), None)
            data["repair_results"] = results
        _CACHE = data
        _FINISHED_AT = time.time()
    try:
        auto_repair_state.clear(reception)
    except Exception:
        pass
