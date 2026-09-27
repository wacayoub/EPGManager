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

from Plugins.Plugin import PluginDescriptor

from .core.logger import configure as configure_logging, get_logger
from .core.config import Config
from .core.manager import Manager
from .core.scheduler import Scheduler
from .version import __version__

log = get_logger(__name__)

_manager = None
_scheduler = None
_config = None


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


def autostart(reason, **kwargs):
    """Called by Enigma2 at WHERE_SESSIONSTART (reason==0) and shutdown
    (reason==1). Session start is when we arm the no-cron scheduler."""
    global _scheduler
    if reason == 0:
        manager = get_manager()
        config = get_config()

        def _on_update_due():
            scheduler.mark_update_started()

            def _on_complete(results):
                scheduler.mark_update_finished()
                log.info("Scheduled update complete: %s", results)

            manager.update_all_async(on_complete=_on_complete)

        _scheduler = Scheduler(config, on_update_due=_on_update_due)
        _scheduler.start()
        log.info("EPG Manager autostart complete - scheduler armed "
                 "(no cron, no manual script execution required)")
    elif reason == 1:
        if _scheduler:
            _scheduler.stop()
            log.info("EPG Manager scheduler stopped (Enigma2 shutdown)")


def main(session, **kwargs):
    """Extensions-menu entrypoint - opens the main GUI screen."""
    from .ui.main import EPGManagerMainScreen
    session.open(EPGManagerMainScreen, get_manager(), get_config())


def Plugins(**kwargs):
    icon = "plugin.png"
    return [
        PluginDescriptor(
            name="EPG Manager",
            description="Standalone EPG updates for Medi1TV, 2M/Chada, SNRT, "
                        "beIN Sports, Almajd and Arryadia - smart mapping and native import.",
            where=PluginDescriptor.WHERE_SESSIONSTART,
            fnc=autostart,
        ),
        PluginDescriptor(
            name="EPG Manager",
            description="Standalone EPG generation, smart mapping and native Enigma2 import",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            icon=icon,
            fnc=main,
        ),
        PluginDescriptor(
            name="EPG Manager",
            description="Standalone EPG generation, smart mapping and native Enigma2 import",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon=icon,
            fnc=main,
        ),
    ]
