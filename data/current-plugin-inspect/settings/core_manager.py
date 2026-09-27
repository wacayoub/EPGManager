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
MIN_SOURCE_COOLDOWN_SECONDS = 20 * 60  # 20 minutes


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
    def __init__(self, config=None):
        self.config = config or Config()
        self._sources = {}          # source_id -> EPGSource instance
        self._status = {}           # source_id -> SourceStatus
        self._lock = threading.Lock()
        self._busy = False
        self._executor = None
        self._cancel_event = threading.Event()
        self._last_attempt = {}     # source_id -> epoch seconds of last attempt start
        self._job_source_ids = []
        self._job_completed = set()
        self._job_started_at = None
        self._job_finished_at = None

    # -- registry -------------------------------------------------------
    def register(self, source):
        self._sources[source.id] = source
        self._status[source.id] = SourceStatus(source.id, source.name)
        log.debug("Registered source '%s' (%s)", source.id, source.name)

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
        return {
            "active": bool(self._busy),
            "total": total,
            "completed": completed,
            "running": running,
            "percent": max(0, min(100, percent)),
            "elapsed_seconds": elapsed,
        }

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

    # -- update entrypoints (always async) -------------------------------
    def update_all_async(self, on_complete=None):
        if self._busy:
            log.warning("update_all_async called while an update is already running - ignored")
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_all, args=(on_complete,), daemon=True)
        t.start()
        return True

    def update_source_async(self, source_id, on_complete=None):
        if self._busy:
            log.warning("update_source_async called while an update is already running - ignored")
            return False
        if source_id not in self._sources:
            log.error("Unknown source id '%s'", source_id)
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_one_wrapper, args=(source_id, on_complete), daemon=True)
        t.start()
        return True

    def update_selected_async(self, source_ids, on_complete=None):
        """Update exactly the requested source IDs in one coordinated job.

        This avoids launching several independent threads from the GUI: each
        call shares one cancellation event, one busy state and one bounded
        ThreadPoolExecutor. It is the correct implementation for the
        dashboard's "selected sources" action.
        """
        if self._busy:
            log.warning("update_selected_async called while an update is already running - ignored")
            return False
        ids = [sid for sid in source_ids if sid in self._sources and self.config.is_source_enabled(sid)]
        if not ids:
            log.info("No enabled sources selected for update")
            return False
        self._cancel_event = threading.Event()
        t = threading.Thread(target=self._run_selected, args=(ids, on_complete), daemon=True)
        t.start()
        return True

    # -- internal worker-thread bodies -----------------------------------

    def _native_import_after_update_if_enabled(self, results):
        """Run standalone native EPG import after a successful update cycle.

        This is intentionally opt-in and never runs merely by opening Smart
        Mapping.  It mirrors EPG-Import's automatic import behaviour while
        keeping EPG Manager standalone.
        """
        try:
            if not self.config.get_auto_import_after_update():
                return
            if not any(bool(v) for v in (results or {}).values()):
                log.info("Native auto-import skipped: no source update succeeded")
                return
            from . import native_importer
            plan = native_importer.build_import_plan(
                self.config.get_epg_output_dir(),
                only_iptv=self.config.get_native_import_only_iptv(),
                long_desc_days=self.config.get_native_long_desc_days())
            if not plan.get("events_ready"):
                log.warning("Native auto-import skipped: no mapped events ready")
                return
            result = native_importer.import_plan(
                plan, clear_before_import=self.config.get_native_clear_before_import())
            log.info("Native auto-import complete: %d events / %d services",
                     result.get("imported_events", 0), result.get("imported_services", 0))
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

        workers = max(1, min(8, self.config.get_parallel_workers()))
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

    def _run_selected(self, source_ids, on_complete):
        self._busy = True
        self._begin_job(source_ids)
        results = {}
        try:
            workers = max(1, min(8, self.config.get_parallel_workers()))
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
            self._native_import_after_update_if_enabled(results)
            if on_complete:
                try:
                    on_complete(results)
                except Exception:
                    log.exception("selected-update callback raised")

    def _run_one_wrapper(self, source_id, on_complete):
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
        self._native_import_after_update_if_enabled({source_id: ok})
        if on_complete:
            try:
                on_complete({source_id: ok})
            except Exception:
                log.exception("on_complete callback raised")

    def _run_one(self, source):
        """Run exactly one source's update(), fully isolated from the
        others. Never raises - always returns True/False and updates
        self._status[source.id] so the GUI can poll it."""
        status = self._status[source.id]

        with self._lock:
            last_attempt = self._last_attempt.get(source.id)
            if last_attempt is not None:
                elapsed = time.time() - last_attempt
                if elapsed < MIN_SOURCE_COOLDOWN_SECONDS:
                    remaining_min = int((MIN_SOURCE_COOLDOWN_SECONDS - elapsed) / 60) + 1
                    status.status = STATUS_COOLDOWN
                    status.error_message = (
                        "On cooldown - attempted %d minute(s) ago. Waiting "
                        "%d more minute(s) to avoid getting the site to "
                        "rate-limit/block us. Change 'Update interval' or "
                        "wait it out; this protects sources like SNRT that "
                        "have blocked repeated rapid requests before." %
                        (int(elapsed / 60), remaining_min)
                    )
                    log.warning("[%s] Skipped - %s", source.id, status.error_message)
                    return False
            self._last_attempt[source.id] = time.time()
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
                    log.info("[%s] CANCELLED after %.1fs", source.id, duration)
                    return False
                elif result and getattr(result, "ok", False):
                    status.status = STATUS_SUCCESS
                    log.info("[%s] SUCCESS - %d programmes in %.1fs",
                              source.id, status.program_count, duration)
                    return True
                else:
                    status.status = STATUS_WARNING
                    status.error_message = getattr(
                        result, "message", "Update produced no valid data")
                    log.warning("[%s] WARNING - %s", source.id, status.error_message)
                    return False

        except Exception as e:
            duration = time.time() - start
            with self._lock:
                status.status = STATUS_FAILED
                status.duration_seconds = round(duration, 1)
                status.error_message = str(e)
            log.exception("[%s] FAILED after %.1fs", source.id, duration)
            return False

    def run_storage_check(self, watch_paths, max_total_mb=50):
        monitor = StorageMonitor(watch_paths, max_total_mb=max_total_mb)
        return monitor.check_and_warn()
