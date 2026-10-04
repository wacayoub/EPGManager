# -*- coding: utf-8 -*-
"""Fast local Native Import bridge built on the installed OE-Alliance EPG Import engine.

Why this exists
---------------
EPG Manager already has two pieces of information that EPG Import needs:

* local XMLTV files in /etc/epgimport/jedi_epg
* direct XMLTV channel-id -> Enigma2 service-reference maps in EPG-Import
  channels.xml format, generated per selected source from Reception Lists or
  supplied by the provider (Rytec)

Manual Native Import must *not* rescan/fuzzy-match every XMLTV channel against
all bouquets before every import.  That was both very slow (O(channels*services))
and was the main reason the Manual screen could sit at 0% for a long time.

This module loads the same ``<channel id=...>SERVICE_REFERENCE</channel>``
structure used by Rytec/EPG Import directly into the ``channels.items``
dictionary consumed by OE-Alliance XMLTV-Import and then
lets the receiver's installed EPG Import engine do what it already does well:
stream XMLTV, parse dates/categories, and call eEPGCache.importEvents().

No network access is performed here.  Source URLs are local filesystem paths.
"""
from __future__ import print_function

import importlib
import os
import time

from . import channel_mapper
from .logger import get_logger
from .mapping_store import MappingStore
from . import srp_channel_map
from . import activity_store

log = get_logger(__name__)


class StaticChannelMap(object):
    """Minimal EPGChannel-compatible object with an already-built map.

    OE-Alliance EPGImport only needs ``items``, ``downloadables()`` and
    ``update()`` from ``source.channels``.  Because the SRP channel map is prepared before Native Import, there is no
    receiver-wide matching pass here.
    """

    def __init__(self, items):
        self.items = items or {}

    def downloadables(self):
        # Returning None tells EPGImport that there is no channel-map download.
        return None

    def update(self, _filter_callback, _downloaded_file=None):
        # Mapping is already loaded from MappingStore; intentionally no-op.
        return None


class LocalEPGSource(object):
    """Small object matching the fields used by EPGImport.EPGImport."""

    def __init__(self, source_id, description, path, channel_items):
        self.source_id = str(source_id or "")
        self.description = str(description or self.source_id or os.path.basename(path))
        self.url = str(path)
        self.urls = [str(path)]
        self.parser = "gen_xmltv"
        self.nocheck = 1
        self.offset = 0
        self.format = "xml"
        self.channels = StaticChannelMap(channel_items)


_LOCAL_ALIASES = {
    "medi1tv": ("medi1tv", "local_medi1tv"),
    "chada_2m": ("chada_2m", "local_chada_2m"),
    "snrt": ("snrt", "local_snrt"),
    "bein_sports": ("bein_sports", "local_bein_sports"),
    "arryadia": ("arryadia", "local_arryadia"),
}


def _source_aliases(source_id):
    sid = str(source_id or "").lower()
    aliases = list(_LOCAL_ALIASES.get(sid, (sid,)))
    if sid.startswith("local_"):
        aliases.append(sid[6:])
    elif sid:
        aliases.append("local_" + sid)
    out = []
    for value in aliases:
        value = str(value or "").lower()
        if value and value not in out:
            out.append(value)
    return out


