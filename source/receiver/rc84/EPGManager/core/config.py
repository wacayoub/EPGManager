# -*- coding: utf-8 -*-
"""
core/config.py

Persistent plugin configuration (spec section 34/39).

Uses Enigma2's native `config` module (ConfigSubsection/ConfigYesNo/etc.)
when running inside Enigma2, so settings show up naturally in the plugin's
Settings screen and are stored the same way every other Enigma2 plugin
stores its settings (/etc/enigma2/settings). When NOT running inside
Enigma2 (unit tests, CLI debug), falls back to a plain JSON file at
/etc/enigma2/epgmanager.conf so the same Config API works identically in
both environments.
"""

import json
import os
import time

from .logger import get_logger
from ..version import CONFIG_SCHEMA_VERSION

log = get_logger(__name__)

JSON_FALLBACK_PATH = "/etc/enigma2/epgmanager.conf"
LEGACY_EPG_OUTPUT_DIR = "/etc/epgimport/jedi_epg"
DEFAULT_EPG_OUTPUT_DIR = "/etc/epgmanager_epg"

NATIVE_SETTINGS_PATH = "/etc/enigma2/epgmanager_native_settings.json"
NATIVE_SETTING_KEYS = (
    # Schedule ownership moved to the single Main Settings screen in v6.2.
    # Keep this mirror limited to Native Import behaviour so an old native
    # settings file can no longer overwrite the global download/import times.
    "native_import_schedule_enabled", "native_import_time",
    "native_import_only_iptv", "native_clear_before_import",
    "native_long_desc_days", "native_confirm_before_import",
    "native_restart_after_import", "epg_output_dir",
)

DEFAULTS = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "schedule_mode": "daily",       # "daily", "weekly", "monthly" (all run
                                      # once at a fixed time of day) or
                                      # "interval" (run every N hours since
                                      # the last successful update)
    "download_schedule_enabled": True,
    "background_source_updates": False,
    "background_local_time": "06:00",
    "daily_update_time": "06:00",   # HH:MM, Casablanca local time - the
                                      # time-of-day used by daily/weekly/monthly
    "native_import_schedule_enabled": False,
    "native_import_time": "08:20",  # after staggered GitHub refresh window
    "weekly_update_day": 0,         # 0=Monday .. 6=Sunday, used when
                                      # schedule_mode == "weekly"
    "monthly_update_day": 1,        # 1-28, used when schedule_mode ==
                                      # "monthly" (capped at 28 to sidestep
                                      # "day 31 doesn't exist in February"
                                      # entirely rather than special-casing it)
    "update_interval_hours": 6,     # used when schedule_mode == "interval"
    "epg_days": 7,
    "epg_output_dir": DEFAULT_EPG_OUTPUT_DIR,  # where generated XMLTV files go
    "retry_count": 2,
    "timeout_seconds": 20,
    "parallel_workers": 2,
    "logging_level": "INFO",
    "debug_mode": False,
    "auto_import_after_update": False,
    "safe_auto_map_threshold": 97,
    "suggestion_threshold": 90,
    "auto_jump_best_match": True,
    "map_next_unmapped": False,
    "protect_manual_mappings": True,
    "color_id_health": True,
    "performance_mode": "balanced",
    "safe_source_replacement": True,
    "reject_abnormal_drop": True,
    "skip_unchanged_sources": True,
    "auto_sync_enabled": False,
    "native_import_after_sync": True,
    "native_import_only_iptv": False,
    "native_clear_before_import": False,
    "native_long_desc_days": 5,
    "native_confirm_before_import": True,
    "native_restart_after_import": False,
    "native_preflight": True,
    "last_update": None,
    "last_native_import": None,
    "sources_enabled": {
        "medi1tv": True,
        "chada_2m": True,
        "snrt": True,
        "bein_sports": True,
        "arryadia": True,
    },
}

SCHEDULE_MODES = ("daily", "weekly", "monthly", "interval")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

try:
    from Components.config import (
        config, ConfigSubsection, ConfigYesNo, ConfigInteger,
        ConfigSelection, ConfigText, ConfigClock,
    )
    _HAVE_ENIGMA_CONFIG = True
except ImportError:
    _HAVE_ENIGMA_CONFIG = False


