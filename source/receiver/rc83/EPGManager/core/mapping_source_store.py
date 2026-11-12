# -*- coding: utf-8 -*-
"""Persistent allow-list for Smart Mapping automatic source selection."""
from __future__ import print_function
import json
import os

from . import source_variant_policy

PATH = "/etc/enigma2/epgmanager_mapping_sources.json"
_CACHE = {}

def _path_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return (0, 0)



class MappingSourceStore(object):
    def __init__(self, path=PATH):
        self.path = path
        sig = _path_sig(path)
        cached = _CACHE.get(path)
        if cached and cached.get("sig") == sig:
            self.data = {"version": 2, "enabled": None if cached.get("enabled") is None else list(cached.get("enabled") or [])}
        else:
            self.data = self._load()
            _CACHE[path] = {"sig": _path_sig(path), "enabled": None if self.data.get("enabled") is None else list(self.data.get("enabled") or [])}

    @staticmethod
    def _normalise(source_ids, fallback=None, explicit_choice=None):
        """Normalise Smart Mapping source ownership.

        MappingSourceStore is an additional allow-list layered on top of
        SourcePreferences.  Older builds did not apply the AR/EN parallel-feed
        policy here, so a stale explicit allow-list could silently re-enable
        Saudi 2/4/6 [EN] even after Smart Sources had selected 1/3/5 [AR].
        Reconcile with the current fallback while preserving a real explicit
        EN choice when SourcePreferences itself also selects EN.
        """
        rows = source_variant_policy.normalize_selected(source_ids or [], explicit_choice=explicit_choice)
        fb = set(source_variant_policy.normalize_selected(fallback or []))
        out = list(rows)
        # If the explicit list contains the EN sibling from an old config but
        # current source preferences explicitly contain AR, migrate to AR.
        for ar, en in source_variant_policy.PAIRS:
            if en in out and ar in fb and en not in fb:
                out = [ar if x == en else x for x in out]
        return source_variant_policy.normalize_selected(out, explicit_choice=explicit_choice)

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("enabled"), list):
                rows = self._normalise([str(x) for x in data.get("enabled") if str(x)])
                return {"version": 2, "enabled": rows}
        except Exception:
            pass
        return {"version": 2, "enabled": None}

    def is_explicit(self):
        return isinstance(self.data.get("enabled"), list)

    def effective(self, fallback=None):
        fallback_rows = [str(x) for x in (fallback or []) if str(x)]
        if self.is_explicit():
            rows = self._normalise(self.data.get("enabled") or [], fallback=fallback_rows)
            # Keep the in-memory value migrated immediately; disk is rewritten
            # only on the next explicit source change to avoid I/O at screen open.
            self.data["enabled"] = list(rows)
            return set(rows), True
        rows = self._normalise(fallback_rows)
        return set(rows), bool(rows)

    def set_enabled(self, source_ids, fallback=None, explicit_choice=None):
        rows = self._normalise(source_ids, fallback=fallback, explicit_choice=explicit_choice)
        self.data["enabled"] = sorted(set(rows))
        self.save()
        return set(self.data["enabled"])

    def toggle(self, source_id, fallback=None):
        current, _active = self.effective(fallback=fallback)
        sid = str(source_id or "")
        if not sid:
            return current
        if sid in current:
            current.remove(sid)
            return self.set_enabled(current, fallback=fallback)
        current.add(sid)
        # A deliberate toggle selects this exact sibling and removes its pair.
        return self.set_enabled(current, fallback=fallback, explicit_choice=sid)

    def reset(self):
        self.data["enabled"] = None
        self.save()

    def save(self):
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            _CACHE[self.path] = {"sig": _path_sig(self.path), "enabled": None if self.data.get("enabled") is None else list(self.data.get("enabled") or [])}
        except Exception:
            pass
        # Source ownership affects automatic SRP maps. Invalidate only the tiny
        # source-map index; XMLTV downloads and Global SRP cache remain intact.
        try:
            idx = "/etc/enigma2/epgmanager_srp_maps.json"
            if os.path.exists(idx):
                os.unlink(idx)
        except Exception:
            pass
        return True
