# -*- coding: utf-8 -*-
"""Zero-preflight Native EPGImport path used by Import All.

Remote feeds are handed directly to the installed OE-Alliance EPGImport
engine. Local EPGManager generators use their already prepared XML file.
The direct runner itself remains mapping-read-only: it consumes persistent
Service-Reference maps and MappingStore manual locks exactly as saved. RC9 may
auto-prepare a missing first-use source *before* this runner is constructed; once
here, Import All never rebuilds or republishes mapping ownership.
"""
from __future__ import print_function
import os
import time
import threading
from xml.etree import ElementTree as ET

from . import channel_mapper, channel_registry, source_catalog, srp_channel_map, source_priority, source_readiness, source_fast_status, source_variant_policy, external_sources, source_channel_cache, native_importer
from . import programme_feed_policy, service_epg_sanitizer, precision_match_engine, deterministic_matcher, openepg_arabic_overlay, epg_priority_guard
from .native_epgimport_bridge import (EPGImportLocalRunner, LocalEPGSource, _source_aliases,
                                      CountingEPGCacheProxy, _load_epgimport_class)
from .logger import get_logger

log = get_logger(__name__)

_MAP_FILE_CACHE = {}
_CANON_REF_CACHE = {}


def _canon_ref(ref):
    """Canonicalize hot-path ServiceRefs once per Enigma2 process.

    The same receiver refs can occur hundreds of thousands of times across
    selected source maps.  canonical_service_ref() performs IPTV classification,
    splitting and upper-casing every time; caching the result is both safe and a
    major RC20 CPU reduction.
    """
    raw = str(ref or "").strip()
    if not raw:
        return ""
    cached = _CANON_REF_CACHE.get(raw)
    if cached is not None:
        return cached
    value = channel_registry.canonical_service_ref(raw) or raw
    if len(_CANON_REF_CACHE) >= 100000:
        _CANON_REF_CACHE.clear()
    _CANON_REF_CACHE[raw] = value
    return value


def _store_rows(store):
    """Get one read-only MappingStore snapshot without copying 75k+ rows."""
    if store is None:
        return {}
    try:
        getter = getattr(store, "all_readonly", None)
        if getter is not None:
            rows = getter()
        else:
            rows = store.all()
        return rows if isinstance(rows, dict) else {}
    except Exception:
        return {}


def _strict_service_key(name):
    """Identity for duplicate DVB expansion: semantic feed stays significant."""
    try:
        family, variant = programme_feed_policy._known_family_variant(name)
    except Exception:
        family = variant = ""
    if family:
        return "family:%s:%s" % (family, variant or "base")
    try:
        tokens = programme_feed_policy._tokens(name)
        technical = set(programme_feed_policy._TECHNICAL)
    except Exception:
        tokens = str(name or "").casefold().split(); technical = set()
    tokens = [x for x in tokens if x not in technical]
    return "name:" + " ".join(tokens) if tokens else ""


def _equivalent_registry_index():
    """Build one receiver-only reference index for fast Import All.

    8.1.6 includes both DVB Reception Lists and IPTV bouquets in ``by_ref``.
    The strict equivalent-name expansion remains SAT-only.  This is important:
    provider-wide channels.xml files can contain tens of thousands of Service
    References which do not exist on this receiver.  Import All must never spend
    CPU validating or importing those foreign references.
    """
    try:
        sat_rows, _rebuilt = channel_registry.load_satellite_registry(force=False)
    except Exception:
        sat_rows = []
    try:
        iptv_rows, _rebuilt = channel_registry.load_iptv_registry(force=False)
    except Exception:
        iptv_rows = []
    by_ref, by_key = {}, {}
    for service in list(sat_rows or []) + list(iptv_rows or []):
        raw = str((service or {}).get("ref") or "").strip()
        if not raw:
            continue
        cref = _canon_ref(raw)
        by_ref[cref] = service
        if channel_mapper.classify_service_ref(raw) == "IPTV":
            continue
        key = _strict_service_key((service or {}).get("clean_name") or (service or {}).get("name"))
        if not key:
            continue
        try:
            sat = programme_feed_policy.satellite_family((service or {}).get("orbital"))
        except Exception:
            sat = "UNKNOWN"
        by_key.setdefault((sat, key), []).append(service)
    return by_ref, by_key


def _expand_equivalent_refs(mapping, registry_index):
    """Add strict same-feed receiver duplicates after ownership selection."""
    by_ref, by_key = registry_index or ({}, {})
    if not by_ref or not by_key:
        return {k: list(v) for k, v in (mapping or {}).items()}, 0
    out, added = {}, 0
    equiv_cache = {}
    for cid, refs in (mapping or {}).items():
        keep = []
        seen = set()
        seeds = []
        for ref in refs or []:
            raw = str(ref or "").strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            keep.append(raw)
            seeds.append(_canon_ref(raw))
        for cref in seeds:
            candidates = equiv_cache.get(cref)
            if candidates is None:
                candidates = []
                service = by_ref.get(cref)
                if service is not None:
                    key = _strict_service_key((service or {}).get("clean_name") or (service or {}).get("name"))
                    try:
                        sat = programme_feed_policy.satellite_family((service or {}).get("orbital"))
                    except Exception:
                        sat = "UNKNOWN"
                    for candidate in by_key.get((sat, key)) or []:
                        raw = str((candidate or {}).get("ref") or "").strip()
                        if raw:
                            candidates.append(raw)
                equiv_cache[cref] = candidates
            for raw in candidates:
                if raw not in seen:
                    seen.add(raw)
                    keep.append(raw)
                    added += 1
        if keep:
            out[str(cid)] = keep
    return out, added



def _filter_semantic_ref_conflicts(mapping, registry_index, source=None):
    """Drop automatic channel-id -> SRP rows that violate programme-feed identity.

    This is the final defense after every mapper/provider cache.  It deliberately
    runs before manual overrides, so a user manual mapping remains authoritative.
    Opaque provider IDs pass; only proven family/feed conflicts are rejected.
    """
    by_ref = (registry_index or ({}, {}))[0] or {}
    if not by_ref:
        return {k:list(v) for k,v in (mapping or {}).items()}, []
    out, rejected = {}, []
    for cid, refs in (mapping or {}).items():
        keep=[]
        for ref in refs or []:
            raw=str(ref or '').strip()
            if not raw:
                continue
            if channel_mapper.classify_service_ref(raw) == 'IPTV':
                if raw not in keep: keep.append(raw)
                continue
            cref=_canon_ref(raw)
            service=by_ref.get(cref)
            if service is None:
                if raw not in keep: keep.append(raw)
                continue
            try:
                allowed, reason, _local_fb = programme_feed_policy.feed_compatible_with_source(
                    service, candidate_name=str(cid or ''), display_name=str(cid or ''), source=source)
            except Exception:
                allowed, reason = False, 'semantic guard exception'
            if not allowed:
                rejected.append({'channel_id':str(cid),'ref':raw,'service':str(service.get('name') or ''),'reason':str(reason or '')})
                continue

            # beta121 final source-context firewall for generated maps.  Old
            # channels.xml files can survive package upgrades; therefore Import
            # All must not trust a stale 2M->Egypt or Al Aoula->Belgium SRP just
            # because the file predates the new matcher. Provider-owned native
            # SRP maps pass ``source=None`` and remain authoritative.
            if source is not None:
                try:
                    route_ok, _route_score, route_reason = programme_feed_policy.route(
                        service, source, {'channel_id': str(cid or ''), 'display_name': str(cid or '')})
                except Exception:
                    route_ok, route_reason = False, 'source-context guard exception'
                if not route_ok:
                    rejected.append({'channel_id':str(cid),'ref':raw,'service':str(service.get('name') or ''),
                                     'reason':str(route_reason or 'source-context mismatch')})
                    continue
                try:
                    verdict = precision_match_engine.evaluate(
                        service, source, {'source_id': str(source_catalog.mapping_source_id(source) or ''),
                                          'channel_id': str(cid or ''), 'display_name': str(cid or '')}, balanced=True)
                except Exception as exc:
                    verdict = {'auto': False, 'reason': 'precision filter error: %s' % exc}
                if not verdict.get('auto'):
                    rejected.append({'channel_id':str(cid),'ref':raw,'service':str(service.get('name') or ''),
                                     'reason':str(verdict.get('reason') or 'not precision-proven')})
                    continue
            if raw not in keep:
                keep.append(raw)
        if keep:
            out[str(cid)] = keep
    return out, rejected


