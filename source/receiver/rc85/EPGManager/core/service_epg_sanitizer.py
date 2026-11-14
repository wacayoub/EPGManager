# -*- coding: utf-8 -*-
"""One-time selective cleanup of stale EPGManager events after feed-policy fixes.

Older beta builds could inject a domestic/base EPG into semantic variants such
as Al Aoula Inter or 2M Monde.  Rebuilding channels.xml stops future bad writes
but does not remove already-cached Enigma2 events.  beta121 therefore flushes
only *currently unmapped semantic variants*, once, immediately before the next
Native Import.  Fresh mapped services are handled by the first-write proxy in
direct_native_import.py.

No global epg.dat/cache wipe is performed.
"""
from __future__ import print_function

import os

from . import channel_mapper, channel_registry, programme_feed_policy
from .logger import get_logger

log = get_logger(__name__)

PENDING = "/etc/enigma2/.epgmanager_beta121_stale_epg_cleanup_pending"
LEGACY_PENDING = "/etc/enigma2/.epgmanager_beta118_stale_epg_cleanup_pending"
OLDER_PENDING = "/etc/enigma2/.epgmanager_beta117_stale_epg_cleanup_pending"


def _canonical(ref):
    raw = str(ref or "").strip()
    return channel_registry.canonical_service_ref(raw) or raw


def _triplet(ref):
    """Return (sid,onid,tsid) for a DVB Service Reference, else None."""
    raw = str(ref or "").strip()
    if not raw or channel_mapper.classify_service_ref(raw) == "IPTV":
        return None
    parts = raw.split(":")
    if len(parts) < 6:
        return None
    try:
        sid = int(parts[3], 16)
        tsid = int(parts[4], 16)
        onid = int(parts[5], 16)
    except Exception:
        return None
    if sid < 0 or tsid < 0 or onid < 0:
        return None
    return sid, onid, tsid


def _mapped_refs(sources):
    out = set()
    for source in sources or []:
        try:
            rows = getattr(getattr(source, "channels", None), "items", {}) or {}
        except Exception:
            rows = {}
        for refs in rows.values():
            for ref in refs or []:
                c = _canonical(ref)
                if c:
                    out.add(c)
    return out


def _sensitive_unmapped(service):
    """Only semantic variants known to have been mis-mapped in old betas."""
    name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    try:
        family, variant = programme_feed_policy._known_family_variant(name)
    except Exception:
        return False
    if not family:
        return False
    # Base feeds are not purged: they can legitimately carry broadcaster EIT.
    return bool(variant and variant != "base")


def sanitize_once(cache, sources, max_services=384):
    pending_paths = [x for x in (PENDING, LEGACY_PENDING, OLDER_PENDING) if x and os.path.exists(x)]
    if not pending_paths:
        return {"ok": True, "skipped": True, "flushed": 0}
    if cache is None or not hasattr(cache, "flushEPG"):
        return {"ok": False, "reason": "eEPGCache.flushEPG unavailable", "flushed": 0}

    mapped = _mapped_refs(sources)
    try:
        services, _rebuilt = channel_registry.load_satellite_registry(force=False)
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "flushed": 0}

    flushed = 0
    failed = 0
    seen_triplets = set()
    # Prioritize the families that old betas could collapse.  On a receiver
    # with thousands of services, an arbitrary lamedb order must not consume
    # the bounded cleanup budget before Al Aoula/2M/Arryadia/etc are reached.
    def _cleanup_priority(service):
        name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
        try:
            fam, var = programme_feed_policy._known_family_variant(name)
        except Exception:
            fam, var = "", ""
        rank = {"al_aoula": 0, "2m": 1, "arryadia": 2, "medi1tv": 3,
                "i24news": 4, "alkass": 5, "aljazeera": 6}.get(fam, 20)
        return (rank, str(name).casefold())
    ordered_services = sorted(list(services or []), key=_cleanup_priority)
    for service in ordered_services:
        if flushed >= int(max_services):
            break
        raw = str((service or {}).get("ref") or "").strip()
        cref = _canonical(raw)
        if not raw or not cref or cref in mapped or not _sensitive_unmapped(service):
            continue
        triplet = _triplet(raw)
        if not triplet or triplet in seen_triplets:
            continue
        seen_triplets.add(triplet)
        try:
            cache.flushEPG(*triplet)
            flushed += 1
        except Exception:
            failed += 1
            if failed <= 4:
                log.exception("Selective stale-EPG cleanup failed for %s", service.get("name"))

    # Mark complete even if individual legacy refs disappeared from lamedb. A
    # cache API failure keeps the marker so the next Import All can retry; this
    # is bounded to the small known semantic-variant set, never a global wipe.
    if failed == 0:
        for marker in pending_paths:
            try:
                os.unlink(marker)
            except OSError:
                pass
    log.info("beta121 stale semantic EPG cleanup: %d flushed, %d failed", flushed, failed)
    return {"ok": failed == 0, "flushed": flushed, "failed": failed,
            "mapped_refs": len(mapped), "retry_pending": bool(failed)}
