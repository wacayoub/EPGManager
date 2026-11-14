# -*- coding: utf-8 -*-
"""Tiny GitHub control-plane synchronizer for EPGManager rc35.

Only the small JSON metadata files are fetched here. XMLTV programme payloads
remain on the normal import path, so opening the dashboard never downloads or
parses large EPG files.
"""
from __future__ import print_function

import csv
import io
import json
import os
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from .downloader import Downloader
from .logger import get_logger

log = get_logger(__name__)

BASE = "https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds"
REPORT_CSV = "https://raw.githubusercontent.com/wacayoub/EPGManager/main/reports/epg-source-id-monitoring.csv"
CACHE_PATH = "/etc/enigma2/epgmanager_github_direct_status.json"
DIRECT_SOURCES = (
    ("ext_epgscrapers_morocco", "Morocco", "morocco"),
    ("ext_epgscrapers_bein", "beIN", "bein"),
    ("ext_epgscrapers_elcinema", "ElCinema", "elcinema"),
    ("ext_epgscrapers_osn", "OSN", "osn"),
    ("ext_epgscrapers_sport24", "Sport24", "sport24"),
    ("ext_epgscrapers_dubaiplus", "Dubai+", "dubaiplus"),
    ("ext_epgscrapers_starzplay", "STARZPLAY GCC", "starzplay"),
    ("ext_epgscrapers_shahid", "Shahid / MBC", "shahid"),
    ("ext_epgscrapers_rotana", "Rotana", "rotana"),
    ("ext_epgscrapers_stctv", "STC TV", "stctv"),
    ("ext_epgscrapers_aljazeera", "Al Jazeera", "aljazeera"),
    ("ext_epgscrapers_alkass", "Al Kass", "alkass"),
    ("ext_epgscrapers_tunisiatv", "TunisiaTV", "tunisiatv"),
    ("ext_epgscrapers_tabie", "Tabie QMC", "tabie"),
)
DIRECT_IDS = tuple(x[0] for x in DIRECT_SOURCES)
_LOCK = threading.RLock()
_RUNNING = False

STALE_AFTER_HOURS = 36.0


