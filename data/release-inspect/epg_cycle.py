# -*- coding: utf-8 -*-
"""Automatic EPG cycle with real stage progress for v6.3.1.

Selected source(s) -> cached beta1 mapping -> OE-Alliance Native Import.  A one-source
cycle stays scoped to that source and does not recalculate the global receiver
coverage report.
"""
from __future__ import print_function
import os
import threading
import time
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import external_sources, source_catalog, channel_mapper, srp_channel_map, activity_store, channel_registry, sat_coverage, source_guard, source_readiness, source_channel_cache, source_quality
from .source_preferences import SourcePreferences
from .mapping_store import MappingStore
from .logger import get_logger

log = get_logger(__name__)

BETA17_AUDIT_PENDING = "/etc/enigma2/.epgmanager_beta17_provider_audit_pending"
BETA18_DIRECT_IMPORT = True
BETA96_ROUTING_PENDING = {
    "bein_sports": "/etc/enigma2/.epgmanager_beta96_bein_routing_pending",
}
BETA96_ROUTING_DONE = {
    "bein_sports": "/etc/epgmanager_epg/.beta96_bein_routing_done",
}


class EPGCycleRunner(object):
    def __init__(self, manager, config, on_complete=None, force_download=False,
                 preferences=None, scope_label="FULL EPG", cache_only=False,
                 import_only=False, update_only=False):
        self.manager = manager
        self.config = config
        self.on_complete = on_complete
        self.force_download = bool(force_download)
        self.cache_only = bool(cache_only)
        self.import_only = bool(import_only)
        self.update_only = bool(update_only)
        self.preferences = preferences or SourcePreferences()
        self.scope_label = str(scope_label or "FULL EPG")
        self.running = False
        self.done = False
        self.stage = "IDLE"
        self.error = None
        self.result = None
        self.started_at = None
        self.import_runner = None
        self._thread = None
        self.progress_percent = 0
        self.progress_detail = ""
        self.source_completed = 0
        self.source_total = 0
        self._routing_repair_mids = set()

    def _progress(self, percent, detail=None, stage=None):
        self.progress_percent = max(0, min(100, int(percent or 0)))
        if detail is not None:
            self.progress_detail = str(detail or "")
        if stage:
            self.stage = str(stage)

    def start(self):
        if self.running or self.manager.is_busy():
            return False
        self.running = True
        self.done = False
        self.stage = "PREPARING"
        self.started_at = time.time()
        if self.import_only:
            self._progress(0, "Preparing selected sources", "START")
        elif self.update_only:
            self._progress(1, "Preparing source update", "UPDATE")
        else:
            self._progress(1, "Preparing selected EPG sources", "PREPARING")
        self.manager._busy = True
        self.manager._cancel_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        return True

    def _selected_rows(self):
        by_id = source_catalog.by_catalogue_id()
        return [by_id[sid] for sid in self.preferences.selected_catalogue_ids() if sid in by_id]

    def _sync_direct_xml_for_import(self, selected):
        """Download a fresh local XMLTV snapshot for every selected direct feed.

        rc36.8 contract: Import All is GitHub -> local XML -> Native Import.
        Refresh is atomic: the current validated receiver XML stays in place
        until a replacement has downloaded and passed validation.  If GitHub is
        temporarily unavailable, Import All may reuse that local file only when
        its complete quality profile still proves future EPG. Persistent SRP maps
        are deliberately preserved because programme refreshes do not change a
        user-approved ServiceRef mapping.
        """
        selected = list(selected or [])
        direct = [x for x in selected if bool((x or {}).get("direct_feed"))]
        report = {"checked": len(direct), "fresh": 0, "failed": 0, "rows": [], "paths": {}}
        if not direct:
            return report

        epg_dir = self.config.get_epg_output_dir()
        try:
            if not os.path.isdir(epg_dir):
                os.makedirs(epg_dir)
        except Exception:
            pass

        # Remove obsolete direct/group payloads only. Mapping files and user
        # choices are intentionally untouched.
        obsolete = [
            "ext_epgscrapers_2m.xml", "ext_epgscrapers_chada.xml",
            "2m.xml", "chada.xml",
        ]
        try:
            for name in os.listdir(epg_dir):
                low = str(name).lower()
                if low.startswith("group-") and low.endswith(".xml"):
                    obsolete.append(name)
                elif low.startswith("ext_epgscrapers_") and low.endswith(".xml"):
                    obsolete.append(name)
        except Exception:
            pass
        for name in set(obsolete):
            try:
                path = os.path.join(epg_dir, name)
                if os.path.isfile(path):
                    os.unlink(path)
            except Exception:
                pass

        workers = max(1, min(3, len(direct)))
        self._progress(4, "Downloading fresh XML from GitHub", "GITHUB XML SYNC")

        def _validated_local(item, path):
            if not path or not os.path.isfile(path):
                return None
            info = source_guard.inspect_xml(path) or {}
            channels = int(info.get("channels") or 0)
            programmes = int(info.get("programmes") or 0)
            if channels <= 0 or programmes <= 0:
                return None
            profile = source_quality.audit_file(item, path) or {}
            future_last = int(((profile.get("overall") or {}).get("future_last") or 0))
            if future_last <= int(time.time()):
                return None
            return {"channels": channels, "programmes": programmes,
                    "future_last": future_last}

        def _fetch(item):
            sid = str((item or {}).get("id") or "")
            name = str((item or {}).get("name") or sid)
            current = external_sources.local_xml_path(item, epg_dir)
            try:
                path = external_sources.download_source(
                    item, epg_dir, retries=max(2, int(self.config.get_retry_count())),
                    timeout=max(12, int(self.config.get_timeout_seconds())), force=True)
                valid = _validated_local(item, path)
                if not valid:
                    raise RuntimeError("Downloaded XML has no usable future channel/programme data")
                return {"id": sid, "name": name, "ok": True, "path": path,
                        "channels": valid["channels"], "programmes": valid["programmes"],
                        "fallback": False}
            except Exception as exc:
                # download_source() never replaces a working file on transport
                # failure.  Reuse it only if it still contains future EPG.
                valid = _validated_local(item, current)
                if valid:
                    return {"id": sid, "name": name, "ok": True, "path": current,
                            "channels": valid["channels"], "programmes": valid["programmes"],
                            "fallback": True, "warning": "GitHub refresh failed; future local last-good reused: %s" % exc}
                raise

        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch, item): item for item in direct}
            for future in as_completed(futures):
                item = futures[future]
                sid = str((item or {}).get("id") or "")
                name = str((item or {}).get("name") or sid)
                try:
                    row = future.result()
                    report["fresh"] += 1
                    report["paths"][sid] = row.get("path")
                except Exception as exc:
                    row = {"id": sid, "name": name, "ok": False, "error": str(exc)}
                    report["failed"] += 1
                    log.exception("Fresh direct XML sync failed for %s", name)
                report["rows"].append(row)
                completed += 1
                pct = 4 + int(22.0 * completed / max(1, len(direct)))
                self._progress(pct, "%d / %d GitHub XML feed(s) downloaded" %
                               (completed, len(direct)), "GITHUB XML SYNC")
        return report

    def _local_cache_freshness(self, item, path):
        """Return (fresh, age_seconds, max_age_seconds) for local source caches.

        Most historical local generators keep the legacy behaviour (no explicit
        age cap). Sources with a one-day contract can declare
        ``max_usable_cache_age_seconds``. A stale file remains on disk as a
        recovery artefact but is never counted as usable/importable EPG.
        """
        max_age = 0
        try:
            manager_id = item.get("manager_id") or source_catalog.mapping_source_id(item)
            src = getattr(self.manager, "_sources", {}).get(manager_id)
            max_age = int(getattr(src, "max_usable_cache_age_seconds", 0) or item.get("max_cache_age_seconds") or 0)
        except Exception:
            try:
                max_age = int(item.get("max_cache_age_seconds") or 0)
            except Exception:
                max_age = 0
        if not max_age:
            return True, 0, 0
        try:
            age = max(0, int(time.time() - os.path.getmtime(path)))
        except Exception:
            return False, 0, max_age
        return bool(age <= max_age), age, max_age

    def _run_external(self, item):
        """Refresh one external source directly from its provider URL.

        Universal remote-source policy (8.2.2-rc5): external XMLTV is never
        downloaded to /etc and never read back from a receiver-side XML copy.
        We stream a bounded provider sample, persist only compact channel-ID
        metadata, and build a missing SRP map from a transient /tmp catalogue.
        """
        started = time.time()
        if (item or {}).get("grouped_feed"):
            sid = str((item or {}).get("id") or "")
            mid = str(source_catalog.mapping_source_id(item) or sid)
            name = str((item or {}).get("name") or sid)
            try:
                path = external_sources.download_source(
                    item, self.config.get_epg_output_dir(),
                    retries=max(1, int(self.config.get_retry_count())),
                    timeout=self.config.get_timeout_seconds(), force=self.force_download)
                ids = channel_mapper.read_xmltv_channel_ids_fast(path, mid, name)
                if ids:
                    source_channel_cache.put(
                        sid, name, ids, provider=item.get("provider") or "",
                        region=item.get("region") or "", language=item.get("language") or "",
                        method="grouped-local-xml")
                from .source_preferences import FixedCatalogueSelection
                selection = FixedCatalogueSelection(sid)
                map_report = srp_channel_map.prepare_selected_maps(
                    [item], {sid: {"ok": True, "path": path}}, selection,
                    self.config.get_epg_output_dir(), include_iptv=True, progress_cb=None)
                info = source_guard.inspect_xml(path)
                map_ready = bool(srp_channel_map.map_path_for_source(mid))
                ok = bool(int(info.get("programmes") or 0) > 0 and map_ready)
                source_readiness.put(sid, {
                    "source_id": sid, "source_name": name,
                    "state": "READY" if ok else "MAP NEEDED", "ready": ok,
                    "channels": int(info.get("channels") or len(ids)),
                    "programmes": int(info.get("programmes") or 0),
                    "programmes_partial": False, "complete": True,
                    "map_ready": map_ready,
                    "mapped_refs": int((map_report or {}).get("mapped_service_refs") or 0),
                    "transport": "grouped-local-xml", "local_path": path,
                    "url": str(item.get("url") or "")})
                return {
                    "ok": ok, "path": path, "url": str(item.get("url") or ""),
                    "channels": int(info.get("channels") or len(ids)),
                    "programmes": int(info.get("programmes") or 0),
                    "map_ready": map_ready,
                    "mapped_refs": int((map_report or {}).get("mapped_service_refs") or 0),
                    "transport": "grouped-local-xml", "remote_authoritative": True,
                    "elapsed": max(0.0, time.time() - started),
                    "error": "" if ok else "Grouped XML ready but ServiceRef map has no usable links",
                }
            except Exception as exc:
                log.exception("Grouped XML refresh failed for %s", name)
                return {"ok": False,
                        "path": external_sources.local_xml_path(item, self.config.get_epg_output_dir()),
                        "url": str(item.get("url") or ""), "channels": 0, "programmes": 0,
                        "transport": "grouped-local-xml", "remote_authoritative": True,
                        "elapsed": max(0.0, time.time() - started), "error": str(exc)}
        try:
            row = source_readiness.probe_remote(
                item, self.config.get_epg_output_dir(),
                timeout=self.config.get_timeout_seconds(), prepare_map=True,
                quality_scan=6 * 1024 * 1024, max_programmes=700)
            ok = bool((row or {}).get("ready"))
            return {
                "ok": ok,
                "path": "",  # external feeds never have a persistent local XML path
                "url": str((row or {}).get("url") or item.get("url") or ""),
                "channels": int((row or {}).get("channels") or 0),
                "programmes": int((row or {}).get("programmes") or 0),
                "map_ready": bool((row or {}).get("map_ready")),
                "mapped_refs": int((row or {}).get("mapped_refs") or 0),
                "transport": "remote-stream",
                "remote_authoritative": True,
                "elapsed": max(0.0, time.time() - started),
                "error": "" if ok else str((row or {}).get("state") or "Remote source is not ready"),
            }
        except Exception as exc:
            return {"ok": False, "path": "", "url": str(item.get("url") or ""),
                    "channels": 0, "programmes": 0, "transport": "remote-stream",
                    "remote_authoritative": True,
                    "elapsed": max(0.0, time.time() - started), "error": str(exc)}

    def _run_external_live(self, item):
        """Compatibility wrapper: external source truth is always the live URL.

        Import All itself uses DirectEPGImportRunner and hands EPGImport the
        provider URL directly.  This helper is retained for older call paths but
        follows the same remote-only policy and never falls back to local XML.
        """
        return self._run_external(item)

    def _run_local(self, item):
        manager_id = item.get("manager_id")
        if not manager_id or manager_id not in self.manager._sources:
            return {"ok": False, "error": "Local source is unavailable"}
        source = self.manager._sources[manager_id]
        try:
            path = source.get_output_path(self.config)
        except Exception:
            path = None
        before = source_guard.inspect_xml(path) if path else {}
        ok = self.manager._run_one(source, force=self.force_download)
        status = self.manager.get_status(manager_id) or {}
        guard = source_guard.evaluate_after_refresh(
            path, before, self.config, manager_id, item.get("name") or manager_id) if path else {"accepted": bool(ok)}
        # If the source itself succeeded but the safety guard restored the old
        # cache, expose this as WARNING.  Otherwise manager._run_one() leaves
        # the source as SUCCESS and the next manual refresh is incorrectly
        # blocked by the one-hour successful-download cooldown.
        if guard.get("restored"):
            try:
                with self.manager._lock:
                    st = self.manager._status.get(manager_id)
                    if st is not None:
                        st.status = "WARNING"
                        st.error_message = (
                            "Unsafe refreshed XMLTV rejected: %s. Last Known Good restored." %
                            str(guard.get("reason") or "abnormal source drop")
                        )
                status = self.manager.get_status(manager_id) or status
            except Exception:
                pass
        channels = int(guard.get("channels") or 0)
        state = str(status.get("status") or "").upper()
        existing = bool(path and os.path.exists(path) and os.path.getsize(path) > 32) if path else False
        same_as_before = bool(before.get("exists") and guard.get("accepted") and
                              str(guard.get("sha256") or "") == str(before.get("sha256") or ""))
        cache_fresh, cache_age, cache_max_age = self._local_cache_freshness(item, path) if existing else (False, 0, 0)
        preserved_fallback = bool((not ok) and before.get("exists") and int(before.get("programmes") or 0) > 0 and
                                  guard.get("accepted") and (guard.get("unchanged") or same_as_before) and cache_fresh)
        restored_usable = bool(guard.get("restored") and cache_fresh)
        cooldown_usable = bool(state == "COOLDOWN" and existing and cache_fresh)
        effective_ok = bool(guard.get("accepted") and (ok or restored_usable or preserved_fallback or cooldown_usable))
        stale_cache = bool(existing and not cache_fresh)
        programmes = int(guard.get("programmes") or status.get("program_count") or 0)
        warning = (status.get("error_message") or "") if preserved_fallback else ""
        if stale_cache and not ok:
            age_text = "%.1fh" % (float(cache_age) / 3600.0) if cache_age else "unknown age"
            warning = ((status.get("error_message") or "Fresh guide unavailable") +
                       ". Existing XMLTV is stale (%s) and was not treated as usable." % age_text)
        refreshed = bool(ok and state != "COOLDOWN" and not guard.get("restored"))
        return {"ok": effective_ok, "path": path, "channels": channels,
                "programmes": programmes,
                "refreshed": refreshed, "cooldown": bool(state == "COOLDOWN"),
                "unchanged": bool(guard.get("unchanged")),
                "fallback": bool(restored_usable or preserved_fallback), "guard": guard,
                "elapsed": float(status.get("duration_seconds") or 0.0),
                "warning": warning,
                "kept_cache": bool(preserved_fallback or restored_usable),
                "stale_cache": stale_cache, "cache_age_seconds": int(cache_age or 0),
                "cache_max_age_seconds": int(cache_max_age or 0),
                "error": "" if effective_ok else (warning or status.get("error_message") or guard.get("reason") or "")}


    def _run_cached(self, item):
        """Use the already-generated XMLTV file without network/scraping."""
        started = time.time()
        path = None
        source_id = source_catalog.mapping_source_id(item)
        if item.get("kind") == "local":
            manager_id = item.get("manager_id") or source_id
            try:
                if manager_id in self.manager._sources:
                    path = self.manager._sources[manager_id].get_output_path(self.config)
            except Exception:
                path = None
            if not path:
                for filename, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                    if pair[0] == manager_id:
                        path = os.path.join(self.config.get_epg_output_dir(), filename)
                        break
        else:
            # External providers never use a receiver-side XMLTV copy.  A
            # cache-only check may use only the compact ID catalogue derived
            # from the remote URL; programme import still goes to that URL.
            try:
                channels = source_channel_cache.count(source_id)
            except Exception:
                channels = 0
            return {"ok": bool(channels), "path": "", "channels": int(channels),
                    "remote_authoritative": True, "transport": "remote-catalog-cache",
                    "elapsed": max(0.0, time.time() - started),
                    "error": "" if channels else "Remote channel catalogue is not prepared yet."}
        try:
            if not path or not os.path.isfile(path) or os.path.getsize(path) <= 32:
                return {"ok": False, "path": path, "error": "No EPGManager XMLTV file. Update this source first."}
            if item.get("kind") == "local":
                cache_fresh, cache_age, cache_max_age = self._local_cache_freshness(item, path)
                if not cache_fresh:
                    return {"ok": False, "path": path, "cached": True, "stale_cache": True,
                            "cache_age_seconds": int(cache_age or 0),
                            "cache_max_age_seconds": int(cache_max_age or 0),
                            "error": "Cached XMLTV is stale; refresh this source before import."}
            channels = 0
            if not self.import_only:
                channels = len(channel_mapper.read_xmltv_channel_ids_fast(
                    path, source_id, item.get("name") or source_id))
            return {"ok": True, "path": path, "channels": channels,
                    "elapsed": max(0.0, time.time() - started), "cached": True}
        except Exception as exc:
            return {"ok": False, "path": path, "error": str(exc), "cached": True}


    def _import_artifact_state(self, item):
        """Cheap, network-free readiness check used before Import Source/All.

        RC9 contract:
        - local EPGManager sources need their generated XMLTV + SRP map;
        - external sources never need a local XMLTV copy, but need the compact
          remote Channel-ID shard + SRP map used by Smart Mapping/Native Import.
        Missing first-use artifacts trigger one automatic source-scoped refresh
        before the import continues. Existing prepared sources stay on the fast
        read-only path.
        """
        item = item or {}
        sid = str(item.get("id") or "")
        mid = str(source_catalog.mapping_source_id(item) or "")
        map_path = ""
        try:
            map_path = srp_channel_map.map_path_for_source(mid)
        except Exception:
            map_path = ""
        map_ready = bool(map_path and os.path.isfile(map_path))
        state = {"source_id": sid, "mapping_id": mid, "map_ready": map_ready,
                 "catalog_ready": False, "xml_ready": False, "ready": False,
                 "reason": ""}
        if str(item.get("kind") or "").lower() != "local":
            try:
                state["catalog_ready"] = bool(source_channel_cache.has(sid))
            except Exception:
                state["catalog_ready"] = False
            if item.get("grouped_feed"):
                path = external_sources.local_xml_path(item, self.config.get_epg_output_dir())
                state["path"] = path
                try:
                    state["xml_ready"] = bool(path and os.path.isfile(path) and os.path.getsize(path) > 32)
                except Exception:
                    state["xml_ready"] = False
                state["ready"] = bool(state["xml_ready"] and map_ready)
                if not state["xml_ready"]:
                    state["reason"] = "Grouped XML missing"
                elif not map_ready:
                    state["reason"] = "Grouped XML ready; ServiceRef map missing"
                return state
            # RC20 FAST IMPORT CONTRACT:
            # Native EPGImport consumes the persistent ServiceRef map + live URL.
            # A compact Channel-ID catalogue is useful for Smart Mapping, but it
            # is NOT an import dependency once the SRP map already exists.  RC19
            # incorrectly required both, so Import All could re-probe dozens of
            # remote XMLTV feeds (up to 6 MB / 700 programmes each) before every
            # import even though their maps were already valid.
            state["ready"] = bool(map_ready)
            if not map_ready:
                state["reason"] = ("ServiceRef map missing; cached Channel-ID catalogue available"
                                   if state["catalog_ready"] else
                                   "ServiceRef map and Channel-ID catalogue missing")
            return state

        path = ""
        manager_id = str(item.get("manager_id") or mid)
        try:
            if manager_id in getattr(self.manager, "_sources", {}):
                path = self.manager._sources[manager_id].get_output_path(self.config)
        except Exception:
            path = ""
        if not path:
            filename = str(item.get("output_file") or "")
            if filename:
                path = os.path.join(self.config.get_epg_output_dir(), filename)
            else:
                for filename, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                    if pair[0] == manager_id:
                        path = os.path.join(self.config.get_epg_output_dir(), filename)
                        break
        state["path"] = path
        try:
            xml_ready = bool(path and os.path.isfile(path) and os.path.getsize(path) > 32)
        except Exception:
            xml_ready = False
        if xml_ready:
            try:
                fresh, _age, _max_age = self._local_cache_freshness(item, path)
                xml_ready = bool(fresh)
            except Exception:
                xml_ready = False
        state["xml_ready"] = xml_ready
        state["ready"] = bool(xml_ready and map_ready)
        if not xml_ready:
            state["reason"] = "EPGManager XMLTV missing or stale"
        elif not map_ready:
            state["reason"] = "ServiceRef map missing"
        return state

    def _auto_prepare_for_import(self, selected):
        """Prepare only missing first-use artifacts before Native Import.

        RC18 multi-import rule:
        - already-ready sources are reused with zero I/O;
        - local EPGManager generators remain sequential to protect RAM/CPU;
        - missing remote catalogues are probed in a small bounded pool;
        - SRP map publication remains sequential because the shared SRP index is
          a single-writer structure;
        - mapping ownership is read-only throughout this stage.

        This gives the user a real multi-source prepare path without ever
        running several Native EPGImport writers against eEPGCache at once.
        """
        selected = list(selected or [])
        report = {"checked": len(selected), "prepared": 0, "already_ready": 0,
                  "failed": 0, "rows": [], "parallel_remote_workers": 0}
        if not selected:
            return report

        local_needed = any(str((x or {}).get("kind") or "").lower() == "local" for x in selected)
        if local_needed:
            try:
                self.manager.ensure_sources_registered()
            except Exception:
                log.exception("Could not lazy-load local EPGManager sources for import auto-prepare")

        total = len(selected)
        completed = 0
        local_missing = []
        remote_missing = []

        # First pass is cache-only and cheap.  It also means a batch containing
        # ten sources but only two first-use feeds performs network work for two.
        for item in selected:
            sid = str((item or {}).get("id") or "")
            name = str((item or {}).get("name") or sid)
            before = self._import_artifact_state(item)
            # rc36.4: grouped feeds are full receiver XML artefacts. On first
            # import, download the complete group file and publish its SRP map
            # instead of preparing only a transient remote channel header.
            if item.get("grouped_feed") and not before.get("ready"):
                old_force = self.force_download
                try:
                    self.force_download = not bool(before.get("xml_ready"))
                    grouped_result = self._run_external(item)
                finally:
                    self.force_download = old_force
                before = self._import_artifact_state(item)
                if before.get("ready") and (grouped_result or {}).get("ok"):
                    report["prepared"] += 1
                    report["rows"].append({"id": sid, "name": name,
                                           "action": "grouped-full-xml", "ok": True,
                                           "detail": str((grouped_result or {}).get("path") or "READY")})
                    completed += 1
                    continue
            if before.get("ready"):
                report["already_ready"] += 1
                report["rows"].append({"id": sid, "name": name, "action": "reuse", "ok": True})
                completed += 1
                continue
            if str((item or {}).get("kind") or "").lower() == "local":
                local_missing.append((item, before))
            else:
                remote_missing.append((item, before))

        # Local HTML/scraper sources intentionally stay serial.  On small Vu+
        # receivers, parallel parser stacks cost more RAM/CPU than they save.
        for item, before in local_missing:
            sid = str((item or {}).get("id") or "")
            name = str((item or {}).get("name") or sid)
            pct = 2 + int(52.0 * completed / max(1, total))
            self._progress(pct, "%s  •  %s" % (name, before.get("reason") or "preparing"), "AUTO PREPARE")
            ok = False
            detail = ""
            try:
                if not before.get("xml_ready"):
                    old_force = self.force_download
                    try:
                        self.force_download = True
                        result = self._run_local(item)
                    finally:
                        self.force_download = old_force
                    detail = str((result or {}).get("error") or (result or {}).get("warning") or "")
                row = source_readiness.probe_local(
                    item, self.config.get_epg_output_dir(), prepare_map=True)
                after = self._import_artifact_state(item)
                ok = bool(after.get("ready"))
                if not detail:
                    detail = str((row or {}).get("state") or "")
            except Exception as exc:
                detail = str(exc)
                log.exception("Automatic import preparation failed for %s", name)

            report["rows"].append({"id": sid, "name": name, "action": "auto-refresh",
                                   "ok": bool(ok), "detail": detail})
            if ok:
                report["prepared"] += 1
            else:
                report["failed"] += 1
            completed += 1

        # RC20: first-use remote preparation is CHANNEL-HEADER ONLY.  Import All
        # never needs a programme-quality sample just to construct a missing SRP
        # map.  Reuse an existing compact catalogue with zero network I/O; only
        # truly missing catalogues are harvested concurrently and parsing stops at
        # the first <programme> whenever the feed exposes normal <channel> rows.
        remote_probe = {}
        remote_network = []
        for item, before in remote_missing:
            sid = str((item or {}).get("id") or "")
            if before.get("catalog_ready"):
                remote_probe[sid] = {"item": item, "before": before,
                                     "row": {"state": "CACHED CHANNEL IDS"}, "error": ""}
            else:
                remote_network.append((item, before))

        if remote_network:
            try:
                configured = (self.config.get_effective_parallel_workers()
                              if hasattr(self.config, "get_effective_parallel_workers") else 2)
            except Exception:
                configured = 2
            # Header harvesting is light I/O and does not touch eEPGCache.  Six
            # bounded workers are safe on Zero 4K and drastically shorten first use.
            workers = max(1, min(6, int(configured or 2), len(remote_network)))
            report["parallel_remote_workers"] = int(workers)
            self._progress(2 + int(52.0 * completed / max(1, total)),
                           "%d new remote map(s) • %d fast header worker(s)" % (len(remote_network), workers),
                           "FAST MAP PREPARE")

            def _probe_remote_first_use(item):
                return source_readiness.harvest_remote_catalog_fast(
                    item, timeout=self.config.get_timeout_seconds())

            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(_probe_remote_first_use, item): (item, before)
                               for item, before in remote_network}
                    for future in as_completed(futures):
                        item, before = futures[future]
                        sid = str((item or {}).get("id") or "")
                        name = str((item or {}).get("name") or sid)
                        try:
                            row = future.result() or {}
                            remote_probe[sid] = {"item": item, "before": before, "row": row, "error": ""}
                        except Exception as exc:
                            remote_probe[sid] = {"item": item, "before": before, "row": {}, "error": str(exc)}
                            log.exception("Fast first-use catalogue harvest failed for %s", name)
                        completed += 1
                        pct = 2 + int(42.0 * completed / max(1, total))
                        self._progress(pct, "%d / %d source(s) prepared" % (completed, total), "FAST MAP PREPARE")
            except Exception:
                log.exception("Fast remote catalogue pool failed")

        # Publish only missing SRP maps, one at a time, from the compact channel
        # shards created above.  No second provider download is needed.
        remote_done = 0
        for item, before in remote_missing:
            sid = str((item or {}).get("id") or "")
            name = str((item or {}).get("name") or sid)
            result = remote_probe.get(sid) or {"row": {}, "error": "Remote preparation did not complete"}
            row = dict(result.get("row") or {})
            detail = str(result.get("error") or row.get("state") or "")
            ok = False
            mapped_refs = 0
            catalog = None
            try:
                after = self._import_artifact_state(item)
                if after.get("catalog_ready") and not after.get("map_ready"):
                    channels = source_channel_cache.get(sid)
                    if channels:
                        self._progress(46 + int(14.0 * remote_done / max(1, len(remote_missing))),
                                       "%s  •  publishing map" % name, "MAP CACHE")
                        catalog = source_readiness._write_temporary_catalog(sid, channels)
                        map_ok, mapped_refs = source_readiness._prepare_map(
                            item, catalog, self.config.get_epg_output_dir())
                        if map_ok:
                            cached = source_readiness.get(sid) or row
                            cached["map_ready"] = True
                            cached["mapped_refs"] = max(int(cached.get("mapped_refs") or 0), int(mapped_refs or 0))
                            if int(cached.get("programmes") or 0) > 0 and not bool(cached.get("placeholder")):
                                cached["state"] = "READY"
                                cached["ready"] = True
                            source_readiness.put(sid, cached)
                after = self._import_artifact_state(item)
                ok = bool(after.get("ready"))
                if not detail:
                    detail = "READY" if ok else str(after.get("reason") or "Not ready")
            except Exception as exc:
                detail = str(exc)
                log.exception("Sequential SRP publication failed for %s", name)
            finally:
                if catalog:
                    try:
                        os.unlink(catalog)
                    except Exception:
                        pass

            report["rows"].append({"id": sid, "name": name, "action": "parallel-auto-refresh",
                                   "ok": bool(ok), "detail": detail, "mapped_refs": int(mapped_refs or 0)})
            if ok:
                report["prepared"] += 1
            else:
                report["failed"] += 1
            remote_done += 1

        return report

    @staticmethod
    def _routing_pending(mid):
        marker = BETA96_ROUTING_PENDING.get(str(mid or "").lower())
        return bool(marker and os.path.exists(marker))

    @staticmethod
    def _mark_routing_repaired(mid):
        mid = str(mid or "").lower()
        pending = BETA96_ROUTING_PENDING.get(mid)
        done = BETA96_ROUTING_DONE.get(mid)
        try:
            if pending and os.path.exists(pending):
                os.unlink(pending)
        except OSError:
            pass
        try:
            if done:
                parent = os.path.dirname(done)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent)
                with open(done, "a"):
                    os.utime(done, None)
        except Exception:
            pass

    @staticmethod
    def _restore_manual_overrides(mid):
        """Re-apply user-owned mappings after a generated cache rebuild.

        beta96 must replace two stale automatic source maps, but Smart Mapping
        manual decisions belong to the user and must survive that migration.
        Explicit blocked refs are also pruned from the rebuilt generated map.
        """
        mid = str(mid or "").lower()
        if not mid:
            return
        store = MappingStore()
        for key, rec in (store.all() or {}).items():
            if "::" not in str(key):
                continue
            sid, cid = str(key).split("::", 1)
            if sid.lower() != mid or str((rec or {}).get("mode") or "").lower() != "manual":
                continue
            refs = list((rec or {}).get("refs") or [])
            srp_channel_map.apply_mapping_override(mid, cid, refs, remove=not bool(refs))

        # Respect manual UNMAP tombstones too. They are receiver-ref scoped.
        blocked = list(store.blocked_refs() or [])
        if not blocked:
            return
        row = (srp_channel_map.load_index().get("sources") or {}).get(mid) or {}
        path = str((row or {}).get("map_path") or "")
        if not path or not os.path.isfile(path):
            return
        mapping = srp_channel_map._parse_channels_xml_preserve(path)
        changed = False
        for cid in list(mapping.keys()):
            before = list(mapping.get(cid) or [])
            after = [ref for ref in before if not store.is_blocked(ref)]
            if after != before:
                changed = True
            if after:
                mapping[cid] = after
            else:
                mapping.pop(cid, None)
        if changed:
            srp_channel_map.write_channels_xml(mapping, path)

    def _repair_pending_routing(self, selected, results):
        pending = []
        for item in selected or []:
            mid = str(source_catalog.mapping_source_id(item) or "").lower()
            if self._routing_pending(mid) and (results.get(item.get("id")) or {}).get("ok"):
                pending.append(item)
        if not pending:
            return {"sources": 0, "routing_repair": False}
        self._progress(38, "One-time beIN routing repair", "ROUTING REPAIR")
        report = srp_channel_map.prepare_selected_maps(
            pending, results, self.preferences, self.config.get_epg_output_dir(),
            include_iptv=True, progress_cb=self._map_progress)
        index = srp_channel_map.load_index().get("sources") or {}
        built = []
        for item in pending:
            mid = str(source_catalog.mapping_source_id(item) or "").lower()
            row = index.get(mid) or {}
            map_path = str((row or {}).get("map_path") or "")
            if map_path and os.path.isfile(map_path):
                self._restore_manual_overrides(mid)
                built.append(mid)
        repaired = []
        if built:
            # Run the provider-aware layer against the freshly rebuilt maps.
            # force=True bypasses an old beta95 coverage signature.
            try:
                cov = sat_coverage.enrich_selected_maps(
                    pending, results, self.config.get_epg_output_dir(),
                    progress_cb=self._map_progress, force=True)
                report["coverage"] = cov
                if (cov or {}).get("ok"):
                    post_index = srp_channel_map.load_index().get("sources") or {}
                    for mid in built:
                        row = post_index.get(mid) or {}
                        map_path = str((row or {}).get("map_path") or "")
                        if map_path and os.path.isfile(map_path) and int((row or {}).get("refs") or 0) > 0:
                            self._routing_repair_mids.add(mid)
                            repaired.append(mid)
                            self._mark_routing_repaired(mid)
            except Exception as exc:
                log.exception("beta96 routing repair coverage failed")
                report["coverage"] = {"ok": False, "error": str(exc)}
        report["routing_repair"] = True
        report["built"] = built
        report["repaired"] = repaired
        return report

    def _missing_map_items(self, selected, results):
        """Return feeds needing the persistent beta1 mapping cache.

        Daily XMLTV programme refreshes do not invalidate the map.  It is
        rebuilt when the receiver lamedb changes, after a beta schema change,
        or explicitly from Smart Mapping.
        """
        index = srp_channel_map.load_index()
        direct = index.get("sources") or {}
        if int(index.get("version") or 0) < 10:
            return list(selected or [])
        current_sat_sig = channel_registry.satellite_registry_signature()
        missing = []
        for item in selected or []:
            mid = source_catalog.mapping_source_id(item)
            if self._routing_pending(mid):
                missing.append(item)
                continue
            row = direct.get(mid) or direct.get(str(mid).lower()) or {}
            map_path = str(row.get("map_path") or "")
            xml_path = str((results.get(item.get("id")) or {}).get("path") or "")
            stale_sat = False
            if not bool((item or {}).get("native_srp")) and not bool((row or {}).get("official")):
                stale_sat = (row.get("sat_signature") != current_sat_sig)
            local_xml_missing = bool(item.get("kind") == "local" and not xml_path)
            if not map_path or not os.path.isfile(map_path) or local_xml_missing or stale_sat:
                missing.append(item)
        return missing

    def _build_missing_maps(self, selected, results):
        missing = self._missing_map_items(selected, results)
        if not missing:
            return {"sources": 0, "mapped_channel_ids": 0, "mapped_service_refs": 0,
                    "unmapped_channel_ids": 0, "elapsed": 0.0, "mode": "cached_beta1_mapping",
                    "cache_reused": True}
        self._progress(20, "Creating mapping cache for %d feed(s)" % len(missing), "MAP CACHE")
        local_missing = [x for x in missing if x.get("kind") == "local"]
        remote_missing = [x for x in missing if x.get("kind") != "local"]
        reports = []
        if local_missing:
            reports.append(srp_channel_map.prepare_selected_maps(
                local_missing, results, self.preferences, self.config.get_epg_output_dir(),
                include_iptv=True, progress_cb=self._map_progress))
        for item in remote_missing:
            reports.append(source_readiness.prepare_remote_map(
                item, self.config.get_epg_output_dir(), force=True,
                timeout=self.config.get_timeout_seconds(), progress_cb=self._map_progress))
        merged = {"sources": 0, "mapped_channel_ids": 0, "mapped_service_refs": 0,
                  "unmapped_channel_ids": 0, "elapsed": 0.0,
                  "mode": "remote-url-authoritative"}
        for report in reports:
            report = report or {}
            for key in ("sources", "mapped_channel_ids", "mapped_service_refs", "unmapped_channel_ids"):
                merged[key] += int(report.get(key) or 0)
            merged["elapsed"] += float(report.get("elapsed") or 0.0)
        return merged

    def _map_progress(self, info):
        phase = str((info or {}).get("phase") or "BETA1 MAP")
        cur = int((info or {}).get("current") or 0)
        total = int((info or {}).get("total") or 0)
        detail = str((info or {}).get("detail") or "")
        # The receiver registry is only touched when a mapping really has to be
        # built/refreshed.  Keep the wording explicit; normal Import All never
        # enters this path after the one-time beta17 audit.
        if "reading reception lists" in detail.lower():
            detail = detail.replace("reading Reception Lists", "refreshing receiver channels")
            detail = detail.replace("Reading Reception Lists", "Refreshing receiver channels")
        fraction = (float(cur) / float(total)) if total > 0 else 0.0
        base = 20 if self.import_only else 42
        span = 45 if self.import_only else 32
        self._progress(base + int(span * fraction), detail or phase, "MAP CACHE" if self.import_only else "BETA1 MAP")

    def _worker(self):
        selected = self._selected_rows()
        ids = [x.get("id") for x in selected]
        self.manager._begin_job(ids)
        if not selected:
            self._finalize(False, "No country/source selected")
            return
        self.source_total = len(selected)
        self.source_completed = 0

        # RC9 import contract: Import Source / Import All stays read-only for
        # already-prepared mappings, but a first-use source must not require a
        # separate GREEN Refresh. Missing source-scoped artifacts are prepared
        # automatically, once, immediately before Native Import.  Existing maps
        # are never rebuilt here and MappingStore ownership is never rewritten.
        if self.import_only:
            # rc36.8: Import All always refreshes the selected direct XMLTV feeds
            # from GitHub into /etc/epgmanager_epg before Native Import.
            direct_sync = self._sync_direct_xml_for_import(selected)
            self._direct_xml_sync = direct_sync
            failed_ids = set(str((r or {}).get("id") or "") for r in
                             (direct_sync or {}).get("rows", []) if not (r or {}).get("ok"))
            ready_selected = [x for x in selected if str((x or {}).get("id") or "") not in failed_ids]
            if not ready_selected:
                self._finalize(False, "GitHub XML download failed for all selected sources",
                               extra={"direct_xml_sync": direct_sync})
                return
            self._progress(28, "Checking ServiceRef maps for fresh XML feeds", "AUTO PREPARE")
            auto_prepare = self._auto_prepare_for_import(ready_selected)
            self._service_map_repair = {
                "ok": not bool(int((auto_prepare or {}).get("failed") or 0)),
                "needed": int((auto_prepare or {}).get("prepared") or 0) + int((auto_prepare or {}).get("failed") or 0),
                "remaining": int((auto_prepare or {}).get("failed") or 0),
                "cache_reused": not bool(int((auto_prepare or {}).get("prepared") or 0)),
                "mapping_locked": True,
                "auto_prepare": auto_prepare,
            }
            if int((auto_prepare or {}).get("prepared") or 0):
                self._progress(62, "%d source(s) auto-prepared" % int(auto_prepare.get("prepared") or 0), "PREPARE")
            else:
                self._progress(62, "Prepared source cache reused", "PREPARE")
            self._start_direct_native_on_main_thread(ready_selected)
            return
        if self.cache_only:
            if self.import_only:
                self._progress(3, "Importing selected EPG sources", "IMPORT ALL")
            else:
                self._progress(3, "Using cached XMLTV for %d feed(s)" % len(selected), "LOCAL CACHE")
        else:
            self._progress(3, "Downloading/generating %d feed(s)" % len(selected), "SOURCES")
        results = {}
        if self.cache_only:
            if self.import_only:
                # Local generators are deliberately NOT run here. They are
                # prepared by the lightweight 06:00 maintenance job. Internet
                # feeds, however, are fetched live exactly like Native
                # EPGImport/Rytec, so Import All never waits for background
                # cache preparation.
                local_items = [x for x in selected if x.get("kind") == "local"]
                remote_items = [x for x in selected if x.get("kind") != "local"]
                for item in local_items:
                    sid = item.get("id")
                    results[sid] = self._run_cached(item)
                    self.manager._mark_job_complete(sid)
                    self.source_completed += 1
                    pct = 3 + int(22.0 * self.source_completed / max(1, self.source_total))
                    self._progress(pct, "%d / %d source(s) ready" % (self.source_completed, self.source_total), "IMPORT ALL")
                if remote_items:
                    workers = max(1, min(4, self.config.get_effective_parallel_workers() if hasattr(self.config, "get_effective_parallel_workers") else 2, len(remote_items)))
                    try:
                        with ThreadPoolExecutor(max_workers=workers) as executor:
                            futures = {executor.submit(self._run_external_live, item): item for item in remote_items}
                            for future in as_completed(futures):
                                item = futures[future]
                                sid = item.get("id")
                                try:
                                    results[sid] = future.result()
                                except Exception as exc:
                                    results[sid] = {"ok": False, "error": str(exc)}
                                self.manager._mark_job_complete(sid)
                                self.source_completed += 1
                                pct = 3 + int(22.0 * self.source_completed / max(1, self.source_total))
                                self._progress(pct, "%d / %d source(s) ready" % (self.source_completed, self.source_total), "IMPORT ALL")
                    except Exception as exc:
                        log.exception("Live remote source stage failed")
                ready = [x for x in selected if (results.get(x.get("id")) or {}).get("ok")]
                skipped = [x for x in selected if not (results.get(x.get("id")) or {}).get("ok")]
                self._skipped_import_sources = [str(x.get("name") or x.get("id")) for x in skipped]
                if not ready:
                    names = ", ".join(self._skipped_import_sources[:5])
                    self._finalize(False, "No selected EPG source is currently importable%s" % ((": " + names) if names else ""),
                                   extra={"downloads": results})
                    return
            else:
                for item in selected:
                    sid = item.get("id")
                    results[sid] = self._run_cached(item)
                    self.manager._mark_job_complete(sid)
                    self.source_completed += 1
                    pct = 3 + int(35.0 * self.source_completed / max(1, self.source_total))
                    self._progress(pct, "%d / %d cached feed(s) ready" % (self.source_completed, self.source_total), "LOCAL CACHE")
        # Vu+ low-memory rule: local web scrapers are Python/HTML heavy and
        # some of them already use their own small internal worker pool.
        # Running several local scrapers in parallel multiplies RAM/CPU and
        # can make Enigma2 unresponsive.  Keep local bundles sequential; pure
        # pre-built XMLTV downloads may use at most two workers.
        if not self.cache_only:
            local_items = [x for x in selected if x.get("kind") == "local"]
            remote_items = [x for x in selected if x.get("kind") != "local"]
            if local_items and not self.manager.ensure_sources_registered():
                self._finalize(False, "Could not load local EPG source modules")
                return
            try:
                # Local HTML scrapers remain sequential: parallelising them is a
                # bad trade on Vu+ Zero 4K (RAM spikes + Python CPU contention).
                for item in local_items:
                    sid=item.get("id")
                    try: results[sid]=self._run_local(item)
                    except Exception as exc:
                        log.exception("Cycle local source failed: %s", item.get("name")); results[sid]={"ok":False,"error":str(exc)}
                    self.manager._mark_job_complete(sid); self.source_completed += 1
                    pct = 3 + int(35.0 * self.source_completed / max(1, self.source_total))
                    self._progress(pct, "%d / %d source feed(s) ready" % (self.source_completed, self.source_total), "SOURCES")

                # Pre-built remote XMLTV feeds are I/O bound.  Use the selected
                # performance-mode budget (up to four workers); local HTML
                # generators remain protected/sequential.
                if remote_items:
                    configured = self.config.get_effective_parallel_workers() if hasattr(self.config, "get_effective_parallel_workers") else self.config.get_parallel_workers()
                    workers = max(1, min(4, int(configured or 2), len(remote_items)))
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        futures={executor.submit(self._run_external,item):item for item in remote_items}
                        for future in as_completed(futures):
                            item=futures[future]; sid=item.get("id")
                            try: results[sid]=future.result()
                            except Exception as exc:
                                log.exception("Cycle remote source failed: %s", item.get("name")); results[sid]={"ok":False,"error":str(exc)}
                            self.manager._mark_job_complete(sid); self.source_completed += 1
                            pct = 3 + int(35.0 * self.source_completed / max(1, self.source_total))
                            self._progress(pct, "%d / %d source feed(s) ready" % (self.source_completed, self.source_total), "SOURCES")
            except Exception as exc:
                log.exception("EPG cycle source stage failed")
                self._finalize(False, str(exc))
                return

            self.config.set_last_update(time.time())
            ok_count = sum(1 for r in results.values() if r.get("ok"))
            fail_count = len(results) - ok_count
            activity_store.record_download(
                sources=len(results), ok=ok_count, failed=fail_count,
                fallback=sum(1 for r in results.values() if r.get("fallback")),
                unchanged=sum(1 for r in results.values() if r.get("unchanged")),
                channels=sum(int(r.get("channels") or 0) for r in results.values()),
                programmes=sum(int(r.get("programmes") or 0) for r in results.values()),
                elapsed=max(0.0, time.time() - self.started_at))

        if self.update_only:
            # A successful refresh is the cheapest moment to migrate the two
            # affected generated maps; the next Native Import then stays fast.
            try:
                self._repair_pending_routing(selected, results)
            except Exception:
                log.exception("beta96 routing repair after source refresh failed")
            ok_count = sum(1 for row in results.values() if (row or {}).get("ok"))
            total_count = len(results)
            kept_count = sum(1 for row in results.values() if (row or {}).get("kept_cache"))
            stale_count = sum(1 for row in results.values() if (row or {}).get("stale_cache"))
            fresh_count = sum(1 for row in results.values()
                              if (row or {}).get("refreshed",
                                  bool((row or {}).get("ok") and not (row or {}).get("kept_cache") and not (row or {}).get("stale_cache"))))
            # Keeping a recent Last Known Good cache is operationally safe, but
            # a stale cache is preserved only for recovery and must never be
            # advertised as usable/importable EPG.
            ok = bool(ok_count > 0)
            if total_count == 1 and stale_count == 1:
                one = next(iter(results.values())) or {}
                warning = str(one.get("warning") or one.get("error") or "Existing cache is stale.")
                message = "Fresh guide unavailable; stale cache preserved but not used. %s" % warning
                ok = False
            elif total_count == 1 and kept_count == 1:
                one = next(iter(results.values())) or {}
                warning = str(one.get("warning") or "New feed is incomplete; Last Known Good kept.")
                message = "No complete new EPG yet; recent Last Known Good kept. %s" % warning
            else:
                message = "%d fresh, %d cache kept, %d stale, %d/%d usable" % (fresh_count, kept_count, stale_count, ok_count, total_count)
            self._finalize(ok, message, extra={"downloads": results, "sources_ok": ok_count, "sources_total": total_count,
                                                "sources_fresh": fresh_count, "sources_kept": kept_count,
                                                "sources_stale": stale_count})
            return

        # Normal imports reuse persistent channels.xml mappings. Mapping is
        # generated with the beta1 golden engine only once for feeds that have no cache yet; Smart Mapping
        # can later refine/override it without forcing a rebuild on every import.
        ready_selected = [x for x in selected if (results.get(x.get("id")) or {}).get("ok")]
        try:
            map_report = self._build_missing_maps(ready_selected, results)
        except Exception as exc:
            log.exception("Persistent mapping cache preparation failed")
            self._finalize(False, "Mapping cache failed: %s" % exc, extra={"downloads": results})
            return

        # Provider-aware SAT coverage is *not* part of the normal Import All
        # fast path.  Run it only when a source/receiver map was genuinely
        # rebuilt, or once after upgrading to beta17 so old wrong dispatches
        # (beIN France on 7W, TV5->MBC5, etc.) can be repaired.
        # Subsequent imports jump directly to Native EPGImport.
        maps_changed = bool(int((map_report or {}).get("sources") or 0))
        full_import = str(self.scope_label or "").upper() in ("FAST IMPORT", "FULL EPG")
        audit_pending = full_import and os.path.exists(BETA17_AUDIT_PENDING)
        if maps_changed or audit_pending:
            try:
                self._progress(66 if self.import_only else 75,
                               "One-time provider/channel routing audit" if audit_pending else "Updating new SAT mappings",
                               "SAT ROUTING")
                coverage_report = sat_coverage.enrich_selected_maps(
                    ready_selected, results, self.config.get_epg_output_dir(),
                    progress_cb=self._map_progress, force=bool(audit_pending or self._routing_repair_mids))
                if isinstance(map_report, dict):
                    map_report["coverage"] = coverage_report
                if audit_pending and (coverage_report or {}).get("ok"):
                    try:
                        os.unlink(BETA17_AUDIT_PENDING)
                    except OSError:
                        pass
            except Exception as exc:
                log.exception("SAT provider routing failed; continuing with beta1 maps")
                if isinstance(map_report, dict):
                    map_report["coverage"] = {"ok": False, "error": str(exc)}
        elif isinstance(map_report, dict):
            map_report["coverage"] = {
                "ok": True, "skipped": True, "cache_reused": True,
                "reason": "unchanged receiver/source mappings"
            }

        if not self.config.get_native_import_after_sync():
            self._finalize(True, "EPG cache ready", extra={"downloads": results, "mapping": map_report})
            return
        self._progress(70 if self.import_only else 77, "Starting Native EPG Import", "NATIVE IMPORT")
        self._start_native_on_main_thread(results, map_report, ready_selected)

    def _start_direct_native_on_main_thread(self, selected):
        """Low-impact Native import.

        All file parsing, channels.xml merging and priority arbitration are
        prepared on the existing EPGCycle worker thread.  Only the tiny
        eEPGCache/EPGImport ``beginImport`` hand-off is posted to Enigma's main
        thread.  This removes the most common pre-import spinner on Vu+ boxes.
        """
        try:
            from .direct_native_import import DirectEPGImportRunner
            # Do not reset the visible job to 3% after AUTO PREPARE has already
            # completed.  RC19 made a long import look frozen even when it had
            # progressed.  Direct-plan construction is the final pre-import stage.
            self._progress(64, "Building fast receiver-only import plan", "FAST PREPARE")
            runner = DirectEPGImportRunner(
                selected,
                epg_dir=self.config.get_epg_output_dir(),
                only_iptv=self.config.get_native_import_only_iptv(),
                long_desc_days=self.config.get_native_long_desc_days(),
                clear_before_import=self.config.get_native_clear_before_import(),
                on_done=lambda result: self._native_done(result, {}, {"mode":"direct_import_low_impact"}))
            sources, stats = runner.prepare()
            if not sources:
                skipped = list(stats.get("skipped_source_names") or [])
                self._finalize(False,
                    "No mapped source is ready yet" + ((": " + ", ".join(skipped[:4])) if skipped else ""),
                    extra={"import_prepare": stats, "skipped_sources": skipped})
                return
            self._skipped_import_sources = list(stats.get("skipped_source_names") or [])
            self.import_runner = runner
            self.manager._native_auto_runner = runner
            self._progress(5, "Native EPGImport ready", "NATIVE IMPORT")
        except Exception as exc:
            log.exception("Direct Native Import preparation failed")
            self._finalize(False, "Native Import preparation failed: %s" % exc)
            return

        def start_native():
            try:
                runner.start()
            except Exception as exc:
                log.exception("Direct Native Import could not start")
                self._finalize(False, "Native Import failed to start: %s" % exc)
        try:
            from twisted.internet import reactor
            reactor.callFromThread(start_native)
        except Exception as exc:
            self._finalize(False, "Enigma/Twisted reactor unavailable: %s" % exc)

    def _start_native_on_main_thread(self, downloads, map_report, ready_selected=None):
        # Same low-impact rule for the non-direct/full-cycle bridge: parsing
        # source files/maps happens here on the cycle worker, not in Enigma UI.
        try:
            from .native_epgimport_bridge import EPGImportLocalRunner
            self._progress(78, "Preparing Native Import off the UI thread", "NATIVE IMPORT")
            runner = EPGImportLocalRunner(
                epg_dir=self.config.get_epg_output_dir(), only_iptv=self.config.get_native_import_only_iptv(),
                long_desc_days=self.config.get_native_long_desc_days(), clear_before_import=self.config.get_native_clear_before_import(),
                source_ids=[source_catalog.mapping_source_id(x) for x in (ready_selected or [])],
                on_done=lambda result: self._native_done(result, downloads, map_report))
            sources, stats = runner.prepare()
            if not sources:
                self._finalize(False, "No Service Name map produced an importable source",
                               extra={"downloads": downloads, "mapping": map_report, "import_prepare": stats})
                return
            self.import_runner = runner
            self.manager._native_auto_runner = runner
        except Exception as exc:
            log.exception("Native Import preparation failed")
            self._finalize(False, "Native Import preparation failed: %s" % exc,
                           extra={"downloads": downloads, "mapping": map_report})
            return

        def start_native():
            try:
                runner.start()
            except Exception as exc:
                log.exception("Native Import stage could not start")
                self._finalize(False, "Native Import failed to start: %s" % exc,
                               extra={"downloads": downloads, "mapping": map_report})
        try:
            from twisted.internet import reactor
            reactor.callFromThread(start_native)
        except Exception as exc:
            self._finalize(False, "Enigma/Twisted reactor unavailable: %s" % exc,
                           extra={"downloads": downloads, "mapping": map_report})

    def _native_done(self, import_result, downloads, map_report):
        ok = bool((import_result or {}).get("ok"))
        # Only a successful import advances the success timestamp. Failed or
        # zero-event scheduled imports must remain retryable.
        if ok:
            self.config.set_last_native_import(time.time())
        # beta125: lamedb/source signature drift refreshes persistent maps only
        # AFTER the current import. This is deliberately non-blocking: the
        # existing channels.xml map was already filtered fail-closed by the
        # native importer against the live receiver/PrecisionMatch policy.
        try:
            repair = dict(getattr(self, "_service_map_repair", {}) or {})
            if repair.get("background_refresh"):
                srp_master_engine.rebuild_async(
                    config=self.config, epg_dir=self.config.get_epg_output_dir(), force=False)
                log.info("Service-map cache reused; incremental SRP refresh scheduled in background")
        except Exception:
            log.exception("Could not schedule background Service-map refresh")
        self._progress(100, "%d event(s) imported" % int((import_result or {}).get("imported_events") or 0), "DONE" if ok else "ERROR")
        skipped = list(getattr(self, "_skipped_import_sources", []) or [])
        if ok and skipped:
            message = "EPG import completed; skipped unavailable: %s" % ", ".join(skipped[:4])
        else:
            message = "EPG import completed" if ok else "Native Import completed with no events"
        self._finalize(ok, message,
                       extra={"downloads": downloads, "mapping": map_report, "import": import_result,
                              "skipped_sources": skipped,
                              "direct_xml_sync": dict(getattr(self, "_direct_xml_sync", {}) or {})})

    def _finalize(self, ok, message, extra=None):
        self.running = False
        self.done = True
        self.stage = "DONE" if ok else "ERROR"
        self.error = None if ok else str(message)
        if ok:
            self.progress_percent = 100
        self.manager._busy = False
        self.manager._finish_job()
        result = {"ok": bool(ok), "message": str(message), "stage": self.stage,
                  "scope": self.scope_label, "elapsed": round(max(0.0, time.time() - (self.started_at or time.time())), 2)}
        if extra:
            result.update(extra)
        self.result = result
        activity_store.record_cycle(ok=bool(ok), message=str(message), stage=self.stage, elapsed=result["elapsed"])
        callback = self.on_complete
        if callback:
            try:
                callback(result)
            except Exception:
                log.exception("EPG cycle completion callback failed")
        # Do not pin the completed import engine / mapping report in RAM.
        # Dashboard state is persisted in activity_store and is enough after
        # completion. This matters on low-memory Vu+ receivers.
        self.on_complete = None
        self.import_runner = None
        self._thread = None
        if getattr(self.manager, "_native_auto_runner", None) is not None:
            self.manager._native_auto_runner = None
        if getattr(self.manager, "_cycle_runner", None) is self:
            self.manager._cycle_runner = None
        try:
            gc.collect()
        except Exception:
            pass

    def status(self):
        percent = int(self.progress_percent or 0)
        detail = self.progress_detail
        native = None
        if self.running and self.stage == "NATIVE IMPORT" and self.import_runner is not None:
            try:
                native = self.import_runner.status()
                total = int(native.get("total_sources") or 0)
                index = int(native.get("source_index") or 0)
                if total > 0:
                    native_base = 5 if self.import_only else 77
                    percent = max(percent, min(99, native_base + int((99-native_base) * index / total)))
                src = native.get("source_name") or "EPG Import"
                detail = "%s  •  %d event(s) imported" % (src, int(native.get("imported_events") or 0))
            except Exception:
                native = None
        return {"running": self.running, "done": self.done, "stage": self.stage, "error": self.error,
                "elapsed": max(0, int(time.time() - (self.started_at or time.time()))), "result": self.result,
                "percent": max(0, min(100, percent)), "detail": detail,
                "source_completed": int(self.source_completed), "source_total": int(self.source_total),
                "native": native}
