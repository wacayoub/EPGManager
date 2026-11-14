# -*- coding: utf-8 -*-
"""
core/cache.py

Generic JSON-backed key/value cache with size capping, shared by the
translation system (Arabic translation cache, custom_dictionary.json,
unknown_shows.json) and available to any other source that wants simple
persistent caching (e.g. detailed-description caching for SNRT).
"""

import json
import os
import time

from .logger import get_logger

log = get_logger(__name__)


class JSONCache(object):
    def __init__(self, path, max_entries=5000):
        self.path = path
        self.max_entries = max_entries
        self._data = self._load()
        self._dirty = False

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Cache file %s is unreadable (%s); starting fresh. "
                            "The broken file is left in place for inspection.",
                            self.path, e)
        return {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def set(self, key, value):
        self._data[key] = value
        self._dirty = True
        if len(self._data) > self.max_entries:
            self._evict_oldest()

    def update(self, mapping):
        self._data.update(mapping)
        self._dirty = True

    def _evict_oldest(self):
        # Simple FIFO eviction based on insertion order (dicts preserve
        # insertion order in Python 3.7+, which covers every realistic
        # Enigma2 Python build).
        overflow = len(self._data) - self.max_entries
        for k in list(self._data.keys())[:overflow]:
            del self._data[k]
        log.debug("Evicted %d oldest entries from %s to stay under cap (%d)",
                  overflow, self.path, self.max_entries)

    def save(self, force=False):
        if not (self._dirty or force):
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
            self._dirty = False
        except Exception as e:
            log.warning("Could not persist cache %s: %s", self.path, e)

    def as_dict(self):
        return dict(self._data)

    def __len__(self):
        return len(self._data)


class StorageMonitor(object):
    """Section 38: keep temp/cache/log growth bounded. Call periodically
    from the scheduler (e.g. once a day) rather than on every update."""

    def __init__(self, watch_paths, max_total_mb=50):
        self.watch_paths = watch_paths
        self.max_total_mb = max_total_mb

    def total_size_mb(self):
        total = 0
        for p in self.watch_paths:
            if os.path.isfile(p):
                total += os.path.getsize(p)
            elif os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for name in files:
                        try:
                            total += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
        return total / (1024 * 1024)

    def check_and_warn(self):
        size = self.total_size_mb()
        if size > self.max_total_mb:
            log.warning(
                "EPG Manager data footprint is %.1f MB, above the %.1f MB "
                "soft limit. Consider clearing old .bak files or the "
                "translation cache from the Settings screen.",
                size, self.max_total_mb,
            )
        return size