def _dvb_triplet(ref):
    """Parse Service Reference to eEPGCache.flushEPG(sid,onid,tsid)."""
    raw = str(ref or "").strip()
    if not raw or channel_mapper.classify_service_ref(raw) == "IPTV":
        return None
    parts = raw.split(":")
    if len(parts) < 6:
        return None
    try:
        return int(parts[3], 16), int(parts[5], 16), int(parts[4], 16)
    except Exception:
        return None


class FirstWriteFlushProxy(CountingEPGCacheProxy):
    """Flush stale events for one DVB service only when fresh events exist.

    This is deliberately first-write, not pre-import: a dead/empty source can
    never blank a working service.  One runner tracks triplets globally, so a
    second selected source can merge additional events without flushing the
    first source again. IPTV refs are never selectively flushed.
    """
    def __init__(self, cache, enabled=True):
        CountingEPGCacheProxy.__init__(self, cache)
        self.selective_flush_enabled = bool(enabled and hasattr(cache, "flushEPG"))
        self.flushed_triplets = set()
        self.flush_failures = 0

    def _flush_services(self, services):
        if not self.selective_flush_enabled:
            return
        values = services if isinstance(services, (list, tuple, set)) else [services]
        for ref in values:
            triplet = _dvb_triplet(ref)
            if not triplet or triplet in self.flushed_triplets:
                continue
            try:
                self._cache.flushEPG(*triplet)
                self.flushed_triplets.add(triplet)
            except Exception:
                self.flush_failures += 1
                if self.flush_failures <= 4:
                    log.exception("Selective eEPGCache flush failed for %s", ref)

    def importEvents(self, services, events):
        self._flush_services(services)
        return CountingEPGCacheProxy.importEvents(self, services, events)

    def importEvent(self, service, events):
        self._flush_services(service)
        return CountingEPGCacheProxy.importEvent(self, service, events)


def _direct_row(index, source_id):
    rows = index.get("sources") or {}
    for alias in _source_aliases(source_id):
        row = rows.get(alias) or rows.get(str(alias).lower())
        if row:
            return row
    return None


def _channel_items_for(row, only_iptv=False):
    path = str((row or {}).get("map_path") or "")
    if not path or not os.path.isfile(path):
        return {}
    # beta54: parsing the same channels.xml files on every Import Source/All
    # wastes CPU. Cache by mtime+size; any map rebuild invalidates naturally.
    try:
        st = os.stat(path)
        sig = (int(st.st_mtime), int(st.st_size), bool(only_iptv))
    except Exception:
        sig = None
    cached = _MAP_FILE_CACHE.get(path) if sig is not None else None
    if cached and cached[0] == sig:
        # Callers treat this mapping as immutable and immediately build a pruned
        # result.  Returning the cached object avoids cloning tens of thousands
        # of lists on every Import All.
        return cached[1]
    parsed = srp_channel_map.parse_channels_xml(path)
    out = {}
    for channel_id, refs in (parsed or {}).items():
        clean = []
        for ref in refs or []:
            ref = str(ref or "").strip()
            if not ref:
                continue
            if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                continue
            if ref not in clean:
                clean.append(ref)
        if clean:
            out[str(channel_id).lower()] = clean
    if sig is not None:
        # Keep the cache bounded on low-memory receivers.
        if len(_MAP_FILE_CACHE) >= 32 and path not in _MAP_FILE_CACHE:
            try:
                _MAP_FILE_CACHE.pop(next(iter(_MAP_FILE_CACHE)))
            except Exception:
                _MAP_FILE_CACHE.clear()
        _MAP_FILE_CACHE[path] = (sig, {k: list(v) for k, v in out.items()})
    return out


def _local_path(item, epg_dir):
    filename = str((item or {}).get("output_file") or "")
    if filename:
        return os.path.join(epg_dir, filename)
    mid = source_catalog.mapping_source_id(item)
    for fn, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
        if pair[0] == mid:
            return os.path.join(epg_dir, fn)
    return ""


def _manual_ref_targets(store, store_rows=None):
    """Return service-ref -> exact MANUAL (source_id, channel_id) targets.

    A manual source selection is not merely a provider preference.  It locks the
    receiver service to one exact XMLTV ID.  Import All must therefore suppress
    every competing automatic ID, including a second ID from the SAME source.
    """
    targets = {}
    if store is None:
        return targets
    rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    for key, record in rows.items():
        if "::" not in str(key) or not isinstance(record, dict):
            continue
        if str(record.get("mode") or "").lower() != "manual":
            continue
        mid, cid = str(key).split("::", 1)
        target = (str(mid or "").strip().lower(), str(cid or "").strip().lower())
        if not target[0] or not target[1]:
            continue
        for ref in record.get("refs") or []:
            raw = str(ref or "").strip()
            if not raw:
                continue
            canon = _canon_ref(raw)
            for value in (raw, canon):
                if value:
                    targets.setdefault(str(value), set()).add(target)
    return targets


def _persisted_ref_targets(store, store_rows=None):
    """Return canonical service-ref -> persisted exact owner candidates."""
    targets = {}
    if store is None:
        return targets
    rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    seen_by_ref = {}
    for key, record in rows.items():
        if "::" not in str(key) or not isinstance(record, dict):
            continue
        mid, cid = str(key).split("::", 1)
        mid = str(mid or "").strip().lower()
        cid = str(cid or "").strip().lower()
        if not mid or not cid:
            continue
        mode = str(record.get("mode") or "").strip().lower()
        candidate = {
            "source_id": mid,
            "channel_id": cid,
            "mode": mode,
            "confidence": int(record.get("confidence") or 0),
        }
        sig = (mid, cid, mode)
        for ref in record.get("refs") or []:
            raw = str(ref or "").strip()
            if not raw:
                continue
            canon = _canon_ref(raw)
            if not canon:
                continue
            seen = seen_by_ref.setdefault(canon, set())
            if sig in seen:
                continue
            seen.add(sig)
            targets.setdefault(canon, []).append(dict(candidate))
    return targets


