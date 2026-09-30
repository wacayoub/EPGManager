# -*- coding: utf-8 -*-
"""
plugin.py

Enigma2 plugin entrypoint. This is the file Enigma2's PluginComponent
imports; it defines the standard `Plugins()` function every Enigma2 plugin
must expose.

Startup flow (spec section 4/36) - NO CRON, NO manual `python3 script.py`:

    Enigma2 boots
        -> PluginComponent scans Plugins/Extensions/EPGManager/plugin.py
        -> our PluginDescriptor with where=PluginDescriptor.WHERE_SESSIONSTART
           fires autostart(session) once the session exists
        -> autostart() builds core.manager.Manager, registers all sources,
           and starts core.scheduler.Scheduler
        -> Scheduler immediately checks "is an update due?" and, if so,
           kicks off a background update via Manager.update_all_async()
        -> Scheduler then arms an eTimer to keep checking every 5 minutes,
           so it reacts to interval changes without a cron table anywhere.

The extensions-menu entry (WHERE_EXTENSIONSMENU) opens ui.main.EPGManagerMainScreen
so the user can also trigger a manual update / see status / change settings.
"""

import os
import threading
import time
from datetime import datetime, timedelta

from Plugins.Plugin import PluginDescriptor

from .core.logger import configure as configure_logging, get_logger
from .core.config import Config
from .core.manager import Manager
from .version import __version__

log = get_logger(__name__)

_manager = None
_config = None
_auto_timer = None
_auto_busy = False
_auto_last_attempt = 0.0


def _build_sources():
    from .sources.medi1tv import Medi1TVSource
    from .sources.chada_2m import Chada2MSource
    from .sources.snrt import SNRTSource
    from .sources.bein_sports import BeinSportsSource
    from .sources.almajd import AlmajdSource
    from .sources.arryadia import ArryadiaSource

    return [
        Medi1TVSource(), Chada2MSource(), SNRTSource(),
        BeinSportsSource(), AlmajdSource(), ArryadiaSource(),
    ]


def get_manager():
    """Lazily build (once) and return the shared Manager instance. Used by
    both autostart() and every UI screen so they share one status table."""
    global _manager, _config
    if _manager is None:
        _config = Config()
        configure_logging(level=_config.get_logging_level(),
                           debug=_config.get_debug_mode())
        _manager = Manager(config=_config)
        for source in _build_sources():
            _manager.register(source)
        log.info("EPG Manager v%s initialized with %d source(s)",
                  __version__, len(_manager._sources))
    return _manager


def get_config():
    get_manager()  # ensures _config is built
    return _config


