# -*- coding: utf-8 -*-
"""Bounded Service Reference map repair for beta116-beta121.

Healthy maps are a strict fast path. Real map migrations run once, and one
unavailable provider can no longer keep a global migration marker alive and
force every selected source through the matcher on every Import All.
"""
from __future__ import print_function

import os
import time

from . import (channel_registry, provider_srp_map, source_catalog,
               source_channel_cache, source_channel_sync, srp_channel_map,
               srp_master_engine, balanced_exact_enricher)
from .logger import get_logger

log = get_logger(__name__)
# beta116-118 really changed generated Service Reference maps and therefore
# required one revalidation. beta119 changed only Smart-Mapping ranking/UI
# context; the SRP engine and its safety policy were bit-for-bit unchanged.
# Treating beta119 as a map migration caused every selected generated source to
# be rematched unnecessarily on the next Import All.
REVALIDATE_PENDING_PATHS = (
    "/etc/enigma2/.epgmanager_beta116_service_map_repair_pending",
    "/etc/enigma2/.epgmanager_beta117_service_map_repair_pending",
    "/etc/enigma2/.epgmanager_beta118_service_map_repair_pending",
)
NOOP_PENDING_PATHS = (
    "/etc/enigma2/.epgmanager_beta119_service_map_repair_pending",
)
PENDING_PATHS = REVALIDATE_PENDING_PATHS + NOOP_PENDING_PATHS
BALANCED_EXACT_PENDING = "/etc/enigma2/.epgmanager_beta126_balanced_exact_pending"
NATIVE_ARABIC_PENDING = "/etc/enigma2/.epgmanager_beta127_native_arabic_coverage_pending"


def _unlink(paths):
    for path in paths or ():
        try:
            os.unlink(path)
        except OSError:
            pass


def _pending_revalidation_paths():
    """Return only migrations that still genuinely need a full SRP pass.

    Older beta116-118 code kept the global pending marker when even one selected
    provider remained unavailable.  A successful SRP master state may therefore
    be newer than the marker although the marker still exists.  In that case the
    expensive generated-map revalidation has already happened and must not run
    again on every Import All.
    """
    # beta119 never changed the SRP matcher/policy. Its marker is always stale
    # from the Service-Map point of view and can be discarded immediately.
    _unlink([p for p in NOOP_PENDING_PATHS if os.path.exists(p)])

    try:
        state = srp_master_engine.load_state() or {}
        checked = int(state.get("checked") or 0)
        has_maps = bool(state.get("source_maps"))
    except Exception:
        checked, has_maps = 0, False

    pending = []
    for path in REVALIDATE_PENDING_PATHS:
        if not os.path.exists(path):
            continue
        try:
            marker_ts = int(os.path.getmtime(path))
        except Exception:
            marker_ts = 0
        # If SRP master completed after this marker was created, the global
        # migration itself is done. Any individual unavailable map is handled by
        # _ready() below without forcing every healthy source through the matcher.
        if has_maps and checked and checked >= marker_ts:
            _unlink((path,))
            continue
        pending.append(path)
    return pending



def _row(index, mid):
    rows = (index or {}).get("sources") or {}
    return rows.get(str(mid)) or rows.get(str(mid).lower()) or {}


def _map_exists(item, index):
    mid = source_catalog.mapping_source_id(item)
    row = _row(index, mid)
    path = str((row or {}).get("map_path") or "")
    return bool(path and os.path.isfile(path))


def _ready(item, index, sat_sig):
    mid = source_catalog.mapping_source_id(item)
    row = _row(index, mid)
    path = str((row or {}).get("map_path") or "")
    if not path or not os.path.isfile(path):
        return False
    if bool((item or {}).get("native_srp")) or bool((row or {}).get("official")):
        return True
    return str((row or {}).get("sat_signature") or "") == str(sat_sig or "")


