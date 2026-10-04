# -*- coding: utf-8 -*-
"""Direct XMLTV channel-id -> Enigma2 Service Reference (SRP) maps.

v6.4 deliberately removes the receiver-wide Auto Sync stage.  OE-Alliance
EPG Import does not need a global fuzzy-mapping pass: each XMLTV source is
paired with a channels XML file containing one or more exact Enigma2 service
references for every XMLTV channel id, exactly like Rytec's
``rytec.channels*.xml`` files.

For provider-supplied maps (currently Rytec), the official map is reused
unchanged.  For EPG Manager/OpenEPG/EPGShare feeds, a small source-scoped map
is generated from OpenATV ``TV -> Reception Lists`` plus IPTV bouquets.  The
resolver only examines the channel declarations of the selected XMLTV source;
it never computes receiver-wide coverage or creates thousands of No-EPG rows.
"""
from __future__ import print_function

import json
import os
import re
import time
from xml.etree import ElementTree as ET

from . import channel_mapper, channel_registry, external_sources, source_catalog, activity_store, epgimport_export
from .logger import get_logger

log = get_logger(__name__)

INDEX_PATH = "/etc/enigma2/epgmanager_srp_maps.json"
MAP_DIRNAME = "epgmanager_channels"

_COUNTRY_WORDS = {
    "FR": set(["fr", "france", "francais", "french"]),
    "MENA": set(["ar", "arab", "arabic", "arabia", "mena"]),
    "MA": set(["ma", "maroc", "morocco", "marocain"]),
    "EG": set(["eg", "egypt", "egyptian"]),
    "SA": set(["sa", "ksa", "saudi", "arabia"]),
    "QA": set(["qa", "qatar"]), "AE": set(["ae", "uae", "emirates"]),
    "ES": set(["es", "spain", "espana"]), "IT": set(["it", "italy", "italia"]),
    "DE": set(["de", "germany", "deutschland"]), "PT": set(["pt", "portugal"]),
    "UK": set(["uk", "gb", "britain", "british"]),
}

# High-confidence spelling identities only.  This is not a fuzzy auto-sync
# dictionary.  It exists so our own XMLTV ids can point directly to the SRPs
# users actually have in Reception Lists (e.g. AlAoula -> Al Aoula Inter HD).
_IDENTITY_ALIASES = {
    "alaoula": "alaoula", "aloula": "alaoula", "aloula1": "alaoula",
    "almaghribiya": "almaghribiya", "almaghribia": "almaghribiya",
    "arrabiaa": "arrabiaa", "arrabia": "arrabiaa", "arabiaa": "arrabiaa",
    "assadisa": "assadisa", "assadissa": "assadisa", "asadisa": "assadisa", "asadissa": "assadisa",
    "tamazight": "tamazight",
    "arryadia": "arryadia", "arriyadia": "arryadia", "arriadia": "arryadia",
    "medi1": "medi1", "medi1tv": "medi1",
    "2m": "2m", "2mmonde": "2m", "2mmaroc": "2m", "2mnational": "2m",
    "chada": "chada", "chadatv": "chada",
    "aflam": "aflam", "aflamma": "aflam",
}

_QUALITY_WORDS = set(["hd", "sd", "fhd", "uhd", "4k", "hevc", "h265", "backup", "feed", "channel"])
_MA_MENA_QUALIFIERS = set([
    "inter", "international", "intl", "monde", "world", "arabic", "arabe", "arab",
    "maroc", "morocco", "marocain", "national", "sat", "tv",
])

# Distinct Arryadia side feeds must not all collapse into the main channel.
_STRONG_MOROCCO_IDENTITIES = set([
    "alaoula", "almaghribiya", "arrabiaa", "assadisa", "tamazight",
    "arryadia", "2m", "medi1", "chada", "aflam",
])

_SPECIAL_SUFFIXES = {
    "arryadia_tnt": set(["tnt"]),
    "arryadia_hd1": set(["hd1"]),
    "arryadia_hd2": set(["hd2"]),
    "arryadia_hd3": set(["hd3"]),
}


def _safe_id(value):
    return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def load_index(path=INDEX_PATH):
    return _read_json(path)


def map_path_for_source(source_id, index_path=INDEX_PATH):
    row = (load_index(index_path).get("sources") or {}).get(str(source_id)) or {}
    path = str(row.get("map_path") or "")
    return path if path and os.path.exists(path) else ""


