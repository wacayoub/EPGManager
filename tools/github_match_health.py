# -*- coding: utf-8 -*-
"""Fast, non-blocking, per-ID EPG health from EPG-Scrapers' live GitHub audits.

All UI lookup functions are RAM-only. The only HTTP requests execute in one
bounded daemon worker; each source's report is fetched at most once per TTL.
No receiver XMLTV cache, bouquet, mappings or importer state is changed.
"""
from __future__ import print_function
from datetime import datetime
import json
import threading
import time
from urllib.request import Request, urlopen

REPORT_BASE = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/reports/"
SUPPORTED = frozenset(("morocco", "bein", "osn", "shahid", "elcinema",
                       "rotana", "dubaiplus", "sport24", "stctv", "aljazeera",
                       "starzplay", "alkass", "tunisiatv", "tabie"))
FRESH_SECONDS = 72 * 3600
RETRY_SECONDS = 1800
REPORT_RELOAD_SECONDS = 3600
READY_SECONDS = 900
_MAX_REPORT_BYTES = 260000
_reports = {}
_attempts = {}
_pending = set()
_generation = 0
_lock = threading.RLock()

def source_key(source_id):
    sid = str(source_id or "").strip().lower()
    for prefix in ("ext_epgscrapers_", "direct_"):
        if sid.startswith(prefix):
            key = sid[len(prefix):]
            return key if key in SUPPORTED else ""
    return ""

def _epoch(value):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def classify(report, channel_id, now=None):
    """NO_EPG only when freshly proven empty; UNKNOWN is not an empty feed."""
    now = float(time.time() if now is None else now)
    if not isinstance(report, dict):
        return "UNKNOWN"
    generated = _epoch(report.get("generated_utc"))
    if not generated or now < generated - 3600 or now - generated > FRESH_SECONDS:
        return "UNKNOWN"
    row = (report.get("rows") or {}).get(str(channel_id or "").strip().casefold())
    if not isinstance(row, dict):
        return "UNKNOWN"
    try:
        programmes = int(row.get("programmes") or 0)
    except Exception:
        programmes = 0
    stop = _epoch(row.get("latest_stop_utc"))
    reason = str(row.get("reason") or "").upper()
    if programmes > 0 and stop > now + READY_SECONDS:
        return "EPG_WARN" if str(row.get("status") or "").upper() != "PASS" else "EPG_OK"
    if reason == "NO_FUTURE_EPG" and programmes == 0:
        return "NO_EPG"
    return "UNKNOWN"

def health(source_id, channel_id, now=None):
    key = source_key(source_id)
    if not key:
        return None  # Not a direct GitHub source. Caller may use its own health.
    with _lock:
        report = _reports.get(key)
    return classify(report, channel_id, now)

def generation():
    with _lock:
        return _generation

def is_busy():
    with _lock:
        return bool(_pending)

def _fetch(key):
    req = Request(REPORT_BASE + key + "-48h-audit.json",
                  headers={"User-Agent":"EPGManager-BestMatch/1.0",
                           "Cache-Control":"no-cache"})
    with urlopen(req, timeout=9) as response:
        raw = response.read(_MAX_REPORT_BYTES + 1)
    if len(raw) > _MAX_REPORT_BYTES:
        raise ValueError("audit size limit exceeded")
    obj = json.loads(raw.decode("utf-8"))
    feeds = obj.get("feeds") or []
    entry = next((row for row in feeds if (row or {}).get("feed") == key + ".xml.gz"), None)
    if not entry:
        raise ValueError("source audit feed missing")
    rows = entry.get("rows") or []
    if not isinstance(rows, list) or len(rows) > 3000:
        raise ValueError("invalid source row count")
    return {"generated_utc":obj.get("generated_utc"),
            "rows":{str(row.get("id") or "").casefold():row
                    for row in rows if isinstance(row,dict) and row.get("id")}}

def _worker(keys):
    global _generation
    from concurrent.futures import ThreadPoolExecutor, as_completed
    fetched = {}
    try:
        with ThreadPoolExecutor(max_workers=min(3, len(keys))) as pool:
            futures = {pool.submit(_fetch, key): key for key in keys}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    fetched[key] = future.result()
                except Exception:
                    pass
    finally:
        # A single UI generation prevents repeated expensive receiver repaints.
        with _lock:
            for key, report in fetched.items():
                _reports[key] = report
            if fetched:
                _generation += 1
            for key in keys:
                _pending.discard(key)

def refresh_async(source_ids, now=None):
    """Schedule only missing/expired reports; never runs I/O in calling thread."""
    now = float(time.time() if now is None else now)
    keys = set(source_key(sid) for sid in source_ids or [])
    wanted = []
    with _lock:
        for key in sorted(keys - {""}):
            cached = _reports.get(key)
            if key in _pending:
                continue
            # Retry stale/failed reports periodically, never on every cursor move.
            if now - float(_attempts.get(key) or 0) < RETRY_SECONDS:
                continue
            if cached and now - float(_attempts.get(key) or 0) < REPORT_RELOAD_SECONDS:
                continue
            _attempts[key] = now
            _pending.add(key)
            wanted.append(key)
    if wanted:
        threading.Thread(target=_worker,args=(wanted,),daemon=True).start()
    return bool(wanted)
