# -*- coding: utf-8 -*-
"""
core/timezone_manager.py

Single source of truth for Morocco (Africa/Casablanca) time handling.

Background
----------
Morocco returned permanently to legal GMT (UTC+0) on 2026-09-20.  Receiver
images and CI runners may still ship tzdata describing the former UTC+1 regime,
so dates on/after that legal transition are pinned to UTC+0 here.  Historical
dates before the transition retain the old Morocco/Ramadan handling.

Two of the six original scripts got this wrong or fragile:
  - 7.ARRYADIA.py hardcoded `timezone(timedelta(hours=1))` - always wrong
    during the Ramadan pause.
  - Medi1TV / SNRT sources rely on pytz's tzdata for
    Africa/Casablanca, which is correct IF the box's tzdata package is
    up to date - but Enigma2 images can ship stale tzdata for years.
  - Historical pre-2026 dates still use the shared Hijri/tzdata cross-check.
    Post-2026-09-20 dates never call that logic to reintroduce UTC+1.

Usage
-----
    from core.timezone_manager import TimezoneManager
    tzm = TimezoneManager()
    aware_dt = tzm.localize(naive_datetime)
    xmltv_str = tzm.format_xmltv(aware_dt)
"""

import re
import time
from datetime import datetime, timedelta, timezone

from .logger import get_logger
from .json_atomic import mutate_json, read_json

log = get_logger(__name__)

_TZ_NAME = "Africa/Casablanca"

# Decree 2.26.530: at 02:00 local on Sunday 2026-09-20 Morocco moved back
# one hour and adopted GMT permanently.  02:00 under the former UTC+1 rules is
# 01:00 UTC.  Keep both cutoffs explicit so stale box tzdata cannot re-add +1.
_PERMANENT_GMT_LOCAL = datetime(2026, 9, 20, 2, 0, 0)
_PERMANENT_GMT_UTC = datetime(2026, 9, 20, 1, 0, 0, tzinfo=timezone.utc)

# Cache file so the Ramadan cross-check survives restarts and repeated runs
# within the same day don't hit the network repeatedly.
_HIJRI_CACHE_PATH = "/etc/epgmanager_epg/.hijri_offset_cache.json"
_HIJRI_API = "http://api.aladhan.com/v1/gToH?date={date}"

# --------------------------------------------------------------------------
# Backend timezone object: prefer pytz, fall back to zoneinfo (3.9+), fall
# back to a fixed offset ONLY as an absolute last resort (with a loud
# warning, since that reintroduces the Ramadan bug on purpose as a
# last-ditch "still boot the plugin" measure).
# --------------------------------------------------------------------------
try:
    import pytz
    _BACKEND = "pytz"
except ImportError:
    pytz = None
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        _BACKEND = "zoneinfo"
    except ImportError:
        ZoneInfo = None
        _BACKEND = "fixed"


