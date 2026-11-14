"""Freshness gate for cached per-channel XMLTV measurements.

Control-plane metadata alone cannot certify programme quality. An older local
profile cannot overrule a newer monitor observation, particularly with NO_EPG.
"""
import math


def authoritative_profile(profile, monitor_checked, now):
    if not isinstance(profile, dict):
        return False
    if not profile.get('specific') or not profile.get('profile_complete'):
        return False
    if 'future_last' not in profile:
        return False
    try:
        checked = float(profile.get('profile_checked') or 0)
        monitor = float(monitor_checked or 0)
        observed = float(now)
        if not all(math.isfinite(v) for v in (checked, monitor, observed)):
            return False
        return checked > 0 and observed - 86400 <= checked <= observed and checked >= monitor
    except (TypeError, ValueError, OverflowError):
        return False
