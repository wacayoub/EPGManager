# -*- coding: utf-8 -*-
"""Safe full SID reset/rebuild for EPGManager rc37.

The operation resets only EPGManager mapping/cache state. It never modifies
Enigma2 lamedb, bouquets, favourites or tuner configuration. A small tar.gz
backup is created before destructive changes, then the seven current GitHub
direct feeds are refreshed and their canonical XMLTV IDs are rebuilt against
the live receiver services.
"""
from __future__ import print_function

import glob
import os
import shutil
import tarfile
import time

from .logger import get_logger
from .mapping_store import MappingStore
from .source_preferences import SourcePreferences, FixedCatalogueSelection
from . import (channel_mapper, external_sources, github_direct_sync, source_catalog,
               source_channel_cache, srp_channel_map, srp_master_engine)

log = get_logger(__name__)

DIRECT_IDS = tuple(github_direct_sync.DIRECT_IDS)

_DERIVED_FILES = (
    "/etc/enigma2/epgmanager_srp_maps.json",
    "/etc/enigma2/epgmanager_srp_master.json",
    "/etc/enigma2/epgmanager_source_channels.json",
    "/etc/enigma2/epgmanager_source_readiness.json",
    "/etc/enigma2/epgmanager_source_index.json",
    "/etc/enigma2/epgmanager_source_fast_status.json",
    "/etc/enigma2/epgmanager_source_guard.json",
    "/etc/enigma2/epgmanager_mapping_view_cache.json",
    "/etc/enigma2/epgmanager_id_audit.json",
    "/etc/enigma2/epgmanager_id_harvest_state.json",
    "/etc/enigma2/epgmanager_first_use_remote_mapping.json",
    "/etc/enigma2/epgmanager_auto_repair_state.json",
    "/etc/enigma2/epgmanager_balanced_exact_state.json",
    "/etc/enigma2/epgmanager_xmltv_index.json",
    "/etc/enigma2/epgmanager_channel_registry.json",
    "/etc/enigma2/epgmanager_sat_registry.json",
    "/etc/enigma2/epgmanager_iptv_registry.json",
    "/etc/enigma2/epgmanager_smart_name_index.dirty",
    "/etc/enigma2/epgmanager_smart_name_index.json.gz",
    "/etc/enigma2/epgmanager_system_check.json",
)


def _epg_dir(config=None):
    try:
        if config is not None:
            return str(config.get_epg_output_dir())
    except Exception:
        pass
    return channel_mapper.EPG_DIR


def _backup_paths(epg_dir):
    paths = [
        "/etc/enigma2/epgmanager_mappings.json",
        "/etc/enigma2/epgmanager_source_preferences.json",
        "/etc/enigma2/epgmanager_srp_maps.json",
        "/etc/enigma2/epgmanager_srp_master.json",
        source_channel_cache.DIR,
        os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME),
    ]
    return [x for x in paths if os.path.exists(x)]


def _make_backup(epg_dir):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = "/etc/enigma2/epgmanager_sid_reset_backup-%s.tar.gz" % stamp
    try:
        with tarfile.open(out, "w:gz") as tf:
            for path in _backup_paths(epg_dir):
                arc = path.lstrip("/")
                tf.add(path, arcname=arc, recursive=True)
        return out
    except Exception:
        log.exception("Could not create SID reset backup")
        return ""


def _unlink(path):
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.unlink(path)
            return 1
    except Exception:
        pass
    return 0


