# -*- coding: utf-8 -*-
"""
core/scheduler.py

Enigma2-native scheduling using eTimer - explicitly NOT cron, NOT a
standalone `python3 script.py` process, per spec section 4.

Flow (spec section 4/35/36):
    Enigma2 starts -> plugin __init__ registers a session/autostart hook
    -> EPGManager.core.manager.Manager is created -> Scheduler.start()
    -> checks last successful update timestamp
    -> if now - last_update >= interval: trigger an update in a background
       thread immediately
    -> regardless, arms an eTimer for the next check (a short poll interval,
       e.g. every 5 minutes) so it can react to interval/settings changes
       without needing to compute one huge far-future delay up front.

This module is importable outside of Enigma2 (e.g. for unit tests / CLI
debug mode) - if `enigma.eTimer` can't be imported, a minimal drop-in
replacement backed by `threading.Timer` is used instead, so core logic is
identical either way.
"""

import time

from .logger import get_logger

log = get_logger(__name__)

try:
    from enigma import eTimer
    _HAVE_ENIGMA = True
except ImportError:
    _HAVE_ENIGMA = False

    import threading

    class eTimer(object):
        """Minimal eTimer look-alike for non-Enigma2 environments (unit
        tests, CLI debug mode). Mimics the subset of the real eTimer API
        that this plugin uses: callback via .timeout.get().append(fn),
        .start(ms, singleShot), .stop()."""

        class _Signal(object):
            def __init__(self):
                self._callbacks = []

            def get(self):
                return self._callbacks

        def __init__(self):
            self.timeout = self._Signal()
            # Current OpenATV exposes ``timer.callback``. Keep the older
            # ``timeout.get()`` signal too so tests cover both APIs.
            self.callback = self.timeout.get()
            self._thread = None
            self._stopped = True

        def start(self, ms, singleShot=False):
            self._stopped = False
            interval = ms / 1000.0

            def _run():
                while not self._stopped:
                    time.sleep(interval)
                    if self._stopped:
                        break
                    for cb in list(self.timeout.get()):
                        try:
                            cb()
                        except Exception:
                            log.exception("Timer callback raised")
                    if singleShot:
                        break

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()

        def startLongTimer(self, seconds):
            self.start(max(1, int(seconds)) * 1000, True)

        def stop(self):
            self._stopped = True


def connect_timer(timer, callback):
    """Attach a callback across current and older Enigma2 eTimer bindings."""
    callbacks = getattr(timer, "callback", None)
    if callbacks is not None:
        if callback not in callbacks:
            callbacks.append(callback)
        return
    signal = getattr(timer, "timeout", None)
    if signal is None:
        raise AttributeError("eTimer exposes neither callback nor timeout")
    callbacks = signal.get()
    if callback not in callbacks:
        callbacks.append(callback)


POLL_INTERVAL_MS = 5 * 60 * 1000  # source scheduler poll; no source/EPG work is done here
NATIVE_IMPORT_POLL_MS = 60 * 1000    # Auto Import accuracy: at most about one minute late
STARTUP_QUIET_SECONDS = 10 * 60       # never start a heavy source cycle just after Enigma2 boot
NATIVE_IMPORT_BOOT_GRACE_SECONDS = 5 * 60
NATIVE_IMPORT_CATCHUP_WINDOW_SECONDS = 3 * 60 * 60
SCHEDULE_LATE_WINDOW_SECONDS = 10 * 60 # no source-cycle catch-up hours later; wait for next real slot


