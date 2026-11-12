# -*- coding: utf-8 -*-
"""Health evaluation helpers shared by the dashboard and Health Check screen."""
import datetime

HEALTH_GOOD = "GOOD"
HEALTH_WARNING = "WARNING"
HEALTH_FAILED = "FAILED"
HEALTH_IDLE = "NOT CHECKED"
HEALTH_DISABLED = "DISABLED"
HEALTH_RUNNING = "UPDATING"


def _parse_ddmm(value, today=None):
    if not value:
        return None
    today = today or datetime.date.today()
    try:
        day, month = [int(x) for x in str(value).split("/")[:2]]
    except Exception:
        return None
    # Pick the closest sensible year around today. This handles EPG ranges
    # crossing New Year without requiring the UI status dictionary to carry
    # a year in its compact display value.
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(datetime.date(year, month, day))
        except Exception:
            pass
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - today).days))


def evaluate_health(status, enabled=True, today=None):
    """Return a compact health dict for one source.

    The thresholds are intentionally conservative: one-day EPG sources are
    considered healthy, while zero programmes/coverage or an already expired
    range are surfaced prominently.
    """
    if not enabled:
        return {"level": HEALTH_DISABLED, "score": 0, "message": "Source disabled"}

    raw = (status or {}).get("status", "IDLE")
    if raw == "RUNNING":
        return {"level": HEALTH_RUNNING, "score": 50, "message": "Update in progress"}
    if raw == "FAILED":
        msg = (status.get("error_message") or "Last update failed").strip()
        return {"level": HEALTH_FAILED, "score": 0, "message": msg}
    if raw in ("WARNING", "CANCELLED", "COOLDOWN"):
        msg = (status.get("error_message") or "Source needs attention").strip()
        return {"level": HEALTH_WARNING, "score": 35, "message": msg}

    programmes = int((status or {}).get("program_count") or 0)
    days = int((status or {}).get("days_covered") or 0)
    date_to = _parse_ddmm((status or {}).get("date_to"), today=today)
    today = today or datetime.date.today()

    if raw == "SUCCESS":
        if programmes <= 0:
            return {"level": HEALTH_FAILED, "score": 0, "message": "0 programmes in last result"}
        if date_to is not None and date_to < today:
            return {"level": HEALTH_FAILED, "score": 5, "message": "EPG data is expired"}
        if days <= 0:
            return {"level": HEALTH_WARNING, "score": 40, "message": "No EPG day coverage detected"}
        if programmes < 5:
            return {"level": HEALTH_WARNING, "score": 55, "message": "Very few programmes returned"}
        return {"level": HEALTH_GOOD, "score": 100, "message": "EPG data looks healthy"}

    return {"level": HEALTH_IDLE, "score": 20, "message": "Run an update to check this source"}


def summarize_health(statuses, enabled_map):
    summary = {HEALTH_GOOD: 0, HEALTH_WARNING: 0, HEALTH_FAILED: 0,
               HEALTH_IDLE: 0, HEALTH_DISABLED: 0, HEALTH_RUNNING: 0}
    for st in statuses:
        sid = st.get("id")
        h = evaluate_health(st, enabled_map.get(sid, True))
        summary[h["level"]] = summary.get(h["level"], 0) + 1
    return summary
