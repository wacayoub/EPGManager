# -*- coding: utf-8 -*-
"""Fast source-status service for EPGManager beta30.

The previous readiness manager parsed a bounded part of every XMLTV feed in
order to count channels/programmes.  That is useful for quality arbitration,
but unnecessary for the Smart Sources dashboard and too slow for 100+ feeds.

beta30 dashboard policy:
- no XMLTV parsing;
- no XMLTV body download;
- lightweight HTTP HEAD (or a 1-byte Range fallback when HEAD is rejected);
- only selected/pinned remote sources checked with a small worker pool;
- one shared Rytec channels-map availability check;
- results cached for six hours;
- detailed programme/channel quality stays on-demand in Smart Mapping/Deep Check.
"""
from __future__ import print_function

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_tz, mktime_tz

try:
    import requests
except ImportError:
    requests = None

from .logger import get_logger
from .scheduler import eTimer, connect_timer
from . import source_catalog, source_fast_status, external_sources, ui_activity, remote_http_cache
from .downloader import DEFAULT_HEADERS

log = get_logger(__name__)

STARTUP_QUIET_SECONDS = 60
REFRESH_TTL = 6 * 3600
POLL_INTERVAL_MS = 10 * 60 * 1000
MAX_WORKERS = 2
CONNECT_TIMEOUT = 2.5
READ_TIMEOUT = 3.5


