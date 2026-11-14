# -*- coding: utf-8 -*-
"""Low-impact automatic maintenance of selected EPG source caches.

Design goals for Vu+ / Enigma2:
- Import All remains the only path that fetches full remote programme XMLTV.
- Remote channel catalogues are never pre-warmed automatically.
- Smart Mapping navigation itself never starts network I/O.
- Exactly one low-priority catalogue/source job runs at a time and yields to UI.
- Local EPG Manager generators run once per day at 06:00 (configurable).
- Remote Channel-ID preparation is explicit (Refresh Source), not background work.
- A failed local refresh keeps the previous XML and is backed off before retry.
"""
from __future__ import print_function
import os
import time
from datetime import timedelta

from .logger import get_logger
from .scheduler import eTimer, connect_timer
from .source_preferences import SourcePreferences
from . import (source_catalog, external_sources, source_state, srp_channel_map,
               channel_mapper, source_channel_cache, sat_coverage, ui_activity, remote_channel_catalog,
               source_channel_sync, openepg_id_catalog)

log = get_logger(__name__)

POLL_INTERVAL_MS = 5 * 60 * 1000
STARTUP_QUIET_SECONDS = 35
NEXT_SOURCE_DELAY_MS = 25 * 1000
DAILY_TIMER_MIN_MS = 1000
ERROR_RETRY_SECONDS = 2 * 60 * 60
COVERAGE_PENDING = "/etc/enigma2/.epgmanager_beta18_coverage_pending"


