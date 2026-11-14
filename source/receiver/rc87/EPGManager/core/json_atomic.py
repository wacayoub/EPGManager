# -*- coding: utf-8 -*-
"""Small thread-safe JSON persistence primitives for Enigma2.

All writes happen through a same-directory unique temporary file followed by
``os.replace`` so readers never observe a partially written JSON document.
A per-path RLock serializes read/modify/write transactions inside the Enigma2
process (the plugin's normal concurrency model).
"""
from __future__ import print_function
import json
import os
import tempfile
import threading

_LOCKS = {}
_LOCKS_GUARD = threading.RLock()


def lock_for(path):
    key = os.path.abspath(str(path))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def read_json(path, default=None):
    if default is None:
        default = {}
    lock = lock_for(path)
    with lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data
        except Exception:
            return default.copy() if isinstance(default, dict) else default


def write_json(path, data):
    lock = lock_for(path)
    with lock:
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise


def mutate_json(path, mutator, default=None):
    if default is None:
        default = {}
    lock = lock_for(path)
    with lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = default.copy() if isinstance(default, dict) else default
        result = mutator(data)
        if result is not None:
            data = result
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return data