def _purge_derived(epg_dir):
    removed = 0
    for path in _DERIVED_FILES:
        removed += _unlink(path)
        removed += _unlink(path + ".tmp")
    try:
        if os.path.isdir(source_channel_cache.DIR):
            shutil.rmtree(source_channel_cache.DIR)
            removed += 1
    except Exception:
        log.exception("Could not clear source-channel shards")
    try:
        os.makedirs(source_channel_cache.DIR)
    except Exception:
        pass
    # Clear process-resident shard caches too; deleting the files alone is not
    # enough during the same Enigma2 session.
    try:
        source_channel_cache._ROW_MEM.clear()
        source_channel_cache._ROW_SIG.clear()
        source_channel_cache._LEGACY_MEM = None
        source_channel_cache._LEGACY_SIG = None
    except Exception:
        pass
    try:
        from . import direct_native_import
        direct_native_import._MAP_FILE_CACHE.clear()
    except Exception:
        pass

    map_dir = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
    try:
        if os.path.isdir(map_dir):
            for name in os.listdir(map_dir):
                path = os.path.join(map_dir, name)
                if os.path.isfile(path) or os.path.islink(path):
                    os.unlink(path); removed += 1
        else:
            os.makedirs(map_dir)
    except Exception:
        log.exception("Could not clear generated SID maps")

    # Bulk/exclusive backups outside the normal directory should not be able to
    # resurrect retired SIDs after a deliberate full reset.
    for pattern in (os.path.join(map_dir, "*.pre-*"), os.path.join(map_dir, "*.tmp")):
        for path in glob.glob(pattern):
            removed += _unlink(path)
    return removed