class AutoReadinessManager(object):
    """Compatibility name retained; implementation is now HEAD-only status."""
    def __init__(self, config, busy_cb=None):
        self.config = config
        self.busy_cb = busy_cb or (lambda: False)
        self._timer = eTimer()
        connect_timer(self._timer, self._tick)
        self._quiet_until = time.time() + STARTUP_QUIET_SECONDS
        self._running = False
        self._stopped = False
        self._active_source = None

    def start(self):
        self._stopped = False
        # First pass quickly after Enigma2 startup; later passes are cache/TTL based.
        self._timer.start(3000, False)
        log.info("Fast Smart Sources status armed: strict HEAD-only, 2 workers, no catalogue/XMLTV parsing")

    def stop(self):
        self._stopped = True
        try: self._timer.stop()
        except Exception: pass

    @staticmethod
    def _head_one(url):
        if requests is None:
            return False, "python3-requests unavailable", 0, 0, 0, "", ""
        response = None
        started = time.monotonic()
        try:
            headers = dict(DEFAULT_HEADERS)
            headers["Accept-Encoding"] = "identity"
            response = requests.head(str(url), headers=headers, allow_redirects=True,
                                     timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), verify=True)
            code = int(response.status_code or 0)
            latency_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
            if 200 <= code < 400:
                try: size = int(response.headers.get("Content-Length") or 0)
                except Exception: size = 0
                last_modified = str(response.headers.get("Last-Modified") or "")
                etag = str(response.headers.get("ETag") or "")
                try:
                    remote_updated = int(mktime_tz(parsedate_tz(last_modified))) if last_modified else 0
                except Exception:
                    remote_updated = 0
                return True, "HTTP %d" % code, size, remote_updated, latency_ms, etag, last_modified
            if code in (400, 403, 405, 416):
                try: response.close()
                except Exception: pass
                response = requests.get(str(url), headers=dict(headers, Range="bytes=0-0"),
                                        stream=True, allow_redirects=True,
                                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), verify=True)
                code = int(response.status_code or 0)
                latency_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
                if code in (200, 206):
                    last_modified = str(response.headers.get("Last-Modified") or "")
                    etag = str(response.headers.get("ETag") or "")
                    try:
                        remote_updated = int(mktime_tz(parsedate_tz(last_modified))) if last_modified else 0
                    except Exception:
                        remote_updated = 0
                    return True, "HTTP %d range" % code, 0, remote_updated, latency_ms, etag, last_modified
            return False, "HTTP %d" % code, 0, 0, latency_ms, "", ""
        except Exception as exc:
            return False, str(exc), 0, 0, int(max(0.0, (time.monotonic() - started) * 1000.0)), "", ""
        finally:
            try:
                if response is not None: response.close()
            except Exception:
                pass

    @classmethod
    def _check_urls(cls, item):
        urls = list((item or {}).get("urls") or [])
        if not urls and (item or {}).get("url"):
            urls = [item.get("url")]
        last = "No URL"
        last_latency = 0
        for url in urls:
            ok, msg, size, remote_updated, latency_ms, etag, last_modified = cls._head_one(url)
            last_latency = latency_ms
            if ok:
                return True, str(url), msg, size, remote_updated, latency_ms, etag, last_modified
            last = "%s: %s" % (url, msg)
        return False, "", last, 0, 0, last_latency, "", ""

    @classmethod
    def _check_rytec_map(cls):
        last = ""
        for url in external_sources.RYTEC_CHANNEL_MAP_URLS:
            ok, msg, size, remote_updated, latency_ms, etag, last_modified = cls._head_one(url)
            if ok:
                return True, str(url), msg
            last = "%s: %s" % (url, msg)
        return False, "", last

    def _epg_dir(self):
        try: return self.config.get_epg_output_dir()
        except Exception: return "/etc/epgmanager_epg"

    def _local_row(self, item):
        filename = str((item or {}).get("output_file") or "")
        path = os.path.join(self._epg_dir(), filename) if filename else ""
        exists = bool(path and os.path.isfile(path) and os.path.getsize(path) > 32)
        return {
            "source_id": str(item.get("id") or ""),
            "source_name": str(item.get("name") or item.get("id") or ""),
            "state": "LOCAL" if exists else "WAITING",
            "online": exists,
            "channel_status": "LOCAL" if exists else "-",
            "programme_status": "LOCAL" if exists else "-",
            "url": path,
            "message": "Local XMLTV file available" if exists else "Local generator will create the XMLTV file",
            "remote_updated": int(os.path.getmtime(path)) if exists else 0,
            "mode": "fast-status",
        }

    def _remote_row(self, item, rytec_map_online=None, prepare_catalog=False):
        ok, url, msg, size, remote_updated, latency_ms, etag, last_modified = self._check_urls(item)
        sid = str((item or {}).get("id") or "")
        previous = source_fast_status.get(sid) or {}
        failures = 0 if ok else (int(previous.get("consecutive_failures") or 0) + 1)
        last_success = int(time.time()) if ok else int(previous.get("last_success") or 0)
        try:
            if ok:
                remote_http_cache.record_success(sid, url, etag=etag, last_modified=last_modified,
                                                 status=200, latency_ms=latency_ms, content_length=size)
            else:
                remote_http_cache.record_failure(sid, url or str((item or {}).get("url") or ""),
                                                 msg, latency_ms=latency_ms)
        except Exception:
            pass
        provider = str((item or {}).get("provider") or "").upper()
        catalog_count = 0
        catalog_ok = False
        # Lightweight mapping catalogues are independent from programme-feed
        # reachability. This keeps Smart Mapping useful during provider
        # throttling/temporary .gz failures, without downloading XMLTV bodies.
        catalog_meta = {}
        # beta81 cleanup: readiness is now *strictly* status-only.  It never
        # downloads even a small Channel-ID catalogue.  Mixing status checks
        # with catalogue preparation created hidden network/CPU work after boot
        # and made Smart Mapping feel unpredictable on small receivers.
        # Channel-ID catalogues are created only by an explicit Source Refresh
        # (or from already downloaded/local source data).
        prepare_catalog = False
        if provider == "RYTEC":
            map_ok = bool(rytec_map_online)
            channel_status = "NATIVE SRP" if map_ok else "MAP OFF"
        elif catalog_count:
            map_ok = True
            channel_status = "%d IDs" % catalog_count
        else:
            map_ok = ok
            channel_status = "INLINE" if ok else "OFFLINE"
        generated = int((catalog_meta or {}).get("catalog_generated_at") or 0)
        stale = False
        stale_age_days = 0.0
        # EPGShare catalogues carry a generation timestamp.  A reachable but
        # years-old guide must never be labelled READY/ONLINE for programme use.
        if provider == "EPGSHARE" and generated:
            stale_age_days = max(0.0, float(time.time() - generated) / 86400.0)
            stale = stale_age_days > 4.0
        if stale:
            state = "STALE"
            programme_status = "STALE"
            msg = "Catalogue is %.0f day(s) old; IDs kept only as aliases" % stale_age_days
        else:
            state = "ONLINE" if ok else ("CATALOG OK" if catalog_ok else "OFFLINE")
            programme_status = "ONLINE" if ok else ("RETRY" if catalog_ok else "OFFLINE")
        return {
            "source_id": str(item.get("id") or ""),
            "source_name": str(item.get("name") or item.get("id") or ""),
            "state": state,
            "online": bool(ok),
            "usable_epg": bool(ok and not stale),
            "stale": bool(stale),
            "channel_online": bool(map_ok),
            "channel_status": channel_status,
            "programme_status": programme_status,
            "url": url or str((item or {}).get("url") or ""),
            "content_length": int(size or 0),
            "remote_updated": int(remote_updated or 0),
            "latency_ms": int(latency_ms or 0),
            "etag": str(etag or ""),
            "last_modified": str(last_modified or ""),
            "last_success": int(last_success or 0),
            "consecutive_failures": int(failures or 0),
            "catalog_channels": int(catalog_count or 0),
            "advertised_channels": int((item or {}).get("advertised_channels") or 0),
            "advertised_programmes": int((item or {}).get("advertised_programmes") or 0),
            "catalog_generated_at": generated,
            "catalog_age_days": round(stale_age_days, 2),
            "message": msg,
            "mode": "fast-status",
        }

    def _needs_refresh(self, item):
        sid = str((item or {}).get("id") or "")
        return not source_fast_status.fresh(sid, REFRESH_TTL)

    def _run_batch(self):
        try:
            items = list(source_catalog.all_sources())
            # Final-release governor: automatic status probes are useful only
            # for local generators plus sources the user actually selected or
            # pinned. Probing ~190 cold remote feeds every TTL window wastes
            # sockets/CPU on a small receiver and does not improve mapping.
            try:
                from .source_preferences import SourcePreferences
                prefs = SourcePreferences()
                selected = set(prefs.selected_catalogue_ids())
                pinned = set(prefs.pinned_catalogue_ids())
            except Exception:
                selected, pinned = set(), set()
            wanted = selected | pinned
            pending = [x for x in items if self._needs_refresh(x) and
                       (str((x or {}).get("kind") or "").lower() == "local" or
                        str((x or {}).get("id") or "") in wanted)]
            pending.sort(key=lambda x: (0 if str((x or {}).get("kind") or "").lower() == "local" else 1,
                                        0 if str(x.get("id") or "") in selected else 1,
                                        str(x.get("provider") or ""), str(x.get("name") or "").casefold()))
            if not pending:
                # beta81: status service has one responsibility only.  SRP/ID
                # rebuilds are event-driven elsewhere (lamedb verification,
                # explicit Refresh/Import), never piggy-backed on a status poll.
                return
            results = {}
            # Local status is just a stat() and costs practically nothing.
            remotes = []
            for item in pending:
                if str(item.get("kind") or "").lower() == "local":
                    results[str(item.get("id") or "")] = self._local_row(item)
                else:
                    remotes.append(item)

            # Rytec has one shared channels map; check it once, not for every feed.
            has_rytec = any(str(x.get("provider") or "").upper() == "RYTEC" for x in remotes)
            rytec_map_online = None
            if has_rytec:
                rytec_map_online, _, _ = self._check_rytec_map()

            if remotes:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    future_map = {
                        pool.submit(
                            self._remote_row, item, rytec_map_online,
                            str(item.get("id") or "") in selected
                        ): item for item in remotes
                    }
                    for future in as_completed(future_map):
                        item = future_map[future]
                        sid = str(item.get("id") or "")
                        self._active_source = sid
                        try:
                            results[sid] = future.result()
                        except Exception as exc:
                            results[sid] = {
                                "source_id": sid, "source_name": item.get("name") or sid,
                                "state": "OFFLINE", "online": False,
                                "channel_status": "OFFLINE", "programme_status": "OFFLINE",
                                "message": str(exc), "mode": "fast-status",
                            }
            if results:
                source_fast_status.put_many(results)
                online = sum(1 for row in results.values() if row.get("online"))
                stale = sum(1 for row in results.values() if row.get("stale"))
                log.info("Fast source status complete: %d/%d online, %d stale; no XMLTV bodies parsed", online, len(results), stale)
                # beta81: no hidden catalogue harvest or SRP rebuild is
                # launched from the status service.  This keeps the service
                # genuinely HEAD-only and prevents background work competing
                # with the remote-control UI.
        finally:
            self._active_source = None
            self._running = False

    def _tick(self):
        if self._stopped:
            return
        # beta79 UI-priority governor: do not launch a 100+ feed HEAD batch while
        # the user is actively navigating EPGManager.  Re-check shortly after
        # the UI becomes idle; no status data is lost because results are TTL cached.
        if ui_activity.active():
            try:
                self._timer.start(15000, False)
            except Exception:
                pass
            return
        now = time.time()
        if now < self._quiet_until:
            # beta37 armed the first timer at +3s but kept a +6s quiet window,
            # then returned here without re-arming the one-shot eTimer. The
            # automatic lightweight catalogue/SRP preparation therefore never
            # started on a normal GUI boot. Re-arm for the end of the quiet
            # window instead of silently stopping the worker forever.
            try:
                self._timer.start(max(1000, int((self._quiet_until - now) * 1000) + 250), False)
            except Exception:
                pass
            return
        if not self._running:
            self._running = True
            threading.Thread(target=self._run_batch, daemon=True).start()
        try:
            self._timer.start(POLL_INTERVAL_MS, False)
        except Exception:
            pass

    def status(self):
        return {"active_source": self._active_source, "quiet": max(0, int(self._quiet_until - time.time())),
                "mode": "head-only", "running": bool(self._running)}