def _base_identity(name, expected_codes=None):
    base = channel_mapper.normalize_name(name)
    if not base:
        return ""
    words = [w for w in base.split() if w not in _QUALITY_WORDS]
    expected = set(str(x) for x in (expected_codes or []))
    remove = set()
    for code in expected:
        remove.update(_COUNTRY_WORDS.get(code, set()))
    if expected & set(["MA", "MENA"]):
        remove.update(_MA_MENA_QUALIFIERS)
    words = [w for w in words if w not in remove]
    compact = "".join(words)
    return _IDENTITY_ALIASES.get(compact, compact)


def _channel_identity(channel_id, display_name, expected_codes=None):
    cid = str(channel_id or "").strip().lower()
    # Preserve explicit Arryadia auxiliary IDs. Their display names are the
    # only safe way to distinguish TNT/HD1/HD2/HD3 from the main feed.
    for suffix, required in _SPECIAL_SUFFIXES.items():
        if cid == suffix:
            return "arryadia+" + next(iter(required))
    return _base_identity(display_name or channel_id, expected_codes)


def _service_identity(service, expected_codes=None):
    name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    norm = channel_mapper.normalize_name(name)
    tokens = norm.split()
    token_set = set(tokens)
    # Preserve Arryadia TNT/HD1/HD2/HD3 as distinct identities. Remove the
    # suffix temporarily to confirm the remaining name is really Arryadia.
    for suffix in ("tnt", "hd1", "hd2", "hd3"):
        if suffix in token_set:
            reduced = " ".join(x for x in tokens if x != suffix)
            if _base_identity(reduced, expected_codes) == "arryadia":
                return "arryadia+" + suffix
    return _base_identity(name, expected_codes)


def _compatible(expected, service, identity=None):
    expected = set(str(x) for x in (expected or []))
    explicit = str((service or {}).get("explicit_region") or "")
    # A known Moroccan channel remains the same channel when simulcast on
    # Hotbird/Astra/Es'hail/etc. Reception Lists. Do not use orbital position
    # to reject those exact identities. IPTV prefixes remain authoritative.
    if (identity in _STRONG_MOROCCO_IDENTITIES and "MA" in expected and
            str((service or {}).get("service_type") or "") == "SAT" and not explicit):
        return True
    ok, _why = channel_registry.region_compatible(expected, service)
    return bool(ok)


def _source_channels(xml_path, source_id, source_name):
    return channel_mapper.read_xmltv_channel_ids_fast(xml_path, source_id, source_name)


def _mapping_candidates(expected, include_iptv=True, force_registry=False):
    sat, _rebuilt = channel_registry.load_satellite_registry(force=force_registry)
    iptv = []
    if include_iptv:
        try:
            iptv, _ = channel_registry.load_iptv_registry(force=force_registry)
        except Exception:
            iptv = []
    return sat + iptv


def _build_identity_index(services, expected):
    by_identity = {}
    for service in services:
        identity = _service_identity(service, expected)
        if not _compatible(expected, service, identity=identity):
            continue
        if not identity:
            continue
        ref = str(service.get("ref") or "").strip()
        if not ref:
            continue
        bucket = by_identity.setdefault(identity, [])
        if all(str(x.get("ref") or "") != ref for x in bucket):
            bucket.append(service)
    return by_identity


def write_channels_xml(mapping, path):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    root = ET.Element("channels")
    for channel_id in sorted(mapping, key=lambda x: str(x).lower()):
        for ref in mapping.get(channel_id) or []:
            el = ET.SubElement(root, "channel", id=str(channel_id))
            el.text = str(ref)
    try:
        ET.indent(root, space="  ")
    except Exception:
        pass
    body = ET.tostring(root, encoding="unicode")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="utf-8"?>\n')
        fh.write(body)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def parse_channels_xml(path, wanted_ids=None):
    out = {}
    wanted = set(str(x).lower() for x in (wanted_ids or []))
    if not path or not os.path.exists(path):
        return out
    try:
        context = ET.iterparse(path, events=("end",))
        for _event, elem in context:
            tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
            if tag == "channel":
                cid = str(elem.get("id") or "").strip().lower()
                ref = str(elem.text or "").strip()
                if cid and ref and (not wanted or cid in wanted):
                    bucket = out.setdefault(cid, [])
                    if ref not in bucket:
                        bucket.append(ref)
            elem.clear()
    except Exception as exc:
        log.warning("Could not parse SRP channels map %s: %s", path, exc)
    return out