class Config(object):
    def __init__(self):
        if _HAVE_ENIGMA_CONFIG:
            self._init_enigma_config()
        else:
            self._data = self._load_json()

    # -- Enigma2-native backend ---------------------------------------------
    def _init_enigma_config(self):
        config.plugins.epgmanager = ConfigSubsection()
        c = config.plugins.epgmanager
        c.download_schedule_enabled = ConfigYesNo(default=DEFAULTS["download_schedule_enabled"])
        c.background_source_updates = ConfigYesNo(default=DEFAULTS["background_source_updates"])
        c.background_local_time = ConfigClock(default=0)
        c.background_local_time.value = [6, 0]
        c.schedule_mode = ConfigSelection(
            default="daily",
            choices=[("daily", "Once a day at a fixed time"),
                     ("weekly", "Once a week, on a chosen day"),
                     ("monthly", "Once a month, on a chosen day"),
                     ("interval", "Every N hours since last update")])

        # ConfigClock gives the native Enigma2 "adjust with LEFT/RIGHT"
        # time-of-day picker (same widget used by timers/wakeup screens) -
        # much friendlier on a remote than typing "06:00" character by
        # character. default=0 (epoch) is used only because ConfigClock's
        # constructor requires SOME valid timestamp to call localtime() on
        # - the actual default hour/minute is set explicitly right after,
        # so this is unaffected by the box's timezone/clock state at boot.
        # Shared time-of-day for daily/weekly/monthly modes.
        c.daily_update_time = ConfigClock(default=0)
        c.daily_update_time.value = [6, 0]
        c.native_import_schedule_enabled = ConfigYesNo(default=DEFAULTS["native_import_schedule_enabled"])
        c.native_import_time = ConfigClock(default=0)
        c.native_import_time.value = [8, 20]

        c.weekly_update_day = ConfigSelection(
            default="0", choices=[(str(i), name) for i, name in enumerate(WEEKDAY_NAMES)])
        c.monthly_update_day = ConfigInteger(default=DEFAULTS["monthly_update_day"], limits=(1, 28))

        c.update_interval_hours = ConfigInteger(
            default=DEFAULTS["update_interval_hours"], limits=(1, 24))
        c.epg_days = ConfigInteger(default=DEFAULTS["epg_days"], limits=(1, 14))
        c.epg_output_dir = ConfigText(default=DEFAULT_EPG_OUTPUT_DIR, fixed_size=False)
        c.retry_count = ConfigInteger(default=DEFAULTS["retry_count"], limits=(1, 10))
        c.timeout_seconds = ConfigInteger(default=DEFAULTS["timeout_seconds"], limits=(5, 60))
        c.parallel_workers = ConfigInteger(default=DEFAULTS["parallel_workers"], limits=(1, 8))
        c.logging_level = ConfigSelection(
            default="INFO",
            choices=[("DEBUG", "Debug"), ("INFO", "Info"),
                     ("WARNING", "Warning"), ("ERROR", "Error")])
        c.debug_mode = ConfigYesNo(default=False)
        c.auto_import_after_update = ConfigYesNo(default=False)
        c.safe_auto_map_threshold = ConfigInteger(default=97, limits=(90, 100))
        c.suggestion_threshold = ConfigInteger(default=90, limits=(80, 96))
        c.auto_jump_best_match = ConfigYesNo(default=True)
        c.map_next_unmapped = ConfigYesNo(default=False)
        c.protect_manual_mappings = ConfigYesNo(default=True)
        c.color_id_health = ConfigYesNo(default=DEFAULTS["color_id_health"])
        c.performance_mode = ConfigSelection(default="balanced", choices=[("low", "Low CPU"), ("balanced", "Balanced"), ("fast", "Fast")])
        c.safe_source_replacement = ConfigYesNo(default=True)
        c.reject_abnormal_drop = ConfigYesNo(default=True)
        c.skip_unchanged_sources = ConfigYesNo(default=True)
        c.auto_sync_enabled = ConfigYesNo(default=False)
        c.native_import_after_sync = ConfigYesNo(default=True)
        c.native_import_only_iptv = ConfigYesNo(default=False)
        c.native_clear_before_import = ConfigYesNo(default=False)
        c.native_long_desc_days = ConfigInteger(default=5, limits=(0, 14))
        c.native_confirm_before_import = ConfigYesNo(default=True)
        c.native_restart_after_import = ConfigYesNo(default=False)
        c.native_preflight = ConfigYesNo(default=True)
        c.last_update = ConfigText(default="")
        c.last_native_import = ConfigText(default="")
        for src_id, enabled in DEFAULTS["sources_enabled"].items():
            setattr(c, "src_%s" % src_id, ConfigYesNo(default=enabled))
        self._c = c
        # Native Import has its own small atomic mirror. Some OE-A/OpenATV
        # builds recreate ConfigSubsection objects when plugins reload, which can
        # make a successful-looking GREEN Save appear to be lost. Restoring this
        # mirror makes Native Import settings deterministic across screen reopen
        # and Enigma2 restart while still saving to the normal Enigma2 config.
        self._load_native_settings_overlay()

    def _load_native_settings_overlay(self):
        if not _HAVE_ENIGMA_CONFIG:
            return
        try:
            with open(NATIVE_SETTINGS_PATH, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            for key in NATIVE_SETTING_KEYS:
                if key not in data:
                    continue
                element = getattr(self._c, key, None)
                if element is None:
                    continue
                value = data[key]
                if key in ("daily_update_time", "native_import_time") and isinstance(value, (list, tuple)) and len(value) >= 2:
                    value = [int(value[0]), int(value[1])]
                element.value = value
        except Exception as exc:
            if os.path.exists(NATIVE_SETTINGS_PATH):
                log.warning("Could not restore Native Import settings: %s", exc)

    def save_native_settings(self):
        """Persist Native Import values both through Enigma2 and an atomic mirror."""
        if not _HAVE_ENIGMA_CONFIG:
            self._save_json()
            return True
        data = {}
        for key in NATIVE_SETTING_KEYS:
            element = getattr(self._c, key, None)
            if element is None:
                continue
            value = element.value
            if isinstance(value, tuple):
                value = list(value)
            data[key] = value
            if hasattr(element, "save"):
                element.save()
        parent = os.path.dirname(NATIVE_SETTINGS_PATH)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = NATIVE_SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, NATIVE_SETTINGS_PATH)
        try:
            from Components.config import configfile
            configfile.save()
        except Exception:
            log.exception("Could not flush Enigma2 configfile after Native Import save")
            raise
        return True

    # -- JSON fallback backend ----------------------------------------------
    def _load_json(self):
        if os.path.exists(JSON_FALLBACK_PATH):
            try:
                with open(JSON_FALLBACK_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = dict(DEFAULTS)
                merged.update(data)
                return merged
            except Exception as e:
                log.warning("Could not parse %s (%s); using defaults", JSON_FALLBACK_PATH, e)
        return dict(DEFAULTS)

    def _save_json(self):
        try:
            os.makedirs(os.path.dirname(JSON_FALLBACK_PATH), exist_ok=True)
            tmp = JSON_FALLBACK_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, JSON_FALLBACK_PATH)
        except Exception as e:
            log.warning("Could not persist %s: %s", JSON_FALLBACK_PATH, e)

    # -- public accessors (identical behavior on both backends) -------------
    def get_download_schedule_enabled(self):
        return bool(self._c.download_schedule_enabled.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("download_schedule_enabled", True))

    def set_download_schedule_enabled(self, value):
        if _HAVE_ENIGMA_CONFIG:
            self._c.download_schedule_enabled.value = bool(value)
            self._c.download_schedule_enabled.save()
        else:
            self._data["download_schedule_enabled"] = bool(value)
            self._save_json()

    def get_native_import_schedule_enabled(self):
        return bool(self._c.native_import_schedule_enabled.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_import_schedule_enabled", False))

    def set_native_import_schedule_enabled(self, value):
        if _HAVE_ENIGMA_CONFIG:
            self._c.native_import_schedule_enabled.value = bool(value)
            self._c.native_import_schedule_enabled.save()
        else:
            self._data["native_import_schedule_enabled"] = bool(value)
            self._save_json()

    def get_native_import_time(self):
        if _HAVE_ENIGMA_CONFIG:
            h, m = self._c.native_import_time.value
            return int(h), int(m)
        raw = self._data.get("native_import_time", "08:20")
        try:
            hh, mm = raw.split(":")
            hh, mm = int(hh), int(mm)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except Exception:
            pass
        return 6, 15

    def set_native_import_time(self, hour, minute):
        if _HAVE_ENIGMA_CONFIG:
            self._c.native_import_time.value = [int(hour), int(minute)]
            self._c.native_import_time.save()
        else:
            self._data["native_import_time"] = "%02d:%02d" % (int(hour), int(minute))
            self._save_json()


    def get_last_native_import(self):
        if _HAVE_ENIGMA_CONFIG:
            raw = self._c.last_native_import.value
            return float(raw) if raw else None
        return self._data.get("last_native_import")

    def set_last_native_import(self, timestamp):
        if _HAVE_ENIGMA_CONFIG:
            self._c.last_native_import.value = str(timestamp)
            self._c.last_native_import.save()
        else:
            self._data["last_native_import"] = timestamp
            self._save_json()

    def get_background_source_updates(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.background_source_updates.value)
        return bool(self._data.get("background_source_updates", True))

    def set_background_source_updates(self, enabled):
        if _HAVE_ENIGMA_CONFIG:
            self._c.background_source_updates.value = bool(enabled)
            self._c.background_source_updates.save()
        else:
            self._data["background_source_updates"] = bool(enabled)
            self._save_json()

    def get_background_local_time(self):
        if _HAVE_ENIGMA_CONFIG:
            h, m = self._c.background_local_time.value
            return int(h), int(m)
        raw = self._data.get("background_local_time", "06:00")
        try:
            hh, mm = raw.split(":")
            return int(hh), int(mm)
        except Exception:
            return 6, 0

    def set_background_local_time(self, hour, minute):
        if _HAVE_ENIGMA_CONFIG:
            self._c.background_local_time.value = [int(hour), int(minute)]
            self._c.background_local_time.save()
        else:
            self._data["background_local_time"] = "%02d:%02d" % (int(hour), int(minute))
            self._save_json()

    def get_schedule_mode(self):
        return self._c.schedule_mode.value if _HAVE_ENIGMA_CONFIG else self._data["schedule_mode"]

    def set_schedule_mode(self, mode):
        if mode not in SCHEDULE_MODES:
            raise ValueError("schedule_mode must be one of %s" % (SCHEDULE_MODES,))
        if _HAVE_ENIGMA_CONFIG:
            self._c.schedule_mode.value = mode
            self._c.schedule_mode.save()
        else:
            self._data["schedule_mode"] = mode
            self._save_json()

    def get_daily_update_time(self):
        """Returns (hour, minute) as ints. On the Enigma2 backend this
        reads ConfigClock's [hour, minute] list directly; the JSON-fallback
        backend still parses an "HH:MM" string (falls back to 06:00 on any
        parse error rather than crashing the scheduler over a malformed
        setting)."""
        if _HAVE_ENIGMA_CONFIG:
            h, m = self._c.daily_update_time.value
            return int(h), int(m)

        raw = self._data["daily_update_time"]
        try:
            hh, mm = raw.split(":")
            hh, mm = int(hh), int(mm)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except Exception:
            pass
        log.warning("Invalid daily_update_time %r, falling back to 06:00", raw)
        return 6, 0

    def set_daily_update_time(self, hour, minute):
        if _HAVE_ENIGMA_CONFIG:
            self._c.daily_update_time.value = [hour, minute]
            self._c.daily_update_time.save()
        else:
            self._data["daily_update_time"] = "%02d:%02d" % (hour, minute)
            self._save_json()

    def get_weekly_update_day(self):
        """Returns an int 0=Monday..6=Sunday, used when schedule_mode ==
        'weekly'."""
        if _HAVE_ENIGMA_CONFIG:
            try:
                return int(self._c.weekly_update_day.value)
            except (TypeError, ValueError):
                return 0
        return int(self._data.get("weekly_update_day", 0))

    def set_weekly_update_day(self, day):
        day = max(0, min(6, int(day)))
        if _HAVE_ENIGMA_CONFIG:
            self._c.weekly_update_day.value = str(day)
            self._c.weekly_update_day.save()
        else:
            self._data["weekly_update_day"] = day
            self._save_json()

    def get_monthly_update_day(self):
        """Returns an int 1-28, used when schedule_mode == 'monthly'.
        Capped at 28 (rather than 31) so every calendar month, including
        February, always has that day - avoids "day 31 doesn't exist this
        month" edge cases entirely instead of special-casing them."""
        if _HAVE_ENIGMA_CONFIG:
            try:
                return int(self._c.monthly_update_day.value)
            except (TypeError, ValueError):
                return 1
        return int(self._data.get("monthly_update_day", 1))

    def set_monthly_update_day(self, day):
        day = max(1, min(28, int(day)))
        if _HAVE_ENIGMA_CONFIG:
            self._c.monthly_update_day.value = day
            self._c.monthly_update_day.save()
        else:
            self._data["monthly_update_day"] = day
            self._save_json()

    def get_epg_output_dir(self):
        """Directory generated XMLTV files are written to. Configurable
        (spec: "add in settings the path where the xml file will export")
        so operators can point EPG Manager at a different EPG-Importer
        directory, an HDD/USB mount, etc. Falls back to the historical
        default /etc/epgmanager_epg if unset/blank."""
        if _HAVE_ENIGMA_CONFIG:
            value = (self._c.epg_output_dir.value or "").strip()
        else:
            value = (self._data.get("epg_output_dir") or "").strip()
        if value == LEGACY_EPG_OUTPUT_DIR:
            return DEFAULT_EPG_OUTPUT_DIR
        return value or DEFAULT_EPG_OUTPUT_DIR

    def set_epg_output_dir(self, path):
        path = (path or "").strip() or DEFAULT_EPG_OUTPUT_DIR
        if _HAVE_ENIGMA_CONFIG:
            self._c.epg_output_dir.value = path
            self._c.epg_output_dir.save()
        else:
            self._data["epg_output_dir"] = path
            self._save_json()

    def get_update_interval_hours(self):
        if _HAVE_ENIGMA_CONFIG:
            return self._c.update_interval_hours.value
        return self._data["update_interval_hours"]

    def set_update_interval_hours(self, hours):
        if _HAVE_ENIGMA_CONFIG:
            self._c.update_interval_hours.value = hours
            self._c.update_interval_hours.save()
        else:
            self._data["update_interval_hours"] = hours
            self._save_json()

    def get_epg_days(self):
        return self._c.epg_days.value if _HAVE_ENIGMA_CONFIG else self._data["epg_days"]

    def get_retry_count(self):
        return self._c.retry_count.value if _HAVE_ENIGMA_CONFIG else self._data["retry_count"]

    def get_timeout_seconds(self):
        return self._c.timeout_seconds.value if _HAVE_ENIGMA_CONFIG else self._data["timeout_seconds"]

    def get_parallel_workers(self):
        return self._c.parallel_workers.value if _HAVE_ENIGMA_CONFIG else self._data["parallel_workers"]

    def get_performance_mode(self):
        if _HAVE_ENIGMA_CONFIG:
            return str(self._c.performance_mode.value or "balanced")
        return str(self._data.get("performance_mode", "balanced") or "balanced")

    def get_effective_parallel_workers(self):
        """CPU-aware worker budget used by source jobs.

        Low CPU is intentionally single-threaded. Balanced caps at two workers
        on Vu+ class hardware. Fast allows up to four, but never exceeds the
        operator's legacy Parallel workers value.
        """
        base = max(1, int(self.get_parallel_workers()))
        mode = self.get_performance_mode()
        if mode == "low":
            return 1
        if mode == "fast":
            return max(1, min(4, base if base >= 3 else 3))
        return max(1, min(2, base))

    def get_safe_source_replacement(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.safe_source_replacement.value)
        return bool(self._data.get("safe_source_replacement", True))

    def get_reject_abnormal_drop(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.reject_abnormal_drop.value)
        return bool(self._data.get("reject_abnormal_drop", True))

    def get_skip_unchanged_sources(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.skip_unchanged_sources.value)
        return bool(self._data.get("skip_unchanged_sources", True))

    def get_logging_level(self):
        return self._c.logging_level.value if _HAVE_ENIGMA_CONFIG else self._data["logging_level"]

    def get_debug_mode(self):
        return self._c.debug_mode.value if _HAVE_ENIGMA_CONFIG else self._data["debug_mode"]

    def get_last_update(self):
        if _HAVE_ENIGMA_CONFIG:
            raw = self._c.last_update.value
            return float(raw) if raw else None
        return self._data.get("last_update")

    def set_last_update(self, timestamp):
        if _HAVE_ENIGMA_CONFIG:
            self._c.last_update.value = str(timestamp)
            self._c.last_update.save()
        else:
            self._data["last_update"] = timestamp
            self._save_json()

    def get_native_import_only_iptv(self):
        return bool(self._c.native_import_only_iptv.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_import_only_iptv", False))

    def get_native_clear_before_import(self):
        return bool(self._c.native_clear_before_import.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_clear_before_import", False))

    def get_native_long_desc_days(self):
        return int(self._c.native_long_desc_days.value) if _HAVE_ENIGMA_CONFIG else int(self._data.get("native_long_desc_days", 5))

    def get_native_confirm_before_import(self):
        return bool(self._c.native_confirm_before_import.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_confirm_before_import", True))

    def get_native_restart_after_import(self):
        return bool(self._c.native_restart_after_import.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_restart_after_import", False))

    def get_native_preflight(self):
        return bool(self._c.native_preflight.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_preflight", True))

    def is_source_enabled(self, source_id):
        if _HAVE_ENIGMA_CONFIG:
            attr = getattr(self._c, "src_%s" % source_id, None)
            return attr.value if attr is not None else True
        return self._data.get("sources_enabled", {}).get(source_id, True)

    def set_source_enabled(self, source_id, enabled):
        if _HAVE_ENIGMA_CONFIG:
            attr = getattr(self._c, "src_%s" % source_id, None)
            if attr is not None:
                attr.value = enabled
                attr.save()
        else:
            self._data.setdefault("sources_enabled", {})[source_id] = enabled
            self._save_json()

    def last_update_human(self):
        ts = self.get_last_update()
        if not ts:
            return "Never"
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


    def get_auto_import_after_update(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.auto_import_after_update.value)
        return bool(self._data.get('auto_import_after_update', False))

    def set_auto_import_after_update(self, value):
        if _HAVE_ENIGMA_CONFIG:
            self._c.auto_import_after_update.value = bool(value)
            self._c.auto_import_after_update.save()
        else:
            self._data['auto_import_after_update'] = bool(value)
            self._save_json()

    def get_safe_auto_map_threshold(self):
        if _HAVE_ENIGMA_CONFIG:
            return int(self._c.safe_auto_map_threshold.value)
        return int(self._data.get('safe_auto_map_threshold', 97))

    # Compatibility aliases used by dashboard code. In beta14 "automatic"
    # means background source maintenance, never download+mapping+import.
    def get_cycle_enabled(self):
        return self.get_background_source_updates()

    def set_cycle_enabled(self, value):
        return self.set_background_source_updates(value)

    def get_cycle_time(self):
        return self.get_background_local_time()

    def set_cycle_time(self, hour, minute):
        return self.set_background_local_time(hour, minute)

    def get_auto_sync_enabled(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.auto_sync_enabled.value)
        return bool(self._data.get("auto_sync_enabled", False))

    def get_native_import_after_sync(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.native_import_after_sync.value)
        return bool(self._data.get("native_import_after_sync", True))

    def get_auto_jump_best_match(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.auto_jump_best_match.value)
        return bool(self._data.get("auto_jump_best_match", True))

    def get_map_next_unmapped(self):
        # Removed from the beta63 UX. Assign always stays on the current service.
        return False

    def get_protect_manual_mappings(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.protect_manual_mappings.value)
        return bool(self._data.get("protect_manual_mappings", True))

    def get_color_id_health(self):
        if _HAVE_ENIGMA_CONFIG:
            return bool(self._c.color_id_health.value)
        return bool(self._data.get("color_id_health", True))

    def get_auto_sync_threshold(self):
        if _HAVE_ENIGMA_CONFIG:
            return int(self._c.safe_auto_map_threshold.value)
        return int(self._data.get("safe_auto_map_threshold", 97))

    def get_suggestion_threshold(self):
        if _HAVE_ENIGMA_CONFIG:
            return int(self._c.suggestion_threshold.value)
        return int(self._data.get("suggestion_threshold", 90))

