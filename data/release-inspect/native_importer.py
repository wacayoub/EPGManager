# -*- coding: utf-8 -*-
"""Native XMLTV -> Enigma2 EPG cache importer.

This module is independent from EPG-Importer.  Parsing/mapping can be unit tested
on a normal Python installation; the actual eEPGCache import is loaded lazily and
therefore only runs on a receiver.
"""
from __future__ import print_function

import calendar
import os
import re
from collections import defaultdict
from xml.etree import ElementTree as ET

from . import channel_mapper
from .logger import get_logger
from .mapping_store import MappingStore

log = get_logger(__name__)

_TZ_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def parse_xmltv_time(value):
    """Return UTC epoch seconds for common XMLTV timestamps.

    Handles YYYYMMDDHHMMSS, optional timezone offsets (+0100/+01:00), and Z.
    """
    if not value:
        return None
    parts = str(value).strip().split()
    raw = parts[0]
    if len(raw) < 12:
        return None
    raw = raw[:14].ljust(14, "0")
    try:
        year = int(raw[0:4]); month = int(raw[4:6]); day = int(raw[6:8])
        hour = int(raw[8:10]); minute = int(raw[10:12]); second = int(raw[12:14])
        epoch = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    except Exception:
        return None

    if len(parts) > 1:
        tz = parts[1].strip()
        if tz.upper() == "Z":
            return int(epoch)
        match = _TZ_RE.match(tz)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            offset = (int(match.group(2)) * 60 + int(match.group(3))) * 60
            epoch -= sign * offset
    return int(epoch)


def _first_text(elem, tag):
    for node in elem.findall(tag):
        if node.text:
            return node.text.strip()
    return ""


def _category_code(text):
    """Small conservative DVB content-nibble approximation.

    0 means unknown/general and is accepted by eEPGCache.  Using a small map
    avoids incorrectly categorising content when providers use free-form XMLTV
    categories.
    """
    value = (text or "").lower()
    if any(w in value for w in ("sport", "football", "soccer", "tennis")):
        return 0x40
    if any(w in value for w in ("news", "journal", "actualité", "actualite")):
        return 0x20
    if any(w in value for w in ("movie", "film", "cinema")):
        return 0x10
    if any(w in value for w in ("kids", "children", "cartoon", "enfant")):
        return 0x50
    if any(w in value for w in ("documentary", "documentaire")):
        return 0xA0
    return 0


def _map_key(source_id, channel_id):
    return "%s::%s" % (str(source_id or "").lower(), str(channel_id or "").lower())

def _local_tag(tag):
    """Return an XML local-name, accepting providers that use namespaces."""
    value = str(tag or "")
    return value.split("}", 1)[-1] if "}" in value else value


def build_mapping(epg_dir, bouquet_dir=channel_mapper.BOUQUET_DIR, min_score=82, store=None, adaptive=True, source_ids=None):
    """Build source-aware XMLTV -> service mapping.

    v6.1 keeps source_id in the key so identical channel IDs from different
    providers cannot overwrite each other.  The legacy channel-id-only key is
    also populated when it is unambiguous for backward compatibility.
    """
    store = store or MappingStore()
    epg_channels = channel_mapper.list_epg_channels(epg_dir=epg_dir)
    if source_ids:
        selected = set(str(x) for x in source_ids)
        aliases = {"local_medi1tv":"medi1tv", "local_chada_2m":"chada_2m", "local_snrt":"snrt",
                   "local_bein_sports":"bein_sports", "local_arryadia":"arryadia"}
        selected |= set(aliases.get(x, x) for x in list(selected))
        epg_channels = [x for x in epg_channels if x.get("source_id") in selected]
    catalog = channel_mapper.scan_bouquets(bouquet_dir=bouquet_dir)
    results = channel_mapper.smart_match_channels(epg_channels, catalog, min_score=min_score, store=store)
    mapping = defaultdict(list)
    legacy_seen = {}
    for item in results:
        sid = item.get("source_id")
        cid = item.get("channel_id")
        refs = []
        for match in item.get("matches", []):
            ref = match.get("ref")
            if ref and ref not in refs:
                refs.append(ref)
        if refs:
            mapping[_map_key(sid, cid)] = refs
            key = str(cid or "").lower()
            if key not in legacy_seen:
                legacy_seen[key] = refs
            elif legacy_seen[key] != refs:
                legacy_seen[key] = None
    for key, refs in legacy_seen.items():
        if refs:
            mapping[key] = refs
    return results, dict(mapping)



