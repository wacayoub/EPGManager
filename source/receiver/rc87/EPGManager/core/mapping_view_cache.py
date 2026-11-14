# -*- coding: utf-8 -*-
"""Persistent validated Smart-Mapping ownership cache.

The expensive part of the Smart Mapping screen is not listing DVB services; it
is re-validating thousands of already-generated SRP ownership rows against the
PrecisionMatch policy every time the screen opens.  The validated result is
stable while lamedb, SRP state, source selections and manual mappings are
unchanged, so keep a compact local snapshot and reuse it on the next open.

This mirrors the cache-first/lazy pattern used by fast Enigma2 tools: paint from
local state first and recompute only after an input signature changes.
"""
from __future__ import print_function

import json
import os
import threading
import time

from . import channel_registry, srp_master_engine
from . import mapping_store as _mapping_store_mod
from . import source_preferences as _source_preferences_mod

PATH = "/etc/enigma2/epgmanager_mapping_view_cache.json"
VERSION = 2
POLICY_REVISION = "precisionmatch-v4-beta127-native-arabic"
_LOCK = threading.RLock()


def _file_sig(path):
    try:
        st = os.stat(path)
        return [str(path), int(st.st_mtime), int(st.st_size)]
    except OSError:
        return [str(path), 0, 0]


def signature():
    """Cheap dependency signature; no XMLTV/bouquet parsing."""
    try:
        receiver = channel_registry.satellite_registry_signature()
    except Exception:
        receiver = []
    return {
        "policy": POLICY_REVISION,
        "receiver": receiver,
        # Do not key the validated UI ownership cache to SRP state mtime. A
        # low-impact background SRP refresh after Import All may legitimately
        # rewrite that file without invalidating mappings already proven against
        # the same receiver/source/manual context. The next import overwrites
        # this cache with the refreshed validated ownership set.
        "manual": _file_sig(getattr(_mapping_store_mod, "DEFAULT_PATH", "/etc/enigma2/epgmanager_mappings.json")),
        "sources": _file_sig(getattr(_source_preferences_mod, "PATH", "/etc/enigma2/epgmanager_source_preferences.json")),
    }


def _canon(ref):
    raw = str(ref or "").strip()
    if not raw:
        return ""
    try:
        return channel_registry.canonical_service_ref(raw) or raw.rstrip(":") or raw
    except Exception:
        return raw.rstrip(":") or raw


def _compact(data):
    info_out = {}
    seen = {}
    for key, infos in dict((data or {}).get("mapped_info_by_ref") or {}).items():
        canon = _canon(key)
        if not canon:
            continue
        bucket = info_out.setdefault(canon, [])
        sigs = seen.setdefault(canon, set())
        for info in infos or []:
            row = dict(info or {})
            sig = (str(row.get("source_id") or ""), str(row.get("channel_id") or ""), str(row.get("mode") or ""))
            if sig in sigs:
                continue
            sigs.add(sig)
            bucket.append(row)
    mapped_refs = set(info_out)
    for ref in (data or {}).get("mapped_refs") or []:
        canon = _canon(ref)
        if canon:
            mapped_refs.add(canon)
    return {
        "mapped_epg_keys": sorted(str(x) for x in ((data or {}).get("mapped_epg_keys") or []) if str(x)),
        "mapped_refs": sorted(mapped_refs),
        "mapped_info_by_ref": info_out,
    }


def save(data, path=PATH):
    payload = _compact(data)
    body = {
        "version": VERSION,
        "created": int(time.time()),
        "signature": signature(),
        "payload": payload,
    }
    parent = os.path.dirname(path)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + ".tmp"
        with _LOCK:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(path + ".tmp")
        except OSError:
            pass
        return False


def load(path=PATH):
    try:
        with _LOCK:
            with open(path, "r", encoding="utf-8") as fh:
                body = json.load(fh)
        if int(body.get("version") or 0) != VERSION:
            return None
        if body.get("signature") != signature():
            return None
        payload = body.get("payload") or {}
        if not isinstance(payload, dict):
            return None
        return {
            "mapped_epg_keys": set(str(x) for x in (payload.get("mapped_epg_keys") or []) if str(x)),
            "mapped_refs": set(str(x) for x in (payload.get("mapped_refs") or []) if str(x)),
            "mapped_info_by_ref": dict(payload.get("mapped_info_by_ref") or {}),
            "cache_created": int(body.get("created") or 0),
            "cache_validated": True,
        }
    except Exception:
        return None


def invalidate(path=PATH):
    try:
        os.unlink(path)
    except OSError:
        pass
