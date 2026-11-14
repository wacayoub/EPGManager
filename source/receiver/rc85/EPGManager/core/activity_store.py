# -*- coding: utf-8 -*-
"""Small persistent activity snapshot used by the dashboard.

The dashboard must never parse XMLTV files or rescan bouquets just to render.
Heavy workers record compact summaries here and Home only reads this JSON.
"""
from __future__ import print_function

import json
import os
import time

from .json_atomic import mutate_json, write_json

PATH = "/etc/enigma2/epgmanager_activity.json"


def load(path=PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data, path=PATH):
    try:
        write_json(path, data)
        return True
    except Exception:
        return False


def update(section, values, path=PATH):
    row = dict(values or {})
    row.setdefault("timestamp", int(time.time()))
    def _mut(data):
        if not isinstance(data, dict): data = {}
        data[str(section)] = row
        return data
    try:
        mutate_json(path, _mut, {})
    except Exception:
        pass
    return row


def record_download(**values):
    return update("last_download", values)


def record_import(**values):
    return update("last_import", values)


def record_mapping(**values):
    return update("mapping", values)


def record_cycle(**values):
    return update("cycle", values)