def _legacy_mappings_by_source(store=None):
    """Compatibility fallback for pre-v6.4 MappingStore data."""
    store = store or MappingStore()
    grouped = {}
    for key, record in (store.all() or {}).items():
        if "::" not in str(key):
            continue
        source_id, channel_id = str(key).split("::", 1)
        source_id = source_id.strip().lower()
        channel_id = channel_id.lower()
        if not source_id or not channel_id or not isinstance(record, dict):
            continue
        if str(record.get("mode") or "").lower() != "manual":
            continue
        refs = []
        for ref in record.get("refs") or []:
            ref = str(ref or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        if refs:
            grouped.setdefault(source_id, {})[channel_id] = refs
    return grouped


def prepare_local_sources(epg_dir=channel_mapper.EPG_DIR, only_iptv=False,
                          source_ids=None, store=None):
    """Prepare local EPGImport source adapters without parsing programme data.

    This operation is intentionally very cheap: directory listing + stat() +
    direct channels.xml lookup only. Empty files and files with no SRP map are
    skipped immediately.
    """
    index = srp_channel_map.load_index()
    direct_index = index.get("sources") or {}
    legacy_mappings = _legacy_mappings_by_source(store=store)
    rows = list(channel_mapper.iter_source_xml_files(epg_dir, source_ids=source_ids))

    sources = []
    stats = {
        "total_files": len(rows),
        "ready_sources": 0,
        "empty_files": 0,
        "unreadable_files": 0,
        "unmapped_sources": 0,
        "mapped_channel_ids": 0,
        "mapped_service_refs": 0,
        "skipped_source_names": [],
    }

    for source_id, source_name, path in rows:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 32:
                stats["empty_files"] += 1
                continue
        except Exception:
            stats["unreadable_files"] += 1
            continue

        channel_items = {}
        direct_row = None
        for alias in _source_aliases(source_id):
            direct_row = direct_index.get(alias) or direct_index.get(str(alias).lower())
            if direct_row:
                break
        if direct_row:
            map_path = str((direct_row or {}).get("map_path") or "")
            try:
                wanted = [x.get("channel_id") for x in channel_mapper.read_xmltv_channel_ids_fast(path, source_id, source_name)]
            except Exception:
                wanted = []
            parsed = srp_channel_map.parse_channels_xml(map_path, wanted_ids=wanted)
            for channel_id, refs in parsed.items():
                clean_refs = []
                for ref in refs:
                    if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                        continue
                    if ref not in clean_refs:
                        clean_refs.append(ref)
                if clean_refs:
                    channel_items[str(channel_id).lower()] = clean_refs

        # Legacy manual mappings are a compatibility fallback only. They are
        # not generated or maintained by the v6.4 workflow.
        if not channel_items:
            for alias in _source_aliases(source_id):
                for channel_id, refs in (legacy_mappings.get(alias) or {}).items():
                    clean_refs = []
                    for ref in refs:
                        if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                            continue
                        if ref not in clean_refs:
                            clean_refs.append(ref)
                    if clean_refs:
                        channel_items[str(channel_id).lower()] = clean_refs

        if not channel_items:
            stats["unmapped_sources"] += 1
            stats["skipped_source_names"].append(str(source_name))
            continue

        source = LocalEPGSource(source_id, source_name, path, channel_items)
        sources.append(source)
        stats["ready_sources"] += 1
        stats["mapped_channel_ids"] += len(channel_items)
        stats["mapped_service_refs"] += sum(len(x) for x in channel_items.values())

    return sources, stats


def _load_epgimport_class():
    """Load the installed EPG Import engine from common Enigma2 locations."""
    errors = []
    candidates = (
        "Plugins.Extensions.EPGImport.EPGImport",
        "Plugins.SystemPlugins.EPGImport.EPGImport",
    )
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, "EPGImport", None)
            if cls is not None:
                return cls, module_name
        except Exception as exc:
            errors.append("%s: %s" % (module_name, exc))
    raise RuntimeError(
        "EPG Import engine was not found on this receiver. "
        "Install/enable EPG Import first. Details: %s" % "; ".join(errors[:2]))




class CountingEPGCacheProxy(object):
    """Delegate to eEPGCache while counting successful/failed event writes."""

    def __init__(self, cache):
        self._cache = cache
        self.success_events = 0
        self.failed_events = 0
        self.failed_calls = 0
        self.mapped_services = set()

    @staticmethod
    def _event_count(events):
        try:
            return len(events)
        except Exception:
            return 1 if events else 0

    def _remember_services(self, services):
        if isinstance(services, (list, tuple, set)):
            values = services
        else:
            values = [services]
        for ref in values:
            ref = str(ref or "").strip()
            if ref:
                self.mapped_services.add(ref)

    def importEvents(self, services, events):
        count = self._event_count(events)
        try:
            result = self._cache.importEvents(services, events)
            self.success_events += count
            self._remember_services(services)
            return result
        except Exception:
            self.failed_events += count
            self.failed_calls += 1
            if self.failed_calls <= 5:
                log.exception("eEPGCache.importEvents failed")
            raise

    def importEvent(self, service, events):
        count = self._event_count(events)
        try:
            result = self._cache.importEvent(service, events)
            self.success_events += count
            self._remember_services(service)
            return result
        except Exception:
            self.failed_events += count
            self.failed_calls += 1
            if self.failed_calls <= 5:
                log.exception("eEPGCache.importEvent failed")
            raise

    def save(self):
        if hasattr(self._cache, "save"):
            return self._cache.save()

    def timeUpdated(self):
        if hasattr(self._cache, "timeUpdated"):
            return self._cache.timeUpdated()

    def load(self):
        if hasattr(self._cache, "load"):
            return self._cache.load()

    def __getattr__(self, name):
        return getattr(self._cache, name)


