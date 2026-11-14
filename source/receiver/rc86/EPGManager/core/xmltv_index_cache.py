# -*- coding: utf-8 -*-
"""Persistent lightweight XMLTV channel-header cache.

Only <channel> ids/display-names are cached. Programme data is never stored
here. A file is reparsed only when its mtime or size changes.
"""
from __future__ import print_function
import json
import os

from .logger import get_logger

log = get_logger(__name__)
PATH = "/etc/enigma2/epgmanager_xmltv_index.json"


def _signature(path):
    try:
        st = os.stat(path)
        return [int(st.st_mtime), int(st.st_size)]
    except Exception:
        return [0, 0]


def load(path=PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("files", {})
            return data
    except Exception:
        pass
    return {"version": 1, "files": {}}


def save(data, path=PATH):
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception("Could not save XMLTV index cache")
        return False


def get_or_read(path, source_id, source_name, reader, cache_path=PATH):
    data = load(cache_path)
    files = data.setdefault("files", {})
    key = os.path.realpath(path)
    sig = _signature(path)
    row = files.get(key) or {}
    if row.get("signature") == sig and isinstance(row.get("channels"), list):
        channels = []
        for item in row.get("channels") or []:
            x = dict(item)
            x["source_id"] = str(source_id)
            x["source_name"] = str(source_name)
            x["epg_xml_path"] = path
            channels.append(x)
        return channels, False

    channels = list(reader(path, source_id, source_name) or [])
    compact = []
    for item in channels:
        compact.append({"channel_id": item.get("channel_id"), "display_name": item.get("display_name")})
    files[key] = {"signature": sig, "source_id": str(source_id), "source_name": str(source_name), "channels": compact}
    # Drop vanished paths to keep the file small.
    for old in list(files.keys()):
        if old != key and not os.path.exists(old):
            files.pop(old, None)
    save(data, cache_path)
    return channels, True
