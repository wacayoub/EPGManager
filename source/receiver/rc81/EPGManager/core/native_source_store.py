# -*- coding: utf-8 -*-
from __future__ import print_function
import json, os

PATH = "/etc/enigma2/epgmanager_native_sources.json"
DEFAULT_LOCAL = ["local_medi1tv", "local_chada_2m", "local_snrt", "local_bein_sports", "local_arryadia"]

def _retired(source_id):
    sid = str(source_id or "").lower()
    return sid in ("almajd", "local_almajd") or sid.startswith("ext_iptv_epg_") or sid.startswith("ext_iptvepg_")

class NativeSourceStore(object):
    def __init__(self, path=PATH):
        self.path = path
        self.selected = set(DEFAULT_LOCAL)
        self._load()
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("selected"), list):
                self.selected = set(str(x) for x in data["selected"] if not _retired(x))
        except Exception:
            pass
    def save(self):
        try:
            parent = os.path.dirname(self.path)
            if parent and not os.path.isdir(parent): os.makedirs(parent)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"selected": sorted(self.selected)}, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass
    def is_selected(self, source_id): return source_id in self.selected
    def toggle(self, source_id):
        if source_id in self.selected: self.selected.remove(source_id)
        else: self.selected.add(source_id)
        self.save(); return source_id in self.selected
    def set_selected(self, ids): self.selected = set(x for x in ids if not _retired(x)); self.save()
    def get_selected(self): return set(self.selected)