def reset_and_rebuild(config=None, store=None, progress_cb=None):
    """Reset all EPGManager SID state and rebuild from current direct feeds.

    rc39 uses a transactional order: all current direct feeds are refreshed and
    their canonical XMLTV IDs are validated *before* any current mapping state
    is removed.  If network/source validation fails, the old SID state remains
    untouched.  A rollback archive is also mandatory before the destructive
    phase starts.
    """
    started = time.time()
    epg_dir = _epg_dir(config)
    if not os.path.isdir(epg_dir):
        os.makedirs(epg_dir)

    def progress(percent, text):
        if progress_cb:
            try:
                progress_cb(int(percent), str(text))
            except Exception:
                pass

    try:
        timeout = max(15, int(config.get_timeout_seconds())) if config is not None else 30
    except Exception:
        timeout = 30

    # Phase 1: refresh/validate every direct feed while the current mappings are
    # still intact.  The XML files themselves are safe to refresh in place; the
    # mapping/cache purge below never deletes them.
    prefs = SourcePreferences()
    by_id = source_catalog.by_catalogue_id()
    preflight = {}
    source_reports = []
    total = max(1, len(DIRECT_IDS))
    progress(2, "Preflighting %d current GitHub feeds" % len(DIRECT_IDS))
    for index, sid in enumerate(DIRECT_IDS, 1):
        item = by_id.get(sid) or {}
        name = str(item.get("name") or sid)
        progress(2 + int(30.0 * (index-1) / total), "Validating %s from GitHub" % name)
        row = {"source_id": sid, "name": name, "ok": False, "channels": 0, "mapped_refs": 0}
        try:
            if not item:
                raise RuntimeError("Direct source is missing from current catalogue")
            path = external_sources.download_source(
                item, epg_dir, retries=2, timeout=timeout, force=True)
            mid = str(source_catalog.mapping_source_id(item) or sid)
            ids = channel_mapper.read_xmltv_channel_ids_fast(path, mid, name)
            if not ids:
                raise RuntimeError("No XMLTV channel IDs in refreshed feed")
            row.update({"ok": True, "path": path, "channels": len(ids)})
            preflight[sid] = {"item": item, "path": path, "mid": mid, "ids": ids}
        except Exception as exc:
            row["error"] = str(exc)
            log.exception("SID reset preflight failed for %s", name)
        source_reports.append(row)

    preflight_ok = sum(1 for row in source_reports if row.get("ok"))
    if preflight_ok != len(DIRECT_IDS):
        progress(100, "SID reset aborted: source preflight failed")
        return {
            "ok": False,
            "phase": "preflight",
            "error": "All %d direct feeds must validate before SID reset" % len(DIRECT_IDS),
            "backup": "",
            "removed_mappings": 0,
            "removed_files": 0,
            "selected_sources": list(DIRECT_IDS),
            "sources_ok": int(preflight_ok),
            "sources_total": len(DIRECT_IDS),
            "sources": source_reports,
            "master": {},
            "elapsed": round(max(0.0, time.time() - started), 2),
            "safety": "aborted before mapping purge; lamedb/bouquets untouched",
        }

    # Phase 2: backup is mandatory.  Never destroy the user's current SID state
    # without a rollback archive.
    progress(34, "Backing up current SID state")
    backup = _make_backup(epg_dir)
    if not backup:
        progress(100, "SID reset aborted: backup failed")
        return {
            "ok": False,
            "phase": "backup",
            "error": "Could not create SID reset rollback archive",
            "backup": "",
            "removed_mappings": 0,
            "removed_files": 0,
            "selected_sources": list(DIRECT_IDS),
            "sources_ok": int(preflight_ok),
            "sources_total": len(DIRECT_IDS),
            "sources": source_reports,
            "master": {},
            "elapsed": round(max(0.0, time.time() - started), 2),
            "safety": "aborted before mapping purge; lamedb/bouquets untouched",
        }

    store = store or MappingStore()
    reset_stats = store.reset_all_sid_mappings()
    progress(40, "Removing old SID maps and caches")
    removed_files = _purge_derived(epg_dir)

    selected = prefs.set_selected_catalogue_ids(DIRECT_IDS, save=True)
    try:
        prefs.set_pinned_catalogue_ids([x for x in prefs.pinned_catalogue_ids() if x in DIRECT_IDS], save=True)
    except Exception:
        pass

    # Phase 3: publish canonical caches/maps from the already-validated feeds.
    rebuild_rows = []
    for index, sid in enumerate(DIRECT_IDS, 1):
        pf = preflight[sid]
        item, path, mid, ids = pf["item"], pf["path"], pf["mid"], pf["ids"]
        name = str(item.get("name") or sid)
        progress(42 + int(38.0 * (index-1) / total), "Rebuilding canonical SIDs for %s" % name)
        row = {"source_id": sid, "name": name, "ok": False, "channels": len(ids), "mapped_refs": 0, "path": path}
        try:
            source_channel_cache.put(
                sid, name, ids, provider=item.get("provider") or "",
                region=item.get("region") or "", language=item.get("language") or "",
                method="sid-reset-current-xml")
            selection = FixedCatalogueSelection(sid)
            report = srp_channel_map.prepare_selected_maps(
                [item], {sid: {"ok": True, "path": path}}, selection,
                epg_dir, include_iptv=True, progress_cb=None)
            row.update({
                "ok": True,
                "mapped_ids": int((report or {}).get("mapped_channel_ids") or 0),
                "mapped_refs": int((report or {}).get("mapped_service_refs") or 0),
                "unmapped_ids": int((report or {}).get("unmapped_channel_ids") or 0),
            })
        except Exception as exc:
            row["error"] = str(exc)
            log.exception("SID reset canonical rebuild failed for %s", name)
        rebuild_rows.append(row)

    healthy = sum(1 for row in rebuild_rows if row.get("ok"))
    progress(82, "Building global one-owner SID database")
    try:
        master = srp_master_engine.rebuild(config=config, epg_dir=epg_dir, force=True)
    except Exception as exc:
        master = {"ok": False, "reason": str(exc)}
        log.exception("Global SRP master rebuild failed after SID reset")

    # Remove reconstruction snapshots. The full reset backup above is the single
    # rollback point and prevents obsolete pre-bulk files resurrecting old SIDs.
    map_dir = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
    cleanup_backups = 0
    for path in glob.glob(os.path.join(map_dir, "*.pre-*")):
        cleanup_backups += _unlink(path)

    all_sources_ok = (healthy == len(DIRECT_IDS))
    master_ok = bool((master or {}).get("ok", False))
    ok = bool(all_sources_ok and master_ok)
    progress(100, "SID reset complete: %d/%d sources rebuilt" % (healthy, len(rebuild_rows)))
    return {
        "ok": ok,
        "phase": "complete" if ok else "rebuild",
        "error": "" if ok else "Canonical SID rebuild/master validation incomplete; rollback archive preserved",
        "backup": backup,
        "removed_mappings": int((reset_stats or {}).get("removed_mappings") or 0),
        "removed_files": int(removed_files),
        "cleanup_backups": int(cleanup_backups),
        "selected_sources": list(selected),
        "sources_ok": int(healthy),
        "sources_total": len(rebuild_rows),
        "sources": rebuild_rows,
        "master": master,
        "elapsed": round(max(0.0, time.time() - started), 2),
        "safety": "transactional preflight + rollback backup; lamedb/bouquets untouched",
    }
