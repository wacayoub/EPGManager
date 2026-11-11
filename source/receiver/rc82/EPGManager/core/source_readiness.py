# -*- coding: utf-8 -*-
"""Low-impact source readiness cache for Smart Sources.

Purpose:
- tell the user whether a selected feed really exposes channel IDs + programme data;
- prepare a missing persistent SRP map *outside* Import All;
- never run a receiver-wide/background queue at boot.

Remote feeds are streamed directly from provider URLs with source_channel_sync;
no full remote XMLTV is stored locally. Local EPGManager feeds are validated from
their already-generated XML.
"""
from __future__ import print_function

import json
import os
import re
import tempfile
import time
from xml.sax.saxutils import escape

from .logger import get_logger
from . import source_channel_cache, source_channel_sync, source_quality, source_catalog, srp_channel_map
from .validation import validate_xmltv_file
from .json_atomic import lock_for, mutate_json, write_json

log = get_logger(__name__)
PATH = "/etc/enigma2/epgmanager_source_readiness.json"
VERSION = 1
REMOTE_TTL = 24 * 3600
_MEM = None
_MEM_SIG = None


def _sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _load():
    global _MEM, _MEM_SIG
    with lock_for(PATH):
        sig = _sig(PATH)
        if isinstance(_MEM, dict) and sig == _MEM_SIG:
            return _MEM
        try:
            with open(PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.setdefault("version", VERSION)
        data.setdefault("sources", {})
        _MEM, _MEM_SIG = data, sig
        return data


def _save(data):
    global _MEM, _MEM_SIG
    try:
        with lock_for(PATH):
            write_json(PATH, data)
            _MEM, _MEM_SIG = data, _sig(PATH)
        return True
    except Exception:
        log.exception("Could not save source readiness cache")
        return False


def all_rows():
    """Return one in-memory snapshot for table rendering."""
    return dict((_load().get("sources") or {}))

def get(source_id):
    return dict(((_load().get("sources") or {}).get(str(source_id)) or {}))


def put(source_id, row):
    global _MEM, _MEM_SIG
    payload = dict(row or {})
    payload["checked"] = int(time.time())
    def _mut(data):
        if not isinstance(data, dict): data = {}
        data.setdefault("version", VERSION)
        data.setdefault("sources", {})[str(source_id)] = payload
        return data
    try:
        with lock_for(PATH):
            data = mutate_json(PATH, _mut, {"version": VERSION, "sources": {}})
            _MEM, _MEM_SIG = data, _sig(PATH)
    except Exception:
        log.exception("Could not save source readiness cache")
    return payload


def fresh(source_id, max_age=REMOTE_TTL):
    row = get(source_id)
    try:
        return bool(row and (time.time() - float(row.get("checked") or 0)) < int(max_age))
    except Exception:
        return False


def _safe(value):
    return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"


def _write_temporary_catalog(source_id, rows):
    """Create a tiny channels-only XML in /tmp only while a new SRP map is built.

    Remote readiness never persists provider XMLTV or channel-catalog XML in
    /etc.  Smart Mapping uses the sharded JSON channel cache; the frozen mapper
    only needs this transient XML for a few seconds when a map is missing.
    """
    fd, path = tempfile.mkstemp(prefix="epgmanager_%s_" % _safe(source_id), suffix=".channels.xml", dir="/tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
            for row in rows or []:
                cid = str((row or {}).get("channel_id") or "").strip()
                if not cid:
                    continue
                name = str((row or {}).get("display_name") or cid)
                fh.write('  <channel id="%s"><display-name>%s</display-name></channel>\n' %
                         (escape(cid, {'"': '&quot;'}), escape(name)))
            fh.write('</tv>\n')
        return path
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(path)
        except Exception:
            pass
        raise


def _map_ready(item):
    mid = source_catalog.mapping_source_id(item)
    return bool(mid and srp_channel_map.map_path_for_source(mid))


def _prepare_map(item, catalog_path, epg_dir):
    """Build one missing map with the frozen beta1/beta12 engine only."""
    if _map_ready(item):
        return True, 0
    try:
        from .source_preferences import FixedCatalogueSelection
        selection = FixedCatalogueSelection(item.get("id"))
        report = srp_channel_map.prepare_selected_maps(
            [item], {item.get("id"): {"ok": True, "path": catalog_path}},
            selection, epg_dir, include_iptv=True, progress_cb=None)
        ok = bool(_map_ready(item))
        return ok, int((report or {}).get("mapped_service_refs") or 0)
    except Exception as exc:
        log.warning("Readiness map preparation failed for %s: %s", item.get("name"), exc)
        return False, 0


def _row_from_quality(item, channels, quality, map_ready, mapped_refs=0, sampled=True):
    overall = dict((quality or {}).get("overall") or {})
    programs = int(overall.get("programs") or 0)
    placeholder = bool(overall.get("placeholder"))
    desc_ratio = float(overall.get("desc_ratio") or 0.0)
    arabic_ratio = float(overall.get("arabic_ratio") or 0.0)
    days = float(overall.get("days") or 0.0)
    complete = bool((quality or {}).get("complete"))
    if programs <= 0:
        state = "NO EPG"
        ready = False
    elif placeholder:
        state = "WEAK"
        ready = False
    elif not map_ready:
        state = "MAP NEEDED"
        ready = False
    else:
        state = "READY"
        ready = True
    return {
        "source_id": str(item.get("id") or ""),
        "source_name": str(item.get("name") or item.get("id") or ""),
        "state": state,
        "ready": ready,
        "channels": int(channels or 0),
        "programmes": programs,
        "programmes_partial": bool(sampled and not complete),
        "desc_ratio": round(desc_ratio, 3),
        "arabic_ratio": round(arabic_ratio, 3),
        "days": round(days, 2),
        "placeholder": placeholder,
        "map_ready": bool(map_ready),
        "mapped_refs": int(mapped_refs or 0),
        "complete": complete,
    }


def harvest_remote_catalog_fast(item, timeout=8, max_scan=8 * 1024 * 1024):
    """Harvest only the compact Channel-ID catalogue needed for a missing SRP map.

    RC20 separates *mapping readiness* from *EPG quality probing*.  Import All
    must not read hundreds of programme rows merely to discover channel IDs.
    Prefer an explicit provider channels.xml when available, otherwise stop the
    XMLTV stream at the first <programme>.  A small programme-sample fallback is
    retained only for non-standard feeds which declare IDs solely on programme
    rows.  No remote programme XML is persisted.
    """
    sid = str((item or {}).get("id") or "")
    rows = []
    meta = {}
    errors = []

    # Zero-network fast path: an existing compact shard is already sufficient.
    try:
        rows = source_channel_cache.get(sid) if sid else []
    except Exception:
        rows = []
    if rows:
        return {"state": "CACHED CHANNEL IDS", "ready": False, "catalog_ready": True,
                "channels": len(rows), "programmes": 0, "map_ready": _map_ready(item),
                "url": str((item or {}).get("url") or ""), "scanned_bytes": 0,
                "transport": "channel-cache", "method": "compact-cache"}

    # Provider-owned channels.xml is usually much smaller than the programme feed.
    if (item or {}).get("channels_urls"):
        try:
            rows, meta = source_channel_sync.fetch_provider_channel_map(
                item, timeout=timeout, max_scan=max(4 * 1024 * 1024, int(max_scan or 0)), fast=True)
        except Exception as exc:
            errors.append(str(exc))
            rows = []

    # Standard XMLTV: parse only the <channel> declaration block.
    if not rows:
        try:
            rows, meta = source_channel_sync.fetch_channel_ids_only(
                item, timeout=timeout, max_scan=max(2 * 1024 * 1024, int(max_scan or 0)),
                connect_timeout=3.0, fast=True)
        except Exception as exc:
            errors.append(str(exc))
            rows = []

    # Compatibility fallback for unusual programme-only feeds.  Keep it much
    # smaller than RC19's 6 MB / 700-programme quality probe.
    if not rows:
        try:
            rows, meta = source_channel_sync.fetch_channels(
                item, timeout=timeout, quality_scan=1024 * 1024, max_programmes=120)
        except Exception as exc:
            errors.append(str(exc))
            rows = []

    if not rows:
        raise RuntimeError("Fast Channel-ID harvest failed (%s)" % "; ".join(errors[-2:]))

    source_channel_cache.put(
        sid, item.get("name") or sid, rows,
        provider=item.get("provider") or "", region=item.get("region") or "",
        language=item.get("language") or "", method=str((meta or {}).get("method") or "fast-import-map"))
    row = {
        "source_id": sid, "source_name": str(item.get("name") or sid),
        "state": "MAP NEEDED" if not _map_ready(item) else "MAP READY",
        "ready": False, "catalog_ready": True, "channels": len(rows),
        "programmes": 0, "programmes_partial": True, "placeholder": False,
        "map_ready": _map_ready(item), "mapped_refs": 0, "complete": False,
        "url": str((meta or {}).get("url") or item.get("url") or ""),
        "scanned_bytes": int((meta or {}).get("scanned_bytes") or 0),
        "transport": "stream-header",
        "method": str((meta or {}).get("method") or "fast-import-map"),
    }
    return put(sid, row)


def probe_remote(item, epg_dir, timeout=8, prepare_map=True, quality_scan=5 * 1024 * 1024,
                 max_programmes=600, guard_cb=None, cooperative_sleep=None):
    """Probe exactly one remote feed. No global queue and no full XMLTV download."""
    sid = str((item or {}).get("id") or "")
    rows, meta = source_channel_sync.fetch_channels(
        item, timeout=timeout, quality_scan=quality_scan, max_programmes=max_programmes,
        guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
    quality = dict((meta or {}).get("quality") or {})
    # Keep the provider catalogue available to Smart Mapping.
    source_channel_cache.put(
        sid, item.get("name") or sid, rows,
        provider=item.get("provider") or "", region=item.get("region") or "",
        language=item.get("language") or "")
    if quality:
        quality["source_name"] = str(item.get("name") or sid)
        source_quality.save(sid, quality)
    map_ready = _map_ready(item)
    mapped_refs = 0
    overall = dict((quality or {}).get("overall") or {})
    useful_epg = int(overall.get("programs") or 0) > 0 and not bool(overall.get("placeholder"))
    # Do not spend receiver CPU or flash storage on a persistent remote XML.
    # If the frozen mapper needs a channels XML, create a tiny /tmp file and
    # delete it immediately after map preparation.
    if prepare_map and useful_epg and not map_ready:
        catalog = None
        try:
            catalog = _write_temporary_catalog(sid, rows)
            map_ready, mapped_refs = _prepare_map(item, catalog, epg_dir)
        finally:
            if catalog:
                try:
                    os.unlink(catalog)
                except Exception:
                    pass
    row = _row_from_quality(item, len(rows), quality, map_ready, mapped_refs=mapped_refs, sampled=True)
    if useful_epg and not prepare_map and not map_ready:
        # Catalogue/background readiness is complete even though we intentionally
        # avoid building 100+ receiver SRP maps for sources the user has not
        # selected. Selecting/importing the source promotes it to READY by
        # building the map on demand.
        row["state"] = "EPG READY"
        row["ready"] = False
        row["catalog_ready"] = True
    row.update({"url": str((meta or {}).get("url") or ""),
                "scanned_bytes": int((meta or {}).get("scanned_bytes") or 0),
                "transport": "stream",
                "catalog_path": ""})
    return put(sid, row)



def prepare_remote_map(item, epg_dir, force=False, timeout=8, progress_cb=None):
    """Build/rebuild one remote SRP map from the provider URL only.

    The provider XMLTV is streamed; only channel ID/name rows are retained.
    A tiny channels-only XML exists in /tmp for the duration of the frozen
    mapper call and is deleted immediately afterwards.
    """
    started = time.time()
    sid = str((item or {}).get("id") or "")
    if not sid:
        return {"sources": 0, "mapped_channel_ids": 0, "mapped_service_refs": 0,
                "unmapped_channel_ids": 0, "elapsed": 0.0, "ok": False,
                "error": "Missing source id"}
    try:
        # Prefer the exact source XML header.  If a feed has no channel block,
        # fetch_channels() continues into a bounded programme sample and still
        # derives membership from the same provider URL.
        rows, meta = source_channel_sync.fetch_channels(
            item, timeout=timeout, quality_scan=6 * 1024 * 1024,
            max_programmes=700)
        source_channel_cache.put(
            sid, item.get("name") or sid, rows,
            provider=item.get("provider") or "", region=item.get("region") or "",
            language=item.get("language") or "", method="remote-url")
        catalog = _write_temporary_catalog(sid, rows)
        try:
            from .source_preferences import FixedCatalogueSelection
            selection = FixedCatalogueSelection(sid)
            report = srp_channel_map.prepare_selected_maps(
                [item], {sid: {"ok": True, "path": catalog, "url": str((meta or {}).get("url") or "")}},
                selection, epg_dir, include_iptv=True, progress_cb=progress_cb)
        finally:
            try:
                os.unlink(catalog)
            except Exception:
                pass
        report = dict(report or {})
        report["ok"] = bool(_map_ready(item))
        report["remote_authoritative"] = True
        report["url"] = str((meta or {}).get("url") or item.get("url") or "")
        report["elapsed"] = max(float(report.get("elapsed") or 0.0), time.time() - started)
        return report
    except Exception as exc:
        return {"sources": 0, "mapped_channel_ids": 0, "mapped_service_refs": 0,
                "unmapped_channel_ids": 0, "elapsed": max(0.0, time.time() - started),
                "ok": False, "remote_authoritative": True, "error": str(exc)}

def probe_local(item, epg_dir, prepare_map=True, guard_cb=None, cooperative_sleep=None):
    sid = str((item or {}).get("id") or "")
    filename = str((item or {}).get("output_file") or "")
    path = os.path.join(epg_dir, filename) if filename else ""
    if not path or not os.path.isfile(path) or os.path.getsize(path) <= 32:
        return put(sid, {"source_id": sid, "source_name": item.get("name") or sid,
                         "state": "NO FILE", "ready": False, "channels": 0,
                         "programmes": 0, "programmes_partial": False,
                         "map_ready": _map_ready(item), "complete": True})
    vr = validate_xmltv_file(path, min_programs=1)
    quality = source_quality.ensure_local(item, epg_dir) or {}
    map_ready = _map_ready(item)
    mapped_refs = 0
    if prepare_map and bool(vr.ok) and not map_ready:
        map_ready, mapped_refs = _prepare_map(item, path, epg_dir)
    row = _row_from_quality(item, int(vr.channel_count or 0), quality, map_ready,
                            mapped_refs=mapped_refs, sampled=False)
    # Validation is the source of truth for exact local programme count.
    row["programmes"] = int(vr.program_count or 0)
    row["programmes_partial"] = False
    if not vr.ok:
        row["state"] = "NO EPG" if int(vr.program_count or 0) <= 0 else "NOK"
        row["ready"] = False
    row["file_sig"] = list(_sig(path) or [])
    return put(sid, row)


def probe(item, epg_dir, timeout=8, prepare_map=True, quality_scan=5 * 1024 * 1024,
          max_programmes=600, guard_cb=None, cooperative_sleep=None):
    if guard_cb is not None:
        try:
            if bool(guard_cb()):
                raise source_channel_sync.SyncDeferred("Receiver is busy")
        except source_channel_sync.SyncDeferred:
            raise
        except Exception:
            pass
    if str((item or {}).get("kind") or "").lower() == "local":
        return probe_local(item, epg_dir, prepare_map=prepare_map,
                           guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
    return probe_remote(item, epg_dir, timeout=timeout, prepare_map=prepare_map,
                        quality_scan=quality_scan, max_programmes=max_programmes,
                        guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