def local_xmltv_files(epg_dir=channel_mapper.EPG_DIR):
    """Return all local XMLTV inputs that Manual Native Import can consume.

    This helper intentionally performs no network access.
    """
    return list(channel_mapper.iter_source_xml_files(epg_dir, source_ids=None))

def preflight(epg_dir, bouquet_dir=channel_mapper.BOUQUET_DIR, source_ids=None,
              only_iptv=False, min_score=94, adaptive=True):
    """Fast diagnostics before an import starts."""
    files = list(channel_mapper.iter_source_xml_files(epg_dir, source_ids=source_ids))
    info = {
        "ok": False, "files": len(files), "selected_sources": len(set(source_ids or [])),
        "readable_files": 0, "empty_files": 0, "xml_errors": 0, "mapped_channels": 0,
        "events_sampled": 0, "warnings": [], "errors": [],
    }
    if not files:
        info["errors"].append("No usable local XMLTV file is available in the EPG directory.")
        return info
    for _sid, _name, path in files:
        try:
            size = os.path.getsize(path)
            if size <= 32:
                info["empty_files"] += 1
                continue
            info["readable_files"] += 1
            # Parse only enough to prove that the document contains programmes.
            seen = 0
            for _event, elem in ET.iterparse(path, events=("end",)):
                if elem.tag == "programme":
                    seen += 1
                    info["events_sampled"] += 1
                    elem.clear()
                    if seen >= 5:
                        break
                else:
                    elem.clear()
        except ET.ParseError:
            info["xml_errors"] += 1
        except Exception as exc:
            info["warnings"].append("%s: %s" % (_name, exc))
    try:
        _rows, mapping = build_mapping(epg_dir, bouquet_dir=bouquet_dir, min_score=min_score, adaptive=adaptive, source_ids=source_ids)
        keys = [k for k in mapping if "::" in k]
        info["mapped_channels"] = len(keys)
    except Exception as exc:
        info["warnings"].append("Mapping scan: %s" % exc)
    if info["empty_files"]:
        info["warnings"].append("%d XMLTV file(s) are empty" % info["empty_files"])
    if info["xml_errors"]:
        info["warnings"].append("%d XMLTV file(s) are malformed" % info["xml_errors"])
    if info["mapped_channels"] == 0:
        info["warnings"].append("No channels are currently mapped")
    info["ok"] = info["readable_files"] > 0 and info["xml_errors"] < info["files"]
    return info


def import_event_compat(cache, service_ref, event):
    """Import one XMLTV event using the same call shape as EPG Import.

    OE-Alliance EPG Import calls ``epgcache.importEvents(service_ref, (event,))``
    for each parsed event.  Keeping the service reference as a plain string and
    the event container to a single tuple avoids SWIG/image-specific crashes
    seen when passing a tuple of service strings or very large event batches.
    """
    ref = str(service_ref)
    event = tuple(event)
    first_error = None
    if hasattr(cache, "importEvents"):
        try:
            cache.importEvents(ref, (event,))
            return "importEvents"
        except Exception as exc:
            first_error = exc
    if hasattr(cache, "importEvent"):
        try:
            try:
                from enigma import eServiceReference
                legacy_ref = eServiceReference(ref)
            except Exception:
                legacy_ref = ref
            cache.importEvent(legacy_ref, (event,))
            return "importEvent"
        except Exception as exc:
            if first_error is None:
                first_error = exc
    raise first_error or RuntimeError("No compatible Enigma2 EPG import API")


