# -*- coding: utf-8 -*-
"""Persistent lightweight state for automatic EPG source maintenance.

The state file is intentionally tiny.  It avoids parsing XMLTV files just to
answer questions such as "when was this feed checked?" or "why is it stale?".
Programme/channel metrics continue to live in source_index_cache.
"""
from __future__ import print_function
import json
import os
import time
import threading

from .json_atomic import lock_for, mutate_json, write_json

DEFAULT_PATH = "/etc/epgmanager_epg/source_state.json"
_MEM = {}
_MEM_SIG = {}

def _sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None



def load(path=DEFAULT_PATH):
    with lock_for(path):
        sig = _sig(path)
        if path in _MEM and _MEM_SIG.get(path) == sig:
            return _MEM[path]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("sources", {})
                _MEM[path] = data
                _MEM_SIG[path] = sig
                return data
        except Exception:
            pass
        data = {"version": 1, "sources": {}}
        _MEM[path] = data
        _MEM_SIG[path] = sig
        return data


def save(data, path=DEFAULT_PATH):
    try:
        with lock_for(path):
            write_json(path, data)
            _MEM[path] = data
            _MEM_SIG[path] = _sig(path)
        return True
    except Exception:
        return False


def get(source_id, path=DEFAULT_PATH):
    sid = str(source_id or "")
    return dict((load(path).get("sources") or {}).get(sid) or {}) if sid else {}


def update(source_id, path=DEFAULT_PATH, **fields):
    sid = str(source_id or "")
    if not sid:
        return {}
    holder = {}
    def _mut(data):
        if not isinstance(data, dict):
            data = {"version": 1, "sources": {}}
        data.setdefault("version", 1)
        sources = data.setdefault("sources", {})
        row = dict(sources.get(sid) or {})
        for key, value in fields.items():
            if value is None:
                row.pop(key, None)
            else:
                row[key] = value
        row["updated_at"] = int(time.time())
        sources[sid] = row
        holder["row"] = dict(row)
        return data
    try:
        with lock_for(path):
            data = mutate_json(path, _mut, {"version": 1, "sources": {}})
            _MEM[path] = data
            _MEM_SIG[path] = _sig(path)
    except Exception:
        return {}
    return holder.get("row", {})


def all_rows(path=DEFAULT_PATH):
    return dict(load(path).get("sources") or {})