def _generated_age_hours(value):
    """Best-effort age for ISO-8601 feed metadata; None means unknown."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (time.time() - float(dt.timestamp())) / 3600.0)
    except Exception:
        return None



def load():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    parent = os.path.dirname(CACHE_PATH)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


def _fetch_one(source_id, label, stem):
    url = "%s/%s.json" % (BASE, stem)
    started = time.monotonic()
    dl = Downloader(retries=1, timeout=4)
    try:
        resp = dl.get(url)
        payload = resp.json()
        channels = int(payload.get("channels") or payload.get("input_channels") or 0)
        programmes = int(payload.get("programmes") or 0)
        zero_epg = int(payload.get("zero_epg_channels") or 0)
        active_raw = payload.get("active_channels")
        if active_raw is None:
            active = max(0, channels - zero_epg)
        else:
            active = int(active_raw or 0)
        zero_ids = [str(x) for x in (payload.get("zero_epg_ids") or []) if str(x or "").strip()]
        if zero_epg <= 0 and channels > active:
            zero_epg = max(0, channels - active)
        coverage = payload.get("coverage_pct")
        if coverage is None:
            coverage = (100.0 * active / channels) if channels > 0 else 0.0
        coverage = float(coverage or 0.0)
        generated = str(payload.get("generated_utc") or payload.get("generated") or "")
        age_hours = _generated_age_hours(generated)
        stale = bool(age_hours is not None and age_hours > STALE_AFTER_HOURS)
        online = bool(channels > 0 and programmes > 0)
        degraded = bool(online and (stale or zero_epg > 0 or coverage < 99.95))
        state = "OFFLINE" if not online else ("STALE" if stale else ("DEGRADED" if degraded else "OK"))
        return source_id, {
            "id": source_id,
            "label": label,
            "stem": stem,
            "ok": online,
            "degraded": degraded,
            "stale": stale,
            "state": state,
            "channels": channels,
            "active_channels": active,
            "zero_epg_channels": zero_epg,
            "zero_epg_ids": zero_ids,
            "programmes": programmes,
            "coverage_pct": coverage,
            "generated": generated,
            "generated_age_hours": age_hours,
            "latency_ms": int(max(0.0, (time.monotonic() - started) * 1000.0)),
            "checked": int(time.time()),
            "url": "%s/%s.xml.gz" % (BASE, stem),
        }
    except Exception as exc:
        return source_id, {
            "id": source_id, "label": label, "stem": stem, "ok": False,
            "degraded": True, "stale": False, "state": "OFFLINE",
            "channels": 0, "active_channels": 0, "zero_epg_channels": 0,
            "zero_epg_ids": [], "programmes": 0, "coverage_pct": 0.0,
            "generated": "", "generated_age_hours": None,
            "latency_ms": int(max(0.0, (time.monotonic() - started) * 1000.0)),
            "checked": int(time.time()), "error": str(exc),
            "url": "%s/%s.xml.gz" % (BASE, stem),
        }
    finally:
        try:
            dl.close()
        except Exception:
            pass



def _fetch_id_monitor():
    """Fetch the GitHub per-ID monitoring table once for all direct feeds."""
    dl = Downloader(retries=1, timeout=8)
    try:
        resp = dl.get(REPORT_CSV)
        text = resp.text or ""
        rows = {stem: [] for _sid, _label, stem in DIRECT_SOURCES}
        for item in csv.DictReader(io.StringIO(text)):
            if str(item.get("provider") or "").strip().lower() != "direct":
                continue
            stem = str(item.get("source") or "").strip().lower()
            if stem not in rows:
                continue
            status = str(item.get("status") or "").strip().upper()
            future = int(float(item.get("future_programmes") or 0))
            hours = float(item.get("future_hours") or 0.0)
            rows[stem].append({
                "id": str(item.get("id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "status": status,
                "on": bool(status == "OK" and future > 0),
                "has_epg": bool(future > 0),
                "future_programmes": future,
                "future_hours": hours,
                "reason": str(item.get("reason") or "").strip(),
            })
        return rows
    except Exception as exc:
        log.warning("GitHub ID monitor fetch failed: %s", exc)
        return {}
    finally:
        try:
            dl.close()
        except Exception:
            pass


def source_stem(source_id):
    wanted = str(source_id or "").strip().lower()
    for sid, _label, stem in DIRECT_SOURCES:
        if str(sid).lower() == wanted:
            return stem
    return ""


def id_monitor_row(source_id, channel_id, data=None):
    """Return cached GitHub monitoring metadata for one direct XMLTV ID.

    The lookup is case-insensitive and cache-only.  An empty dict means the
    monitoring report has no opinion yet; callers must not treat UNKNOWN as
    broken or remove a mapping merely because the report is unavailable.
    """
    stem = source_stem(source_id)
    cid = str(channel_id or "").strip().casefold()
    if not stem or not cid:
        return {}
    payload = data if isinstance(data, dict) else load()
    for row in ((payload.get("id_monitor") or {}).get(stem) or []):
        if str((row or {}).get("id") or "").strip().casefold() == cid:
            return dict(row or {})
    return {}


def id_health(source_id, channel_id, data=None):
    """Classify a direct ID as OK / WARNING / NO_EPG / UNKNOWN.

    rc43 safety order:
      1. a complete local XML profile is authoritative when it has a dynamic
         future horizon;
      2. a remote NO_EPG row cannot overrule a newer local XML generation;
      3. otherwise the GitHub monitor is used while fresh.

    This prevents a lagging monitoring CSV from painting a newly repaired ID
    red or pruning its automatic mapping.
    """
    sid = str(source_id or "").strip()
    cid = str(channel_id or "").strip()
    now = int(time.time())
    from .quality_profile_freshness import authoritative_profile
    payload = data if isinstance(data, dict) else load()
    try:
        monitor_checked = int(payload.get("id_monitor_checked") or payload.get("checked") or 0)
    except (ValueError, TypeError, OverflowError):
        return "UNKNOWN"

    # A complete profile is produced automatically whenever a direct XML file
    # is downloaded.  It costs no network I/O here and is the closest truth to
    # what Import All will actually inject.
    try:
        from . import source_quality
        local = source_quality.get_channel_cached(sid, cid) or {}
        if authoritative_profile(local, monitor_checked, now):
            future_last = int(local.get("future_last") or 0)
            if future_last > now:
                return "OK"
            return "NO_EPG"
    except Exception:
        pass

    row = id_monitor_row(sid, cid, data=payload)
    if not row:
        return "UNKNOWN"
    checked = int(payload.get("id_monitor_checked") or payload.get("checked") or 0)
    if checked and (time.time() - checked) > 24 * 3600:
        return "UNKNOWN"

    future = int(row.get("future_programmes") or 0)
    status = str(row.get("status") or "").upper()
    if future <= 0:
        # If the receiver has a newer direct XML than the monitor snapshot, the
        # red verdict is stale by definition.  Keep it UNKNOWN until the local
        # full-file audit above can prove green/red.
        stem = source_stem(sid)
        if stem and checked:
            try:
                local_path = "/etc/epgmanager_epg/%s.xml" % stem
                if os.path.isfile(local_path) and int(os.path.getmtime(local_path)) > checked:
                    return "UNKNOWN"
            except Exception:
                pass
        return "NO_EPG"
    # Positive monitor metadata is not a measured usable guide.
    # Keep review until a current channel-specific profile can certify it.
    return "WARNING"

def dead_id(source_id, channel_id, data=None):
    return id_health(source_id, channel_id, data=data) == "NO_EPG"


def filter_usable_rows(source_id, rows, data=None):
    """Drop only IDs proven to have zero future EPG; preserve UNKNOWN rows."""
    payload = data if isinstance(data, dict) else load()
    out = []
    for row in rows or []:
        cid = str((row or {}).get("channel_id") or "").strip()
        if cid and dead_id(source_id, cid, data=payload):
            continue
        out.append(row)
    return out


def refresh_id_monitor_sync():
    """Refresh only the small per-ID GitHub monitoring CSV synchronously."""
    rows = _fetch_id_monitor()
    if not rows:
        return {}
    payload = load() or {}
    payload["id_monitor"] = rows
    payload["id_monitor_checked"] = int(time.time())
    payload.setdefault("version", 2)
    payload.setdefault("repo", "wacayoub/EPGManager")
    payload.setdefault("branch", "main")
    _save(payload)
    return rows

def is_running():
    with _LOCK:
        return bool(_RUNNING)


def sync_async(force=False, min_age=60):
    """Refresh all current GitHub direct sources and their ID-monitor metadata.

    Returns True only when a new worker was launched. The dashboard polls the
    local cache; worker threads never touch Enigma2 widgets.
    """
    global _RUNNING
    with _LOCK:
        if _RUNNING:
            return False
        cached = load()
        try:
            age = time.time() - float(cached.get("checked") or 0)
        except Exception:
            age = 10 ** 9
        if not force and age < int(min_age):
            return False
        _RUNNING = True

    def worker():
        global _RUNNING
        rows = {}
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = [pool.submit(_fetch_one, *src) for src in DIRECT_SOURCES]
                for fut in as_completed(futs):
                    sid, row = fut.result()
                    rows[sid] = row
            id_monitor = _fetch_id_monitor()
            # A manual GREEN sync is a real source synchronization: refresh all
            # current local XMLTV files as well, not just the tiny JSON cards.
            if force:
                try:
                    from . import external_sources
                    try:
                        from .config import get_epg_output_dir
                        epg_dir = get_epg_output_dir()
                    except Exception:
                        epg_dir = "/etc/epgmanager_epg"
                    for sid, _label, stem in DIRECT_SOURCES:
                        try:
                            path = external_sources.download_source(sid, epg_dir, retries=2, timeout=30, force=True)
                            rows.setdefault(sid, {})["local_synced"] = int(time.time())
                            rows[sid]["local_path"] = path
                        except Exception as exc:
                            rows.setdefault(sid, {})["local_sync_error"] = str(exc)
                            rows[sid]["ok"] = False
                except Exception:
                    log.exception("GitHub full XML sync failed")
            now = int(time.time())
            payload = {
                "version": 2,
                "repo": "wacayoub/EPGManager",
                "branch": "main",
                "checked": now,
                "id_monitor_checked": now if id_monitor else int((load() or {}).get("id_monitor_checked") or 0),
                "sources": rows,
                "id_monitor": id_monitor if id_monitor else ((load() or {}).get("id_monitor") or {}),
            }
            from .sync_timing import preserve_sync_receipts
            with _LOCK:
                payload["sources"] = preserve_sync_receipts(
                    (load() or {}).get("sources"), payload["sources"])
                _save(payload)
            log.info("GitHub direct sync complete: %d/%d healthy", sum(1 for x in rows.values() if x.get("ok")), len(rows))
        except Exception:
            log.exception("GitHub direct sync failed")
        finally:
            with _LOCK:
                _RUNNING = False

    threading.Thread(target=worker, name="EPGM-GitHubSync", daemon=True).start()
    return True
