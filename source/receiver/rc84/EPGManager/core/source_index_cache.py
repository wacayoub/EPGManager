# -*- coding: utf-8 -*-
"""Persistent lightweight XMLTV source index.

Stores only file signature + channel/program counts/health so the EPG Sources
screen can render immediately without reparsing large XMLTV files every time.
"""
from __future__ import print_function
import json
import os
import time

from .json_atomic import mutate_json, lock_for

_MEM = {}
_MEM_SIG = {}

PATH = "/etc/enigma2/epgmanager_source_index.json"


def signature(path):
    """Stable JSON-safe [mtime,size] signature for a local XMLTV file."""
    try:
        st = os.stat(path)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)))
        return [mtime_ns, int(st.st_size)]
    except Exception:
        return None


def same_signature(cached_sig, current_sig):
    if not cached_sig or not current_sig:
        return False
    try:
        return [int(cached_sig[0]), int(cached_sig[1])] == [int(current_sig[0]), int(current_sig[1])]
    except Exception:
        return False


def _load(path=PATH):
    # Keep the memory snapshot and its file signature coherent with concurrent
    # source workers.  Without this outer lock, writer A could publish an older
    # in-memory payload after writer B had already replaced the JSON file.
    with lock_for(path):
        sig = signature(path)
        if path in _MEM and _MEM_SIG.get(path) == sig:
            return _MEM[path]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data = data if isinstance(data, dict) else {}
        except Exception:
            data = {}
        _MEM[path] = data
        _MEM_SIG[path] = sig
        return data


def load_all(path=PATH):
    return _load(path)


def get(source_id, path=PATH):
    return _load(path).get(str(source_id), {})


def update_many(entries, path=PATH):
    if not entries:
        return
    def _mut(data):
        if not isinstance(data, dict):
            data = {}
        for sid, value in entries.items():
            row = dict(value or {})
            row["updated_at"] = int(time.time())
            data[str(sid)] = row
        return data
    try:
        with lock_for(path):
            data = mutate_json(path, _mut, {})
            _MEM[path] = data
            _MEM_SIG[path] = signature(path)
    except Exception:
        pass


def update(source_id, value, path=PATH):
    update_many({source_id: value}, path)