def _stale_generated(item, index, sat_sig):
    """True when a reusable generated map exists but its lamedb signature moved."""
    if not _map_exists(item, index):
        return False
    mid = source_catalog.mapping_source_id(item)
    row = _row(index, mid)
    if bool((item or {}).get("native_srp")) or bool((row or {}).get("official")):
        return False
    return str((row or {}).get("sat_signature") or "") != str(sat_sig or "")


def _harvest_catalog(item):
    """Fetch channel declarations only; never persist programme XMLTV."""
    sid = str((item or {}).get("id") or "")
    if not sid or source_channel_cache.has(sid):
        return {"ok": True, "cached": True, "channels": source_channel_cache.count(sid)}
    try:
        rows, meta = source_channel_sync.fetch_channel_ids_only(
            item, timeout=9, max_scan=8 * 1024 * 1024, cooperative_sleep=0.003)
        source_channel_cache.put(
            sid, item.get("name") or sid, rows,
            provider=item.get("provider") or "", region=item.get("region") or "",
            language=item.get("language") or "")
        return {"ok": True, "channels": len(rows), "method": "xmltv-channel-header",
                "url": str((meta or {}).get("url") or "")}
    except source_channel_sync.SyncDeferred:
        return {"ok": False, "deferred": True, "reason": "receiver busy"}
    except Exception as exc:
        log.warning("Channel-ID harvest failed for %s: %s", item.get("name"), exc)
        return {"ok": False, "reason": str(exc)}


