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
DEFAULT_EPG_OUTPUT_DIR = "/etc/epgimport/jedi_epg"

DEFAULTS = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "schedule_mode": "daily",       # "daily", "weekly", "monthly" (all run
                                      # once at a fixed time of day) or
                                      # "interval" (run every N hours since
                                      # the last successful update)
    "daily_update_time": "06:00",   # HH:MM, Casablanca local time - the
                                      # time-of-day used by daily/weekly/monthly
    "weekly_update_day": 0,         # 0=Monday .. 6=Sunday, used when
                                      # schedule_mode == "weekly"
    "monthly_update_day": 1,        # 1-28, used when schedule_mode ==
                                      # "monthly" (capped at 28 to sidestep
                                      # "day 31 doesn't exist in February"
                                      # entirely rather than special-casing it)
    "update_interval_hours": 6,     # used when schedule_mode == "interval"
    "epg_days": 7,
    "epg_output_dir": DEFAULT_EPG_OUTPUT_DIR,  # where generated XMLTV files go
    "retry_count": 3,
    "timeout_seconds": 15,
    "parallel_workers": 4,
    "logging_level": "INFO",
    "debug_mode": False,
    "auto_import_after_update": False,
    "safe_auto_map_threshold": 95,
    "native_import_only_iptv": False,
    "native_clear_before_import": False,
    "native_long_desc_days": 5,
    "native_confirm_before_import": True,
    "native_restart_after_import": False,
    "native_ai_match": True,
    "native_ai_threshold": 94,
    "native_preflight": True,
    "last_update": None,
    "sources_enabled": {
        "medi1tv": True,
        "chada_2m": True,
        "snrt": True,
        "bein_sports": True,
        "almajd": True,
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
        c.safe_auto_map_threshold = ConfigInteger(default=95, limits=(85, 100))
        c.native_import_only_iptv = ConfigYesNo(default=False)
        c.native_clear_before_import = ConfigYesNo(default=False)
        c.native_long_desc_days = ConfigInteger(default=5, limits=(0, 14))
        c.native_confirm_before_import = ConfigYesNo(default=True)
        c.native_restart_after_import = ConfigYesNo(default=False)
        c.native_ai_match = ConfigYesNo(default=True)
        c.native_ai_threshold = ConfigInteger(default=94, limits=(85, 100))
        c.native_preflight = ConfigYesNo(default=True)
        c.last_update = ConfigText(default="")
        for src_id, enabled in DEFAULTS["sources_enabled"].items():
            setattr(c, "src_%s" % src_id, ConfigYesNo(default=enabled))
        self._c = c

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
        default /etc/epgimport/jedi_epg if unset/blank."""
        if _HAVE_ENIGMA_CONFIG:
            value = (self._c.epg_output_dir.value or "").strip()
        else:
            value = (self._data.get("epg_output_dir") or "").strip()
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

    def get_native_ai_match(self):
        return bool(self._c.native_ai_match.value) if _HAVE_ENIGMA_CONFIG else bool(self._data.get("native_ai_match", True))

    def get_native_ai_threshold(self):
        return int(self._c.native_ai_threshold.value) if _HAVE_ENIGMA_CONFIG else int(self._data.get("native_ai_threshold", 94))

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
        return int(self._data.get('safe_auto_map_threshold', 95))

