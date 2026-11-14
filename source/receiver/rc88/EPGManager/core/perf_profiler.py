# -*- coding: utf-8 -*-
"""Tiny in-process timing profiler for EPGManager beta81.

No timers, threads, disk writes or network.  Callers explicitly record a few
high-value UI timings so performance problems can be measured on the receiver
instead of guessed from development hardware.
"""
from __future__ import print_function
import threading

_LOCK = threading.RLock()
_ROWS = {}

def record(name, seconds):
    try:
        ms = max(0.0, float(seconds) * 1000.0)
    except Exception:
        return
    key = str(name or "operation")
    with _LOCK:
        row = _ROWS.setdefault(key, {"count": 0, "total_ms": 0.0, "last_ms": 0.0, "max_ms": 0.0})
        row["count"] += 1
        row["total_ms"] += ms
        row["last_ms"] = ms
        row["max_ms"] = max(float(row.get("max_ms") or 0.0), ms)

def report():
    with _LOCK:
        out = []
        for name, row in _ROWS.items():
            count = max(1, int(row.get("count") or 0))
            x = dict(row)
            x["avg_ms"] = float(row.get("total_ms") or 0.0) / count
            out.append((name, x))
        return sorted(out, key=lambda pair: pair[0].lower())

def reset():
    with _LOCK:
        _ROWS.clear()