def _latest_scheduled_slot(now_dt, config):
    """Return the most recent configured receiver-side sync/import slot."""
    mode = config.get_schedule_mode()
    hour, minute = config.get_daily_update_time()

    if mode == "interval":
        return None

    if mode == "weekly":
        weekday = config.get_weekly_update_day()
        days_back = (now_dt.weekday() - weekday) % 7
        slot = (now_dt - timedelta(days=days_back)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        if slot > now_dt:
            slot -= timedelta(days=7)
        return slot

    if mode == "monthly":
        day = config.get_monthly_update_day()
        slot = now_dt.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if slot > now_dt:
            year = now_dt.year
            month = now_dt.month - 1
            if month < 1:
                month = 12
                year -= 1
            slot = slot.replace(year=year, month=month, day=day)
        return slot

    return now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _direct_auto_due():
    config = get_config()
    if not config.get_auto_import_after_update():
        return False

    now = time.time()
    last = config.get_last_update()
    mode = config.get_schedule_mode()

    if mode == "interval":
        interval = max(1, int(config.get_update_interval_hours())) * 3600
        return last is None or (now - float(last)) >= interval

    slot = _latest_scheduled_slot(datetime.fromtimestamp(now), config)
    if slot is None:
        return False
    slot_ts = time.mktime(slot.timetuple())
    return now >= slot_ts and (last is None or float(last) < slot_ts)


def _run_direct_auto():
    """Sync selected finished XMLTV feeds, then import them into Enigma2.

    Provider websites are never scraped on the receiver. Only completed
    GitHub/online XMLTV files selected in Native Sources are downloaded.
    """
    global _auto_busy, _auto_last_attempt
    if _auto_busy:
        return

    # Avoid hammering remote feeds if the receiver is offline and a due job
    # keeps failing. The scheduler checks every five minutes, but retries a
    # failed cycle at most every thirty minutes.
    now = time.time()
    if _auto_last_attempt and (now - _auto_last_attempt) < 1800:
        return

    _auto_busy = True
    _auto_last_attempt = now

    def worker():
        global _auto_busy
        synced = 0
        failed = []
        try:
            from .core import external_sources, native_importer, source_catalog
            from .core.native_source_store import NativeSourceStore

            config = get_config()
            selected_ids = set(NativeSourceStore().get_selected() or [])
            candidates = []
            for item in source_catalog.all_sources():
                sid = item.get("id")
                if sid not in selected_ids:
                    continue
                if item.get("kind") == "local" or item.get("dynamic"):
                    continue
                candidates.append(item)

            if not candidates:
                log.warning("Automatic EPG skipped: no online XMLTV sources selected")
                return

            epg_dir = config.get_epg_output_dir()
            for item in candidates:
                try:
                    external_sources.download_source(
                        item, epg_dir,
                        retries=max(1, int(config.get_retry_count())),
                        timeout=max(10, int(config.get_timeout_seconds())))
                    synced += 1
                except Exception as exc:
                    failed.append("%s: %s" % (item.get("name") or item.get("id"), exc))
                    log.warning("Automatic feed sync failed for %s: %s", item.get("id"), exc)

            if not synced:
                log.error("Automatic EPG cycle failed: no selected feed could be synced")
                return

            usable_ids = [x.get("id") for x in candidates if x.get("id")]
            if config.get_native_preflight():
                check = native_importer.preflight(
                    epg_dir,
                    source_ids=usable_ids,
                    only_iptv=config.get_native_import_only_iptv(),
                    min_score=config.get_native_ai_threshold())
                if check.get("errors"):
                    raise RuntimeError("Preflight failed: %s" % "; ".join(check.get("errors") or []))

            plan = native_importer.build_import_plan(
                epg_dir,
                min_score=config.get_native_ai_threshold(),
                only_iptv=config.get_native_import_only_iptv(),
                long_desc_days=config.get_native_long_desc_days(),
                source_ids=usable_ids,
                adaptive=config.get_native_ai_match())

            services = plan.get("services") or {}
            if not services:
                log.warning("Automatic EPG import skipped: no mapped events are ready")
                # Sync succeeded, so do not re-download every five minutes.
                config.set_last_update(time.time())
                return

            result = native_importer.import_plan(
                plan,
                clear_before_import=config.get_native_clear_before_import())
            config.set_last_update(time.time())
            log.info(
                "Automatic direct EPG complete: %d feed(s) synced, %d service(s), %d event(s), %d warning(s)",
                synced,
                int(result.get("services") or len(services)),
                int(result.get("events") or 0),
                len(failed))
        except Exception:
            log.exception("Automatic direct EPG sync/import failed")
        finally:
            _auto_busy = False

    threading.Thread(target=worker, daemon=True).start()


def _auto_tick():
    global _auto_timer
    try:
        if _direct_auto_due():
            _run_direct_auto()
    except Exception:
        log.exception("Automatic EPG scheduler tick failed")
    finally:
        try:
            if _auto_timer is not None:
                _auto_timer.start(300000, True)
        except Exception:
            pass


def _start_auto_timer():
    global _auto_timer
    if _auto_timer is not None:
        return
    try:
        from enigma import eTimer
        _auto_timer = eTimer()
        callback = _auto_timer.callback if hasattr(_auto_timer, "callback") else _auto_timer.timeout.get()
        callback.append(_auto_tick)
        # First check shortly after GUI/session startup, then every five minutes.
        _auto_timer.start(30000, True)
        log.info("Automatic direct EPG scheduler armed")
    except Exception:
        log.exception("Could not start automatic direct EPG scheduler")
        _auto_timer = None


def _stop_auto_timer():
    global _auto_timer
    if _auto_timer is None:
        return
    try:
        _auto_timer.stop()
    except Exception:
        pass
    _auto_timer = None


def autostart(reason, **kwargs):
    """Session hook for GitHub direct-feed mode.

    GitHub performs provider scraping. The receiver only synchronizes selected
    finished XMLTV feeds and, when Automatic import EPG is enabled, imports
    them into Enigma2 at the configured schedule.
    """
    if reason == 0:
        get_manager()
        _start_auto_timer()
        log.info("EPG Manager v%s direct-feed auto scheduler active", __version__)
    elif reason == 1:
        _stop_auto_timer()
        log.info("EPG Manager shutdown")



def main(session, **kwargs):
    """Extensions-menu entrypoint - opens the main GUI screen."""
    from .ui.main import EPGManagerMainScreen
    session.open(EPGManagerMainScreen, get_manager(), get_config())


def Plugins(**kwargs):
    icon = "plugin.png"
    return [
        PluginDescriptor(
            name="EPG Manager",
            description="GitHub direct XMLTV feeds, smart mapping and native import.",
            where=PluginDescriptor.WHERE_SESSIONSTART,
            fnc=autostart,
        ),
        PluginDescriptor(
            name="EPG Manager",
            description="Direct XMLTV sync, smart mapping and native Enigma2 import",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            icon=icon,
            fnc=main,
        ),
        PluginDescriptor(
            name="EPG Manager",
            description="Direct XMLTV sync, smart mapping and native Enigma2 import",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon=icon,
            fnc=main,
        ),
    ]
