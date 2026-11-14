# -*- coding: utf-8 -*-
"""Direct XMLTV-ID health and bounded repair for EPGManager rc39.

Goals:
- GREEN IDs really have future EPG.
- RED IDs are only those proven to have zero future programmes.
- stale automatic ownership for retired/dead IDs is removed safely.
- current direct feeds are rematched against the live receiver registry.
- manual mappings are never deleted by this repair.

The worker is explicit/user-triggered and runs in a daemon thread so the Enigma2
UI remains responsive on small receivers.
"""
from __future__ import print_function

import os
import shutil
import threading
import time

from .logger import get_logger
from .mapping_store import MappingStore
from .source_preferences import SourcePreferences
from . import (channel_mapper, external_sources, github_direct_sync, id_coverage_audit,
               source_catalog, source_channel_cache, source_quality, srp_master_engine)

log = get_logger(__name__)
_LOCK = threading.RLock()
_RUNNING = False
_LAST = {}


def _epg_dir(config=None):
    try:
        if config is not None:
            return str(config.get_epg_output_dir())
    except Exception:
        pass
    return channel_mapper.EPG_DIR


def _selected_direct_ids():
    try:
        selected = set(str(x or "") for x in SourcePreferences().selected_catalogue_ids())
    except Exception:
        selected = set()
    direct = [sid for sid in github_direct_sync.DIRECT_IDS if sid in selected]
    # A fresh/legacy setup may not yet have a saved selection.  In that case use
    # the current direct set.  If the user explicitly selected only other feeds,
    # respect that choice and do not mutate dormant direct-source ownership.
    if selected:
        return direct
    return list(github_direct_sync.DIRECT_IDS)


def _dead_ids_from_monitor(source_id, payload=None):
    """Return only IDs still proven dead after local-vs-monitor arbitration."""
    stem = github_direct_sync.source_stem(source_id)
    data = payload if isinstance(payload, dict) else github_direct_sync.load()
    out = set()
    for row in ((data.get("id_monitor") or {}).get(stem) or []):
        cid = str((row or {}).get("id") or "").strip()
        if cid and github_direct_sync.dead_id(source_id, cid, data=data):
            out.add(cid.lower())
    return out


