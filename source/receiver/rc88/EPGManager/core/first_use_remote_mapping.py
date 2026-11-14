# -*- coding: utf-8 -*-
"""First-use remote Smart Mapping bootstrap.

The normal EPGManager boot path is intentionally cache-only.  On a genuinely
empty installation that can leave the dashboard at 0 mapped IDs until the user
explicitly refreshes sources.  This module closes that first-use gap without
putting programme XMLTV downloads on the UI/boot critical path:

* selected ONLINE sources are read directly from their configured source URL;
* only compact ``<channel id=...>`` declarations / lightweight ID catalogues
  are streamed (programme bodies are never persisted here);
* rows are stored in source_channel_cache as tiny JSON shards, not local XMLTV;
* SRP Master then matches those IDs against the receiver's real lamedb registry;
* the operation runs once in a daemon and never runs on cursor movement.

Existing mappings always win.  This bootstrap is skipped as soon as the
receiver already has mapped ServiceRefs.
"""
from __future__ import print_function

import json
import os
import threading
import time
try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
except Exception:  # pragma: no cover - split Python images are guarded by Depends
    ThreadPoolExecutor = None
    as_completed = None

from .logger import get_logger
from . import (
    source_catalog, source_channel_cache, remote_channel_catalog,
    srp_channel_map, srp_master_engine, provider_srp_map,
    smart_catalog_boot, smart_mapping_warm_cache, mapping_view_cache,
    channel_mapper,
)
from .source_preferences import SourcePreferences

log = get_logger(__name__)

STATE_PATH = "/etc/enigma2/epgmanager_first_use_remote_mapping.json"
_LOCK = threading.RLock()
_BUSY = False
_STARTED = False
_STATUS = {
    "state": "idle", "busy": False, "done": 0, "total": 0,
    "rows": 0, "mapped_refs": 0, "percent": 0, "message": "",
    "started_at": 0, "finished_at": 0,
}
_LAST_ATTEMPT = 0.0
REMOTE_WORKERS = 4
INITIAL_MAP_BUDGET_SECONDS = 45


def _write_state(payload):
    try:
        parent = os.path.dirname(STATE_PATH)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def _set_status(**kwargs):
    with _LOCK:
        _STATUS.update(kwargs)
        return dict(_STATUS)


def status():
    with _LOCK:
        return dict(_STATUS)


def _mapped_refs():
    """Cheap current mapping count; never scans XML or bouquets."""
    try:
        idx = srp_channel_map.load_index() or {}
        refs = 0
        for row in (idx.get("sources") or {}).values():
            refs += int((row or {}).get("refs") or (row or {}).get("service_refs") or 0)
        if refs:
            return int(refs)
    except Exception:
        pass
    try:
        st = srp_master_engine.load_state() or {}
        return len((st.get("service_map") or {}))
    except Exception:
        return 0


def _selected_items():
    try:
        selected = list(SourcePreferences().selected_catalogue_ids() or [])
    except Exception:
        selected = []
    by_id = source_catalog.by_catalogue_id() or {}
    return [dict(by_id.get(sid) or {}) for sid in selected if by_id.get(sid)]


def _is_remote(item):
    item = item or {}
    if str(item.get("kind") or "").lower() == "local":
        return False
    return bool(item.get("catalog_url") or item.get("url") or item.get("urls") or item.get("channels_urls"))


def plan():
    """Return a pure, cheap first-use plan for diagnostics/tests."""
    items = _selected_items()
    remote = [x for x in items if _is_remote(x)]
    return {
        "selected": len(items),
        "remote": len(remote),
        "native_srp": sum(1 for x in remote if bool(x.get("native_srp"))),
        "mapped_refs": _mapped_refs(),
        "source_ids": [str(x.get("id") or "") for x in remote],
    }


def needs_bootstrap():
    p = plan()
    return bool(p.get("mapped_refs", 0) <= 0 and p.get("remote", 0) > 0)


def _cache_remote_item(item):
    sid = str((item or {}).get("id") or "")
    name = str((item or {}).get("name") or sid)
    if not sid:
        return {"ok": False, "source_id": sid, "reason": "missing source id"}
    try:
        rows, meta = remote_channel_catalog.fetch(item, timeout=5, max_scan=8 * 1024 * 1024)
        rows = list(rows or [])
        if not rows:
            raise RuntimeError("remote source returned no channel IDs")
        source_channel_cache.put(
            sid, name, rows,
            provider=str((item or {}).get("provider") or ""),
            region=str((item or {}).get("region") or ""),
            language=str((item or {}).get("language") or ""),
            scope="source",
            method="first-use-remote:%s" % str((meta or {}).get("method") or "xml-link"),
        )
        return {"ok": True, "source_id": sid, "rows": len(rows),
                "method": str((meta or {}).get("method") or "remote")}
    except Exception as exc:
        return {"ok": False, "source_id": sid, "rows": 0, "reason": str(exc)}


