"""Arryadia HD2/HD3 one-hour Grand Format recurrence (viewer-reported).

This is not a current verified official SNRT programme listing. The viewer
reports Grand Format looping hourly, throughout each day, on these two
specific secondary services. Explicit provenance remains attached so XMLTV
quality reports cannot call it official / real-time verified.

Confirmed future broadcaster programmes take precedence, and a disputed
fixture candidate cannot override the recurring slot.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import morocco_epg as base

TZ = ZoneInfo("Africa/Casablanca")
IDS = frozenset(("Arryadia_HD2", "Arryadia_HD3"))
SOURCE = "viewer-reported-arryadia-hourly-grand-format"
TITLE = "Grand Format"
DESCRIPTION = (
    "برنامج «Grand Format» المتكرر على قناة الرياضية. "
    "الجدولة كل ساعة وفق متابعة المشاهد، وليست مواعيد مؤكدة من شبكة البث الرسمية."
)
DAILY_SLOTS = 24


def is_viewer_report(event, now=None):
    """Only exactly one-hour Grand Format slots, on HD2/3, as labelled."""
    if (getattr(event, "channel", None) not in IDS
            or getattr(event, "source", None) != SOURCE
            or getattr(event, "title", None) != TITLE
            or getattr(event, "tl", None) != "fr"
            or getattr(event, "dl", None) != "ar"):
        return False
    start = getattr(event, "start", None)
    stop = getattr(event, "stop", None)
    if not start or not stop or not start.tzinfo or not stop.tzinfo:
        return False
    local_start = start.astimezone(TZ)
    local_stop = stop.astimezone(TZ)
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if local_start.minute != 0 or local_start.second != 0 or local_start.microsecond != 0:
        return False
    if local_stop != local_start + timedelta(hours=1):
        return False
    return now.date() <= local_start.date() < now.date() + timedelta(days=7)


def generate_hourly(days=7, now=None, verified=()):
    """Generate reported one-hour programme loop unless a real event overlaps.

    This purposely does not use/archive any stale SNRT schedule. Time range is
    Casablanca calendar days including today. Any verified same-channel live
    programme supersedes the complete intersecting hours conservatively.
    """
    now = (now or datetime.now(TZ)).astimezone(TZ)
    days = min(max(int(days), 0), 7)
    relevant = tuple(
        e for e in verified
        if e.channel in IDS and e.start and e.stop and e.stop > e.start
    )
    result = []
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        for hour in range(DAILY_SLOTS):
            start = datetime.combine(day, time(hour), TZ)
            stop = start + timedelta(hours=1)
            for cid in sorted(IDS):
                if any(e.channel == cid and e.start < stop and e.stop > start for e in relevant):
                    continue
                result.append(base.Event(
                    cid, start, TITLE, DESCRIPTION, stop, "fr", "ar", SOURCE
                ))
    return result