class BackgroundSourceManager(object):
    def __init__(self, config, manager_factory):
        self.config = config
        self.manager_factory = manager_factory
        self._poll = eTimer()
        self._kick = eTimer()
        self._daily = eTimer()
        connect_timer(self._poll, self._tick)
        connect_timer(self._kick, self._tick)
        connect_timer(self._daily, self._daily_tick)
        self._quiet_until = time.time() + STARTUP_QUIET_SECONDS
        self._active_source = None
        self._stopped = False

    def start(self):
        self._stopped = False
        self._poll.start(POLL_INTERVAL_MS, False)
        # 8.1.5 boot-safety: never catch up source refresh just because Enigma2
        # restarted after the daily slot. A receiver restart must stay CPU/network
        # quiet. The exact next daily slot is still armed below; manual Refresh
        # Source / Import All remain available at any time.
        self._arm_daily_slot()
        try:
            hh, mm = self.config.get_background_local_time()
        except Exception:
            hh, mm = 6, 0
        log.info("Background source manager armed (daily local refresh %02d:%02d Casablanca; no startup catch-up; remote catalogues are explicit)", int(hh), int(mm))

    def stop(self):
        self._stopped = True
        try: self._poll.stop()
        except Exception: pass
        try: self._kick.stop()
        except Exception: pass
        try: self._daily.stop()
        except Exception: pass

    def _enabled(self):
        # Honour the visible Settings toggle.  Local generator maintenance is
        # enabled by default, but a user who disables it must not still get
        # hidden network work at 06:00.
        try:
            return bool(self.config.get_background_source_updates())
        except Exception:
            return True

    def _next_local_slot(self, now_dt):
        """Return the next configured Casablanca refresh slot after now_dt."""
        slot = self._local_slot(now_dt)
        if now_dt < slot:
            return slot
        tomorrow = now_dt + timedelta(days=1)
        return self._local_slot(tomorrow)

    def _arm_daily_slot(self):
        """Arm a one-shot timer for the next exact local refresh slot.

        The 5-minute poll remains as a safety net and startup catch-up path,
        but this timer means a continuously-running receiver no longer relies
        on an arbitrary polling phase to notice 06:00.
        """
        if self._stopped or not self._enabled():
            return
        try:
            from .timezone_manager import TimezoneManager
            now_dt = TimezoneManager().now()
            target = self._next_local_slot(now_dt)
            delay_seconds = max(1, int(max(0.0, target.timestamp() - time.time())))
            self._daily.stop()
            # OpenATV's native eTimer exposes startLongTimer(seconds), which
            # avoids very large millisecond values for day-scale timers.
            if hasattr(self._daily, "startLongTimer"):
                self._daily.startLongTimer(delay_seconds)
            else:
                self._daily.start(max(DAILY_TIMER_MIN_MS, delay_seconds * 1000), True)
            log.info("Next automatic local-source refresh armed for %s", target)
        except Exception:
            log.exception("Could not arm exact daily local-source timer; periodic poll remains active")

    def _daily_tick(self):
        if self._stopped:
            return
        try:
            self._tick()
        finally:
            # Always arm tomorrow, even when today's job was deferred because
            # the GUI/manager was busy. The short kick/poll handles the defer.
            self._arm_daily_slot()

    def _epg_dir(self):
        try: return self.config.get_epg_output_dir()
        except Exception: return "/etc/epgmanager_epg"

    def _local_slot(self, now):
        try:
            hh, mm = self.config.get_background_local_time()
        except Exception:
            hh, mm = 6, 0
        return now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)

    def _source_path(self, item):
        epg_dir = self._epg_dir()
        if item.get("kind") == "local":
            filename = str(item.get("output_file") or "")
            return os.path.join(epg_dir, filename) if filename else None
        # External providers are remote-stream only; no receiver-side XML path.
        return None

    def _retry_seconds(self, item, manager=None):
        """Source-aware retry delay after a non-fresh background result."""
        try:
            mid = str(item.get("manager_id") or source_catalog.mapping_source_id(item) or "")
            src = getattr(manager, "_sources", {}).get(mid) if manager is not None else None
            value = int(getattr(src, "background_retry_seconds", 0) or 0)
            if value > 0:
                return value
        except Exception:
            pass
        return ERROR_RETRY_SECONDS

    @staticmethod
    def _mtime(path):
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) > 32:
                return float(os.path.getmtime(path))
        except Exception:
            pass
        return None

    def _local_due(self, item, now_dt, now_epoch, state):
        slot = self._local_slot(now_dt)
        day_key = now_dt.strftime("%Y-%m-%d")
        if now_dt < slot:
            return False, float(slot.timestamp()), "%02d:%02d" % (slot.hour, slot.minute)

        # A persisted daily marker is authoritative.  It prevents a second
        # scheduled refresh when XML content is unchanged or its mtime is
        # restored by a provider/source guard.
        if str((state or {}).get("daily_refresh_day") or "") == day_key:
            return False, float(self._next_local_slot(now_dt).timestamp()), "%02d:%02d" % (slot.hour, slot.minute)

        # Do not infer a successful daily refresh from XML mtime. A restored
        # Last Known Good or a guard rollback can update mtime without proving
        # that today's provider refresh succeeded. Only a genuine accepted
        # refresh writes daily_refresh_day in done().
        retry_after = float((state or {}).get("retry_after") or 0)
        if retry_after > now_epoch:
            return False, retry_after, "%02d:%02d" % (slot.hour, slot.minute)
        # Startup after 06:00 intentionally catches up here.
        return True, now_epoch, "%02d:%02d" % (slot.hour, slot.minute)

    def _external_due(self, item, now_epoch, state):
        path = self._source_path(item)
        mtime = self._mtime(path)
        ttl = int(external_sources.cache_ttl_seconds(item))
        if mtime is None:
            retry_after = float((state or {}).get("retry_after") or 0)
            if retry_after > now_epoch:
                return False, retry_after, "AUTO"
            return True, now_epoch, "AUTO"
        next_due = mtime + ttl
        if now_epoch < next_due:
            return False, next_due, "AUTO"
        retry_after = float((state or {}).get("retry_after") or 0)
        if retry_after > now_epoch:
            return False, retry_after, "AUTO"
        return True, now_epoch, "AUTO"

    def _local_catalog_needed(self, item):
        if not item or item.get("kind") != "local":
            return False
        sid = str(item.get("id") or "")
        if source_channel_cache.has(sid):
            return False
        path = self._source_path(item)
        try:
            return bool(path and os.path.isfile(path) and os.path.getsize(path) > 32)
        except Exception:
            return False

    def _prepare_local_catalog(self, item, done_cb):
        sid = str(item.get("id") or "")
        mid = source_catalog.mapping_source_id(item)
        started = time.time()
        try:
            path = self._source_path(item)
            rows = channel_mapper.read_xmltv_channel_ids_fast(path, mid, item.get("name") or mid)
            source_channel_cache.put(sid, item.get("name") or sid, rows,
                provider=item.get("provider") or "", region=item.get("region") or "",
                language=item.get("language") or "")
            class OneSelection(object):
                def selected_mapping_ids(self_inner): return set([mid])
                def selected_catalogue_ids(self_inner): return set([sid])
            if not srp_channel_map.map_path_for_source(mid):
                srp_channel_map.prepare_selected_maps([item], {sid:{"ok":True,"path":path}}, OneSelection(),
                    self._epg_dir(), include_iptv=True, progress_cb=None)
            try:
                sat_coverage.enrich_selected_maps([item], {sid:{"ok":True,"path":path}},
                    self._epg_dir(), progress_cb=None, force=False)
            except Exception:
                log.warning("Local provider-aware coverage failed for %s", item.get("name") or sid, exc_info=True)
            source_state.update(sid, catalog_channels=len(rows), catalog_ready=True,
                                last_catalog=int(time.time()), error="")
            done_cb({"ok":True, "channels":len(rows), "elapsed":max(0.0,time.time()-started)})
        except Exception as exc:
            source_state.update(sid, catalog_ready=False, error=str(exc),
                                retry_after=int(time.time()) + ERROR_RETRY_SECONDS)
            done_cb({"ok":False, "error":str(exc)})

    def _remote_catalog_needed(self, item):
        """True only when a selected remote feed has no compact ID catalogue.

        beta80 deliberately separates *catalogue preparation* from SRP/EPG
        preparation.  Missing SRP maps are handled by explicit import/update
        paths; the idle worker never downloads a full programme guide merely to
        make Smart Mapping searchable.
        """
        if not item or item.get("kind") == "local":
            return False
        sid = str(item.get("id") or "")
        if not sid:
            return False
        try:
            return not source_channel_cache.has(sid)
        except Exception:
            return True

    def _prepare_remote_catalog(self, item, manager, done_cb):
        """Pre-warm one selected remote source without downloading programme EPG.

        Preferred order:
        1. provider's tiny ``catalog_url`` (.txt/id list);
        2. streamed XMLTV channel declaration header;
        3. bounded streamed sample only for legacy feeds (not persisted).

        No full XMLTV file is written, no SRP rebuild is started and no
        provider-wide coverage scan runs here.  This keeps the catalogue ready
        *before* Smart Mapping needs it without making EPG Region navigation
        responsible for network work.
        """
        sid = str(item.get("id") or "")
        started = time.time()
        try:
            # External catalogues always come from the concrete source XML URL.
            # No provider text catalogue and no receiver-side XML fallback.
            rows, meta = remote_channel_catalog.fetch(
                item, timeout=min(9, max(5, int(self.config.get_timeout_seconds()))),
                max_scan=6 * 1024 * 1024)
            source_channel_cache.put(
                sid, item.get("name") or sid, rows,
                provider=item.get("provider") or "",
                region=item.get("region") or "",
                language=item.get("language") or "")
            source_state.update(
                sid, status="CATALOG READY", auto="CATALOG ONLY",
                catalog_channels=len(rows), catalog_ready=True,
                last_catalog=int(time.time()), error="", retry_after=None)
            done_cb({"ok": True, "channels": len(rows),
                     "catalog_only": True, "meta": meta or {},
                     "elapsed": max(0.0, time.time() - started)})
        except source_channel_sync.SyncDeferred as exc:
            # UI/import activity is not a failure; retry after the receiver is idle.
            source_state.update(sid, status="WAITING", auto="CATALOG ONLY",
                                error="", retry_after=int(time.time()) + 45)
            done_cb({"ok": False, "deferred": True, "error": str(exc)})
        except Exception as exc:
            log.warning("Background channel catalogue failed for %s: %s", item.get("name") or sid, exc)
            source_state.update(sid, status="CATALOG WAIT", auto="CATALOG ONLY",
                                catalog_ready=False, error=str(exc),
                                retry_after=int(time.time()) + ERROR_RETRY_SECONDS)
            done_cb({"ok": False, "error": str(exc)})

    def _due_candidates(self):
        try:
            from .timezone_manager import TimezoneManager
            now_dt = TimezoneManager().now()
        except Exception:
            from datetime import datetime
            now_dt = datetime.now()
        now_epoch = time.time()
        prefs = SourcePreferences()
        selected = set(prefs.selected_catalogue_ids())
        if not selected:
            return []
        by_id = source_catalog.by_catalogue_id()
        state_rows = source_state.all_rows()
        due = []
        for sid in selected:
            item = by_id.get(sid)
            if not item:
                continue
            state = state_rows.get(sid) or {}
            if item.get("kind") != "local":
                # beta81 cleanup: never pre-warm a remote catalogue in the
                # background.  The receiver stays idle and predictable; a
                # concrete feed is prepared only after the user explicitly
                # Refreshes it (Smart Sources / Smart Mapping YELLOW).
                source_state.update(sid, next_due=None, auto="ON-DEMAND",
                                    status="CATALOG READY" if source_channel_cache.has(sid) else "ON-DEMAND")
                continue
            if self._local_catalog_needed(item):
                cat_item = dict(item)
                cat_item["_catalog_only"] = True
                due.append((-9, now_epoch, source_catalog.source_sort_key(item), cat_item))
                continue
            is_due, next_due, mode = self._local_due(item, now_dt, now_epoch, state)
            priority = 0
            source_state.update(sid, next_due=int(next_due), auto=mode)
            if is_due:
                due.append((priority, float(next_due), source_catalog.source_sort_key(item), item))
        due.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in due]

    def _run_pending_global_coverage(self):
        # beta21: never run a receiver-wide provider/language audit in the
        # session-start background service. Import/Smart Mapping remain explicit
        # user actions and the proven beta18 SAT maps stay untouched.
        return False

    def _tick(self):
        if self._stopped or not self._enabled():
            return
        # beta79: scheduled/local maintenance yields to remote-control UI usage.
        if ui_activity.active():
            try:
                self._kick.start(15000, True)
            except Exception:
                pass
            return
        if time.time() < self._quiet_until:
            return
        if self._active_source:
            return
        try:
            due = self._due_candidates()
        except Exception:
            log.exception("Background source due-check failed")
            return
        if not due:
            if self._run_pending_global_coverage():
                return
            return
        item = due[0]
        sid = str(item.get("id") or "")
        try:
            manager = self.manager_factory()
        except Exception:
            log.exception("Background source manager could not initialize Manager")
            return
        if manager.is_busy():
            log.debug("Background source %s deferred: EPG Manager is busy", sid)
            return
        self._active_source = sid
        is_remote_catalog = bool(item.get("_remote_catalog_only"))
        is_local_catalog = bool(item.get("_catalog_only"))
        source_state.update(sid, status=("DIRECT" if is_remote_catalog else ("READY" if is_local_catalog else "SYNCING")), last_attempt=int(time.time()), error="")
        log.info("Background %s starting: %s", "channel catalogue" if is_remote_catalog else "source refresh", item.get("name") or sid)

        def done(result):
            result = result or {}
            ok = bool(result.get("ok"))
            now = int(time.time())
            fresh_count = int(result.get("sources_fresh") or 0)
            kept_count = int(result.get("sources_kept") or 0)
            stale_count = int(result.get("sources_stale") or 0)
            # Catalogue-only jobs do not expose source counters; their plain
            # ok result is a genuinely fresh operation.
            genuinely_fresh = bool(ok and (is_remote_catalog or is_local_catalog or fresh_count > 0))
            if genuinely_fresh:
                path = self._source_path(item)
                stamp = int(now if is_remote_catalog else (self._mtime(path) or now))
                extra_state = {}
                if item.get("kind") == "local" and not is_local_catalog:
                    try:
                        from .timezone_manager import TimezoneManager
                        local_now = TimezoneManager().now()
                        extra_state = {"daily_refresh_day": local_now.strftime("%Y-%m-%d"),
                                       "daily_refresh_at": int(now)}
                    except Exception:
                        extra_state = {"daily_refresh_at": int(now)}
                source_state.update(sid, status=("CATALOG READY" if is_remote_catalog else "READY"), last_success=stamp,
                                    retry_after=None, error="", **extra_state)
                log.info("Background source refresh complete: %s", item.get("name") or sid)
            else:
                message = str(result.get("message") or result.get("error") or "Update produced no fresh EPG")
                retry = self._retry_seconds(item, manager)
                state = "STALE" if stale_count else ("WAITING" if kept_count or ok else ("DIRECT" if is_remote_catalog else "ERROR"))
                source_state.update(sid, status=state, error=message, retry_after=now + retry)
                log.warning("Background source refresh not fresh: %s - %s (retry in %ds)",
                            item.get("name") or sid, message, retry)
            self._active_source = None
            self._schedule_next()

        if is_remote_catalog:
            import threading
            threading.Thread(target=self._prepare_remote_catalog, args=(item, manager, done), daemon=True).start()
            started = True
        elif is_local_catalog:
            import threading
            threading.Thread(target=self._prepare_local_catalog, args=(item, done), daemon=True).start()
            started = True
        else:
            # A scheduled 06:00 job is a real refresh, not a cache probe.  Force
            # the local generator so the generic one-hour duplicate guard cannot
            # turn today's maintenance into a false "fresh" result.
            started = manager.run_catalogue_update_only_async(sid, on_complete=done, force_download=True)
        if not started:
            self._active_source = None
            source_state.update(sid, status="WAITING", error="Manager busy")

    def _schedule_next(self):
        """Ask main Enigma/Twisted thread to check the next due source soon."""
        if self._stopped:
            return
        def arm():
            try:
                self._kick.stop()
                self._kick.start(NEXT_SOURCE_DELAY_MS, True)
            except Exception:
                pass
        try:
            from twisted.internet import reactor
            reactor.callFromThread(arm)
        except Exception:
            # Periodic timer remains as a safe fallback.
            pass

    def status(self):
        return {
            "enabled": self._enabled(),
            "active_source": self._active_source,
            "quiet": max(0, int(self._quiet_until - time.time())),
        }
