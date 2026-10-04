# -*- coding: utf-8 -*-
"""EPGManager Enigma2 entrypoint.

rc36-fix1: keep plugin discovery deliberately dependency-light.
Enigma2 imports plugin.py while building the plugin menu. Any exception at
module import time makes the whole plugin disappear from Plugin Browser.
All EPGManager internals are therefore loaded lazily only after discovery.
"""

from Plugins.Plugin import PluginDescriptor

try:
    print("[EPGManager] rc75 plugin.py imported")
except Exception:
    pass

_manager = None
_config = None
_background = None
_readiness_background = None
_native_import_scheduler = None


def _elog(message):
    """Never let logging hide the plugin during discovery/startup."""
    try:
        print("[EPGManager] %s" % message)
    except Exception:
        pass


def _logger():
    try:
        from .core.logger import get_logger
        return get_logger(__name__)
    except Exception:
        return None


def _log_exception(message):
    log = _logger()
    if log is not None:
        try:
            log.exception(message)
            return
        except Exception:
            pass
    _elog(message)
    try:
        import traceback
        traceback.print_exc()
    except Exception:
        pass


def _build_sources():
    # Scraping runs remotely; receiver consumes prepared XMLTV feeds.
    return []


def get_config():
    global _config
    if _config is None:
        from .core.config import Config
        from .core.logger import configure as configure_logging
        _config = Config()
        try:
            configure_logging(level=_config.get_logging_level(),
                              debug=_config.get_debug_mode())
        except Exception:
            pass
    return _config


def get_manager():
    global _manager
    if _manager is None:
        from .core.manager import Manager
        _manager = Manager(config=get_config(), source_loader=_build_sources)
        try:
            from .version import __version__
            log = _logger()
            if log is not None:
                log.info("EPG Manager v%s core initialized", __version__)
        except Exception:
            pass
    return _manager


def _start_scheduled_native_import():
    """Start the same fast Import All path used by the GUI.

    Called by eTimer on Enigma2's main thread. Returning False asks the
    scheduler to retry later when EPGManager is temporarily busy.
    """
    try:
        manager = get_manager()
        if manager.is_busy():
            return False
        started = manager.run_fast_import_async(on_complete=lambda _result: None)
        if not started:
            return False
        return getattr(manager, "_cycle_runner", None)
    except Exception:
        _log_exception("Scheduled EPG Import All start failed")
        return None


def refresh_native_import_scheduler():
    """Apply Auto Import settings immediately without restarting Enigma2."""
    global _native_import_scheduler
    try:
        old = _native_import_scheduler
        _native_import_scheduler = None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        config = get_config()
        if not config.get_native_import_schedule_enabled():
            _elog("Native Auto Import scheduler disabled")
            return False
        from .core.scheduler import NativeImportScheduler
        _native_import_scheduler = NativeImportScheduler(config, _start_scheduled_native_import)
        _native_import_scheduler.start()
        hh, mm = config.get_native_import_time()
        _elog("Native Auto Import scheduler active at %02d:%02d Casablanca" % (hh, mm))
        return True
    except Exception:
        _log_exception("Could not apply Native Import scheduler settings")
        _native_import_scheduler = None
        return False


def autostart(reason, **kwargs):
    """Best-effort cache-only initialization; never break plugin discovery."""
    global _background, _readiness_background, _native_import_scheduler
    try:
        if reason == 0:
            config = get_config()

            # Cache-only warm-up is safe on receiver boot. Remote catalogues and
            # GitHub status are refreshed lazily when the EPGManager GUI opens.
            try:
                from .core import smart_mapping_warm_cache
                smart_mapping_warm_cache.ensure_async()
            except Exception:
                _log_exception("Could not pre-warm Smart Mapping cache")

            try:
                from .core import smart_catalog_boot
                smart_catalog_boot.ensure_async()
            except Exception:
                _log_exception("Could not boot Smart Mapping Channel-ID catalogue")

            try:
                from .core import source_metadata
                source_metadata.ensure_recovered()
            except Exception:
                _log_exception("Could not recover source metadata cache")

            _background = None
            _readiness_background = None
            refresh_native_import_scheduler()

        elif reason == 1:
            for obj in (_background, _readiness_background, _native_import_scheduler):
                if obj:
                    try:
                        obj.stop()
                    except Exception:
                        pass
            _native_import_scheduler = None
    except Exception:
        _log_exception("EPGManager autostart failed")


def main(session, **kwargs):
    """Open main GUI; show a visible diagnostic instead of silently failing."""
    try:
        from .ui.main import EPGManagerMainScreen
        config = get_config()
        try:
            from .core import first_use_remote_mapping
            first_use_remote_mapping.ensure_async(config=config, delay=0.1)
        except Exception:
            pass
        session.open(EPGManagerMainScreen, get_manager(), config)
    except Exception as exc:
        _log_exception("EPGManager GUI launch failed")
        try:
            from Screens.MessageBox import MessageBox
            session.open(
                MessageBox,
                "EPGManager could not start:\n%s\n\nSee /tmp/epgmanager-prereq-check.log or Enigma2 log." % exc,
                MessageBox.TYPE_ERROR,
                timeout=15,
            )
        except Exception:
            raise



# EPGMANAGER_ONLINE_UPDATE
def online_update_main(session, **kwargs):
    """Open the GitHub-backed online update screen."""
    from .online_update import EPGManagerOnlineUpdate
    session.open(EPGManagerOnlineUpdate)

def Plugins(**kwargs):
    """Ultra-minimal discovery descriptors for OpenATV recovery testing."""
    try:
        print("[EPGManager] rc75 Plugins() called")
    except Exception:
        pass
    return [
        PluginDescriptor(
            name="EPGManager Auto Import",
            description="EPGManager background scheduler",
            where=PluginDescriptor.WHERE_AUTOSTART,
            fnc=autostart,
        ),
        PluginDescriptor(
            name="EPGManager",
            description="EPG Manager Direct + Grouped XMLTV",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            fnc=main,
        ),
        PluginDescriptor(
            name="EPGManager",
            description="EPG Manager Direct + Grouped XMLTV",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            fnc=main,
        ),

        PluginDescriptor(
            name="EPG Manager Online Update",
            description="Check and install the latest EPG Manager release from GitHub",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=online_update_main,
        ),
    ]
