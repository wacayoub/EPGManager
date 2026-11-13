# -*- coding: utf-8 -*-
"""Tiny persistent availability cache for Smart Sources (beta30).

This cache stores network/file *availability only*.  It deliberately does not
parse XMLTV, count channels, or inspect programmes.  Exact EPG quality remains
an on-demand Smart Mapping / Deep Check responsibility.
"""
from __future__ import print_function

import json
import os
import threading
import time

PATH = "/etc/enigma2/epgmanager_source_fast_status.json"
VERSION = 1
_LOCK = threading.RLock()
_MEM = None
_MEM_SIG = None


def _sig():
    try:
        st = os.stat(PATH)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _load():
    global _MEM, _MEM_SIG
    sig = _sig()
    with _LOCK:
        if isinstance(_MEM, dict) and sig == _MEM_SIG:
            return _MEM
        try:
            with open(PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.setdefault("version", VERSION)
        data.setdefault("sources", {})
        _MEM, _MEM_SIG = data, sig
        return data


def _save(data):
    global _MEM, _MEM_SIG
    with _LOCK:
        parent = os.path.dirname(PATH)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, PATH)
        _MEM, _MEM_SIG = data, _sig()


def get(source_id):
    with _LOCK:
        return dict(((_load().get("sources") or {}).get(str(source_id)) or {}))


def put(source_id, row):
    with _LOCK:
        data = _load()
        payload = dict(row or {})
        payload["checked"] = int(time.time())
        data.setdefault("sources", {})[str(source_id)] = payload
        _save(data)
        return dict(payload)


def put_many(rows):
    with _LOCK:
        data = _load()
        now = int(time.time())
        dst = data.setdefault("sources", {})
        for sid, row in (rows or {}).items():
            payload = dict(row or {})
            payload["checked"] = now
            dst[str(sid)] = payload
        _save(data)


def all_rows():
    with _LOCK:
        return dict(_load().get("sources") or {})


def fresh(source_id, max_age=6 * 3600):
    row = get(source_id)
    try:
        return bool(row and (time.time() - float(row.get("checked") or 0)) < int(max_age))
    except Exception:
        return False
