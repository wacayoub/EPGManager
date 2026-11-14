# -*- coding: utf-8 -*-
"""Complete, low-impact Channel-ID harvester for beta39.

OpenEPG feeds are mapped to Enigma2 services by XMLTV channel ID.  A partial
ID catalogue means the SRP engine can never map the missing channels, no matter
how good the service-name matcher is.  beta39 therefore verifies the *actual*
XMLTV <channel> header for MENA OpenEPG feeds once per day and merges it with
any lightweight .xml.txt catalogue.

No programme data is retained, and parsing stops before the first <programme>.
"""
from __future__ import print_function

import json
import os
import threading
import time

from .logger import get_logger
from . import source_catalog, source_channel_cache, source_channel_sync, openepg_id_catalog, remote_channel_catalog

log = get_logger(__name__)
STATE_PATH = "/etc/enigma2/epgmanager_id_harvest_state.json"
TTL = 24 * 3600
_BUILD_LOCK = threading.Lock()
_ASYNC_LOCK = threading.Lock()
_ASYNC_RUNNING = False
_MENA_CODES = set(("MA", "EG", "SA", "QA", "AE", "PS", "TN", "DZ", "MENA"))
_PRIORITY = {"AE": 0, "SA": 1, "QA": 2, "EG": 3, "PS": 4, "MA": 5, "MENA": 6}


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _is_mena_openepg(item):
    if str((item or {}).get("provider") or "").upper() != "OPENEPG":
        return False
    countries = set(str(x).upper() for x in ((item or {}).get("countries") or []) if x)
    region = str((item or {}).get("region") or "").upper()
    return bool(countries & _MENA_CODES or "MENA" in region or "MOROCCO" in region)


def _priority(item):
    countries = [str(x).upper() for x in ((item or {}).get("countries") or []) if x]
    rank = min([_PRIORITY.get(x, 50) for x in countries] or [50])
    return (rank, str((item or {}).get("name") or "").lower())


def _merge_rows(*groups):
    out = []
    by_id = {}
    for rows in groups:
        for row in rows or []:
            cid = str((row or {}).get("channel_id") or "").strip()
            if not cid:
                continue
            key = cid.lower()
            display = str((row or {}).get("display_name") or cid).strip() or cid
            aliases = [str(x or "").strip() for x in ((row or {}).get("aliases") or []) if str(x or "").strip()]
            if key not in by_id:
                item = {"channel_id": cid, "display_name": display}
                if aliases:
                    item["aliases"] = aliases[:8]
                by_id[key] = item
                out.append(item)
                continue
            item = by_id[key]
            merged = list(item.get("aliases") or [])
            seen_alias = set(str(x).casefold() for x in merged)
            for value in [display] + aliases:
                low = value.casefold()
                if value and low != str(item.get("display_name") or "").casefold() and low not in seen_alias:
                    seen_alias.add(low); merged.append(value)
            if merged:
                item["aliases"] = merged[:8]
    return out


def _state_row(data, sid):
    return dict(((data.get("sources") or {}).get(str(sid))) or {})


def source_state(source_id):
    return _state_row(_read_json(STATE_PATH), source_id)


def complete(source_id):
    row = source_state(source_id)
    return bool(row.get("catalog_complete") and int(row.get("channels") or 0) > 0)


def refresh_source(item, force=False, guard_cb=None):
    sid = str((item or {}).get("id") or "")
    if not sid or not _is_mena_openepg(item):
        return {"ok": False, "skipped": True, "reason": "not MENA OpenEPG"}
    data = _read_json(STATE_PATH)
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    old = _state_row(data, sid)
    now = int(time.time())
    if (not force and old.get("catalog_complete") and int(old.get("checked") or 0) > 0
            and now - int(old.get("checked") or 0) < TTL and source_channel_cache.has(sid)):
        return {"ok": True, "cached": True, "channels": source_channel_cache.count(sid),
                "catalog_complete": True}

    error = ""
    header_rows = []
    header_meta = {}
    try:
        header_rows, header_meta = remote_channel_catalog.fetch(
            item, timeout=10, max_scan=8 * 1024 * 1024)
    except source_channel_sync.SyncDeferred:
        raise
    except Exception as exc:
        error = str(exc)
        log.warning("Source XML verification failed for %s: %s", item.get("name"), exc)

    # The remote XML URL is the only authority. Last-known compact rows may be
    # kept only as a UI cache when the network is down; they do not turn a
    # failed verification into a complete catalogue.
    merged = _merge_rows(header_rows)
    full = bool(header_rows)
    if merged:
        source_channel_cache.put(
            sid, item.get("name") or sid, merged,
            provider=item.get("provider") or "", region=item.get("region") or "",
            language=item.get("language") or "", method="source-xml-url")
    row = {
        "checked": now,
        "channels": len(merged),
        "header_channels": len(header_rows),
        "text_channels": 0,
        "catalog_complete": bool(full),
        "method": "source-xml-url" if full else "unavailable",
        "url": str((header_meta or {}).get("url") or ""),
        "error": error,
    }
    data["sources"][sid] = row
    data["checked"] = now
    _write_json(STATE_PATH, data)
    return dict(row, ok=bool(merged))


def refresh_all(force=False, guard_cb=None, max_sources=None):
    if not _BUILD_LOCK.acquire(False):
        return {"ok": False, "busy": True}
    started = time.time()
    changed = 0
    checked = 0
    complete_count = 0
    errors = 0
    try:
        from .source_preferences import SourcePreferences
        selected = set(SourcePreferences().selected_catalogue_ids())
        items = sorted([x for x in source_catalog.all_sources()
                        if _is_mena_openepg(x) and str((x or {}).get("id") or "") in selected],
                       key=_priority)
        if max_sources:
            items = items[:int(max_sources)]
        before = source_channel_cache.all_counts()
        for item in items:
            sid = str(item.get("id") or "")
            try:
                row = refresh_source(item, force=force, guard_cb=guard_cb)
                checked += 1
                if row.get("catalog_complete"):
                    complete_count += 1
                if int(before.get(sid) or 0) != int(row.get("channels") or 0):
                    changed += 1
            except source_channel_sync.SyncDeferred:
                break
            except Exception:
                errors += 1
                log.exception("ID harvest failed for %s", item.get("name"))
            # Keep the Vu+ responsive even when several feeds need first-time verification.
            time.sleep(0.08)
        return {"ok": True, "checked": checked, "complete": complete_count,
                "changed": changed, "errors": errors,
                "duration_ms": int((time.time() - started) * 1000)}
    finally:
        _BUILD_LOCK.release()


def refresh_async(config=None, force=False, guard_cb=None):
    global _ASYNC_RUNNING
    with _ASYNC_LOCK:
        if _ASYNC_RUNNING:
            return False
        _ASYNC_RUNNING = True

    def worker():
        global _ASYNC_RUNNING
        try:
            result = refresh_all(force=force, guard_cb=guard_cb)
            if result.get("ok"):
                try:
                    from . import srp_master_engine
                    srp_master_engine.rebuild(config=config, force=bool(result.get("changed")))
                except Exception:
                    log.exception("Could not rebuild SRP maps after ID harvest")
        finally:
            with _ASYNC_LOCK:
                _ASYNC_RUNNING = False

    threading.Thread(target=worker, daemon=True).start()
    return True