def _parse_channels_xml_preserve(path):
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
            if tag == "channel":
                cid = str(elem.get("id") or "").strip()
                ref = str(elem.text or "").strip()
                if cid and ref:
                    bucket = out.setdefault(cid, [])
                    if ref not in bucket:
                        bucket.append(ref)
            elem.clear()
    except Exception as exc:
        log.warning("Could not parse cached beta1 map for manual override %s: %s", path, exc)
    return out


def apply_mapping_override(source_id, channel_id, refs=None, remove=False, index_path=INDEX_PATH):
    """Apply only an explicit Smart Mapping correction to a cached beta1 map."""
    row = (load_index(index_path).get("sources") or {}).get(str(source_id)) or {}
    path = str(row.get("map_path") or "")
    if not path:
        return False
    mapping = _parse_channels_xml_preserve(path)
    cid = str(channel_id or "").strip()
    for key in list(mapping.keys()):
        if str(key).lower() == cid.lower():
            mapping.pop(key, None)
    if not remove:
        clean = []
        for ref in refs or []:
            ref = str(ref or "").strip()
            if ref and ref not in clean:
                clean.append(ref)
        if clean:
            mapping[cid] = clean
    write_channels_xml(mapping, path)
    return True


def build_source_map(item, xml_path, expected_codes, epg_dir, include_iptv=True, progress_cb=None):
    """Build or register the direct map for one selected backend source."""
    item = dict(item or {})
    source_id = source_catalog.mapping_source_id(item)
    provider = str(item.get("provider") or "").upper()
    name = str(item.get("name") or source_id)
    expected = set(str(x) for x in (expected_codes or item.get("countries") or []))

    if provider == "RYTEC":
        path = external_sources.download_rytec_channel_map(epg_dir, force=False)
        # The official Rytec map already is the direct channel-id -> SRP map.
        return {"source_id": source_id, "map_path": path, "provider": provider,
                "official": True, "channels": 0, "refs": 0, "unmapped": 0,
                "countries": sorted(expected), "xml_path": xml_path, "name": name}

    channels = _source_channels(xml_path, source_id, name)
    if progress_cb:
        progress_cb({"phase": "SRP MAP", "current": 0, "total": max(1, len(channels)),
                     "detail": "%s: reading Reception Lists" % name})
    services = _mapping_candidates(expected, include_iptv=include_iptv, force_registry=False)
    by_identity = _build_identity_index(services, expected)
    by_token = {}
    compatible_services = []
    for service in services:
        identity = _service_identity(service, expected)
        if not _compatible(expected, service, identity=identity):
            continue
        compatible_services.append(service)
        norm = str(service.get("normalized") or channel_mapper.normalize_name(service.get("clean_name") or service.get("name") or ""))
        for token in set(norm.split()):
            if len(token) >= 2:
                by_token.setdefault(token, []).append(service)

    mapping = {}
    unresolved = []
    total = len(channels)
    for idx, ch in enumerate(channels):
        if progress_cb and (idx == 0 or idx % 20 == 0 or idx + 1 == total):
            progress_cb({"phase": "SRP MAP", "current": idx + 1, "total": max(1, total),
                         "detail": "%s: %s" % (name, ch.get("display_name") or ch.get("channel_id") or "")})
        cid = str(ch.get("channel_id") or "").strip()
        identity = _channel_identity(cid, ch.get("display_name") or cid, expected)
        refs = []
        for service in by_identity.get(identity, []):
            ref = str(service.get("ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)

        # Source-scoped safety fallback for provider spelling differences.
        # This is intentionally not a receiver-wide Auto Sync pass: only the
        # current XMLTV channel is compared with a small token-indexed pool.
        if not refs:
            target_name = str(ch.get("display_name") or cid)
            target_norm = channel_mapper.normalize_name(target_name)
            pool = []
            seen = set()
            for token in target_norm.split()[:3]:
                if len(token) < 2:
                    continue
                for service in by_token.get(token, []):
                    ref = str(service.get("ref") or "")
                    if ref and ref not in seen:
                        seen.add(ref); pool.append(service)
            scored = []
            for service in pool[:250]:
                name = str(service.get("clean_name") or service.get("name") or "")
                score = channel_mapper._token_score(target_name, name)
                if score:
                    scored.append((score, service))
            scored.sort(key=lambda x: -x[0])
            if scored and scored[0][0] >= 97:
                best = scored[0][0]
                second = scored[1][0] if len(scored) > 1 else 0
                if best - second >= 3 or best == 100:
                    best_norm = str(scored[0][1].get("normalized") or channel_mapper.normalize_name(scored[0][1].get("name") or ""))
                    for _score, service in scored:
                        service_norm = str(service.get("normalized") or channel_mapper.normalize_name(service.get("name") or ""))
                        if service_norm != best_norm:
                            continue
                        ref = str(service.get("ref") or "").strip()
                        if ref and ref not in refs:
                            refs.append(ref)

        if refs:
            mapping[cid] = refs
        else:
            unresolved.append(cid)

    map_dir = os.path.join(epg_dir, MAP_DIRNAME)
    map_path = os.path.join(map_dir, "%s.channels.xml" % _safe_id(source_id))
    write_channels_xml(mapping, map_path)
    return {"source_id": source_id, "map_path": map_path, "provider": provider,
            "official": False, "channels": len(mapping),
            "refs": sum(len(v) for v in mapping.values()), "unmapped": len(unresolved),
            "unmapped_ids": unresolved[:50], "countries": sorted(expected), "xml_path": xml_path,
            "name": name, "mapping_mode": "beta1_exact",
            "sat_signature": channel_registry.satellite_registry_signature()}


def prepare_selected_maps(selected_items, downloads, preferences, epg_dir,
                          include_iptv=True, progress_cb=None, index_path=INDEX_PATH):
    """Prepare direct SRP maps for the feeds in the current cycle only."""
    started = time.time()
    contexts = preferences.selected_contexts() if preferences is not None else {}
    sources = {}
    mapped_channels = 0
    mapped_refs = 0
    unresolved = 0
    rows = list(selected_items or [])

    for idx, item in enumerate(rows):
        sid = item.get("id")
        mid = source_catalog.mapping_source_id(item)
        result = (downloads or {}).get(sid) or {}
        xml_path = result.get("path")
        if not xml_path or not os.path.exists(xml_path):
            continue
        ctx = contexts.get(mid) or {}
        countries = set(ctx.get("countries") or item.get("countries") or [])
        if progress_cb:
            progress_cb({"phase": "SRP MAP", "current": idx, "total": max(1, len(rows)),
                         "detail": "Preparing %s" % (item.get("name") or mid)})
        row = build_source_map(item, xml_path, countries, epg_dir,
                               include_iptv=include_iptv, progress_cb=progress_cb)
        sources[mid] = row
        mapped_channels += int(row.get("channels") or 0)
        mapped_refs += int(row.get("refs") or 0)
        unresolved += int(row.get("unmapped") or 0)

    previous = load_index(index_path)
    merged = dict(previous.get("sources") or {}) if isinstance(previous, dict) else {}
    merged.update(sources)
    payload = {"version": 12, "timestamp": int(time.time()), "sources": merged}
    _write_json(index_path, payload)

    # Publish the same source->channels-file contract used by OE-Alliance
    # EPG Import/Rytec.  Keep every still-valid direct map in the file so a
    # one-source YELLOW refresh does not remove definitions prepared earlier.
    try:
        export_rows = []
        for _mid, _row in merged.items():
            _row = dict(_row or {})
            if (_row.get("xml_path") and _row.get("map_path") and
                    os.path.exists(str(_row.get("xml_path"))) and
                    os.path.exists(str(_row.get("map_path")))):
                export_rows.append(_row)
        epgimport_export.save_direct_sources(export_rows)
    except Exception:
        log.exception("Could not export EPG Import compatible source definitions")

    summary = {"sources": len(sources), "mapped_channel_ids": mapped_channels,
               "mapped_service_refs": mapped_refs, "unmapped_channel_ids": unresolved,
               "elapsed": max(0.0, time.time() - started), "timestamp": int(time.time()),
               "mode": "beta1_exact_cached", "beta1_mapping_engine": True}
    activity_store.record_mapping(**summary)
    return summary