def run(config=None, progress_cb=None):
    started = time.time()
    epg_dir = _epg_dir(config)
    direct_ids = _selected_direct_ids()
    if not direct_ids:
        return {"ok": True, "phase": "complete", "sources_ok": 0, "sources_total": 0,
                "red_no_epg": 0, "removed_stale_auto": 0, "mapped_service_refs": 0,
                "unresolved_ids": 0, "elapsed": 0.0,
                "safety": "no direct GitHub source is selected; nothing changed"}
    by_id = source_catalog.by_catalogue_id()
    github_direct_sync.refresh_id_monitor_sync()
    monitor_payload = github_direct_sync.load() or {}

    valid = {}
    dead = {}
    prepared = {}
    reports = []
    total = max(1, len(direct_ids))

    def progress(pct, text):
        if progress_cb:
            try:
                progress_cb(int(pct), str(text))
            except Exception:
                pass

    progress(2, "Refreshing direct XMLTV IDs")
    for pos, sid in enumerate(direct_ids, 1):
        item = by_id.get(sid) or {}
        name = str(item.get("name") or sid)
        row = {"source_id": sid, "name": name, "ok": False, "ids": 0, "dead": 0}
        try:
            if not item:
                raise RuntimeError("source missing from current catalogue")
            progress(3 + int(37.0 * (pos - 1) / total), "Checking %s" % name)
            path = external_sources.download_source(item, epg_dir, retries=2, timeout=30, force=True)
            mid = str(source_catalog.mapping_source_id(item) or sid)
            ids = channel_mapper.read_xmltv_channel_ids_fast(path, mid, name)
            if not ids:
                raise RuntimeError("no XMLTV IDs in refreshed feed")
            valid[sid] = set(str(x.get("channel_id") or "").strip().lower()
                             for x in ids if str(x.get("channel_id") or "").strip())
            dead[sid] = _dead_ids_from_monitor(sid, monitor_payload)
            prepared[sid] = {"item": item, "name": name, "path": path, "ids": ids}
            row.update({"ok": True, "path": path, "ids": len(valid[sid]), "dead": len(dead[sid])})
        except Exception as exc:
            row["error"] = str(exc)
            log.exception("Direct ID repair refresh failed for %s", name)
        reports.append(row)

    healthy = sum(1 for row in reports if row.get("ok"))
    if healthy != len(direct_ids):
        # Keep XML and mapping generations aligned if one provider fails after
        # earlier feeds already refreshed successfully. download_source keeps a
        # .lastgood copy; restore it where available. No mapping/cache state has
        # been pruned yet at this point.
        for info in prepared.values():
            path = str((info or {}).get("path") or "")
            lastgood = path + ".lastgood"
            try:
                if path and os.path.isfile(lastgood):
                    shutil.copy2(lastgood, path)
                    # Keep the persisted quality profile aligned with the
                    # restored generation as well.  Failure here is harmless:
                    # mappings have not been pruned yet and the last-good XML
                    # remains the authoritative payload.
                    try:
                        source_quality.audit_file((info or {}).get("item") or {}, path)
                    except Exception:
                        pass
            except Exception:
                pass
        return {
            "ok": False, "phase": "refresh", "sources_ok": healthy,
            "sources_total": len(direct_ids), "sources": reports,
            "error": "Not all selected direct feeds refreshed; old mappings kept",
            "elapsed": round(time.time() - started, 2),
        }

    # Publish refreshed channel-ID shards only after the whole direct-source
    # preflight succeeds.  This prevents half-old / half-new cache generations.
    for sid, info in prepared.items():
        item = info.get("item") or {}
        source_channel_cache.put(
            sid, info.get("name") or sid, info.get("ids") or [],
            provider=item.get("provider") or "", region=item.get("region") or "",
            language=item.get("language") or "", method="rc43-direct-id-repair")

    progress(44, "Removing stale automatic IDs")
    store = MappingStore()
    pruned = store.prune_invalid_auto_ids(valid, dead, label="Direct ID repair rc39")

    progress(52, "Rebuilding safe receiver mappings")
    master = srp_master_engine.rebuild(config=config, epg_dir=epg_dir, force=True)
    progress(88, "Auditing mapped / unmapped IDs")
    try:
        audit = id_coverage_audit.build(config=config) or {}
    except Exception as exc:
        log.warning("ID coverage audit after direct repair failed: %s", exc)
        audit = {}

    summary = audit.get("summary") or {}
    stats = (master or {}).get("stats") or {}
    ok = bool((master or {}).get("ok"))
    result = {
        "ok": ok, "phase": "complete" if ok else "mapping",
        "sources_ok": healthy, "sources_total": len(direct_ids), "sources": reports,
        "red_no_epg": sum(len(x) for x in dead.values()),
        "removed_stale_auto": int((pruned or {}).get("removed") or 0),
        "removed_no_epg_auto": int((pruned or {}).get("no_epg") or 0),
        "removed_missing_id_auto": int((pruned or {}).get("missing_id") or 0),
        "mapped_service_refs": int(stats.get("mapped_service_refs") or summary.get("receiver_mapped") or 0),
        "unresolved_ids": int(stats.get("unresolved_ids") or summary.get("unlinked_ids") or 0),
        "match_failed": int(summary.get("match_failed") or 0),
        "master": master, "elapsed": round(time.time() - started, 2),
        "safety": "manual locks preserved; only proven zero-EPG IDs are quarantined",
    }
    progress(100, "ID repair complete" if ok else "ID repair needs review")
    return result


def is_running():
    with _LOCK:
        return bool(_RUNNING)


def last_result():
    with _LOCK:
        return dict(_LAST or {})


def repair_async(config=None):
    global _RUNNING
    with _LOCK:
        if _RUNNING:
            return False
        _RUNNING = True

    def worker():
        global _RUNNING, _LAST
        try:
            result = run(config=config)
        except Exception as exc:
            log.exception("Direct ID repair failed")
            result = {"ok": False, "phase": "exception", "error": str(exc)}
        with _LOCK:
            _LAST = dict(result or {})
            _RUNNING = False

    threading.Thread(target=worker, name="EPGM-DirectIdRepair", daemon=True).start()
    return True