def _persisted_target_rank(candidate, source_score):
    """Best-first deterministic lock rank for legacy duplicate saved owners."""
    candidate = candidate or {}
    mode = str(candidate.get("mode") or "").lower()
    if mode == "manual":
        cls = 3
    elif mode in ("auto-repair", "smartmatch-iptv", "smartmatch", "auto"):
        cls = 2
    else:
        cls = 1
    sid = str(candidate.get("source_id") or "").lower()
    return (cls, int(candidate.get("confidence") or 0), source_score,
            sid, str(candidate.get("channel_id") or ""))


def _manual_ref_owners(store, store_rows=None):
    """Return canonical/raw service-ref -> manual source owners.

    beta36 keeps provider-supplied Rytec maps untouched, except where the user
    has an explicit manual mapping. Manual mapping remains the absolute owner.
    """
    owners = {}
    if store is None:
        return owners
    rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    for key, record in rows.items():
        if "::" not in str(key) or not isinstance(record, dict):
            continue
        if str(record.get("mode") or "").lower() != "manual":
            continue
        mid = str(key).split("::", 1)[0].strip().lower()
        for ref in record.get("refs") or []:
            raw = str(ref or "").strip()
            if not raw:
                continue
            canon = _canon_ref(raw)
            for value in (raw, canon):
                if value:
                    owners.setdefault(str(value), set()).add(mid)
    return owners


def _filter_rytec_manual(map_items, mid, manual_owners):
    if not manual_owners:
        return map_items
    out = {}
    mid = str(mid or "").lower()
    for cid, refs in (map_items or {}).items():
        keep = []
        for ref in refs or []:
            raw = str(ref or "").strip()
            canon = _canon_ref(raw)
            owners = manual_owners.get(raw) or manual_owners.get(canon) or set()
            if owners and mid not in owners:
                continue
            if raw and raw not in keep:
                keep.append(raw)
        if keep:
            out[cid] = keep
    return out


def _merge_manual_overrides(parsed, store, only_iptv=False, store_rows=None):
    """Overlay explicit manual mappings onto prepared source maps.

    The frozen Native bridge only falls back to MappingStore when a whole
    source has no channels map. Once SRP Master exists, manual rows therefore
    need to be merged here so manual remains absolute even for populated maps.
    """
    if store is None:
        return parsed
    rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    for key, rec in rows.items():
        if not isinstance(rec, dict) or str(rec.get("mode") or "").lower() != "manual":
            continue
        try:
            mid, cid = str(key).split("::", 1)
        except ValueError:
            continue
        mid = str(mid or "").lower().strip(); cid = str(cid or "").lower().strip()
        if not mid or not cid:
            continue
        clean = []
        for ref in rec.get("refs") or []:
            ref = str(ref or "").strip()
            if not ref:
                continue
            if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                continue
            if ref not in clean:
                clean.append(ref)
        if clean:
            parsed.setdefault(mid, {})[cid] = clean
    return parsed



def _prune_to_receiver_refs(mapping, registry_index):
    """Drop SRPs which are not present on this receiver before expensive work."""
    by_ref = (registry_index or ({}, {}))[0] or {}
    if not by_ref:
        return {k: list(v) for k, v in (mapping or {}).items()}, 0
    out = {}
    dropped = 0
    for cid, refs in (mapping or {}).items():
        keep = []
        seen = set()
        for ref in refs or []:
            raw = str(ref or "").strip()
            if not raw:
                continue
            cref = _canon_ref(raw)
            if cref not in by_ref:
                dropped += 1
                continue
            if raw not in seen:
                seen.add(raw)
                keep.append(raw)
        if keep:
            out[str(cid)] = keep
    return out, dropped