def repair_selected(config, selected, epg_dir, force=False, progress_cb=None, cache_first=False):
    selected = [dict(x or {}) for x in (selected or []) if x]

    # beta126 one-shot middle-ground recovery.  Reuse cached channel IDs and the
    # live SAT registry to add only PROVEN/SAFE EXACT rows.  No network, no fuzzy
    # scan and no SRP Master rebuild. Once attempted, normal imports are purely
    # cache-first again.
    balanced_report = None
    if cache_first and not force and (os.path.exists(BALANCED_EXACT_PENDING) or os.path.exists(NATIVE_ARABIC_PENDING)):
        try:
            balanced_report = balanced_exact_enricher.run(selected, epg_dir, progress_cb=progress_cb)
        except Exception:
            log.exception("beta126 quick SAFE EXACT enrichment failed")
            balanced_report = {"ok": False, "reason": "exception"}
        _unlink((BALANCED_EXACT_PENDING, NATIVE_ARABIC_PENDING))

    sat_sig = channel_registry.satellite_registry_signature()
    index = srp_channel_map.load_index() or {}
    revalidate_pending = _pending_revalidation_paths()

    # beta125 Import-All contract: a channels.xml/SRP map is persistent cache,
    # not disposable preflight.  DirectNativeImport re-checks every cached row
    # against the current receiver + PrecisionMatch before injection, so an
    # existing map can be safely reused even when lamedb changed. A signature
    # change only requests a non-blocking refresh after the import.
    if cache_first and not force:
        missing = [x for x in selected if not _map_exists(x, index)]
        stale = [x for x in selected if _stale_generated(x, index, sat_sig)]
        if not missing:
            # beta126 persistent-cache contract: after a map exists, Import All
            # never launches the expensive global SRP matcher merely because
            # lamedb mtime/signature drifted. Direct Import validates cached rows
            # fail-closed against the live receiver. New channels are picked up
            # by explicit Refresh Source / the next one-shot maintenance, not by
            # a surprise multi-minute matcher after every import.
            _unlink(revalidate_pending)
            return {
                "ok": True, "needed": 0, "remaining": 0, "cache_reused": True,
                "background_refresh": False,
                "stale_cached": bool(stale),
                "stale_names": [str(x.get("name") or x.get("id") or "") for x in stale[:12]],
                "pending_migration": False,
                "balanced_exact": balanced_report,
            }
        # First map/newly enabled source only. Do not rematch healthy cached
        # sources merely because one source is missing.
        need = list(missing)
    else:
        need = list(selected) if (force or revalidate_pending) else [x for x in selected if not _ready(x, index, sat_sig)]
    if not need:
        return {"ok": True, "needed": 0, "remaining": 0, "cache_reused": True, "background_refresh": False,
                "balanced_exact": balanced_report}

    started = time.time()
    if progress_cb:
        progress_cb(1, "Checking exact provider Service References", "SERVICE MAP")

    native = [x for x in need if bool((x or {}).get("native_srp"))]
    generated = [x for x in need if not bool((x or {}).get("native_srp"))]
    native_report = provider_srp_map.register_selected(native, epg_dir, force=False)

    # Local generators use built-in IDs / their small current XML. Remote
    # generated providers need only the <channel> header in the sharded cache.
    harvest = {}
    remote_generated = [x for x in generated if str((x or {}).get("kind") or "").lower() != "local"]
    for pos, item in enumerate(remote_generated):
        if progress_cb:
            progress_cb(1 + int(35.0 * (pos + 1) / max(1, len(remote_generated))),
                        "Reading channel IDs: %s" % (item.get("name") or item.get("id")), "SERVICE MAP")
        # A migration marker forces map regeneration, not network refresh.
        # Reuse any prepared channel shard; fetch only genuinely missing IDs.
        if force or not source_channel_cache.has(item.get("id")):
            harvest[str(item.get("id") or "")] = _harvest_catalog(item)

    master = {"ok": True, "skipped": True}
    if generated:
        if progress_cb:
            progress_cb(40, "Matching selected services to EPG IDs", "SERVICE MAP")
        def _master_progress(percent, detail):
            if progress_cb:
                # Reserve 40..64 for the actual matcher. Unlike beta116-119 the
                # UI now moves while a real rebuild is required instead of being
                # frozen at a misleading 40%%.
                progress_cb(40 + int(24.0 * max(0, min(100, int(percent or 0))) / 100.0),
                            detail or "Matching selected services to EPG IDs", "SERVICE MAP")
        master = srp_master_engine.rebuild(
            config=config, epg_dir=epg_dir, force=True,
            progress_cb=_master_progress, postprocess=False)
        if bool((master or {}).get("ok")):
            # Add native-country Arabic edition fallbacks for newly built maps
            # without another global matcher/network pass.
            try:
                balanced_exact_enricher.run(generated, epg_dir, progress_cb=None)
            except Exception:
                log.exception("beta127 native Arabic coverage enrichment failed")
            # The generated-map migration is complete even if one unrelated
            # native/provider map is temporarily unavailable. Do not make that
            # provider force a full rematch on every future Import All.
            _unlink(revalidate_pending)
    elif revalidate_pending:
        # Nothing selected requires a generated SRP map, so there is no global
        # generated-map migration left to perform. Native provider maps are
        # source-scoped and may retry independently if one is unavailable.
        _unlink(revalidate_pending)

    index = srp_channel_map.load_index() or {}
    sat_sig = channel_registry.satellite_registry_signature()
    if cache_first:
        # Import can proceed from any existing map; stale signatures are handled
        # as asynchronous maintenance, not as a reason to repeat SERVICE MAP.
        remaining = [str(x.get("name") or x.get("id") or "") for x in selected if not _map_exists(x, index)]
    else:
        remaining = [str(x.get("name") or x.get("id") or "") for x in selected if not _ready(x, index, sat_sig)]
    if not remaining:
        _unlink(PENDING_PATHS)
    if progress_cb:
        progress_cb(65, "Service maps ready" if not remaining else "%d source map(s) still unavailable" % len(remaining), "SERVICE MAP")
    return {
        "ok": not bool(remaining), "needed": len(need), "remaining": len(remaining),
        "remaining_names": remaining[:12], "native": native_report, "harvest": harvest,
        "master": master, "elapsed": round(time.time() - started, 3),
        "background_refresh": False,
        "balanced_exact": balanced_report,
    }