class Scheduler(object):
    def __init__(self, config, on_update_due):
        """
        config: core.config.Config instance (provides update_interval_hours,
                last_update timestamp getter/setter).
        on_update_due: callable() invoked (from the eTimer callback, i.e.
                Enigma2's main thread) when an update should start. The
                caller is responsible for immediately handing the real work
                off to a background thread (see core/manager.py) - the
                scheduler itself never does network I/O.
        """
        self.config = config
        self.on_update_due = on_update_due
        self._timer = eTimer()
        connect_timer(self._timer, self._tick)
        self._update_in_progress = False
        self._startup_blocked_slot = None
        self._interval_not_before = None
        self._quiet_until = time.time() + STARTUP_QUIET_SECONDS

    def start(self):
        mode = self.config.get_schedule_mode()
        if mode == "daily":
            hh, mm = self.config.get_daily_update_time()
            log.info("Scheduler starting (poll every %ds, daily update at %02d:%02d Casablanca time)",
                      POLL_INTERVAL_MS // 1000, hh, mm)
        elif mode == "weekly":
            hh, mm = self.config.get_daily_update_time()
            from .config import WEEKDAY_NAMES
            day_name = WEEKDAY_NAMES[self.config.get_weekly_update_day()]
            log.info("Scheduler starting (poll every %ds, weekly update on %s at %02d:%02d Casablanca time)",
                      POLL_INTERVAL_MS // 1000, day_name, hh, mm)
        elif mode == "monthly":
            hh, mm = self.config.get_daily_update_time()
            log.info("Scheduler starting (poll every %ds, monthly update on day %d at %02d:%02d Casablanca time)",
                      POLL_INTERVAL_MS // 1000, self.config.get_monthly_update_day(), hh, mm)
        else:
            log.info("Scheduler starting (poll every %ds, every %dh since last update)",
                      POLL_INTERVAL_MS // 1000, self.config.get_update_interval_hours())
        # Performance rule: never launch a heavy cycle during Enigma2 boot.
        # If today's/this period's slot was already missed while the box was
        # off, skip that stale slot for this boot. The next real slot will run.
        self._arm_startup_guard()
        self._timer.start(POLL_INTERVAL_MS, False)

    def _arm_startup_guard(self):
        """Prevent catch-up analysis from starting automatically after boot."""
        try:
            mode = self.config.get_schedule_mode()
            if mode in ("daily", "weekly", "monthly"):
                from .timezone_manager import TimezoneManager
                tzm = TimezoneManager()
                now = tzm.now()
                fn = {
                    "daily": self._todays_slot,
                    "weekly": self._this_weeks_slot,
                    "monthly": self._this_months_slot,
                }[mode]
                slot = fn(now)
                if now >= slot:
                    self._startup_blocked_slot = float(slot.timestamp())
                    log.info("Startup catch-up suppressed for already-passed %s slot %s", mode, slot)
            elif mode == "interval":
                if self._is_interval_update_due():
                    hours = max(1, int(self.config.get_update_interval_hours()))
                    self._interval_not_before = time.time() + (hours * 3600)
                    log.info("Startup catch-up suppressed; interval cycle deferred for %dh", hours)
        except Exception:
            log.exception("Could not arm startup scheduler guard")

    def stop(self):
        self._timer.stop()

    def mark_update_started(self):
        self._update_in_progress = True

    def mark_update_finished(self):
        self._update_in_progress = False
        self.config.set_last_update(time.time())

    def mark_update_deferred(self):
        """Release the in-progress guard without advancing last_update.

        Used when the scheduled cycle could not start because another EPG
        Manager job is already running. The next scheduler tick must retry.
        """
        self._update_in_progress = False

    def is_update_due(self):
        try:
            if not self.config.get_download_schedule_enabled():
                return False
        except Exception:
            pass
        mode = self.config.get_schedule_mode()
        if mode == "daily":
            return self._is_scheduled_slot_due(self._todays_slot)
        if mode == "weekly":
            return self._is_scheduled_slot_due(self._this_weeks_slot)
        if mode == "monthly":
            return self._is_scheduled_slot_due(self._this_months_slot)
        return self._is_interval_update_due()

    def _is_interval_update_due(self):
        if self._interval_not_before is not None and time.time() < self._interval_not_before:
            return False
        last = self.config.get_last_update()
        if last is None:
            return True
        interval_seconds = self.config.get_update_interval_hours() * 3600
        return (time.time() - last) >= interval_seconds

    def _todays_slot(self, now):
        hour, minute = self.config.get_daily_update_time()
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _this_weeks_slot(self, now):
        """The most recent occurrence (today or earlier this week) of the
        configured weekday/time - e.g. if today is Wednesday and the
        configured day is Monday, this returns this Monday at HH:MM."""
        from datetime import timedelta
        hour, minute = self.config.get_daily_update_time()
        target_weekday = self.config.get_weekly_update_day()  # 0=Monday
        days_since_target = (now.weekday() - target_weekday) % 7
        slot_date = now - timedelta(days=days_since_target)
        return slot_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _this_months_slot(self, now):
        """The most recent occurrence of the configured day-of-month/time -
        this month if that day has already passed, last month otherwise."""
        hour, minute = self.config.get_daily_update_time()
        target_day = self.config.get_monthly_update_day()  # 1-28, always valid
        slot_this_month = now.replace(day=target_day, hour=hour, minute=minute,
                                        second=0, microsecond=0)
        if now >= slot_this_month:
            return slot_this_month
        # Configured day hasn't happened yet this month - the most recent
        # slot is last month's occurrence.
        prev_month = now.month - 1 or 12
        prev_year = now.year - 1 if now.month == 1 else now.year
        return now.replace(year=prev_year, month=prev_month, day=target_day,
                            hour=hour, minute=minute, second=0, microsecond=0)

    def _is_scheduled_slot_due(self, slot_fn):
        """Shared logic for daily/weekly/monthly: due once the most recent
        scheduled slot has passed AND we haven't already completed an
        update since then - so a box that's off at the scheduled moment
        and boots later still gets that period's update (spec: "run
        automatically", not "run at exactly HH:MM:00 or never")."""
        from .timezone_manager import TimezoneManager
        tzm = TimezoneManager()
        now = tzm.now()
        slot = slot_fn(now)

        if self._startup_blocked_slot is not None:
            try:
                if float(slot.timestamp()) <= float(self._startup_blocked_slot):
                    return False
            except Exception:
                pass

        if now < slot:
            return False
        # Vu+ safety: scheduled mode is an exact-slot job, not a catch-up job.
        # If the box was off/busy and the slot is already old, do not suddenly
        # launch a CPU/RAM-heavy source cycle while the user is watching TV.
        try:
            if (now - slot).total_seconds() > SCHEDULE_LATE_WINDOW_SECONDS:
                return False
        except Exception:
            return False

        last = self.config.get_last_update()
        if last is None:
            return True
        last_dt = tzm.from_timestamp(last)
        return last_dt < slot

    def _tick(self, initial=False):
        # Absolutely no automatic analysis during the first minutes after an
        # Enigma2 restart.  The timer itself is cheap; this guard prevents it
        # from ever starting the source/XMLTV pipeline during boot.
        if time.time() < self._quiet_until:
            return
        if self._update_in_progress:
            log.debug("Scheduler tick skipped: an update is already running")
            return
        if self.is_update_due():
            log.info("Update is due%s - triggering background update",
                      " (startup check)" if initial else "")
            try:
                self.on_update_due()
            except Exception:
                log.exception("on_update_due callback raised")
        else:
            log.debug("Update not due yet (mode=%s)", self.config.get_schedule_mode())


class NativeImportScheduler(object):
    """Separate daily scheduler for Native Import.

    Downloads/scrapers and EPG injection are intentionally independent.  This
    scheduler only starts the already-working local OE-Alliance EPG Import
    bridge at ``native_import_time`` and never performs network I/O.
    """
    def __init__(self, config, on_import_due, startup=True):
        self.config = config
        self.on_import_due = on_import_due
        self._timer = eTimer()
        connect_timer(self._timer, self._tick)
        self._runner = None
        self._startup_blocked_slot = None
        self._not_before = time.time() + (NATIVE_IMPORT_BOOT_GRACE_SECONDS if startup else 0)
        self._retry_not_before = 0.0
        self._attempt_slot = None
        self._failed_attempts = 0
        self._blocked_failure_slot = None

    def start(self):
        try:
            hh, mm = self.config.get_native_import_time()
            enabled = self.config.get_native_import_schedule_enabled()
            log.info("Native Import scheduler starting (enabled=%s, daily %02d:%02d GMT+0)", enabled, hh, mm)
            # Reliability: if the receiver restarted shortly after today's
            # scheduled slot and no successful import was recorded, allow one
            # catch-up after a short boot grace. Very stale slots are skipped.
            from .sync_timing import utc_now
            now = utc_now()
            slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now >= slot:
                late = max(0.0, float(now.timestamp()) - float(slot.timestamp()))
                if late > NATIVE_IMPORT_CATCHUP_WINDOW_SECONDS:
                    self._startup_blocked_slot = float(slot.timestamp())
                    log.info("Native Import stale startup slot skipped: %s (late %.0f min)", slot, late / 60.0)
                else:
                    log.info("Native Import catch-up eligible after %ds boot grace for slot %s",
                             NATIVE_IMPORT_BOOT_GRACE_SECONDS, slot)
        except Exception:
            log.exception("Could not read Native Import scheduler settings")
        self._timer.start(NATIVE_IMPORT_POLL_MS, False)

    def stop(self):
        self._timer.stop()

    def _slot_due(self):
        now_epoch = time.time()
        if now_epoch < self._not_before or now_epoch < self._retry_not_before:
            return False
        try:
            if not self.config.get_native_import_schedule_enabled():
                return False
        except Exception:
            return False
        from .sync_timing import utc_now
        now = utc_now()
        hh, mm = self.config.get_native_import_time()
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot:
            return False
        slot_stamp = float(slot.timestamp())
        if self._attempt_slot != slot_stamp:
            self._attempt_slot = slot_stamp
            self._failed_attempts = 0
            self._retry_not_before = 0.0
            self._blocked_failure_slot = None
        if self._blocked_failure_slot is not None and slot_stamp <= float(self._blocked_failure_slot):
            return False
        if self._startup_blocked_slot is not None:
            try:
                if float(slot.timestamp()) <= float(self._startup_blocked_slot):
                    return False
            except Exception:
                pass
        last = self.config.get_last_native_import()
        if last is None:
            return True
        try:
            return float(last) < slot_stamp
        except Exception:
            return True

    def _record_failure(self, message, result=None):
        """Retry a failed scheduled import without marking the slot successful."""
        self._failed_attempts += 1
        if self._failed_attempts >= 3:
            self._blocked_failure_slot = self._attempt_slot
            self._retry_not_before = 0.0
            log.error("%s; failed 3 times, slot blocked until next day: %s", message, result or {})
        else:
            self._retry_not_before = time.time() + (10 * 60)
            log.warning("%s; retry %d/3 in 10 minutes: %s", message, self._failed_attempts + 1, result or {})

    def _tick(self, initial=False):
        runner = self._runner
        if runner is not None:
            if getattr(runner, "running", False):
                return
            if getattr(runner, "done", False):
                result = getattr(runner, "result", None) or {}
                self._runner = None
                if bool(result.get("ok")):
                    self.config.set_last_native_import(time.time())
                    self._failed_attempts = 0
                    self._retry_not_before = 0.0
                    log.info("Scheduled Native Import finished successfully: %s", result)
                    return
                self._record_failure("Scheduled Native Import failed", result)
                return
            # A runner object that never started should not block forever.
            self._runner = None

        if not self._slot_due():
            return
        try:
            result = self.on_import_due()
            # False means "temporarily busy"; retry on the next scheduler tick.
            if result is False:
                log.info("Scheduled Native Import postponed because EPG Manager is busy")
                return
            # None is a real failure (nothing importable). Do not mark the
            # daily slot successful: retry with the same bounded 3-attempt policy.
            if result is None:
                self._record_failure("Scheduled Native Import had no mapped local XMLTV source")
                return
            self._runner = result
            log.info("Scheduled Native Import started%s", " after boot" if initial else "")
        except Exception as exc:
            log.exception("Scheduled Native Import could not start")
            self._record_failure("Scheduled Native Import could not start", {"error": str(exc)})
