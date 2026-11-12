# -*- coding: utf-8 -*-
"""Provider-owned exact XMLTV-id -> Enigma2 Service Reference maps.

Rytec, Azman and Koala publish the same `channels=` contract consumed by OE-Alliance
EPGImport.  These maps are authoritative; EPGManager must preserve the Service
Reference text instead of throwing it away and trying to re-match by name.
"""
from __future__ import print_function

import os
import re
import time
from xml.etree import ElementTree as ET

from . import external_sources, source_catalog, srp_channel_map
from .logger import get_logger

log = get_logger(__name__)


def _safe(value):
    return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"


def _valid_map(path):
    """Return (channels, refs) only for a real EPGImport channels map."""
    channels = set()
    refs = 0
    if not path or not os.path.isfile(path):
        return 0, 0
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            tag = str(elem.tag or "").rsplit("}", 1)[-1]
            if tag == "channel":
                cid = str(elem.get("id") or "").strip()
                ref = str(elem.text or "").strip()
                # A provider catalogue <channel> without SRP text is not a
                # native map. Enigma2 refs contain colon-separated fields.
                if cid and ref and ":" in ref:
                    channels.add(cid.casefold())
                    refs += 1
            elem.clear()
    except Exception:
        return 0, 0
    return len(channels), refs


def _path_for(item, epg_dir):
    provider = str((item or {}).get("provider") or "").upper()
    if provider == "RYTEC":
        return external_sources.rytec_channel_map_path(epg_dir)
    if provider == "AZMAN":
        # All current Poland Azman feeds share one authoritative DVB map.
        return os.path.join(epg_dir, "azman.channels.xml")
    directory = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    mid = source_catalog.mapping_source_id(item)
    return os.path.join(directory, "%s.provider.channels.xml" % _safe(mid))


def ensure_map(item, epg_dir, force=False, max_age=7 * 86400):
    """Ensure one authoritative provider channels map exists locally."""
    item = dict(item or {})
    if not item.get("native_srp"):
        return {"ok": False, "skipped": True, "reason": "not native_srp"}
    provider = str(item.get("provider") or "").upper()
    target = _path_for(item, epg_dir)
    stale_channels = stale_refs = 0
    try:
        if os.path.isfile(target) and os.path.getsize(target) > 32:
            stale_channels, stale_refs = _valid_map(target)
            if (not force and stale_channels and stale_refs and
                    (time.time() - os.path.getmtime(target)) < int(max_age)):
                return {"ok": True, "cached": True, "map_path": target,
                        "channels": stale_channels, "refs": stale_refs}
    except Exception:
        stale_channels = stale_refs = 0

    if provider == "RYTEC":
        try:
            target = external_sources.download_rytec_channel_map(epg_dir, force=force)
            channels, refs = _valid_map(target)
            if not channels or not refs:
                raise RuntimeError("Rytec channels map contains no Service References")
            return {"ok": True, "map_path": target, "channels": channels, "refs": refs}
        except Exception as exc:
            # Provider map is mapping metadata, not programme freshness. A stale
            # but structurally valid last-known-good map is safer than blank EPG.
            if stale_channels and stale_refs:
                return {"ok": True, "cached": True, "stale": True,
                        "map_path": target, "channels": stale_channels, "refs": stale_refs,
                        "warning": str(exc)}
            return {"ok": False, "reason": str(exc)}

    errors = []
    for url in list(item.get("channels_urls") or []):
        try:
            payload, lower = external_sources._download_payload(str(url), 1, 12)
            payload = external_sources._decompress(payload, lower)
            if b"<channel" not in payload[:65536].lower():
                raise ValueError("provider map has no channel rows")
            tmp = target + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(payload)
            channels, refs = _valid_map(tmp)
            if not channels or not refs:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise ValueError("provider map has no exact Service References")
            os.replace(tmp, target)
            return {"ok": True, "map_path": target, "channels": channels,
                    "refs": refs, "url": str(url)}
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
            log.warning("Native provider map failed for %s: %s", item.get("name"), exc)
    if stale_channels and stale_refs:
        return {"ok": True, "cached": True, "stale": True,
                "map_path": target, "channels": stale_channels, "refs": stale_refs,
                "warning": "; ".join(errors[-2:])}
    return {"ok": False, "reason": "; ".join(errors[-2:]) or "no channels_urls"}


def register_selected(items, epg_dir, force=False):
    """Register exact provider maps in the persistent Direct Import index."""
    selected = [dict(x or {}) for x in (items or []) if bool((x or {}).get("native_srp"))]
    if not selected:
        return {"ok": True, "sources": 0, "refs": 0, "failed": []}
    index = srp_channel_map.load_index() or {}
    sources = dict(index.get("sources") or {})
    done = 0
    refs = 0
    failed = []
    shared = {}
    for item in selected:
        mid = str(source_catalog.mapping_source_id(item) or "")
        if not mid:
            continue
        # Do not parse/download Rytec's shared map once per selected country.
        # Koala's two guide variants have distinct URLs/paths and therefore get
        # independent cache keys.
        key = (_path_for(item, epg_dir), tuple(str(x) for x in (item.get("channels_urls") or [])))
        if key not in shared:
            shared[key] = ensure_map(item, epg_dir, force=force)
        result = dict(shared.get(key) or {})
        if not result.get("ok"):
            failed.append(str(item.get("name") or mid))
            continue
        path = str(result.get("map_path") or "")
        provider = str(item.get("provider") or "").upper()
        sources[mid] = {
            "source_id": mid, "map_path": path, "official_map_path": path,
            "provider": provider, "official": True,
            "channels": int(result.get("channels") or 0), "refs": int(result.get("refs") or 0),
            "unmapped": 0, "countries": list(item.get("countries") or []),
            "xml_path": "", "name": str(item.get("name") or mid),
            "mapping_mode": "provider_native_exact",
        }
        done += 1
        refs += int(result.get("refs") or 0)
    payload = dict(index)
    payload["version"] = max(12, int(index.get("version") or 0))
    payload["timestamp"] = int(time.time())
    payload["sources"] = sources
    # Use the frozen module's atomic writer without changing Golden code.
    srp_channel_map._write_json(srp_channel_map.INDEX_PATH, payload)
    return {"ok": not bool(failed), "sources": done, "refs": refs, "failed": failed}
