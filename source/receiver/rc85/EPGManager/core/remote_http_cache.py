# -*- coding: utf-8 -*-
"""Tiny HTTP validator/health cache for remote XMLTV catalogues.

RC7 goals:
- use ETag / Last-Modified only when a compact Channel-ID shard already exists;
- never persist provider XMLTV bodies;
- keep network diagnostics small and independent from Smart Mapping navigation;
- a broken/missing cache must never prevent normal provider access.
"""
from __future__ import print_function
import json
import os
import threading
import time

PATH = "/etc/enigma2/epgmanager_http_validators.json"
VERSION = 1
_LOCK = threading.RLock()
_MEM = None
_MEM_SIG = None


def _sig(path=None):
    path = path or PATH
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _load(path=None):
    global _MEM, _MEM_SIG
    path = path or PATH
    sig = _sig(path)
    with _LOCK:
        if path == PATH and isinstance(_MEM, dict) and _MEM_SIG == sig:
            return _MEM
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.setdefault("version", VERSION)
        data.setdefault("sources", {})
        if path == PATH:
            _MEM, _MEM_SIG = data, sig
        return data


def _save(data, path=None):
    global _MEM, _MEM_SIG
    path = path or PATH
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    if path == PATH:
        _MEM, _MEM_SIG = data, _sig(path)


def get(source_id, url="", path=None):
    sid = str(source_id or "")
    if not sid:
        return {}
    row = dict(((_load(path).get("sources") or {}).get(sid)) or {})
    wanted = str(url or "")
    if wanted and row.get("url") and str(row.get("url")) != wanted:
        return {}
    return row


def conditional_headers(source_id, url="", path=None):
    row = get(source_id, url=url, path=path)
    headers = {}
    etag = str(row.get("etag") or "").strip()
    modified = str(row.get("last_modified") or "").strip()
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    return headers


def record_success(source_id, url, etag="", last_modified="", status=200,
                   latency_ms=0, content_length=0, channel_count=None,
                   not_modified=False, path=None):
    sid = str(source_id or "")
    if not sid:
        return {}
    with _LOCK:
        data = _load(path)
        rows = data.setdefault("sources", {})
        row = dict(rows.get(sid) or {})
        row.update({
            "url": str(url or row.get("url") or ""),
            "last_status": int(status or 0),
            "last_success": int(time.time()),
            "last_check": int(time.time()),
            "latency_ms": int(max(0, latency_ms or 0)),
            "content_length": int(max(0, content_length or 0)),
            "consecutive_failures": 0,
            "last_error": "",
            "not_modified": bool(not_modified),
        })
        if etag:
            row["etag"] = str(etag)
        if last_modified:
            row["last_modified"] = str(last_modified)
        if channel_count is not None:
            row["channel_count"] = int(max(0, channel_count or 0))
        rows[sid] = row
        _save(data, path)
        return dict(row)


def record_failure(source_id, url, error, status=0, latency_ms=0, path=None):
    sid = str(source_id or "")
    if not sid:
        return {}
    with _LOCK:
        data = _load(path)
        rows = data.setdefault("sources", {})
        row = dict(rows.get(sid) or {})
        row.update({
            "url": str(url or row.get("url") or ""),
            "last_status": int(status or 0),
            "last_check": int(time.time()),
            "latency_ms": int(max(0, latency_ms or 0)),
            "last_error": str(error or "")[:300],
            "not_modified": False,
            "consecutive_failures": int(row.get("consecutive_failures") or 0) + 1,
        })
        rows[sid] = row
        _save(data, path)
        return dict(row)


def all_rows(path=None):
    return dict((_load(path).get("sources") or {}))