def refresh_sources_async(config, source_ids, on_complete=None, max_workers=2):
    """HEAD-only refresh for an explicit source set while Smart Sources is open.

    The global daemon intentionally pauses for UI activity; this helper lets the
    table's CHECKED/SOURCE UPDATED columns stay current without downloading any
    XMLTV body.
    """
    ids=set(str(x or "") for x in (source_ids or []) if str(x or ""))
    if not ids:
        if on_complete: on_complete({})
        return False
    items=[x for x in source_catalog.all_sources() if str((x or {}).get("id") or "") in ids and str((x or {}).get("kind") or "").lower() != "local"]
    def worker():
        results={}; mgr=AutoReadinessManager(config)
        rytec_map_online=None
        if any(str((x or {}).get("provider") or "").upper()=="RYTEC" for x in items):
            try: rytec_map_online,_,_=mgr._check_rytec_map()
            except Exception: rytec_map_online=False
        if items:
            with ThreadPoolExecutor(max_workers=max(1,min(2,int(max_workers or 2),len(items)))) as pool:
                futs={pool.submit(mgr._remote_row,item,rytec_map_online,False):item for item in items}
                for fut in as_completed(futs):
                    item=futs[fut]; sid=str(item.get("id") or "")
                    try: results[sid]=fut.result()
                    except Exception as exc: results[sid]={"source_id":sid,"state":"OFFLINE","online":False,"message":str(exc),"mode":"fast-status"}
        if results: source_fast_status.put_many(results)
        if on_complete:
            try: on_complete(results)
            except Exception: pass
    threading.Thread(target=worker,daemon=True).start()
    return True