def _import_events_compat(cache, service_ref, events):
    """Compatibility wrapper; deliberately imports event-by-event."""
    count = 0
    for event in tuple(events or ()):
        import_event_compat(cache, service_ref, event)
        count += 1
    return "importEvents" if count else "empty"

def build_import_plan(epg_dir, mapping=None, bouquet_dir=channel_mapper.BOUQUET_DIR,
                      min_score=82, store=None, max_events_per_service=5000,
                      only_iptv=False, long_desc_days=5, source_ids=None, progress_cb=None, adaptive=True):
    """Parse generated XMLTV files and return an import plan.

    The plan is a dict with service reference -> tuple(event tuples).  Event tuple
    shape matches the Enigma2 eEPGCache.importEvents API used by established XMLTV
    importers: (start, duration, title, short, description, category).
    """
    mapping_results = None
    if mapping is None:
        mapping_results, mapping = build_mapping(epg_dir, bouquet_dir, min_score, store, adaptive=adaptive, source_ids=source_ids)

    by_service = defaultdict(list)
    parsed = 0
    import time as _time
    long_desc_until = _time.time() + max(0, int(long_desc_days or 0)) * 86400
    skipped_unmapped = 0
    skipped_bad_time = 0
    files = 0
    source_stats = []
    source_files = list(channel_mapper.iter_source_xml_files(epg_dir, source_ids=source_ids))
    total_files = len(source_files)

    skipped_empty_files = 0
    skipped_unreadable_files = 0
    for file_index, (_source_id, _source_name, path) in enumerate(source_files, 1):
        # Empty/placeholder XMLTV files are common in jedi_epg. Never hand them
        # to ElementTree: stat() is effectively free and lets Manual jump to
        # the next source immediately.
        try:
            if os.path.getsize(path) <= 32:
                skipped_empty_files += 1
                if progress_cb:
                    try:
                        progress_cb({"phase": "scan", "file_index": file_index, "total_files": total_files,
                                     "source_id": _source_id, "source_name": "%s (empty - skipped)" % _source_name,
                                     "parsed_programmes": parsed,
                                     "events_ready": sum(len(v) for v in by_service.values())})
                    except Exception:
                        pass
                continue
        except Exception:
            skipped_unreadable_files += 1
            continue
        files += 1
        source_parsed = 0
        source_ready_before = sum(len(v) for v in by_service.values())
        if progress_cb:
            try:
                progress_cb({"phase": "scan", "file_index": file_index, "total_files": total_files,
                             "source_id": _source_id, "source_name": _source_name,
                             "parsed_programmes": parsed, "events_ready": source_ready_before})
            except Exception:
                pass
        try:
            iterator = ET.iterparse(path, events=("end",))
            for _event, elem in iterator:
                if _local_tag(elem.tag) != "programme":
                    continue
                parsed += 1
                source_parsed += 1
                if progress_cb and source_parsed % 1000 == 0:
                    try:
                        progress_cb({"phase": "scan", "file_index": file_index, "total_files": total_files,
                                     "source_id": _source_id, "source_name": _source_name,
                                     "parsed_programmes": parsed,
                                     "events_ready": sum(len(v) for v in by_service.values())})
                    except Exception:
                        pass
                channel_id = (elem.get("channel") or "").lower()
                refs = mapping.get(_map_key(_source_id, channel_id)) or mapping.get(channel_id) or []
                if not refs:
                    skipped_unmapped += 1
                    elem.clear()
                    continue
                start = parse_xmltv_time(elem.get("start"))
                stop = parse_xmltv_time(elem.get("stop"))
                if start is None or stop is None or stop <= start:
                    skipped_bad_time += 1
                    elem.clear()
                    continue
                title = _first_text(elem, "title") or "Programme"
                subtitle = _first_text(elem, "sub-title")
                desc = _first_text(elem, "desc")
                if long_desc_days is not None and int(long_desc_days) >= 0 and start > long_desc_until:
                    desc = ""
                category = _category_code(_first_text(elem, "category"))
                data = (int(start), int(stop - start), title, subtitle, desc, int(category))
                for ref in refs:
                    if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                        continue
                    bucket = by_service[ref]
                    if len(bucket) < max_events_per_service:
                        bucket.append(data)
                elem.clear()
        except ET.ParseError as exc:
            log.warning("Skipping malformed XMLTV file %s: %s", path, exc)
        except Exception:
            log.exception("Failed parsing XMLTV file %s", path)
        source_ready_after = sum(len(v) for v in by_service.values())
        source_stats.append({
            "source_id": _source_id, "source_name": _source_name, "path": path,
            "parsed_programmes": source_parsed,
            "events_ready": max(0, source_ready_after - source_ready_before),
        })
        if progress_cb:
            try:
                progress_cb({"phase": "scan", "file_index": file_index, "total_files": total_files,
                             "source_id": _source_id, "source_name": _source_name,
                             "parsed_programmes": parsed, "events_ready": source_ready_after})
            except Exception:
                pass

    for ref in list(by_service):
        by_service[ref].sort(key=lambda item: item[0])

    # Friendly service names for EPG-Import-style progress feedback.
    service_labels = {}
    try:
        for row in (mapping_results or []):
            for match in row.get("matches", []):
                ref = match.get("ref")
                name = match.get("name")
                if ref and name and ref not in service_labels:
                    service_labels[ref] = name
        if len(service_labels) < len(by_service):
            for entry in channel_mapper.scan_bouquets(bouquet_dir=bouquet_dir, use_cache=True, fast_cache=True):
                ref = entry.get("ref")
                if ref in by_service and ref not in service_labels:
                    service_labels[ref] = entry.get("name") or ref
    except Exception:
        pass

    return {
        "services": {ref: tuple(events) for ref, events in by_service.items()},
        "files": files,
        "parsed_programmes": parsed,
        "mapped_services": len(by_service),
        "events_ready": sum(len(v) for v in by_service.values()),
        "skipped_unmapped": skipped_unmapped,
        "skipped_bad_time": skipped_bad_time,
        "skipped_empty_files": skipped_empty_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "mapping_results": mapping_results,
        "source_stats": source_stats,
        "service_labels": service_labels,
        "service_types": {
            "SAT": sum(1 for ref in by_service if channel_mapper.classify_service_ref(ref) == "SAT"),
            "IPTV": sum(1 for ref in by_service if channel_mapper.classify_service_ref(ref) == "IPTV"),
            "DVB": sum(1 for ref in by_service if channel_mapper.classify_service_ref(ref) == "DVB"),
        },
    }



