# -*- coding: utf-8 -*-
"""Tiny in-process UI activity gate for low-power Enigma2 receivers.

Background source maintenance is useful, but it must never compete with remote
control navigation.  Screens mark the EPGManager UI active; background workers
simply defer their next batch until the user leaves the plugin.
"""
from __future__ import print_function
import threading
import time

_LOCK = threading.RLock()
_DEPTH = 0
_COOLDOWN_UNTIL = 0.0

def begin():
    global _DEPTH
    with _LOCK:
        _DEPTH += 1
        return _DEPTH

def end(cooldown=5.0):
    global _DEPTH, _COOLDOWN_UNTIL
    with _LOCK:
        _DEPTH = max(0, _DEPTH - 1)
        if _DEPTH == 0:
            _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + max(0.0, float(cooldown or 0.0)))
        return _DEPTH

def active():
    with _LOCK:
        return bool(_DEPTH > 0 or time.time() < _COOLDOWN_UNTIL)

def status():
    with _LOCK:
        return {"depth": int(_DEPTH), "cooldown": max(0.0, _COOLDOWN_UNTIL - time.time())}