def _fast_single_owner_maps(items, parsed, registry_index, store=None, store_rows=None):
    """Keep one exact source+XMLTV owner per receiver service, CPU-bounded.

    RC20 removes the RC19 quadratic persisted-owner fallback and avoids running
    source-priority scoring for services that already have a valid persisted
    MANUAL/Auto Repair/SmartMatch target.  With mapping health around 97%, this
    eliminates almost all expensive ranking work while preserving identical
    ownership semantics.
    """
    rows = list(items or [])
    item_by_mid = {}
    for item in rows:
        mid = str(source_catalog.mapping_source_id(item) or "").lower()
        if mid:
            item_by_mid[mid] = item
    receiver_by_ref = (registry_index or ({}, {}))[0] or {}
    rank_cache = {}

    def owner_score(mid, cref):
        key = (str(mid or "").lower(), cref)
        cached = rank_cache.get(key)
        if cached is not None:
            return cached
        item = item_by_mid.get(key[0]) or {}
        service = receiver_by_ref.get(cref) or {}
        try:
            value = source_priority.ownership_rank(item, service)
        except Exception:
            value = (-1, ())
        rank_cache[key] = value
        return value

    # rc37: within one provider/source, prefer the XMLTV identity that best
    # matches the receiver service.  This prevents generic catch-all IDs such as
    # beINSports.qa@MENA from stealing beIN SPORTS 1/2/3 services.
    display_index = {}
    for mid, item in item_by_mid.items():
        sid = str((item or {}).get("id") or "")
        try:
            for row in source_channel_cache.get(sid) or []:
                cid = str((row or {}).get("channel_id") or "").strip().lower()
                if cid:
                    display_index[(mid, cid)] = str((row or {}).get("display_name") or cid)
        except Exception:
            pass
    claim_rank_cache = {}
    def claim_score(mid, cid, cref):
        key = (str(mid or "").lower(), str(cid or "").lower(), cref)
        cached = claim_rank_cache.get(key)
        if cached is not None:
            return cached
        service = receiver_by_ref.get(cref) or {}
        service_name = str(service.get("clean_name") or service.get("name") or "")
        label = display_index.get((key[0], key[1])) or str(cid or "")
        try:
            identity_score = int(channel_mapper._token_score(label, service_name) or 0)
        except Exception:
            identity_score = 0
        value = (owner_score(key[0], cref), identity_score)
        claim_rank_cache[key] = value
        return value

    mapping_rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    persisted = _persisted_ref_targets(store, store_rows=mapping_rows)
    # Canonical lookup is enough: _persisted_ref_targets stores both raw and
    # canonical keys, so RC19's per-service full scan of every persisted row was
    # redundant and could become O(receiver_refs * mappings).
    persisted_exact = {}
    for ref_key, candidates in (persisted or {}).items():
        cref = _canon_ref(ref_key)
        bucket = persisted_exact.setdefault(cref, {})
        for cand in candidates or []:
            sid = str((cand or {}).get("source_id") or "").lower()
            cid = str((cand or {}).get("channel_id") or "").lower()
            if not sid or not cid:
                continue
            sig = (sid, cid)
            prev = bucket.get(sig)
            if prev is None or _persisted_target_rank(cand, claim_score(sid, cid, cref)) > \
                    _persisted_target_rank(prev, claim_score(sid, cid, cref)):
                bucket[sig] = cand

    auto_winner = {}
    locked_target = {}
    unresolved_persisted = set()
    claims = 0

    # Pass 1: validate exact persisted targets with dictionary lookups. Services
    # with no persisted candidates can choose their fallback winner immediately.
    for mid, channels in (parsed or {}).items():
        lmid = str(mid or "").lower()
        for cid, refs in (channels or {}).items():
            lcid = str(cid or "").lower()
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                claims += 1
                cref = _canon_ref(raw)
                exact = persisted_exact.get(cref)
                if exact:
                    cand = exact.get((lmid, lcid))
                    if cand is not None:
                        prev_target = locked_target.get(cref)
                        if prev_target is None:
                            locked_target[cref] = (lmid, lcid, cand)
                        else:
                            prev = prev_target[2]
                            if _persisted_target_rank(cand, claim_score(lmid, lcid, cref)) > \
                                    _persisted_target_rank(prev, claim_score(prev_target[0], prev_target[1], cref)):
                                locked_target[cref] = (lmid, lcid, cand)
                    else:
                        unresolved_persisted.add(cref)
                    continue
                prev = auto_winner.get(cref)
                if prev is None or claim_score(lmid, lcid, cref) > claim_score(prev[0], prev[1], cref):
                    auto_winner[cref] = (lmid, lcid)

    # Persisted candidates can all be stale/disabled. Rank only that small subset
    # as fallback instead of scoring every claim up front.
    need_fallback = set(cref for cref in unresolved_persisted if cref not in locked_target)
    if need_fallback:
        for mid, channels in (parsed or {}).items():
            lmid = str(mid or "").lower()
            for cid, refs in (channels or {}).items():
                lcid = str(cid or "").lower()
                for ref in refs or []:
                    raw = str(ref or "").strip()
                    if not raw:
                        continue
                    cref = _canon_ref(raw)
                    if cref not in need_fallback:
                        continue
                    prev = auto_winner.get(cref)
                    if prev is None or claim_score(lmid, lcid, cref) > claim_score(prev[0], prev[1], cref):
                        auto_winner[cref] = (lmid, lcid)

    locked_simple = dict((cref, (row[0], row[1])) for cref, row in locked_target.items())
    out = {}
    suppressed = 0
    kept_refs = set()
    for mid, channels in (parsed or {}).items():
        lmid = str(mid or "").lower()
        dst = {}
        for cid, refs in (channels or {}).items():
            lcid = str(cid or "").lower()
            keep = []
            seen = set()
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                cref = _canon_ref(raw)
                target = locked_simple.get(cref) or auto_winner.get(cref)
                if target != (lmid, lcid):
                    suppressed += 1
                    continue
                if raw not in seen:
                    seen.add(raw)
                    keep.append(raw)
                    kept_refs.add(cref)
            if keep:
                dst[str(cid)] = keep
        if dst:
            out[lmid] = dst
    return out, {
        "ownership_claims": int(claims),
        "single_owner_services": int(len(kept_refs)),
        "persisted_locked_services": int(len(set(cref for cref in kept_refs if cref in locked_simple))),
        "suppressed_duplicate_refs": int(suppressed),
        "rank_cache_entries": int(len(rank_cache)),
        "stale_persisted_fallback_services": int(len(need_fallback)),
    }


def _filter_hard_feed_conflicts_all(parsed, registry_index):
    """Final hard semantic sanitizer, including legacy/manual rows.

    Manual mappings may override *which source* supplies a service, but they may
    not collapse programme siblings such as 2M National -> 2M base or Al Aoula
    Inter -> domestic Al Aoula.  This is deliberately narrower than the source
    country firewall so legitimate expert manual source choices still work.
    """
    by_ref = (registry_index or ({}, {}))[0] or {}
    try:
        _sources = source_catalog.by_mapping_id() or {}
    except Exception:
        _sources = {}
    out = {}
    rejected = 0
    for mid, channels in (parsed or {}).items():
        source = _sources.get(str(mid or "")) or _sources.get(str(mid or "").lower()) or {}
        dst = {}
        for cid, refs in (channels or {}).items():
            keep = []
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                if channel_mapper.classify_service_ref(raw) == "IPTV":
                    if raw not in keep: keep.append(raw)
                    continue
                cref = _canon_ref(raw)
                service = by_ref.get(cref)
                if service is None:
                    if raw not in keep: keep.append(raw)
                    continue
                try:
                    ok, _why, local_fb = programme_feed_policy.feed_compatible_with_source(
                        service, str(cid or ""), str(cid or ""), source=source)
                    if ok:
                        sname = str(service.get("clean_name") or service.get("name") or "")
                        ok = (programme_feed_policy._same_family_presentation_safe(sname, str(cid or ""))
                              if local_fb else deterministic_matcher.compatible_variant(sname, str(cid or "")))
                except Exception:
                    ok = False
                if not ok:
                    rejected += 1
                    continue
                if raw not in keep: keep.append(raw)
            if keep: dst[str(cid)] = keep
        if dst: out[str(mid)] = dst
    return out, rejected