def import_local_xmltv_streaming(epg_dir, bouquet_dir=channel_mapper.BOUQUET_DIR,
                                 min_score=88, only_iptv=False, long_desc_days=5,
                                 clear_before_import=False, source_ids=None,
                                 progress_cb=None, adaptive=False):
    """Parse local XMLTV and inject events immediately, EPG-Import style.

    This is the fast/manual path.  It never downloads anything and it avoids
    building a second in-memory import plan.  The official OE-Alliance EPG
    Importer parses XMLTV in a worker thread and calls eEPGCache.importEvents()
    as events are yielded; this function follows that model for files already
    present in ``jedi_epg``.
    """
    try:
        from enigma import eEPGCache
    except ImportError:
        raise RuntimeError("Native Enigma2 eEPGCache API is not available")

    cache = eEPGCache.getInstance()
    if cache is None or not (hasattr(cache, "importEvents") or hasattr(cache, "importEvent")):
        raise RuntimeError("This Enigma2 image does not expose a compatible EPG import API")

    def emit(**kw):
        if progress_cb:
            try:
                progress_cb(dict(kw))
            except Exception:
                pass

    # Build channel->service mapping once.  list_epg_channels() uses a fast
    # header-only XMLTV scan and bouquet scanning uses the persistent receiver
    # cache, so this no longer parses all programme data before import starts.
    emit(phase="mapping", source_name="Loading channel mappings", file_index=0,
         total_files=0, parsed_programmes=0, imported_events=0,
         skipped_unmapped=0, failed_events=0)
    _mapping_rows, mapping = build_mapping(
        epg_dir, bouquet_dir=bouquet_dir, min_score=min_score,
        store=MappingStore(), adaptive=adaptive, source_ids=source_ids)

    if clear_before_import and hasattr(cache, "flushEPG"):
        try:
            cache.flushEPG()
        except Exception:
            log.exception("Could not clear existing EPG cache before streaming import")

    source_files = list(channel_mapper.iter_source_xml_files(epg_dir, source_ids=source_ids))
    total_files = len(source_files)
    parsed = 0
    imported = 0
    skipped_unmapped = 0
    skipped_bad_time = 0
    skipped_empty_files = 0
    skipped_unreadable_files = 0
    malformed_files = 0
    failed_events = 0
    mapped_services = set()
    source_stats = []

    import time as _time
    long_desc_until = _time.time() + max(0, int(long_desc_days or 0)) * 86400

    for file_index, (_source_id, _source_name, path) in enumerate(source_files, 1):
        try:
            if os.path.getsize(path) <= 32:
                skipped_empty_files += 1
                emit(phase="import", source_name="%s (empty - skipped)" % _source_name,
                     file_index=file_index, total_files=total_files,
                     parsed_programmes=parsed, imported_events=imported,
                     skipped_unmapped=skipped_unmapped, failed_events=failed_events)
                continue
        except Exception:
            skipped_unreadable_files += 1
            continue

        source_parsed = 0
        source_imported = 0
        emit(phase="import", source_name=_source_name, file_index=file_index,
             total_files=total_files, parsed_programmes=parsed,
             imported_events=imported, skipped_unmapped=skipped_unmapped,
             failed_events=failed_events)
        try:
            for _event, elem in ET.iterparse(path, events=("end",)):
                if _local_tag(elem.tag) != "programme":
                    # Do not clear title/desc/category children before their
                    # parent <programme> end event; doing so erases metadata.
                    # The parent is cleared immediately after import, which is
                    # enough to keep memory bounded.
                    continue
                parsed += 1
                source_parsed += 1
                channel_id = (elem.get("channel") or "").lower()
                refs = mapping.get(_map_key(_source_id, channel_id)) or mapping.get(channel_id) or []
                if not refs:
                    skipped_unmapped += 1
                    elem.clear()
                    if source_parsed % 250 == 0:
                        emit(phase="import", source_name=_source_name,
                             file_index=file_index, total_files=total_files,
                             parsed_programmes=parsed, imported_events=imported,
                             skipped_unmapped=skipped_unmapped, failed_events=failed_events)
                    continue

                start = parse_xmltv_time(elem.get("start"))
                stop = parse_xmltv_time(elem.get("stop"))
                if start is None or stop is None or stop <= start:
                    skipped_bad_time += 1
                    elem.clear()
                    continue

                title = _first_text(elem, "title") or "Programme"
                subtitle = _first_text(elem, "sub-title")
                desc = _first_text(elem, "desc")
                if long_desc_days is not None and int(long_desc_days) >= 0 and start > long_desc_until:
                    desc = ""
                category = _category_code(_first_text(elem, "category"))
                # Same six-field tuple used by OE-Alliance XMLTV-Import when no
                # parental rating tuple is present.
                data = (int(start), int(stop - start), title, subtitle, desc, int(category))

                for ref in refs:
                    if only_iptv and channel_mapper.classify_service_ref(ref) != "IPTV":
                        continue
                    try:
                        import_event_compat(cache, ref, data)
                        imported += 1
                        source_imported += 1
                        mapped_services.add(ref)
                    except Exception:
                        failed_events += 1
                        if failed_events <= 5:
                            log.exception("Native streaming import failed for %s", ref)
                elem.clear()

                # Frequent but inexpensive UI feedback; no per-event UI calls.
                if source_parsed % 250 == 0:
                    emit(phase="import", source_name=_source_name,
                         file_index=file_index, total_files=total_files,
                         parsed_programmes=parsed, imported_events=imported,
                         skipped_unmapped=skipped_unmapped, failed_events=failed_events)
        except ET.ParseError as exc:
            malformed_files += 1
            log.warning("Skipping malformed XMLTV file %s: %s", path, exc)
        except Exception:
            skipped_unreadable_files += 1
            log.exception("Failed streaming XMLTV file %s", path)

        source_stats.append({
            "source_id": _source_id, "source_name": _source_name, "path": path,
            "parsed_programmes": source_parsed, "imported_events": source_imported,
        })
        emit(phase="import", source_name=_source_name, file_index=file_index,
             total_files=total_files, parsed_programmes=parsed,
             imported_events=imported, skipped_unmapped=skipped_unmapped,
             failed_events=failed_events)

    try:
        if imported and hasattr(cache, "save"):
            cache.save()
        elif imported and hasattr(cache, "timeUpdated"):
            cache.timeUpdated()
    except Exception:
        log.exception("Could not save/refresh Enigma2 EPG cache after import")

    result = {
        "ok": imported > 0,
        "files": total_files - skipped_empty_files - skipped_unreadable_files,
        "total_files": total_files,
        "parsed_programmes": parsed,
        "imported_events": imported,
        "mapped_services": len(mapped_services),
        "skipped_unmapped": skipped_unmapped,
        "skipped_bad_time": skipped_bad_time,
        "skipped_empty_files": skipped_empty_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "malformed_files": malformed_files,
        "failed_events": failed_events,
        "source_stats": source_stats,
    }
    emit(phase="done", source_name="Complete", file_index=total_files,
         total_files=total_files, parsed_programmes=parsed,
         imported_events=imported, skipped_unmapped=skipped_unmapped,
         failed_events=failed_events)
    return result


