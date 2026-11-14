# -*- coding: utf-8 -*-
"""Persistent compact Smart Mapping name index.

The runtime Smart Mapping index is expensive enough to notice on small Enigma2
receivers when it is rebuilt after every GUI restart, even though the underlying
channel-ID catalogues often did not change.  This cache stores the already-built
name/token buckets in one gzip-compressed JSON file and validates them against
only the active Smart Mapping source IDs plus their lightweight channel-cache
metadata.

It never stores programme data and never touches the frozen SAT/SRP mapping
engines.
"""
from __future__ import print_function

import gzip
import hashlib
import json
import os
import threading
import tempfile
import time

from . import source_channel_cache, source_catalog, source_variant_policy
from .logger import get_logger

log = get_logger(__name__)

PATH = "/etc/enigma2/epgmanager_smart_name_index.json.gz"
DIRTY_PATH = "/etc/enigma2/epgmanager_smart_name_index.dirty"
VERSION = 14
_LOCK = threading.RLock()
_MEM = None
_MEM_SIG = None


def _file_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None



def _is_dirty(path=PATH):
    """Cheap stale check used by latency-sensitive Auto Repair.

    Source refreshes touch one tiny marker.  This avoids rescanning every
    source-channel shard merely to validate the persistent compact index.
    """
    cache_sig = _file_sig(path)
    dirty_sig = _file_sig(DIRTY_PATH)
    if not cache_sig:
        return True
    if not dirty_sig:
        return False
    return int(dirty_sig[0]) >= int(cache_sig[0])


def fast_signature(path=PATH):
    """Return a zero-JSON signature for the last clean compact index."""
    sig = _file_sig(path)
    if not sig or _is_dirty(path):
        return ""
    return "%d:%d:v%d" % (int(sig[0]), int(sig[1]), int(VERSION))


def load_fast_snapshot(path=PATH):
    """Load the persisted compact index without source-shard fingerprinting.

    Safety is maintained by the DIRTY marker: any explicit source refresh or
    source-selection change marks the index stale.  The next normal Smart-index
    rebuild clears the marker.  Auto Repair therefore pays only one gzip read,
    never a directory-wide JSON scan, before channel 1/N.
    """
    if _is_dirty(path):
        return {}
    data = _read_payload(path=path)
    if int(data.get("version") or 0) != VERSION:
        return {}
    index = data.get("index") or {}
    if not isinstance(index, dict) or int(index.get("_format") or 0) != 2:
        return {}
    if not isinstance(index.get("rows"), list) or not isinstance(index.get("buckets"), dict):
        return {}
    return {
        "index": index,
        "source_ids": list(data.get("source_ids") or []),
        "created_at": int(data.get("created_at") or 0),
        "signature": fast_signature(path),
    }


def mark_dirty():
    """Invalidate fast reuse with one tiny filesystem write."""
    try:
        parent = os.path.dirname(DIRTY_PATH) or "."
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(DIRTY_PATH, "w") as fh:
            fh.write(str(int(time.time())))
        return True
    except Exception:
        return False


def normalize_source_ids(source_ids):
    # beta83 discovery index keeps every prepared language shard. Import/source
    # selection still collapses unsafe AR/EN pairs elsewhere, but Find Suggestions
    # must be able to search both names so an English receiver label can discover
    # the Arabic feed with the same XMLTV ID.
    return sorted(set(str(x or "") for x in (source_ids or []) if str(x or "")))



def prepared_source_ids():
    """Return source IDs whose channel names can be searched with zero network I/O.

    All built-in local generators are always searchable from static hints. Remote
    sources are included only when their compact channel-ID shard already exists.
    The function is intended for background warm-up, never the Enigma2 UI thread.
    """
    ids = []
    try:
        for item in source_catalog.all_sources():
            if (item or {}).get("kind") == "local":
                sid = source_catalog.mapping_source_id(item)
                if sid:
                    ids.append(str(sid))
    except Exception:
        pass
    try:
        counts = source_channel_cache.all_counts() or {}
        ids.extend(str(sid) for sid, count in counts.items() if int(count or 0) > 0)
    except Exception:
        pass
    return normalize_source_ids(ids)

def fingerprint(source_ids):
    """Cheap signature for the lean exact index.

    7.0.x opened every source-channel shard to fingerprint the global index.
    The cache DIRTY marker already invalidates explicit source changes, so one
    directory signature plus the two tiny identity-file signatures is enough.
    """
    ids = normalize_source_ids(source_ids)
    rows = [ids, source_channel_cache.fast_signature()]
    root = os.path.dirname(os.path.dirname(__file__))
    for rel in ("data/srp_aliases.json", "data/channel_identity.json"):
        sig = _file_sig(os.path.join(root, rel))
        rows.append([rel, list(sig) if sig else []])
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _read_payload(path=PATH):
    global _MEM, _MEM_SIG
    sig = _file_sig(path)
    if not sig:
        return {}
    # Keep the gzip read under the module lock. Home warm-up and Auto Repair can
    # start only milliseconds apart; without this guard both threads could
    # decompress/JSON-decode the same compact index simultaneously on a Zero 4K.
    # Waiting on an RLock costs no CPU and guarantees one physical read only.
    with _LOCK:
        if isinstance(_MEM, dict) and sig == _MEM_SIG:
            return _MEM
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        _MEM, _MEM_SIG = data, sig
        return data


def load(source_ids, path=PATH):
    if _is_dirty(path):
        return {}
    ids = normalize_source_ids(source_ids)
    if not ids:
        return {}
    data = _read_payload(path=path)
    if int(data.get("version") or 0) != VERSION:
        return {}
    if list(data.get("source_ids") or []) != ids:
        return {}
    try:
        current_fp = fingerprint(ids)
    except Exception:
        return {}
    if str(data.get("fingerprint") or "") != current_fp:
        return {}
    index = data.get("index") or {}
    return index if isinstance(index, dict) else {}


def save(index, source_ids, path=PATH):
    global _MEM, _MEM_SIG
    ids = normalize_source_ids(source_ids)
    if not ids or not isinstance(index, dict) or not index:
        return False
    tmp = None
    try:
        parent = os.path.dirname(path) or "."
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        payload = {
            "version": VERSION,
            "created_at": int(time.time()),
            "source_ids": ids,
            "fingerprint": fingerprint(ids),
            "index": index,
        }
        with _LOCK:
            fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=parent)
            os.close(fd)
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
            tmp = None
            _MEM = payload
            _MEM_SIG = _file_sig(path)
            try:
                if os.path.exists(DIRTY_PATH):
                    os.unlink(DIRTY_PATH)
            except Exception:
                pass
        return True
    except Exception:
        log.exception("Could not save persistent Smart Mapping index")
        try:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False


def invalidate(path=PATH):
    global _MEM, _MEM_SIG
    with _LOCK:
        _MEM = None
        _MEM_SIG = None
    try:
        if os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass
    try:
        if os.path.exists(DIRTY_PATH):
            os.unlink(DIRTY_PATH)
    except Exception:
        pass


def stats(path=PATH):
    data = _read_payload(path=path)
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    return {
        "created_at": int(data.get("created_at") or 0),
        "sources": len(data.get("source_ids") or []),
        "keys": len(((data.get("index") or {}).get("buckets") or {})) if isinstance(data.get("index"), dict) and (data.get("index") or {}).get("_format") == 2 else len(data.get("index") or {}),
        "rows": len(((data.get("index") or {}).get("rows") or [])) if isinstance(data.get("index"), dict) and (data.get("index") or {}).get("_format") == 2 else 0,
        "bytes": int(size),
    }