def _arbitrate_precision_claims(items, parsed, registry_index, store=None):
    """Choose one automatic EPG owner per receiver service.

    beta125 intentionally merged every valid SRP source. With the new SAFE EXACT
    middle tier that can re-introduce cross-country ambiguity for unknown brands.
    Keep manual ownership absolute; otherwise select one strongest PROVEN/SAFE
    claim and fail closed when two different targets are too close.
    """
    by_ref = (registry_index or ({}, {}))[0] or {}
    if not by_ref:
        return parsed, {"precision_claims": 0, "precision_ambiguous_refs": 0, "precision_suppressed_refs": 0}
    by_mid = {str(source_catalog.mapping_source_id(x) or "").lower(): x for x in (items or [])}
    manual = _manual_ref_owners(store)
    manual_targets = _manual_ref_targets(store)
    claims = {}
    total = 0
    for mid, channels in (parsed or {}).items():
        lmid = str(mid or "").lower(); item = by_mid.get(lmid) or {}
        native = bool(item.get("native_srp"))
        for cid, refs in (channels or {}).items():
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw: continue
                cref = _canon_ref(raw)
                service = by_ref.get(cref)
                if service is None:
                    continue
                targets = manual_targets.get(raw) or manual_targets.get(cref) or set()
                if targets:
                    if (lmid, str(cid or "").lower()) in targets:
                        claims.setdefault(cref, []).append({"mid":lmid,"cid":str(cid),"ref":raw,"manual":True,
                            "grade":"MANUAL","confidence":2000,"target_key":"manual:%s::%s"%(lmid,str(cid).lower()),"tier":9999,"item":item})
                    continue
                if native:
                    # Provider-native Rytec/Azman/Koala maps are shared exact
                    # ServiceRef dictionaries and may legitimately be referenced
                    # by several selected country feeds. Keep their historical
                    # union semantics; SAFE arbitration is only for generated
                    # name-derived claims.
                    continue
                try:
                    verdict=precision_match_engine.evaluate(service,item,
                        {"source_id":str(source_catalog.mapping_source_id(item) or ""),
                         "channel_id":str(cid or ""),"display_name":str(cid or "")},balanced=True)
                except Exception:
                    verdict={"auto":False}
                if not verdict.get("auto"):
                    continue
                try: tier=int(source_priority.source_tier(item))
                except Exception: tier=0
                claims.setdefault(cref, []).append({"mid":lmid,"cid":str(cid),"ref":raw,"manual":False,
                    "grade":str(verdict.get("grade") or ""),"confidence":int(verdict.get("confidence") or 0),
                    "target_key":str(verdict.get("target_key") or ""),"tier":tier,"item":item})
                total += 1
    winners=set(); ambiguous=0; suppressed=0
    grade_weight={"MANUAL":3,"PROVEN":2,"SAFE":1}
    for cref, rows in claims.items():
        manual_rows=[r for r in rows if r.get("manual")]
        if manual_rows:
            for r in manual_rows: winners.add((r["mid"],r["cid"],r["ref"]))
            suppressed += max(0,len(rows)-len(manual_rows)); continue
        rows=sorted(rows,key=lambda r:(-grade_weight.get(r.get("grade"),0),-int(r.get("confidence") or 0),-int(r.get("tier") or 0),source_catalog.source_sort_key(r.get("item") or {}),r.get("mid"),r.get("cid")))
        if not rows: continue
        best=rows[0]
        competitor=None
        for r in rows[1:]:
            if str(r.get("target_key") or "") != str(best.get("target_key") or ""):
                competitor=r; break
        if competitor is not None:
            margin = precision_match_engine.AUTO_MARGIN if best.get("grade")=="PROVEN" else precision_match_engine.SAFE_MARGIN
            # Grade advantage is independent proof; equal-grade targets need a
            # clear confidence lead or the service remains unmapped.
            if grade_weight.get(best.get("grade"),0)==grade_weight.get(competitor.get("grade"),0) and \
                    int(best.get("confidence") or 0)-int(competitor.get("confidence") or 0) < int(margin):
                ambiguous += 1; suppressed += len(rows); continue
        winners.add((best["mid"],best["cid"],best["ref"]))
        suppressed += max(0,len(rows)-1)
    # Claims for refs not resolvable in the registry are retained unchanged;
    # they are outside this DVB arbitration layer.
    out={}
    claim_keys=set()
    for _cref, rows in claims.items():
        for r in rows: claim_keys.add((r["mid"],r["cid"],r["ref"]))
    for mid, channels in (parsed or {}).items():
        lmid=str(mid or "").lower(); dst={}
        for cid, refs in (channels or {}).items():
            keep=[]
            for ref in refs or []:
                raw=str(ref or "").strip(); key=(lmid,str(cid),raw)
                if key in claim_keys and key not in winners: continue
                if raw and raw not in keep: keep.append(raw)
            if keep: dst[str(cid)]=keep
        if dst: out[str(mid)]=dst
    return out,{"precision_claims":total,"precision_ambiguous_refs":ambiguous,"precision_suppressed_refs":suppressed}


