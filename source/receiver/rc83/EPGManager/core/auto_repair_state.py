# -*- coding: utf-8 -*-
"""Persistent per-satellite Auto Mapping Repair state.

Only lightweight receiver service references and counters are stored here.
No XMLTV/programme data is persisted.  The purpose is to restore the last
repair result instantly after Enigma2/plugin restart so Mapping Repair does not
need to rescan just to remember REVIEW / AMBIGUOUS / NO MATCH rows.
"""
from __future__ import print_function

import copy
import json
import os
import threading
import time

PATH = "/etc/enigma2/epgmanager_auto_repair_state.json"
VERSION = 1
_LOCK = threading.RLock()
_CACHE = None
_SIG = None


def _signature(path=PATH):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _empty():
    return {"version": VERSION, "satellites": {}}


def _load(path=PATH):
    global _CACHE, _SIG
    sig = _signature(path)
    with _LOCK:
        if isinstance(_CACHE, dict) and sig == _SIG:
            return copy.deepcopy(_CACHE)
        data = _empty()
        if sig:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    data.update(raw)
            except Exception:
                data = _empty()
        data.setdefault("version", VERSION)
        data.setdefault("satellites", {})
        _CACHE = data
        _SIG = sig
        return copy.deepcopy(data)


def _save(data, path=PATH):
    global _CACHE, _SIG
    with _LOCK:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        payload = dict(data or {})
        payload["version"] = VERSION
        payload.setdefault("satellites", {})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        _CACHE = copy.deepcopy(payload)
        _SIG = _signature(path)
    return True


def load_results(path=PATH):
    """Return {reception: result} with copies safe for UI mutation."""
    data = _load(path)
    out = {}
    for key, row in (data.get("satellites") or {}).items():
        if isinstance(row, dict):
            out[str(key)] = dict(row)
    return out


def get(reception, path=PATH):
    key = str(reception or "").strip()
    if not key:
        return {}
    return dict((load_results(path).get(key) or {}))


def save_result(reception, result, path=PATH):
    key = str(reception or "").strip()
    if not key:
        return False
    result = dict(result or {})
    row = {
        "timestamp": int(result.get("timestamp") or time.time()),
        "mapped": int(result.get("mapped") or 0),
        "review": int(result.get("review") or 0),
        "ambiguous": int(result.get("ambiguous") or 0),
        "nomatch": int(result.get("nomatch") or 0),
        "review_refs": [str(x) for x in (result.get("review_refs") or []) if str(x or "").strip()],
        "ambiguous_refs": [str(x) for x in (result.get("ambiguous_refs") or []) if str(x or "").strip()],
        "no_match_refs": [str(x) for x in (result.get("no_match_refs") or []) if str(x or "").strip()],
        "scanned_refs": [str(x) for x in (result.get("scanned_refs") or []) if str(x or "").strip()],
        "prepared_sources": int(result.get("prepared_sources") or 0),
        "indexed_rows": int(result.get("indexed_rows") or 0),
        "catalog_signature": str(result.get("catalog_signature") or ""),
        "previous_skipped": int(result.get("previous_skipped") or 0),
    }
    data = _load(path)
    satellites = dict(data.get("satellites") or {})
    satellites[key] = row
    data["satellites"] = satellites
    return _save(data, path)


def clear(reception=None, path=PATH):
    data = _load(path)
    if reception is None:
        data["satellites"] = {}
    else:
        satellites = dict(data.get("satellites") or {})
        satellites.pop(str(reception or "").strip(), None)
        data["satellites"] = satellites
    return _save(data, path)
