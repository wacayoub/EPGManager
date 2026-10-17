"""Observed Arryadia recurring-programme patterns: confidence hints, NOT EPG.

Audit span 2026-04-17..2026-10-17 had one six-day current-grille sample
(SNRT Sep 21–26), supplemented by qualitative indexed August evidence.
No archived timestamp can ever be used as a date for a new XMLTV programme.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Casablanca")
ARR_IDS = frozenset({
    "Arryadia_HD", "Arryadia_TNT", "Arryadia_HD1",
    "Arryadia_HD2", "Arryadia_HD3",
})

RECURRING_FAMILIES = {
    "arryadia_chabab_football": {
        "sample_events": 16, "sample_days": 5, "sample_period": "2026-09-21/26",
        "daypart_hint": "07:00–11:00", "reliable_future_clock": False,
    },
    "arryadia_chabab_reportage": {
        "sample_events": 8, "sample_days": 5, "sample_period": "2026-09-21/26",
        "daypart_hint": "08:00–11:30", "reliable_future_clock": False,
    },
    "flash_info": {
        "sample_events": 15, "sample_days": 6, "sample_period": "2026-09-21/26",
        "daypart_hint": "04:00/13:00/22:00", "reliable_future_clock": False,
    },
}
_NO_EVENT_TITLES = re.compile(
    r"^(?:programmes?\s+(?:arryadia|الرياضية)|programmes?|"
    r"برامج\s+قناة\s+الرياضية|برامج\s+الرياضية)$",
    re.I,
)


def family_hint(title):
    """Classify an already-observed title; never return a broadcast time."""
    n = " ".join(str(title or "").lower().split())
    if "arryadia chabab" in n or "الرياضية شباب" in n:
        if "reportage" in n or "روبورتاج" in n:
            return "arryadia_chabab_reportage"
        if ("football" in n or "كرة القدم" in n or "foot féminin" in n
                or "كرة القدم النسوية" in n):
            return "arryadia_chabab_football"
    if n == "flash info" or n.startswith("flash info -") or n == "موجز الأخبار":
        return "flash_info"
    return None


def actual_arr_event(event, now=None):
    """Require actual programme provenance, date, channel, title and duration."""
    now = now or datetime.now(TZ)
    if event.channel not in ARR_IDS:
        return False
    if getattr(event, "source", None) != "arryadia-current-official":
        return False
    title = " ".join(str(event.title or "").split())
    if not title or _NO_EVENT_TITLES.fullmatch(title):
        return False
    if not event.start or event.start.tzinfo is None:
        return False
    stop = event.stop
    if stop is None or stop.tzinfo is None or stop <= event.start:
        return False
    # Only authenticated recent/current broadcaster days, never old XML
    # replayed as Last Known Good or September rows moved to October.
    local = now.astimezone(TZ)
    return (
        event.start >= local - timedelta(hours=12)
        and event.start < local + timedelta(days=3)
        and stop <= event.start + timedelta(hours=12)
    )


def validated_rows(rows, now=None):
    return [e for e in rows if actual_arr_event(e, now)]


def observed_family_counts(rows):
    """Return advisory matches only, without scheduling any programme."""
    out = {name: 0 for name in RECURRING_FAMILIES}
    for row in rows:
        if not actual_arr_event(row):
            continue
        key = family_hint(row.title)
        if key:
            out[key] += 1
    return out