def prepare_direct_sources(items, epg_dir, only_iptv=False, store=None):
    """Prepare a receiver-only, single-owner Native EPGImport plan.

    8.1.6 deliberately makes Import All an *import* operation again.  It does
    not re-run PrecisionMatch over every row from every selected provider.
    Current SRP Master v8 and provider-native maps are trusted persistent
    knowledge, pruned to services that actually exist on the receiver, then
    deduplicated to one preferred source owner per service.  Only genuinely
    legacy generated maps still pass the older semantic firewall.
    """
    started = time.time()
    original_items = list(items or [])
    suppressed_parallel = 0
    if len(original_items) > 1:
        by_sid = dict((str((x or {}).get("id") or ""), x) for x in original_items)
        keep_ids = set(source_variant_policy.normalize_selected([x for x in by_sid if x]))
        filtered = []
        for item in original_items:
            sid = str((item or {}).get("id") or "")
            if sid and sid not in keep_ids:
                suppressed_parallel += 1
                continue
            filtered.append(item)
        items = filtered
    else:
        items = original_items

    # Keep the historical import ordering: weak/generic first, preferred local
    # or Arabic/MENA last.  _fast_single_owner_maps uses this same ordering.
    items = source_priority.sort_for_import(items)
    timings = {}
    phase = time.time()
    index = srp_channel_map.load_index() or {}
    parsed = {}
    provider_map_cache = {}
    pruned_map_cache = {}
    source_meta = {}
    registry_index = _equivalent_registry_index()
    receiver_service_count = len((registry_index or ({}, {}))[0] or {})
    timings["registry_ms"] = int((time.time() - phase) * 1000.0)
    expanded_equivalent_refs = 0
    receiver_pruned_refs = 0
    semantic_rejected = []
    trusted_sources = 0
    legacy_validated_sources = 0

    phase = time.time()
    _source_build_started = time.time()
    for item in items or []:
        mid = source_catalog.mapping_source_id(item)
        key = str(mid).lower()
        row = _direct_row(index, mid)
        path = str((row or {}).get("map_path") or "")
        native = bool((item or {}).get("native_srp")) or bool((row or {}).get("official"))
        mapping_mode = str((row or {}).get("mapping_mode") or "").lower()
        trusted = bool(native or mapping_mode == "srp_master_v8")
        route_source = None if native else item

        # Parse shared provider maps once, and more importantly prune an identical
        # shared channels.xml only once. RC19 could re-walk the same huge Rytec map
        # for every selected country/source that referenced it.
        if native:
            if path not in provider_map_cache:
                provider_map_cache[path] = _channel_items_for(row, only_iptv=only_iptv)
            base_map = provider_map_cache.get(path) or {}
        else:
            base_map = _channel_items_for(row, only_iptv=only_iptv)
        prune_key = (path, bool(only_iptv))
        if path and prune_key in pruned_map_cache:
            pruned, dropped = pruned_map_cache[prune_key]
        else:
            pruned, dropped = _prune_to_receiver_refs(base_map, registry_index)
            if path:
                pruned_map_cache[prune_key] = (pruned, dropped)
        receiver_pruned_refs += int(dropped or 0)

        # RC20 defers strict same-feed duplicate expansion until AFTER ownership
        # arbitration. Expanding every competing source first was the major claim
        # explosion behind 200k+ linked refs and multi-minute PREPARE times.
        if trusted:
            safe, rejected = pruned, []
            trusted_sources += 1
        else:
            safe, rejected = _filter_semantic_ref_conflicts(
                pruned, registry_index, source=route_source)
            legacy_validated_sources += 1
        if rejected:
            semantic_rejected.extend(rejected)
        parsed[key] = safe
        source_meta[key] = {"trusted": trusted, "source": route_source}
    timings["map_load_prune_ms"] = int((time.time() - phase) * 1000.0)

    mapping_rows = _store_rows(store)
    manual_keys = set()
    for mk, mrec in (mapping_rows or {}).items():
        if not isinstance(mrec, dict) or str(mrec.get("mode") or "").lower() != "manual":
            continue
        try:
            mmid, mcid = str(mk).split("::", 1)
            manual_keys.add((mmid.strip().lower(), mcid.strip().lower()))
        except Exception:
            continue

    phase = time.time()
    parsed = _merge_manual_overrides(parsed, store, only_iptv=only_iptv, store_rows=mapping_rows)
    parsed, seed_owner_stats = _fast_single_owner_maps(
        items, parsed, registry_index, store=store, store_rows=mapping_rows)
    timings["seed_ownership_ms"] = int((time.time() - phase) * 1000.0)

    # Expand only the already-winning automatic targets. This preserves the same
    # final duplicate-service coverage while keeping the arbitration set close to
    # receiver size instead of source_count x receiver_size. Manual exact rows are
    # deliberately not expanded, matching the RC19 ordering.
    phase = time.time()
    expanded_parsed = {}
    for mid, channels in (parsed or {}).items():
        meta = source_meta.get(str(mid).lower()) or {"trusted": True, "source": None}
        dst = {}
        auto_rows = {}
        for cid, refs in (channels or {}).items():
            if (str(mid).lower(), str(cid).lower()) in manual_keys:
                dst[str(cid)] = list(refs or [])
            else:
                auto_rows[str(cid)] = list(refs or [])
        if auto_rows:
            expanded, added = _expand_equivalent_refs(auto_rows, registry_index)
            expanded_equivalent_refs += int(added or 0)
            if not meta.get("trusted"):
                expanded, rejected = _filter_semantic_ref_conflicts(
                    expanded, registry_index, source=meta.get("source"))
                if rejected:
                    semantic_rejected.extend(rejected)
            dst.update(expanded)
        if dst:
            expanded_parsed[str(mid).lower()] = dst
    parsed = expanded_parsed

    if expanded_equivalent_refs:
        parsed, final_owner_stats = _fast_single_owner_maps(
            items, parsed, registry_index, store=store, store_rows=mapping_rows)
        owner_stats = dict(final_owner_stats or {})
        owner_stats["suppressed_duplicate_refs"] = (
            int((seed_owner_stats or {}).get("suppressed_duplicate_refs") or 0) +
            int((final_owner_stats or {}).get("suppressed_duplicate_refs") or 0))
        owner_stats["seed_ownership_claims"] = int((seed_owner_stats or {}).get("ownership_claims") or 0)
    else:
        owner_stats = seed_owner_stats
    timings["winner_expand_ms"] = int((time.time() - phase) * 1000.0)

    priority_stats = {
        "suppressed_lower_priority_refs": int(owner_stats.get("suppressed_duplicate_refs") or 0),
        "curated_overlay_refs": 0,
        "cached_exact_overlay_refs": 0,
        "provider_rejected_refs": 0,
        "local_empty_fallback_refs": 0,
        "quality_scored_claims": 0,
        "quality_zero_program_claims": 0,
        "quality_placeholder_claims": 0,
        "precision_claims": 0,
        "precision_ambiguous_refs": 0,
        "precision_suppressed_refs": 0,
        "hard_feed_rejected_refs": 0,
    }

    # 8.1.9 MAPPING LOCK: Import All is an EPG injection path only.
    # Never save/publish a new Smart Mapping ownership snapshot here.  The
    # persistent mapping/SRP build and explicit Smart Mapping choices are the
    # sole owners of mapping state, so importing programmes cannot make a
    # receiver channel appear to switch source/ID afterwards.

    sources = []
    stats = {
        "total_files": len(original_items or []), "ready_sources": 0, "empty_files": 0,
        "unreadable_files": 0, "unmapped_sources": 0, "mapped_channel_ids": 0,
        "mapped_service_refs": 0, "skipped_source_names": [],
        "direct_remote_sources": 0, "local_sources": 0,
        "arabic_overlay_sources": 0, "arabic_overlay_programmes": 0,
        "arabic_overlay_titles": 0, "arabic_overlay_descriptions": 0,
        "arabic_overlay_fallbacks": 0,
        "suppressed_lower_priority_refs": int(priority_stats.get("suppressed_lower_priority_refs") or 0),
        "curated_overlay_refs": 0, "cached_exact_overlay_refs": 0,
        "provider_rejected_refs": 0, "local_empty_fallback_refs": 0,
        "quality_scored_claims": 0, "quality_zero_program_claims": 0,
        "quality_placeholder_claims": 0,
        "suppressed_parallel_language_sources": int(suppressed_parallel),
        "expanded_equivalent_service_refs": int(expanded_equivalent_refs),
        "semantic_rejected_refs": int(len(semantic_rejected)),
        "semantic_rejected_samples": list(semantic_rejected[:12]),
        "receiver_service_count": int(receiver_service_count),
        "receiver_pruned_refs": int(receiver_pruned_refs),
        "single_owner_services": int(owner_stats.get("single_owner_services") or 0),
        "persisted_locked_services": int(owner_stats.get("persisted_locked_services") or 0),
        "suppressed_duplicate_refs": int(owner_stats.get("suppressed_duplicate_refs") or 0),
        "trusted_map_sources": int(trusted_sources),
        "legacy_validated_sources": int(legacy_validated_sources),
        "prepare_elapsed": round(max(0.0, time.time() - started), 3),
        "prepare_stage_ms": dict(timings),
        "mapping_state_mutated": False,
        "mode": "receiver-only-single-owner-v4-deferred-expand-fast",
    }
    for item in items or []:
        mid = source_catalog.mapping_source_id(item)
        key = str(mid).lower()
        name = str(item.get("name") or mid)
        channel_items = parsed.get(key) or {}
        if not channel_items:
            stats["unmapped_sources"] += 1
            stats["skipped_source_names"].append(name)
            continue
        if item.get("kind") == "local":
            url = _local_path(item, epg_dir)
            try:
                if not url or not os.path.isfile(url) or os.path.getsize(url) <= 32:
                    stats["empty_files"] += 1
                    stats["skipped_source_names"].append(name)
                    continue
            except Exception:
                stats["unreadable_files"] += 1
                stats["skipped_source_names"].append(name)
                continue
            stats["local_sources"] += 1
        else:
            # rc36.8 direct-feed contract: Import All must consume the exact XML
            # snapshot downloaded to /etc/epgmanager_epg moments earlier. Never
            # bypass that snapshot with a second live remote EPGImport request.
            local_direct = external_sources.local_xml_path(item, epg_dir) if (item.get("direct_feed") or item.get("grouped_feed")) else ""
            if local_direct and os.path.isfile(local_direct) and os.path.getsize(local_direct) > 32:
                url = local_direct
            elif item.get("direct_feed"):
                stats["unreadable_files"] += 1
                stats["skipped_source_names"].append(name)
                continue
            else:
                fast = source_fast_status.get(item.get("id")) or {}
                if fast.get("online") and fast.get("url"):
                    url = str(fast.get("url"))
                else:
                    url = str(item.get("url") or "")
            if not url:
                stats["unreadable_files"] += 1
                stats["skipped_source_names"].append(name)
                continue
            if item.get("direct_feed"):
                stats["local_sources"] += 1
            else:
                stats["direct_remote_sources"] += 1
        overlay_meta = {}
        if item.get("kind") != "local" and key == "ext_openepg_qatar_1":
            try:
                url, overlay_meta = openepg_arabic_overlay.build_transient_overlay(
                    key, name, url, channel_items, registry_index)
            except Exception as exc:
                overlay_meta = {"fallback": True, "reason": str(exc)}
            if overlay_meta.get("enabled"):
                stats["arabic_overlay_sources"] += 1
                stats["arabic_overlay_programmes"] += int(overlay_meta.get("translated_programmes") or 0)
                stats["arabic_overlay_titles"] += int(overlay_meta.get("translated_titles") or 0)
                stats["arabic_overlay_descriptions"] += int(overlay_meta.get("translated_descriptions") or 0)
            elif overlay_meta.get("fallback"):
                stats["arabic_overlay_fallbacks"] += 1

        source = LocalEPGSource(mid, name, url, channel_items)
        if overlay_meta.get("temp_path"):
            source._epgmanager_temp_path = str(overlay_meta.get("temp_path") or "")
            source._epgmanager_overlay_meta = dict(overlay_meta)
        sources.append(source)
        stats["ready_sources"] += 1
        stats["mapped_channel_ids"] += len(channel_items)
        stats["mapped_service_refs"] += sum(len(v) for v in channel_items.values())

    timings["source_build_overlay_ms"] = int(max(0.0, time.time() - _source_build_started) * 1000)
    stats["prepare_stage_ms"] = dict(timings)
    stats["prepare_elapsed"] = round(max(0.0, time.time() - started), 3)
    log.info(
        "Fast Import plan ready in %.2fs: %d receiver services, %d mapped IDs, %d refs; "
        "%d foreign refs pruned, %d duplicate claims suppressed, %d source(s) ready; stages=%s",
        stats["prepare_elapsed"], stats["receiver_service_count"],
        stats["mapped_channel_ids"], stats["mapped_service_refs"],
        stats["receiver_pruned_refs"], stats["suppressed_duplicate_refs"],
        stats["ready_sources"], stats.get("prepare_stage_ms"))
    return sources, stats


