# -*- coding: utf-8 -*-
"""rc85: guide strength of one EPG ID, used to pick the best of several valid IDs.

When several selected sources carry a correct ID for the same receiver channel,
the plugin used to choose only by a fixed source ranking (official / provider
country / language). An official feed with a 3-hour stub could therefore beat
another feed with a full week. Guide strength measures what each ID actually
delivers, from data already on the receiver (no network):

- the local per-ID quality profile, when it is fresh and complete;
- otherwise the GitHub per-ID monitor (future programmes / future hours).

Classes (lower is better):
  STRONG 0  >= 48 h of upcoming guide
  GOOD   1  12-48 h, or UNKNOWN (no data: Rytec and other feeds without a monitor)
  THIN   2  under 12 h
  EMPTY  3  no upcoming programme

UNKNOWN deliberately ranks with GOOD: missing data must never make a feed lose
to a thin guide, nor beat a measured strong one.
"""
from __future__ import print_function

import time

STRONG, GOOD, THIN, EMPTY = 0, 1, 2, 3
CLASS_NAMES = {STRONG: "STRONG", GOOD: "GOOD", THIN: "THIN", EMPTY: "EMPTY"}
STRONG_HOURS = 48.0
GOOD_HOURS = 12.0
MONITOR_MAX_AGE = 24 * 3600

_INDEX = {"key": None, "rows": {}}


def classify_hours(hours):
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return GOOD
    if hours <= 0:
        return EMPTY
    if hours < GOOD_HOURS:
        return THIN
    if hours < STRONG_HOURS:
        return GOOD
    return STRONG


def _monitor_index(data):
    """(stem, id.casefold()) -> row, built once per monitor snapshot."""
    try:
        checked = int(data.get("id_monitor_checked") or data.get("checked") or 0)
    except (TypeError, ValueError, AttributeError):
        checked = 0
    key = (id(data), checked)
    if _INDEX["key"] != key:
        rows = {}
        for stem, items in ((data or {}).get("id_monitor") or {}).items():
            for row in items or []:
                cid = str((row or {}).get("id") or "").strip().casefold()
                if cid:
                    rows[(str(stem).lower(), cid)] = row
        _INDEX["key"] = key
        _INDEX["rows"] = rows
    return _INDEX["rows"], checked


def strength(source_id, channel_id, data=None, now=None, profile_lookup=None):
    """Return (class, hours_or_None, known) for one source ID.

    `data` is the github_direct_sync.load() snapshot (pass it once per run).
    `profile_lookup(source_id, channel_id)` may return a source_quality profile.
    """
    now = float(now if now is not None else time.time())
    sid = str(source_id or "").strip()
    cid = str(channel_id or "").strip()
    if not sid or not cid:
        return GOOD, None, False
    monitor_checked = 0
    if isinstance(data, dict):
        try:
            monitor_checked = int(data.get("id_monitor_checked") or data.get("checked") or 0)
        except (TypeError, ValueError):
            monitor_checked = 0

    # 1. A fresh, complete local profile is the closest truth to the import.
    if profile_lookup is not None:
        try:
            from .quality_profile_freshness import authoritative_profile
            profile = profile_lookup(sid, cid) or {}
            if authoritative_profile(profile, monitor_checked, now):
                future_last = float(profile.get("future_last") or 0)
                hours = max(0.0, (future_last - now) / 3600.0)
                return classify_hours(hours), hours, True
        except Exception:
            pass

    # 2. The GitHub per-ID monitor, only while fresh.
    if not isinstance(data, dict) or not monitor_checked or now - monitor_checked > MONITOR_MAX_AGE:
        return GOOD, None, False
    try:
        from .github_direct_sync import source_stem
        stem = source_stem(sid)
    except Exception:
        stem = ""
    if not stem:
        return GOOD, None, False
    rows, _ = _monitor_index(data)
    row = rows.get((stem.lower(), cid.casefold()))
    if not row:
        return GOOD, None, False
    try:
        future = int(row.get("future_programmes") or 0)
        hours = float(row.get("future_hours") or 0.0)
    except (TypeError, ValueError):
        return GOOD, None, False
    if future <= 0:
        return EMPTY, 0.0, True
    return classify_hours(hours), hours, True


def label(klass, hours):
    """Short text for the Smart Mapping list, e.g. '7d guide', '3h guide'."""
    if klass == EMPTY:
        return "no guide"
    if hours is None:
        return ""
    if hours >= 48:
        return "%dd guide" % int(round(hours / 24.0))
    return "%dh guide" % max(1, int(round(hours)))