def _run(config=None, force=False):
    global _BUSY, _LAST_ATTEMPT
    started = time.time()
    _LAST_ATTEMPT = started
    items = _selected_items()
    remote = [x for x in items if _is_remote(x)]
    native = [x for x in remote if bool(x.get("native_srp"))]
    streamed = [x for x in remote if not bool(x.get("native_srp"))]
    total = len(remote)
    _set_status(state="reading-remote", busy=True, done=0, total=total, percent=1,
                rows=0, mapped_refs=0, message="Reading Channel IDs directly from source XML links",
                started_at=int(started), finished_at=0)

    results = []
    done = 0
    rows_total = 0
    # Two concurrent source-header reads keep first setup reasonably quick while
    # remaining gentle on low-power Enigma2 receivers.
    if streamed:
        if ThreadPoolExecutor is not None and len(streamed) > 1:
            with ThreadPoolExecutor(max_workers=min(REMOTE_WORKERS, max(1, len(streamed)))) as pool:
                futures = [pool.submit(_cache_remote_item, item) for item in streamed]
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    done += 1
                    rows_total += int(result.get("rows") or 0)
                    _set_status(done=done, rows=rows_total,
                                percent=max(1, min(35, int(35.0 * done / max(1, total)))),
                                message="Remote XML Channel IDs: %d/%d sources" % (done, total))
        else:
            for item in streamed:
                result = _cache_remote_item(item)
                results.append(result)
                done += 1
                rows_total += int(result.get("rows") or 0)
                _set_status(done=done, rows=rows_total,
                            percent=max(1, min(35, int(35.0 * done / max(1, total)))),
                            message="Remote XML Channel IDs: %d/%d sources" % (done, total))

    # Native provider maps are also fetched from their remote channels= links;
    # they are mapping metadata, not local programme XMLTV.
    native_result = {"ok": True, "sources": 0, "refs": 0, "failed": []}
    if native:
        try:
            epg_dir = config.get_epg_output_dir() if config is not None else channel_mapper.EPG_DIR
            native_result = provider_srp_map.register_selected(native, epg_dir, force=True) or native_result
        except Exception as exc:
            native_result = {"ok": False, "sources": 0, "refs": 0, "failed": [str(exc)]}
        done += len(native)
        _set_status(done=min(done, total), rows=rows_total, percent=35,
                    message="Building ServiceRef mapping from receiver lamedb")

    _set_status(state="mapping", percent=35,
                message="Matching remote Channel IDs to receiver ServiceRefs")

    def _map_progress(pct, detail):
        try:
            pct = max(0, min(100, int(pct or 0)))
        except Exception:
            pct = 0
        _set_status(state="mapping", percent=max(35, min(99, 35 + int(64.0 * pct / 100.0))),
                    message=str(detail or "Matching remote Channel IDs to receiver ServiceRefs"))

    try:
        rebuilt = srp_master_engine.rebuild(
            config=config, force=True, postprocess=False, progress_cb=_map_progress,
            time_budget=INITIAL_MAP_BUDGET_SECONDS, allow_partial=True) or {}
    except Exception as exc:
        rebuilt = {"ok": False, "reason": str(exc)}

    mapped_refs = 0
    try:
        mapped_refs = int(((rebuilt.get("stats") or {}).get("mapped_service_refs") or 0))
    except Exception:
        mapped_refs = 0
    if not mapped_refs:
        mapped_refs = _mapped_refs()

    # Publish compact remote rows into RAM only after network work has ended.
    try:
        smart_catalog_boot.ensure_async(force=True)
    except Exception:
        pass
    try:
        mapping_view_cache.invalidate()
        smart_mapping_warm_cache.invalidate()
        smart_mapping_warm_cache.ensure_async(force=True)
    except Exception:
        pass

    ok_remote = sum(1 for x in results if x.get("ok"))
    failed = [x for x in results if not x.get("ok")]
    budget_exhausted = bool((rebuilt.get("stats") or {}).get("time_budget_exhausted") or rebuilt.get("partial"))
    if mapped_refs > 0:
        state = "ready-partial" if budget_exhausted else "ready"
    else:
        state = "partial" if (ok_remote or native_result.get("sources")) else "failed"
    summary = {
        "version": 1, "state": state, "timestamp": int(time.time()),
        "selected_sources": len(items), "remote_sources": total,
        "remote_ready": ok_remote, "remote_failed": len(failed),
        "channel_ids": rows_total, "mapped_refs": int(mapped_refs),
        "native_sources": int(native_result.get("sources") or 0),
        "rebuild_ok": bool(rebuilt.get("ok")),
        "time_budget_exhausted": bool(budget_exhausted),
        "sources_deferred": int((rebuilt.get("stats") or {}).get("sources_deferred") or 0),
        "failed_sources": [str(x.get("source_id") or "") for x in failed][:12],
    }
    _write_state(summary)
    final_message = ("Fast initial mapping ready • remaining source maps prepare automatically on Import"
                     if budget_exhausted else
                     ("Initial remote mapping ready" if mapped_refs else
                      "Remote catalogues prepared; no safe receiver match found"))
    _set_status(state=state, busy=False, done=total, total=total, percent=100,
                rows=rows_total, mapped_refs=int(mapped_refs), message=final_message,
                finished_at=int(time.time()))
    log.info("First-use remote mapping %s: %d remote source(s), %d IDs, %d receiver refs",
             state, total, rows_total, mapped_refs)
    return summary


def ensure_async(config=None, force=False, delay=2.0):
    """Start the first-use direct-remote bootstrap once, in background."""
    global _BUSY, _STARTED
    with _LOCK:
        if _BUSY:
            return False
        if not force and not needs_bootstrap():
            return True
        # Avoid repeated network storms if Home is opened several times while a
        # temporary provider/network failure is still fresh.
        if not force and _LAST_ATTEMPT and (time.time() - _LAST_ATTEMPT) < 120:
            return False
        _BUSY = True
        _STARTED = True
        _STATUS.update({"busy": True, "state": "queued", "percent": 1,
                        "message": "Initial remote mapping queued"})

    def worker():
        global _BUSY
        try:
            if delay:
                time.sleep(max(0.0, float(delay)))
            _run(config=config, force=force)
        except Exception as exc:
            log.exception("First-use remote mapping failed")
            _set_status(state="failed", busy=False, message=str(exc), finished_at=int(time.time()))
        finally:
            with _LOCK:
                _BUSY = False
                _STATUS["busy"] = False

    threading.Thread(target=worker, daemon=True, name="EPGM-FirstRemoteMap").start()
    return False