class DirectEPGImportRunner(EPGImportLocalRunner):
    def __init__(self, source_items, epg_dir=channel_mapper.EPG_DIR, only_iptv=False,
                 long_desc_days=5, clear_before_import=False, store=None, on_done=None):
        EPGImportLocalRunner.__init__(self, epg_dir=epg_dir, only_iptv=only_iptv,
                                      long_desc_days=long_desc_days,
                                      clear_before_import=clear_before_import,
                                      source_ids=None, store=store, on_done=on_done)
        self.source_items = list(source_items or [])
        self.stale_cleanup = {}

    def prepare(self):
        self.sources, self.stats = prepare_direct_sources(
            self.source_items, self.epg_dir, only_iptv=self.only_iptv, store=self.store)
        return self.sources, self.stats

    def start(self):
        """Start OE-Alliance EPGImport with per-service stale-event replacement.

        The frozen Golden bridge remains untouched.  The only behavioural
        difference is that a DVB service is flushed immediately before its first
        fresh event batch is written.  Empty/dead feeds therefore never erase EPG.
        """
        if self.running:
            return False
        if not self.sources:
            self.prepare()
        if not self.sources:
            skipped = self.stats.get("unmapped_sources", 0)
            empty = self.stats.get("empty_files", 0)
            raise RuntimeError(
                "No mapped XMLTV source is ready for import. %d source(s) have no Service Reference map and %d file(s) are empty." %
                (skipped, empty))

        try:
            from enigma import eEPGCache
        except Exception as exc:
            raise RuntimeError("Enigma2 eEPGCache is unavailable: %s" % exc)
        cache = eEPGCache.getInstance()
        if cache is None:
            raise RuntimeError("Enigma2 EPG cache is not available")

        try:
            Engine, module_name = _load_epgimport_class()
            self.engine_module = module_name
        except Exception as exc:
            log.warning("EPGImport unavailable; using native eEPGCache fallback: %s", exc)
            if self.clear_before_import and hasattr(cache, "flushEPG"):
                try:
                    cache.flushEPG()
                except Exception:
                    log.exception("Could not clear Enigma2 EPG cache before native fallback")
            return self._start_native_fallback(cache)

        if self.clear_before_import and hasattr(cache, "flushEPG"):
            try:
                cache.flushEPG()
            except Exception:
                log.exception("Could not clear Enigma2 EPG cache before import")

        # One-time migration cleanup for semantic variants which are now
        # intentionally UNMAPPED. Mapped services are handled just-in-time below.
        try:
            self.stale_cleanup = service_epg_sanitizer.sanitize_once(cache, self.sources)
        except Exception:
            self.stale_cleanup = {"ok": False, "reason": "exception"}
            log.exception("beta117 selective stale-EPG migration failed")

        self.cache_proxy = FirstWriteFlushProxy(cache, enabled=not self.clear_before_import)
        importer = Engine(self.cache_proxy, lambda _ref: True)
        importer.sources = list(reversed(self.sources))
        importer.onDone = self._on_done
        self.importer = importer
        self.running = True
        self.done = False
        self.error = None
        self.result = None
        self.started_at = time.time()

        long_desc_until = time.time() + self.long_desc_days * 86400
        try:
            importer.beginImport(longDescUntil=long_desc_until)
        except TypeError:
            importer.beginImport(long_desc_until)
        except Exception as exc:
            self.running = False
            self.done = True
            self.error = str(exc)
            try:
                openepg_arabic_overlay.cleanup(self._transient_overlay_paths())
            except Exception:
                pass
            log.exception("Could not start direct EPG Import engine")
            raise
        return True

    def _start_native_fallback(self, cache):
        """Use Enigma2 eEPGCache directly when the EPGImport plugin is absent.

        The exact prepared source.channels map is reused; no fuzzy remapping is
        performed here.  Parsing runs in a daemon worker so the Enigma2 UI stays
        responsive on Vu+ Zero 4K.
        """
        self.engine_module = "Native eEPGCache fallback"
        self.cache_proxy = FirstWriteFlushProxy(cache, enabled=not self.clear_before_import)
        self.importer = None
        self.running = True
        self.done = False
        self.error = None
        self.result = None
        self.started_at = time.time()
        self._native_fallback_state = {"source_name": "", "source_index": 0,
                                       "events": 0, "failed": 0}
        threading.Thread(target=self._native_fallback_worker,
                         name="EPGM-NativeFallback", daemon=True).start()
        return True

    def _native_fallback_worker(self):
        processed = 0
        bad_time = 0
        unmapped = 0
        transient_paths = self._transient_overlay_paths()
        long_desc_until = time.time() + self.long_desc_days * 86400
        try:
            for pos, source in enumerate(list(self.sources or []), 1):
                path = str(getattr(source, "url", "") or "")
                self._native_fallback_state["source_name"] = str(getattr(source, "description", "") or os.path.basename(path))
                self._native_fallback_state["source_index"] = pos
                if not path or not os.path.isfile(path):
                    continue
                channel_items = getattr(getattr(source, "channels", None), "items", {}) or {}
                try:
                    iterator = ET.iterparse(path, events=("end",))
                    for _event, elem in iterator:
                        if native_importer._local_tag(elem.tag) != "programme":
                            # Keep child text alive until the programme end event.
                            continue
                        processed += 1
                        cid = str(elem.get("channel") or "").strip().lower()
                        refs = channel_items.get(cid) or []
                        if not refs:
                            unmapped += 1; elem.clear(); continue
                        start = native_importer.parse_xmltv_time(elem.get("start"))
                        stop = native_importer.parse_xmltv_time(elem.get("stop"))
                        if start is None or stop is None or stop <= start:
                            bad_time += 1; elem.clear(); continue
                        title = native_importer._first_text(elem, "title") or "Programme"
                        subtitle = native_importer._first_text(elem, "sub-title")
                        desc = native_importer._first_text(elem, "desc")
                        if start > long_desc_until:
                            desc = ""
                        category = native_importer._category_code(native_importer._first_text(elem, "category"))
                        data = (int(start), int(stop-start), title, subtitle, desc, int(category))
                        for ref in refs:
                            try:
                                native_importer.import_event_compat(self.cache_proxy, ref, data)
                            except Exception:
                                self._native_fallback_state["failed"] += 1
                        self._native_fallback_state["events"] = int(getattr(self.cache_proxy, "success_events", 0) or 0)
                        elem.clear()
                except Exception:
                    log.exception("Native fallback XMLTV parse failed for %s", path)
            try:
                if getattr(self.cache_proxy, "success_events", 0) and hasattr(self.cache_proxy, "save"):
                    self.cache_proxy.save()
                elif getattr(self.cache_proxy, "success_events", 0) and hasattr(self.cache_proxy, "timeUpdated"):
                    self.cache_proxy.timeUpdated()
            except Exception:
                log.exception("Native fallback could not save EPG cache")
            proxy = self.cache_proxy
            priority_guard = {}
            try:
                priority_guard = epg_priority_guard.protect_imported_services(
                    getattr(proxy, "mapped_services", set()) or ())
            except Exception:
                log.exception("Could not persist EPGManager-first EPG priority")
                priority_guard = {"ok": False, "error": "exception"}
            self.finished_at = time.time()
            self.running = False
            self.done = True
            self.result = {
                "ok": int(getattr(proxy, "success_events", 0) or 0) > 0,
                "processed_events": int(processed),
                "imported_events": int(getattr(proxy, "success_events", 0) or 0),
                "failed_events": int(getattr(proxy, "failed_events", 0) or 0),
                "written_services": len(getattr(proxy, "mapped_services", set()) or ()),
                "sources": len(self.sources or []),
                "mapped_channel_ids": int(self.stats.get("mapped_channel_ids", 0) or 0),
                "mapped_service_refs": int(self.stats.get("mapped_service_refs", 0) or 0),
                "empty_files": int(self.stats.get("empty_files", 0) or 0),
                "unmapped_sources": int(self.stats.get("unmapped_sources", 0) or 0),
                "skipped_unmapped_programmes": int(unmapped),
                "skipped_bad_time": int(bad_time),
                "elapsed": max(0.0, self.finished_at - (self.started_at or self.finished_at)),
                "reboot_requested": False,
                "engine": self.engine_module,
                "native_fallback": True,
                "selective_flushed_services": len(getattr(proxy, "flushed_triplets", set()) or ()),
                "selective_flush_failures": int(getattr(proxy, "flush_failures", 0) or 0),
                "epg_priority_guard": dict(priority_guard or {}),
            }
            try:
                from . import activity_store
                activity_store.record_import(**self.result)
            except Exception:
                pass
        except Exception as exc:
            self.finished_at = time.time()
            self.running = False
            self.done = True
            self.error = str(exc)
            self.result = {"ok": False, "error": str(exc), "engine": self.engine_module,
                           "native_fallback": True, "processed_events": int(processed)}
            log.exception("Native eEPGCache fallback failed")
        finally:
            try:
                openepg_arabic_overlay.cleanup(transient_paths)
            except Exception:
                pass
            callback = self.on_done_callback
            self.on_done_callback = None
            result = dict(self.result or {})
            # Keep cache proxy alive until the callback is scheduled; then release
            # large maps to keep Vu+ RAM usage bounded.
            self.sources = []
            self.store = None
            def finish():
                try:
                    if callback:
                        callback(result)
                except Exception:
                    log.exception("Native fallback completion callback failed")
                finally:
                    self.cache_proxy = None
            try:
                from twisted.internet import reactor
                reactor.callFromThread(finish)
            except Exception:
                finish()

    def status(self):
        if getattr(self, "engine_module", "") == "Native eEPGCache fallback" and getattr(self, "running", False):
            state = dict(getattr(self, "_native_fallback_state", {}) or {})
            return {"running": True, "done": False,
                    "source_name": state.get("source_name") or "Native XMLTV",
                    "source_index": int(state.get("source_index") or 0),
                    "total_sources": len(self.sources or []),
                    "events": int(state.get("events") or 0),
                    "imported_events": int(state.get("events") or 0),
                    "failed_events": int(state.get("failed") or 0),
                    "engine": self.engine_module,
                    "elapsed": max(0.0, time.time() - (self.started_at or time.time()))}
        return EPGImportLocalRunner.status(self)

    def _transient_overlay_paths(self):
        out = []
        for source in list(self.sources or []):
            path = str(getattr(source, "_epgmanager_temp_path", "") or "")
            if path and path not in out:
                out.append(path)
        return out

    def _on_done(self, **kwargs):
        # Some forks can signal onDone twice; do not record/finalize twice.
        if self.done and not self.running:
            return
        transient_paths = self._transient_overlay_paths()
        proxy = self.cache_proxy
        flushed = len(getattr(proxy, "flushed_triplets", set()) or ()) if proxy is not None else 0
        flush_failed = int(getattr(proxy, "flush_failures", 0) or 0) if proxy is not None else 0
        # The Golden parent finalizer invokes the UI callback. Hold it briefly so
        # beta117 can append selective-flush diagnostics before the UI sees the
        # result; then invoke exactly once with the complete result.
        callback = self.on_done_callback
        self.on_done_callback = None
        try:
            EPGImportLocalRunner._on_done(self, **kwargs)
        finally:
            self.on_done_callback = callback
        priority_guard = {}
        try:
            priority_guard = epg_priority_guard.protect_imported_services(
                getattr(proxy, "mapped_services", set()) or ()) if proxy is not None else {}
        except Exception:
            log.exception("Could not persist EPGManager-first EPG priority")
            priority_guard = {"ok": False, "error": "exception"}
        if isinstance(self.result, dict):
            self.result["selective_flushed_services"] = flushed
            self.result["selective_flush_failures"] = flush_failed
            self.result["stale_cleanup"] = dict(self.stale_cleanup or {})
            self.result["epg_priority_guard"] = dict(priority_guard or {})
        try:
            openepg_arabic_overlay.cleanup(transient_paths)
        except Exception:
            pass
        if callback:
            try:
                callback(self.result)
            except Exception:
                log.exception("Direct EPGImport completion callback failed")
