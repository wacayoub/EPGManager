# -*- coding: utf-8 -*-
"""Sharded persistent source-channel catalogue for Smart Mapping.

beta28 storage policy for small Enigma2 receivers:
- never stores programme data;
- one small JSON file per remote source instead of one giant monolithic JSON;
- get()/has()/count() read only the requested source shard;
- old epgmanager_source_channels.json remains a read-only migration fallback.

This prevents a 10-15 MB catalogue from being parsed/copied whenever one source
is opened and keeps RAM/CPU usage predictable on Vu+ Zero 4K.
"""
from __future__ import print_function

import hashlib
import json
import os
import re
import time

from .logger import get_logger
from .json_atomic import lock_for, mutate_json, write_json

log = get_logger(__name__)
PATH = "/etc/enigma2/epgmanager_source_channels.json"  # legacy beta18-beta27 file
DIR = "/etc/enigma2/epgmanager_source_channels.d"
VERSION = 4

_ROW_MEM = {}
_ROW_SIG = {}
_LEGACY_MEM = None
_LEGACY_SIG = None
_GENERATION = 0


def _is_tv_channel_row(item):
    """Keep mapping catalogues TV-only. XMLTV has no standard media-type field,
    so honour explicit metadata when present and use a conservative name fallback.
    """
    item = item or {}
    kind = str(item.get("media_type") or item.get("type") or item.get("service_type") or "").casefold()
    if kind in ("radio", "audio", "data", "datacast"):
        return False
    if item.get("is_radio") is True or item.get("is_data") is True:
        return False
    display = str(item.get("display_name") or "").strip().casefold()
    cid = str(item.get("channel_id") or "").strip().casefold()
    text = (display + " " + cid).strip()
    if not text:
        return False
    # Placeholder services can carry an arbitrary XMLTV id, therefore testing
    # the concatenated "display + id" string misses an exact "Coming soon"
    # display name.  Mapping Repair is TV-only and must never offer these rows.
    if display in ("coming soon", "test", "data", "service") or "demo loop" in display:
        return False
    if re.search(r"(^|[\s._-])radio([\s._-]|$)", text):
        return False
    if re.search(r"(^|[\s._-])fm([\s._-]|$)", text):
        return False
    if "إذاعة" in text or "اذاعه" in text:
        return False
    if re.search(r"(^|[\s._-])(data|datacast)([\s._-]|$)", text):
        return False
    if "demo loop" in text or text in ("coming soon", "test", "data", "service"):
        return False
    return True


