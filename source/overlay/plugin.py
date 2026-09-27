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
from .version import __version__

log = get_logger(__name__)

_manager = None
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
    """Session hook for direct-feed mode.

    Source scraping and the scheduled 06:00 refresh happen in EPG-Scrapers
    on GitHub. The receiver must not start the legacy local scraper scheduler.
    """
    if reason == 0:
        log.info("EPG Manager v%s direct-feed mode - local source scheduler disabled", __version__)
    elif reason == 1:
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
