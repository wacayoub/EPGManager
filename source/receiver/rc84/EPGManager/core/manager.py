# -*- coding: utf-8 -*-
"""
core/manager.py

The orchestrator. Owns the source registry, runs updates in a background
thread pool (never on the GUI thread), tracks per-source status, and is the
single object the plugin.py / ui/*.py screens talk to.

Threading model (spec section 12/13):
  - update_all() / update_source() ALWAYS spawn a worker thread and return
    immediately. GUI callbacks are marshalled back via Enigma2's own
    thread-safe callback queue (a plain threading.Thread + a shared status
    dict, polled by the GUI's own eTimer - the standard, safe pattern for
    Enigma2 plugins; we do not touch any GUI widget from the worker thread
    directly).
  - A bounded ThreadPoolExecutor runs multiple sources concurrently, sized
    conservatively (default 4, configurable, capped at 8) since Vu+ boxes
    have limited CPU/RAM (spec section 13).
  - One source's exception NEVER aborts the others (spec section 11):
    every source's update() call is individually wrapped in try/except.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .logger import get_logger
from .config import Config
from .cache import StorageMonitor

log = get_logger(__name__)

STATUS_IDLE = "IDLE"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_WARNING = "WARNING"
STATUS_CANCELLED = "CANCELLED"
STATUS_COOLDOWN = "COOLDOWN"

# Minimum time between two attempts on the SAME source, regardless of who
# triggers it (scheduler or a manual dashboard click). This exists purely
# as a safety net against site-side rate limiting / IP bans: SNRT in
# particular has been observed to start blocking requests after repeated
# scrapes in a short window. The real fix for runaway auto-retriggering is
# the last_update persistence in _run_all() below; this cooldown is a
# second, independent guard that also protects against a person
# accidentally mashing "Update Selected" on the same source repeatedly.
MIN_SOURCE_COOLDOWN_SECONDS = 60 * 60  # 1 hour after a successful/recent XML refresh
FAILED_SOURCE_RETRY_SECONDS = 45       # failed manual refresh may retry quickly


class SourceStatus(object):
    def __init__(self, source_id, name):
        self.source_id = source_id
        self.name = name
        self.status = STATUS_IDLE
        self.last_run = None
        self.duration_seconds = None
        self.program_count = 0
        self.error_message = None
        self.days_covered = 0
        self.date_from = None
        self.date_to = None

    def to_dict(self):
        return {
            "id": self.source_id,
            "name": self.name,
            "status": self.status,
            "last_run": self.last_run,
            "duration_seconds": self.duration_seconds,
            "program_count": self.program_count,
            "error_message": self.error_message,
            "days_covered": self.days_covered,
            "date_from": self.date_from.strftime("%d/%m") if self.date_from else None,
            "date_to": self.date_to.strftime("%d/%m") if self.date_to else None,
        }


class Manager(object):
    def __init__(self, config=None, source_loader=None):
        self.config = config or Config()
        self._source_loader = source_loader
        self._sources_loaded = False
        self._sources = {}          # source_id -> EPGSource instance
        self._status = {}           # source_id -> SourceStatus
        self._lock = threading.Lock()
        self._busy = False
        self._executor = None
        self._cancel_event = threading.Event()
        self._last_attempt = {}     # source_id -> epoch seconds of last attempt start
        self._last_outcome = {}     # source_id -> success/warning/failed/cancelled
        self._job_source_ids = []
        self._job_completed = set()
        self._job_started_at = None
        self._job_finished_at = None
        self._native_auto_runner = None
        self._cycle_runner = None

    # -- registry -------------------------------------------------------
    def register(self, source):
        self._sources[source.id] = source
        self._status[source.id] = SourceStatus(source.id, source.name)
        log.debug("Registered source '%s' (%s)", source.id, source.name)

    def ensure_sources_registered(self):
        """Load scraper modules only when a source job actually needs them.

        Merely opening EPG Manager on a Vu+ must not import BeautifulSoup/HTTP
        scraper stacks and keep them resident for the rest of the Enigma2
        session.  The source loader is supplied by plugin.py and is invoked at
        most once, immediately before a local source run.
        """
        if self._sources_loaded:
            return True
        loader = self._source_loader
        if loader is None:
            self._sources_loaded = True
            return True
        try:
            for source in loader() or []:
                self.register(source)
            self._sources_loaded = True
            log.info("Lazy-loaded %d EPG Manager source module(s)", len(self._sources))
            return True
        except Exception:
            log.exception("Could not lazy-load EPG Manager sources")
            return False

    def get_status_all(self):
        with self._lock:
            return [self._status[sid].to_dict() for sid in self._sources]

    def get_status(self, source_id):
        with self._lock:
            st = self._status.get(source_id)
            return st.to_dict() if st else None

    def is_busy(self):
        return self._busy

    def is_cancelling(self):
        """True only while a cancellation has been requested AND a run is
        still winding down. Used by the dashboard to show a distinct
        "CANCELLING..." status instead of just "UPDATING..."."""
        return self._busy and self._cancel_event.is_set()


    def _begin_job(self, source_ids):
        with self._lock:
            self._job_source_ids = list(source_ids)
            self._job_completed = set()
            self._job_started_at = time.time()
            self._job_finished_at = None

    def _mark_job_complete(self, source_id):
        with self._lock:
            self._job_completed.add(source_id)

    def _finish_job(self):
        with self._lock:
            self._job_finished_at = time.time()

    def get_job_progress(self):
        with self._lock:
            total = len(self._job_source_ids)
            completed = len(self._job_completed)
            started_at = self._job_started_at
            finished_at = self._job_finished_at
            ids = list(self._job_source_ids)
            running = 0
            for sid in ids:
                st = self._status.get(sid)
                if st is not None and st.status == STATUS_RUNNING:
                    running += 1
        percent = int(round((completed * 100.0 / total), 0)) if total else 0
        end = finished_at or time.time()
        elapsed = max(0, int(end - started_at)) if started_at else 0
        progress = {
            "active": bool(self._busy),
            "total": total,
            "completed": completed,
            "running": running,
            "percent": max(0, min(100, percent)),
            "elapsed_seconds": elapsed,
        }
        cycle = getattr(self, "_cycle_runner", None)
        if cycle is not None and getattr(cycle, "running", False):
            progress["stage"] = getattr(cycle, "stage", "EPG CYCLE")
        return progress

    def cancel_update(self):
        """Best-effort cancellation: sets a flag checked by the shared
        Downloader before every request attempt (see core/downloader.py).
        In-flight HTTP requests already sent are NOT interrupted mid-flight
        (Python's requests library has no clean way to abort a socket read
        from another thread) - cancellation takes effect at the next
        natural request boundary, which for these sources (many small
        requests per day/channel/programme) is typically within a second
        or two, not instantly."""
        if not self._busy:
            log.debug("cancel_update() called but no update is running - ignored")
            return False
        log.info("Cancellation requested - will take effect at the next request boundary")
        self._cancel_event.set()
        return True

    # -- v6.3 single automatic EPG cycle --------------------------------
    def run_fast_import_async(self, on_complete=None):
        """Import selected EPG with Native-EPGImport style source handling.

        Internet feeds are fetched directly from their provider at import time
        so the user never has to run a separate Update Source step. Local
        EPGManager generators use their already-prepared 06:00 cache. Existing
        SRP/channel maps are reused and only missing source maps are built.
        """
        if self._busy:
            log.warning("Fast EPG import requested while Manager is busy")
            return False
        from .epg_cycle import EPGCycleRunner
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete,
                                preferences=None, scope_label="FAST IMPORT",
                                cache_only=True, import_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_epg_cycle_async(self, on_complete=None, force_download=False):
        if self._busy:
            log.warning("EPG cycle requested while Manager is busy")
            return False
        from .epg_cycle import EPGCycleRunner
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete, force_download=force_download, scope_label="FULL EPG")
        self._cycle_runner = runner
        return runner.start()

    def run_source_cycle_async(self, country_code, source_key, on_complete=None, force_download=False, cache_only=False):
        """Download/generate, build its cached Service Name channel map and Native Import one logical source only.

        This is the Sources-screen YELLOW action.  It deliberately does not
        touch the other selected countries/sources, so testing Morocco 1 for
        example cannot trigger a full receiver-wide source import.
        """
        if self._busy:
            log.warning("Single-source cycle requested while Manager is busy")
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedSourceSelection
        from . import source_catalog
        try:
            selection = FixedSourceSelection(country_code, source_key)
        except Exception:
            log.exception("Invalid single-source selection %s / %s", country_code, source_key)
            return False
        label = source_catalog.source_option_label(country_code, source_key)
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete, force_download=force_download,
                                preferences=selection, scope_label="SOURCE: %s" % label, cache_only=cache_only)
        self._cycle_runner = runner
        return runner.start()

    def run_cached_source_import_async(self, country_code, source_key, on_complete=None):
        """Fast source import: cached XMLTV + persistent map + Native Import."""
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedSourceSelection
        from . import source_catalog
        try:
            selection = FixedSourceSelection(country_code, source_key)
        except Exception:
            log.exception("Invalid single-source selection %s / %s", country_code, source_key)
            return False
        label = source_catalog.source_option_label(country_code, source_key)
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete, preferences=selection,
                                scope_label="SOURCE IMPORT: %s" % label, cache_only=True, import_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_source_update_only_async(self, country_code, source_key, on_complete=None, force_download=True):
        """Refresh one logical source cache without mapping or Native Import."""
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedSourceSelection
        from . import source_catalog
        try:
            selection = FixedSourceSelection(country_code, source_key)
        except Exception:
            log.exception("Invalid single-source selection %s / %s", country_code, source_key)
            return False
        label = source_catalog.source_option_label(country_code, source_key)
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete, force_download=force_download,
                                preferences=selection, scope_label="SOURCE UPDATE: %s" % label, update_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_catalogue_import_async(self, source_id, on_complete=None):
        """Import exactly one Smart Sources feed.

        Remote feeds are fetched directly from their provider (Native
        EPGImport style); local EPGManager feeds use the prepared local XMLTV
        cache. No other selected source participates.
        """
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedCatalogueSelection
        try:
            selection = FixedCatalogueSelection(source_id)
        except Exception:
            log.exception("Invalid catalogue source %s", source_id)
            return False
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete,
                                preferences=selection, scope_label="SOURCE IMPORT",
                                cache_only=True, import_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_catalogue_batch_import_async(self, source_ids, on_complete=None):
        """Import an explicit group of Smart Sources in one native cycle.

        Remote feeds are fetched with the normal bounded worker pool and then
        passed to one Native EPGImport run.  This is substantially cheaper than
        launching N separate import engines.
        """
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedCatalogueSelectionMany
        try:
            selection = FixedCatalogueSelectionMany(source_ids)
        except Exception:
            log.exception("Invalid batch catalogue selection")
            return False
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete,
                                preferences=selection, scope_label="BATCH IMPORT",
                                cache_only=True, import_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_catalogue_batch_update_only_async(self, source_ids, on_complete=None, force_download=True):
        """Refresh an explicit group of feeds as one bounded background job."""
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedCatalogueSelectionMany
        try:
            selection = FixedCatalogueSelectionMany(source_ids)
        except Exception:
            log.exception("Invalid batch catalogue selection")
            return False
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete, force_download=force_download,
                                preferences=selection, scope_label="BATCH REFRESH", update_only=True)
        self._cycle_runner = runner
        return runner.start()

    def run_catalogue_update_only_async(self, source_id, on_complete=None, force_download=False):
        """Refresh exactly one Smart Sources feed without mapping/import.

        This is the background-maintenance entrypoint. It deliberately uses a
        one-feed selection so provider bundles cannot expand unexpectedly.
        """
        if self._busy:
            return False
        from .epg_cycle import EPGCycleRunner
        from .source_preferences import FixedCatalogueSelection
        from . import source_catalog
        try:
            selection = FixedCatalogueSelection(source_id)
            item = source_catalog.by_catalogue_id().get(str(source_id)) or {}
        except Exception:
            log.exception("Invalid catalogue source %s", source_id)
            return False
        label = str(item.get("name") or source_id)
        runner = EPGCycleRunner(self, self.config, on_complete=on_complete,
                                force_download=force_download, preferences=selection,
                                scope_label="BACKGROUND: %s" % label, update_only=True)
        self._cycle_runner = runner
        return runner.start()

    def get_cycle_status(self):
        runner = getattr(self, "_cycle_runner", None)
        if runner is None:
            return {"running": False, "done": False, "stage": "IDLE", "elapsed": 0}
        return runner.status()

    # -- update entrypoints (always async) -------------------------------
    def update_all_async(self, on_complete=None):
        self.ensure_sources_registered()
        if self._busy:
            log.warning("update_all_async called while an update is already running - ignored")
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_all, args=(on_complete,), daemon=True)
        t.start()
        return True

    def update_source_async(self, source_id, on_complete=None, auto_import=True):
        self.ensure_sources_registered()
        if self._busy:
            log.warning("update_source_async called while an update is already running - ignored")
            return False
        if source_id not in self._sources:
            log.error("Unknown source id '%s'", source_id)
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_one_wrapper, args=(source_id, on_complete, auto_import), daemon=True)
        t.start()
        return True

    def update_selected_async(self, source_ids, on_complete=None, auto_import=True):
        """Update exactly the requested source IDs in one coordinated job.

        This avoids launching several independent threads from the GUI: each
        call shares one cancellation event, one busy state and one bounded
        ThreadPoolExecutor. It is the correct implementation for the
        dashboard's "selected sources" action.
        """
        self.ensure_sources_registered()
        if self._busy:
            log.warning("update_selected_async called while an update is already running - ignored")
            return False
        ids = [sid for sid in source_ids if sid in self._sources and self.config.is_source_enabled(sid)]
        if not ids:
            log.info("No enabled sources selected for update")
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_selected, args=(ids, on_complete, auto_import), daemon=True)
        t.start()
        return True

    # -- internal worker-thread bodies -----------------------------------

    def _native_import_after_update_if_enabled(self, results):
        """Start local Native Import through OE-Alliance EPG Import.

        Source updates run in Manager worker threads, while EPG Import is built
        around the Enigma/Twisted reactor.  Marshal the import start back to
        the reactor thread instead of calling eEPGCache/importer code directly
        from the source-update worker.
        """
        try:
            if not self.config.get_auto_import_after_update():
                return
            if not any(bool(v) for v in (results or {}).values()):
                log.info("Native auto-import skipped: no source update succeeded")
                return

            def start_native():
                try:
                    current = getattr(self, "_native_auto_runner", None)
                    if current is not None and getattr(current, "running", False):
                        log.info("Native auto-import skipped: an import is already running")
                        return
                    from .native_epgimport_bridge import EPGImportLocalRunner
                    runner = EPGImportLocalRunner(
                        epg_dir=self.config.get_epg_output_dir(),
                        only_iptv=self.config.get_native_import_only_iptv(),
                        long_desc_days=self.config.get_native_long_desc_days(),
                        clear_before_import=self.config.get_native_clear_before_import())
                    sources, stats = runner.prepare()
                    if not sources:
                        log.warning("Native auto-import skipped: no mapped local XMLTV source (%s)", stats)
                        return
                    self._native_auto_runner = runner
                    runner.start()
                    log.info("Native auto-import started with %d mapped local source(s)", len(sources))
                except Exception:
                    log.exception("Native auto-import start failed")

            try:
                from twisted.internet import reactor
                reactor.callFromThread(start_native)
            except Exception:
                # On a real Enigma2 receiver Twisted is present. Do not start
                # the EPG engine unsafely from the source worker if it is not.
                log.exception("Native auto-import could not access Enigma/Twisted reactor")
        except Exception:
            log.exception("Native auto-import after update failed")

    def _run_all(self, on_complete):
        self._busy = True
        enabled = [s for s in self._sources.values()
                   if self.config.is_source_enabled(s.id)]
        self._begin_job([s.id for s in enabled])
        skipped = [s.id for s in self._sources.values() if s not in enabled]
        if skipped:
            log.info("Skipping disabled sources: %s", ", ".join(skipped))

        workers = max(1, min(4, self.config.get_effective_parallel_workers() if hasattr(self.config, "get_effective_parallel_workers") else self.config.get_parallel_workers()))
        log.info("Starting update of %d source(s) with %d parallel worker(s)",
                  len(enabled), workers)

        results = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            self._executor = executor
            futures = {executor.submit(self._run_one, s): s.id for s in enabled}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    results[sid] = future.result()
                except Exception as e:
                    log.exception("Unexpected error running source '%s'", sid)
                    results[sid] = False
                self._mark_job_complete(sid)

        self._executor = None
        self._busy = False
        self._finish_job()

        # CRITICAL: this must happen unconditionally (success or failure)
        # once a full cycle completes. Without it, the scheduler's
        # "is an update due?" check keeps seeing last_update=None forever
        # and re-triggers a brand new full cycle on every 5-minute poll -
        # which is exactly what was hammering SNRT into an IP-level block.
        # Recording "we attempted a cycle just now" is what makes a
        # persistently-failing source back off until the NEXT scheduled
        # cycle instead of being retried every few minutes indefinitely.
        self.config.set_last_update(time.time())

        ok_count = sum(1 for v in results.values() if v)
        log.info("Update cycle finished: %d/%d source(s) succeeded", ok_count, len(enabled))
        self._native_import_after_update_if_enabled(results)

        if on_complete:
            try:
                on_complete(results)
            except Exception:
                log.exception("on_complete callback raised")

    def _run_selected(self, source_ids, on_complete, auto_import=True):
        self._busy = True
        self._begin_job(source_ids)
        results = {}
        try:
            workers = max(1, min(4, self.config.get_effective_parallel_workers() if hasattr(self.config, "get_effective_parallel_workers") else self.config.get_parallel_workers()))
            with ThreadPoolExecutor(max_workers=min(workers, len(source_ids))) as executor:
                self._executor = executor
                futures = {executor.submit(self._run_one, self._sources[sid]): sid for sid in source_ids}
                for future in as_completed(futures):
                    sid = futures[future]
                    try:
                        results[sid] = future.result()
                    except Exception:
                        log.exception("Unexpected error running selected source '%s'", sid)
                        results[sid] = False
                    self._mark_job_complete(sid)
        finally:
            self._executor = None
            self._busy = False
            self._finish_job()
            self.config.set_last_update(time.time())
            if auto_import:
                self._native_import_after_update_if_enabled(results)
            if on_complete:
                try:
                    on_complete(results)
                except Exception:
                    log.exception("selected-update callback raised")

    def _run_one_wrapper(self, source_id, on_complete, auto_import=True):
        self._busy = True
        self._begin_job([source_id])
        try:
            ok = self._run_one(self._sources[source_id])
        finally:
            self._mark_job_complete(source_id)
            self._busy = False
            self._finish_job()
        # BUG FIX 2026-08-09: this used to be missing entirely, so "Last
        # Update" on the dashboard never changed for manual single-source
        # updates (checkbox "Update Selected", the "9" quick-update
        # shortcut, or the Sources screen's per-source update) - only a
        # full scheduler-triggered cycle through _run_all() touched it.
        # Since manual updates are the primary way most people interact
        # with the dashboard, "Last Update: Never" could persist
        # indefinitely even after successful manual runs. Set unconditionally
        # (attempted, not just succeeded) to match _run_all()'s behavior.
        self.config.set_last_update(time.time())
        if auto_import:
            self._native_import_after_update_if_enabled({source_id: ok})
        if on_complete:
            try:
                on_complete({source_id: ok})
            except Exception:
                log.exception("on_complete callback raised")

    def _run_one(self, source, force=False):
        """Run exactly one source's update(), fully isolated from the
        others. Never raises - always returns True/False and updates
        self._status[source.id] so the GUI can poll it."""
        status = self._status[source.id]

        # One-hour duplicate-download guard. Prefer the generated XML mtime so
        # the protection survives GUI closes and Enigma2 restarts. In-memory
        # last_attempt is kept as a second guard while a file does not yet exist.
        now = time.time()
        last_attempt = self._last_attempt.get(source.id)
        try:
            output_path = source.get_output_path(self.config)
            if output_path and __import__('os').path.exists(output_path) and __import__('os').path.getsize(output_path) > 0:
                file_time = __import__('os').path.getmtime(output_path)
                if last_attempt is None or file_time > last_attempt:
                    last_attempt = file_time
        except Exception:
            pass

        with self._lock:
            if last_attempt is not None:
                elapsed = now - last_attempt
                # A successful/recent XML file keeps the one-hour protection.
                # A FAILED/WARNING/CANCELLED attempt gets only a short backoff;
                # otherwise a user who fixes a network/source problem and
                # presses Refresh again appears to have a dead button for an
                # entire hour.
                outcome = str(self._last_outcome.get(source.id) or "").lower()
                failed_before = (outcome in ("warning", "failed", "cancelled") or
                                 status.status in (STATUS_FAILED, STATUS_WARNING, STATUS_CANCELLED))
                if failed_before:
                    cooldown_seconds = FAILED_SOURCE_RETRY_SECONDS
                elif force:
                    cooldown_seconds = int(getattr(source, "manual_refresh_cooldown_seconds", 60) or 60)
                else:
                    cooldown_seconds = int(getattr(source, "refresh_cooldown_seconds", MIN_SOURCE_COOLDOWN_SECONDS) or MIN_SOURCE_COOLDOWN_SECONDS)
                if 0 <= elapsed < cooldown_seconds:
                    remaining = max(1, int(cooldown_seconds - elapsed + 0.999))
                    status.status = STATUS_COOLDOWN
                    status.last_run = last_attempt
                    if failed_before:
                        status.error_message = (
                            "Previous refresh failed; retry available in %d second(s). "
                            "The last known-good XMLTV file is kept." % remaining
                        )
                    else:
                        remaining_min = max(1, int((cooldown_seconds - elapsed + 59) / 60))
                        status.error_message = (
                            "EPG already downloaded recently. Re-download is locked for %d more minute(s). "
                            "The existing XMLTV cache remains available for Native Import and Smart Mapping." % remaining_min
                        )
                    log.info("[%s] Duplicate download skipped - %s", source.id, status.error_message)
                    return False
            self._last_attempt[source.id] = now
            status.status = STATUS_RUNNING
            status.error_message = None

        start = time.time()
        try:
            result = source.update(config=self.config, cancel_event=self._cancel_event)
            duration = time.time() - start

            with self._lock:
                status.last_run = time.time()
                status.duration_seconds = round(duration, 1)
                status.program_count = getattr(result, "program_count", 0)
                status.days_covered = getattr(result, "days_covered", 0)
                status.date_from = getattr(result, "date_from", None)
                status.date_to = getattr(result, "date_to", None)

                if getattr(result, "cancelled", False):
                    status.status = STATUS_CANCELLED
                    status.error_message = "Cancelled by user"
                    self._last_outcome[source.id] = "cancelled"
                    log.info("[%s] CANCELLED after %.1fs", source.id, duration)
                    return False
                elif result and getattr(result, "ok", False):
                    status.status = STATUS_SUCCESS
                    self._last_outcome[source.id] = "success"
                    log.info("[%s] SUCCESS - %d programmes in %.1fs",
                              source.id, status.program_count, duration)
                    return True
                else:
                    status.status = STATUS_WARNING
                    status.error_message = getattr(
                        result, "message", "Update produced no valid data")
                    self._last_outcome[source.id] = "warning"
                    log.warning("[%s] WARNING - %s", source.id, status.error_message)
                    return False

        except Exception as e:
            duration = time.time() - start
            with self._lock:
                status.status = STATUS_FAILED
                status.duration_seconds = round(duration, 1)
                status.error_message = str(e)
                self._last_outcome[source.id] = "failed"
            log.exception("[%s] FAILED after %.1fs", source.id, duration)
            return False

    def run_storage_check(self, watch_paths, max_total_mb=50):
        monitor = StorageMonitor(watch_paths, max_total_mb=max_total_mb)
        return monitor.check_and_warn()