class EPGImportLocalRunner(object):
    """Asynchronous local import using OE-Alliance EPG Import's own engine."""

    def __init__(self, epg_dir=channel_mapper.EPG_DIR, only_iptv=False,
                 long_desc_days=5, clear_before_import=False, source_ids=None,
                 store=None, on_done=None):
        self.epg_dir = epg_dir
        self.only_iptv = bool(only_iptv)
        self.long_desc_days = max(0, int(long_desc_days or 0))
        self.clear_before_import = bool(clear_before_import)
        self.source_ids = source_ids
        self.store = store or MappingStore()
        self.on_done_callback = on_done

        self.sources = []
        self.stats = {}
        self.importer = None
        self.engine_module = None
        self.running = False
        self.done = False
        self.error = None
        self.result = None
        self.started_at = None
        self.finished_at = None
        self.cache_proxy = None

    def prepare(self):
        self.sources, self.stats = prepare_local_sources(
            self.epg_dir, only_iptv=self.only_iptv,
            source_ids=self.source_ids, store=self.store)
        return self.sources, self.stats

    def start(self):
        if self.running:
            return False
        if not self.sources:
            self.prepare()
        if not self.sources:
            skipped = self.stats.get("unmapped_sources", 0)
            empty = self.stats.get("empty_files", 0)
            raise RuntimeError(
                "No mapped local XMLTV source is ready for import. "
                "%d source(s) have no Service Reference map and %d file(s) are empty." %
                (skipped, empty))

        try:
            from enigma import eEPGCache
        except Exception as exc:
            raise RuntimeError("Enigma2 eEPGCache is unavailable: %s" % exc)

        cache = eEPGCache.getInstance()
        if cache is None:
            raise RuntimeError("Enigma2 EPG cache is not available")

        Engine, module_name = _load_epgimport_class()
        self.engine_module = module_name

        if self.clear_before_import and hasattr(cache, "flushEPG"):
            try:
                cache.flushEPG()
            except Exception:
                log.exception("Could not clear Enigma2 EPG cache before import")

        # The channel filter is not used by StaticChannelMap.update(), but the
        # official constructor expects one.  Keep a permissive callable for
        # compatibility with forks that may inspect it.
        self.cache_proxy = CountingEPGCacheProxy(cache)
        importer = Engine(self.cache_proxy, lambda _ref: True)
        # EPGImport pops from the end; reverse to preserve the visible file order.
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
            # Some older forks expose beginImport() without a keyword argument.
            importer.beginImport(long_desc_until)
        except Exception as exc:
            self.running = False
            self.done = True
            self.error = str(exc)
            log.exception("Could not start EPG Import local engine")
            raise
        return True

    def _on_done(self, **kwargs):
        processed = 0
        try:
            processed = int(getattr(self.importer, "eventCount", 0) or 0)
        except Exception:
            processed = 0
        proxy = self.cache_proxy
        count = int(getattr(proxy, "success_events", 0) or 0) if proxy is not None else 0
        failed = int(getattr(proxy, "failed_events", 0) or 0) if proxy is not None else 0
        services_written = len(getattr(proxy, "mapped_services", set()) or ()) if proxy is not None else 0
        self.finished_at = time.time()
        self.running = False
        self.done = True
        self.result = {
            "ok": count > 0,
            "processed_events": processed,
            "imported_events": count,
            "failed_events": failed,
            "written_services": services_written,
            "sources": len(self.sources),
            "mapped_channel_ids": int(self.stats.get("mapped_channel_ids", 0) or 0),
            "mapped_service_refs": int(self.stats.get("mapped_service_refs", 0) or 0),
            "empty_files": int(self.stats.get("empty_files", 0) or 0),
            "unmapped_sources": int(self.stats.get("unmapped_sources", 0) or 0),
            "elapsed": max(0.0, self.finished_at - (self.started_at or self.finished_at)),
            "reboot_requested": bool(kwargs.get("reboot", False)),
            "engine": self.engine_module or "EPG Import",
        }
        try:
            activity_store.record_import(**self.result)
        except Exception:
            pass
        log.info("Native import via EPG Import finished: %s", self.result)
        callback = self.on_done_callback
        if callback:
            try:
                callback(self.result)
            except Exception:
                log.exception("Native Import completion callback failed")
        # Low-memory Vu+ cleanup: OE-Alliance's importer/parser and our static
        # channel maps may retain thousands of service refs after completion.
        # Keep only the compact result/stats needed by the UI.
        self.on_done_callback = None
        self.importer = None
        self.sources = []
        self.cache_proxy = None
        self.store = None

    def status(self):
        importer = self.importer
        total = len(self.sources)
        if importer is None:
            return {
                "running": False, "done": self.done, "source_name": "",
                "source_index": 0, "total_sources": total, "events": 0,
            }
        try:
            remaining = len(getattr(importer, "sources", []) or [])
        except Exception:
            remaining = 0
        current = getattr(importer, "source", None)
        source_name = getattr(current, "description", "") if current is not None else ""
        # The current source has already been popped from importer.sources.
        source_index = max(0, min(total, total - remaining))
        try:
            events = int(getattr(importer, "eventCount", 0) or 0)
        except Exception:
            events = 0
        proxy = self.cache_proxy
        imported = int(getattr(proxy, "success_events", 0) or 0) if proxy is not None else 0
        failed = int(getattr(proxy, "failed_events", 0) or 0) if proxy is not None else 0
        return {
            "running": bool(self.running),
            "done": bool(self.done),
            "source_name": source_name,
            "source_index": source_index,
            "total_sources": total,
            "events": events,
            "imported_events": imported,
            "failed_events": failed,
            "engine": self.engine_module or "EPG Import",
            "elapsed": max(0.0, time.time() - (self.started_at or time.time())),
        }