class TimezoneManager(object):
    def __init__(self, use_hijri_crosscheck=True, http_timeout=5):
        self.use_hijri_crosscheck = use_hijri_crosscheck
        self.http_timeout = http_timeout
        self._tz = self._build_tz()
        self._hijri_cache = self._load_hijri_cache()
        log.info("TimezoneManager backend=%s hijri_crosscheck=%s",
                 _BACKEND, self.use_hijri_crosscheck)

    # -- backend setup ----------------------------------------------------
    def _build_tz(self):
        if _BACKEND == "pytz":
            return pytz.timezone(_TZ_NAME)
        if _BACKEND == "zoneinfo":
            return ZoneInfo(_TZ_NAME)
        log.warning(
            "Neither pytz nor zoneinfo available - using the legal Morocco "
            "offset policy (GMT from 2026-09-20, legacy best-effort before it). "
            "Install python3-pytz when historical conversion is required."
        )
        return None  # signals fixed-offset mode

    # -- public API ---------------------------------------------------------
    def now(self):
        """Current aware datetime in Morocco legal time."""
        utc_now = datetime.now(timezone.utc)
        if utc_now >= _PERMANENT_GMT_UTC:
            return utc_now
        if _BACKEND == "pytz":
            return utc_now.astimezone(self._tz)
        if _BACKEND == "zoneinfo":
            return utc_now.astimezone(self._tz)
        offset = self._fixed_offset_hint(utc_now.replace(tzinfo=None))
        return utc_now.astimezone(timezone(timedelta(hours=offset)))

    def localize(self, naive_dt):
        """Attach Morocco legal time to a naive Moroccan wall-clock datetime."""
        if naive_dt >= _PERMANENT_GMT_LOCAL:
            return naive_dt.replace(tzinfo=timezone.utc)
        if _BACKEND == "pytz":
            aware = self._tz.localize(naive_dt)
            aware = self._maybe_crosscheck(naive_dt, aware)
            return aware
        if _BACKEND == "zoneinfo":
            aware = naive_dt.replace(tzinfo=self._tz)
            aware = self._maybe_crosscheck(naive_dt, aware)
            return aware

        # fixed-offset last resort
        from datetime import timezone as _tz_fixed
        offset_hours = self._fixed_offset_hint(naive_dt)
        return naive_dt.replace(tzinfo=_tz_fixed(timedelta(hours=offset_hours)))

    def localize_from(self, naive_dt, source_tz_name):
        """Interpret *naive_dt* as wall-clock time in ``source_tz_name`` and
        convert that instant to Africa/Casablanca.

        This is intentionally different from :meth:`localize`, which means the
        incoming wall clock is already Moroccan.  A genuinely foreign-zone
        listing can still be converted explicitly by naming its source zone.
        """
        source_tz_name = str(source_tz_name or "").strip()
        if not source_tz_name or source_tz_name == _TZ_NAME:
            return self.localize(naive_dt)

        try:
            if pytz is not None:
                source_tz = pytz.timezone(source_tz_name)
                source_aware = source_tz.localize(naive_dt)
            else:
                try:
                    from zoneinfo import ZoneInfo as _ZoneInfo
                    source_aware = naive_dt.replace(tzinfo=_ZoneInfo(source_tz_name))
                except Exception:
                    source_aware = None

            if source_aware is not None:
                source_utc = source_aware.astimezone(timezone.utc)
                if source_utc >= _PERMANENT_GMT_UTC:
                    return source_utc
                if self._tz is not None:
                    return source_aware.astimezone(self._tz)
        except Exception as exc:
            log.warning("Could not convert %s wall clock to Casablanca: %s", source_tz_name, exc)

        # Last-resort compatibility path for very small Enigma2 images.  The
        # current 2M provider is Paris-based; when no timezone database exists,
        # use the relative UTC offsets for the date when they can be resolved,
        # otherwise keep the old localize behaviour rather than invent a time.
        return self.localize(naive_dt)

    def format_xmltv(self, aware_dt):
        """Format an aware datetime as XMLTV expects: YYYYMMDDHHMMSS +ZZZZ"""
        return aware_dt.strftime("%Y%m%d%H%M%S %z")

    def today_midnight(self):
        n = self.now()
        return n.replace(hour=0, minute=0, second=0, microsecond=0)

    def from_timestamp(self, epoch_seconds):
        """Convert a POSIX timestamp (as stored by time.time() /
        config.set_last_update()) into an aware Casablanca-local datetime.
        Uses the timezone object's own .astimezone() conversion, which
        correctly resolves the right UTC offset for that specific instant
        (including across a Ramadan DST-pause boundary) rather than
        assuming "now"'s offset also applied at that past moment."""
        utc_dt = datetime.fromtimestamp(epoch_seconds, timezone.utc)
        if utc_dt >= _PERMANENT_GMT_UTC:
            return utc_dt
        if self._tz is not None:
            return utc_dt.astimezone(self._tz)
        # fixed-offset last resort backend
        offset_hours = self._fixed_offset_hint(utc_dt.replace(tzinfo=None))
        return utc_dt.astimezone(timezone(timedelta(hours=offset_hours)))

    # -- Ramadan cross-check (ported & generalized from 2M/Chada script) --
    def _maybe_crosscheck(self, naive_dt, aware_dt):
        """If enabled, verify the pytz/zoneinfo-derived offset against the
        Hijri calendar and correct it if they disagree. Only overrides when
        the API is reachable; otherwise trusts the tzdata-derived offset
        unchanged (same fallback philosophy as the original 2M/Chada
        script)."""
        if naive_dt >= _PERMANENT_GMT_LOCAL:
            return naive_dt.replace(tzinfo=timezone.utc)

        if not self.use_hijri_crosscheck:
            return aware_dt

        try:
            expected_offset = self._get_ramadan_expected_offset(naive_dt)
        except Exception as e:
            log.debug("Hijri cross-check skipped: %s", e)
            return aware_dt

        if expected_offset is None:
            return aware_dt

        current_offset = int(aware_dt.utcoffset().total_seconds() / 3600)
        if current_offset != expected_offset:
            log.warning(
                "Local tzdata says UTC%+d for %s but Hijri cross-check "
                "expects UTC%+d (Ramadan pause). Overriding with the "
                "Hijri-derived offset.",
                current_offset, naive_dt.date(), expected_offset,
            )
            return naive_dt.replace(tzinfo=timezone(timedelta(hours=expected_offset)))

        return aware_dt

    def _get_ramadan_expected_offset(self, naive_dt):
        """Return 0 (Ramadan pause) or 1 (normal DST) for the given date, or
        None if undetermined. Uses a disk+memory cache and the aladhan.com
        Hijri conversion API, exactly like the original 2M/Chada script's
        get_current_marocco_offset(), but shared across all sources."""
        if naive_dt >= _PERMANENT_GMT_LOCAL:
            return 0
        date_key = naive_dt.strftime("%d-%m-%Y")
        if date_key in self._hijri_cache:
            return self._hijri_cache[date_key]

        try:
            import requests
            url = _HIJRI_API.format(date=date_key)
            resp = requests.get(url, timeout=self.http_timeout)
            if resp.status_code == 200:
                data = resp.json()
                hijri_month = int(data["data"]["hijri"]["month"]["number"])
                hijri_day = int(data["data"]["hijri"]["day"])

                is_ramadan_period = False
                if hijri_month == 9:
                    is_ramadan_period = True
                elif hijri_month == 8 and hijri_day >= 24:
                    is_ramadan_period = True
                elif hijri_month == 10 and hijri_day <= 6:
                    is_ramadan_period = True

                offset = 0 if is_ramadan_period else 1
                self._hijri_cache[date_key] = offset
                self._save_hijri_cache()
                return offset
        except Exception as e:
            log.debug("Hijri API unreachable (%s), trusting local tzdata for %s", e, date_key)

        return None  # undetermined -> caller keeps the tzdata-derived offset

    def _fixed_offset_hint(self, naive_dt=None):
        """Best-effort guess used ONLY when neither pytz nor zoneinfo exist.
        Tries the Hijri cache/API too, defaults to +1 (Morocco standard)."""
        naive_dt = naive_dt or datetime.utcnow()
        if naive_dt >= _PERMANENT_GMT_LOCAL:
            return 0
        try:
            offset = self._get_ramadan_expected_offset(naive_dt)
            if offset is not None:
                return offset
        except Exception:
            pass
        return 1

    # -- disk cache ---------------------------------------------------------
    def _load_hijri_cache(self):
        data = read_json(_HIJRI_CACHE_PATH, {})
        return data if isinstance(data, dict) else {}

    def _save_hijri_cache(self):
        try:
            # Keep the cache small and merge with the latest disk snapshot so
            # parallel source workers cannot erase one another's date entries.
            cutoff = datetime.now() - timedelta(days=40)
            local = dict(self._hijri_cache or {})

            def _mut(data):
                merged = dict(data) if isinstance(data, dict) else {}
                merged.update(local)
                pruned = {}
                for k, v in merged.items():
                    try:
                        d = datetime.strptime(k, "%d-%m-%Y")
                        if d >= cutoff:
                            pruned[k] = int(v)
                    except Exception:
                        continue
                return pruned

            self._hijri_cache = mutate_json(_HIJRI_CACHE_PATH, _mut, {})
        except Exception as e:
            log.debug("Could not persist Hijri offset cache: %s", e)