def import_plan(plan, progress_cb=None, clear_before_import=False):
    """Inject a prepared plan into Enigma2's native EPG cache.

    Returns a result dict and never imports/depends on EPG-Importer.
    """
    try:
        from enigma import eEPGCache
    except ImportError:
        raise RuntimeError("Native Enigma2 eEPGCache API is not available")

    cache = eEPGCache.getInstance()
    if cache is None or not hasattr(cache, "importEvents"):
        raise RuntimeError("This Enigma2 image does not expose eEPGCache.importEvents")

    if clear_before_import and hasattr(cache, "flushEPG"):
        try:
            cache.flushEPG()
        except Exception:
            log.exception("Could not clear existing EPG cache before import")

    services = list(plan.get("services", {}).items())
    imported_services = 0
    imported_events = 0
    failed_services = []
    total = len(services)

    for index, (service_ref, events) in enumerate(services, 1):
        if not events:
            continue
        try:
            # Current Enigma2 XMLTV importers pass a service reference string and
            # an iterable of event tuples to this method.
            _import_events_compat(cache, service_ref, events)
            imported_services += 1
            imported_events += len(events)
        except Exception as exc:
            log.exception("Native EPG import failed for %s", service_ref)
            failed_services.append((service_ref, str(exc)))
        if progress_cb:
            try:
                progress_cb(index, total, imported_events)
            except Exception:
                pass

    try:
        if hasattr(cache, "save"):
            cache.save()
    except Exception:
        log.debug("EPG cache save() is not available/required on this image")

    return {
        "ok": imported_services > 0 and not failed_services,
        "imported_services": imported_services,
        "imported_events": imported_events,
        "failed_services": failed_services,
        "total_services": total,
    }