def _signature(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _safe(value):
    text = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"
    digest = hashlib.sha1(str(value or "").encode("utf-8", "ignore")).hexdigest()[:10]
    return "%s-%s.json" % (text[:80], digest)


def _shard_path(source_id):
    return os.path.join(DIR, _safe(source_id))


def _meta_path(source_id):
    return _shard_path(source_id) + ".meta"


def scope_fast(source_id):
    """Read only the tiny sidecar metadata, never the potentially large shard.

    New 7.1.0 source-specific caches create this marker. Legacy shared-provider
    shards have no marker and can therefore be skipped at boot without JSON
    decoding megabytes of duplicated Rytec data.
    """
    try:
        meta = _read_json(_meta_path(source_id))
        return str(meta.get("scope") or "")
    except Exception:
        return ""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _legacy_data():
    global _LEGACY_MEM, _LEGACY_SIG
    sig = _signature(PATH)
    if isinstance(_LEGACY_MEM, dict) and sig == _LEGACY_SIG:
        return _LEGACY_MEM
    data = _read_json(PATH) if sig else {}
    data.setdefault("sources", {})
    _LEGACY_MEM, _LEGACY_SIG = data, sig
    return data


def _load_row(source_id):
    sid = str(source_id)
    path = _shard_path(sid)
    with lock_for(path):
        sig = _signature(path)
        if sid in _ROW_MEM and _ROW_SIG.get(sid) == sig:
            return _ROW_MEM[sid]
        row = _read_json(path) if sig else {}
        if not row:
            # Read-only compatibility for installations not yet migrated by postinst.
            row = dict(((_legacy_data().get("sources") or {}).get(sid)) or {})
            if row:
                row.setdefault("source_id", sid)
        _ROW_MEM[sid] = row
        _ROW_SIG[sid] = sig
        return row


def _save_row(source_id, row):
    global _GENERATION
    sid = str(source_id)
    try:
        path = _shard_path(sid)
        payload = dict(row or {})
        payload["version"] = VERSION
        payload["source_id"] = sid
        with lock_for(path):
            write_json(path, payload)
            try:
                write_json(_meta_path(sid), {
                    "version": VERSION,
                    "source_id": sid,
                    "scope": str(payload.get("scope") or "source"),
                    "method": str(payload.get("method") or ""),
                    "updated_at": int(payload.get("updated_at") or 0),
                    "channels": len(payload.get("channels") or []),
                })
            except Exception:
                pass
            _ROW_MEM[sid] = payload
            _ROW_SIG[sid] = _signature(path)
            _GENERATION += 1
        return True
    except Exception:
        log.exception("Could not save source channel shard for %s", sid)
        return False


def get(source_id, path=PATH):
    # Custom path remains supported for old callers/tests using a monolithic file.
    if path != PATH:
        data = _read_json(path)
        row = ((data.get("sources") or {}).get(str(source_id)) or {})
    else:
        row = _load_row(source_id)
    out = []
    for item in row.get("channels") or []:
        if not isinstance(item, dict) or not _is_tv_channel_row(item):
            continue
        x = dict(item)
        x["source_id"] = str(source_id)
        x["source_name"] = str(row.get("source_name") or source_id)
        x["epg_xml_path"] = ""  # remote data is streamed; no persistent XML path
        out.append(x)
    return out


def has(source_id, path=PATH):
    if path != PATH:
        row = ((_read_json(path).get("sources") or {}).get(str(source_id)) or {})
    else:
        row = _load_row(source_id)
    return bool(row.get("channels"))


def put(source_id, source_name, channels, provider="", region="", language="", path=PATH, scope="source", method=""):
    """Persist one compact channel catalogue.

    ``scope=source`` means the rows are proven members of this concrete EPG
    feed. Provider-wide mapping dictionaries must use another scope and are not
    eligible for Smart Mapping per-source suggestions.
    """
    compact = []
    seen = set()
    for item in channels or []:
        if not _is_tv_channel_row(item):
            continue
        cid = str((item or {}).get("channel_id") or "").strip()
        if not cid or cid.lower() in seen:
            continue
        seen.add(cid.lower())
        row = {"channel_id": cid, "display_name": str((item or {}).get("display_name") or cid)}
        aliases = []
        alias_seen = set()
        for alias in (item or {}).get("aliases") or []:
            text = str(alias or "").strip()
            key = text.casefold()
            if text and key not in alias_seen and key != row["display_name"].casefold():
                alias_seen.add(key); aliases.append(text)
        if aliases:
            row["aliases"] = aliases[:8]
        compact.append(row)
    row = {
        "source_name": str(source_name or source_id),
        "provider": str(provider or ""),
        "region": str(region or ""),
        "language": str(language or ""),
        "scope": str(scope or "source"),
        "method": str(method or ""),
        "updated_at": int(time.time()),
        "channels": compact,
    }
    if path != PATH:
        def _mut(data):
            if not isinstance(data, dict):
                data = {}
            data.setdefault("version", VERSION)
            data.setdefault("sources", {})[str(source_id)] = row
            return data
        try:
            mutate_json(path, _mut, {"version": VERSION, "sources": {}})
            return True
        except Exception:
            return False
    return _save_row(source_id, row)


def count(source_id, path=PATH):
    return len(get(source_id, path=path))


def all_counts(path=PATH):
    if path != PATH:
        data = _read_json(path)
        return {str(sid): len((row or {}).get("channels") or []) for sid, row in (data.get("sources") or {}).items()}
    out = {}
    try:
        if os.path.isdir(DIR):
            for name in os.listdir(DIR):
                if not name.endswith(".json"):
                    continue
                row = _read_json(os.path.join(DIR, name))
                sid = str(row.get("source_id") or "")
                if sid:
                    out[sid] = len(row.get("channels") or [])
    except Exception:
        pass
    # Legacy fallback only for source IDs not migrated yet.
    if _signature(PATH):
        for sid, row in (_legacy_data().get("sources") or {}).items():
            out.setdefault(str(sid), len((row or {}).get("channels") or []))
    return out


def info_fast(source_id):
    """Read tiny sidecar metadata without decoding the channel shard.

    rc73 Smart Mapping freshness checks use this on startup so discovering
    whether a source is current never parses large provider JSON on the UI or
    duplicates smart_catalog_boot's cache loading work.
    """
    sid = str(source_id or "")
    if not sid:
        return {}
    try:
        meta = _read_json(_meta_path(sid))
    except Exception:
        meta = {}
    if not meta:
        return {}
    return {
        "source_id": sid,
        "scope": str(meta.get("scope") or ""),
        "method": str(meta.get("method") or ""),
        "version": int(meta.get("version") or 0),
        "updated_at": int(meta.get("updated_at") or 0),
        "channels": int(meta.get("channels") or 0),
    }


def info(source_id, path=PATH):
    if path != PATH:
        row = ((_read_json(path).get("sources") or {}).get(str(source_id)) or {})
    else:
        row = _load_row(source_id)
    return {
        "source_name": str(row.get("source_name") or source_id),
        "provider": str(row.get("provider") or ""),
        "region": str(row.get("region") or ""),
        "language": str(row.get("language") or ""),
        "scope": str(row.get("scope") or ""),
        "method": str(row.get("method") or ""),
        "version": int(row.get("version") or 0),
        "updated_at": int(row.get("updated_at") or 0),
        "channels": len(row.get("channels") or []),
    }



def source_signature(source_id):
    """Cheap revision for exactly one compact source shard.

    RC7 uses this in country bundles so refreshing Spain/Rytec does not invalidate
    unrelated Saudi/France merged rows.  One source refresh -> one source revision.
    """
    sid = str(source_id or "")
    if not sid:
        return "0:0"
    row = _load_row(sid)
    return "%d:%d:%s" % (int(row.get("updated_at") or 0),
                           len(row.get("channels") or []),
                           str(row.get("method") or ""))

def generation():
    """Return an in-process catalogue revision without touching disk.

    Smart Mapping uses this to notice that the low-priority catalogue prewarmer
    finished while the screen is open.  It is intentionally memory-only so a
    1-2 second UI timer never performs stat()/JSON I/O.
    """
    return int(_GENERATION)


def fast_signature():
    """Cheap persistent revision for Mapping Repair incremental decisions.

    Cache shards are written atomically, so the shard directory mtime changes
    whenever a source catalogue is refreshed.  We deliberately avoid reading
    every JSON shard here: one directory stat + the legacy file stat is enough
    to invalidate old NO-MATCH decisions after a refresh while keeping Auto
    Repair startup effectively free on small receivers.
    """
    try:
        st = os.stat(DIR)
        d = (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        d = (0, 0)
    try:
        st = os.stat(PATH)
        l = (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        l = (0, 0)
    return "%d:%d:%d:%d:%d" % (d[0], d[1], l[0], l[1], int(_GENERATION))
