# -*- coding: utf-8 -*-
"""Low-impact source intelligence for Smart Sources (beta66).

All functions consume caches already produced by EPGManager.  They never
perform network requests, bouquet scans or XMLTV parsing, which keeps the
Smart Sources UI responsive on small Enigma2 receivers.
"""
from __future__ import print_function
import time

from . import source_quality, source_fast_status, source_readiness

GOOD = set(("READY", "EPG READY", "ONLINE", "LOCAL", "CATALOG OK", "OK", "SUCCESS", "DONE"))
BAD = set(("OFFLINE", "NO EPG", "NOK", "ERROR", "FAILED"))


def _age_bonus(source_id):
    checked = 0
    try:
        checked = int((source_readiness.get(source_id) or {}).get("checked") or 0)
    except Exception:
        checked = 0
    if not checked:
        try:
            checked = int((source_fast_status.get(source_id) or {}).get("checked") or 0)
        except Exception:
            checked = 0
    if not checked:
        return 0
    age = max(0, int(time.time()) - checked)
    if age <= 6 * 3600:
        return 10
    if age <= 24 * 3600:
        return 6
    if age <= 72 * 3600:
        return 2
    return 0


def score(item, mapped_count=0):
    """Return a stable 0..100 source score from cached receiver relevance + quality."""
    item = item or {}
    sid = str(item.get("id") or "")
    health = str(item.get("_runtime_state") or item.get("_health") or "").upper()
    mapped = max(0, int(mapped_count or item.get("_mapped_count") or 0))
    total = max(0, int(item.get("_id_total") or 0))
    linked = max(0, int(item.get("_id_linked") or 0))
    conflicts = max(0, int(item.get("_id_conflicts") or 0))

    value = 18
    if health in GOOD:
        value += 20
    elif health in BAD:
        value -= 32
    elif health in ("WEAK", "MAP NEEDED"):
        value -= 8
    else:
        value += 5

    # Receiver relevance is the strongest signal. A source that links nothing
    # on this receiver should not become BEST simply because its feed is rich.
    if mapped:
        value += min(25, 8 + mapped * 2)
    if total:
        ratio = min(1.0, float(linked) / float(total))
        value += int(round(ratio * 28.0))
        if linked:
            value += 5
    elif linked:
        value += min(20, 5 + linked)

    # beta79 performance: detailed source_quality profiles live as one file per
    # feed. Stat-ing ~200 files while opening/changing Smart Sources pages is not
    # worth a small tie-breaker. Readiness/health already carries quality state;
    # the mapping resolver still uses its own detailed quality logic when needed.
    value += _age_bonus(sid)
    value -= min(20, conflicts * 5)
    if item.get("kind") == "local":
        value += 3
    return max(0, min(100, int(value)))


def is_useful(item):
    """True only when cached data says this source can serve this receiver."""
    item = item or {}
    return bool(int(item.get("_mapped_count") or 0) > 0 or int(item.get("_id_linked") or 0) > 0)
