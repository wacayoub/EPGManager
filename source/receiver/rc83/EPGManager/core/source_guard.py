# -*- coding: utf-8 -*-
"""Low-cost last-known-good protection for XMLTV source refreshes.

The XML writers/downloaders are already atomic.  This guard adds one extra
policy layer: a syntactically valid but catastrophically incomplete feed must
not replace a healthy cache.  It uses byte-stream counters instead of parsing
large XML trees, which keeps CPU/RAM usage low on Vu+ receivers.
"""
from __future__ import print_function

import hashlib
import json
import os
import shutil
import time

from .json_atomic import mutate_json, write_json

from .logger import get_logger

log = get_logger(__name__)
STATE_PATH = "/etc/enigma2/epgmanager_source_guard.json"
MAX_OLD_AGE = 14 * 86400
MIN_PROGRAMMES_FOR_RATIO = 20
MIN_ACCEPTED_RATIO = 0.35
MIN_CHANNEL_RATIO = 0.40


def _count_token(chunk, token):
    return chunk.count(token)


def inspect_xml(path):
    row = {"path": path, "exists": False, "size": 0, "sha256": "", "programmes": 0, "channels": 0, "mtime": 0}
    try:
        if not path or not os.path.isfile(path):
            return row
        row["exists"] = True
        row["size"] = int(os.path.getsize(path))
        row["mtime"] = int(os.path.getmtime(path))
        digest = hashlib.sha256()
        programmes = 0
        channels = 0
        tail = b""
        with open(path, "rb") as fh:
            while True:
                block = fh.read(256 * 1024)
                if not block:
                    break
                digest.update(block)
                scan = tail + block
                programmes += _count_token(scan, b"<programme ") - _count_token(tail, b"<programme ")
                channels += _count_token(scan, b"<channel ") - _count_token(tail, b"<channel ")
                tail = scan[-24:]
        row["sha256"] = digest.hexdigest()
        row["programmes"] = int(programmes)
        row["channels"] = int(channels)
    except Exception as exc:
        row["error"] = str(exc)
    return row


def _valid(row):
    return bool(row and row.get("exists") and int(row.get("size") or 0) > 32 and int(row.get("programmes") or 0) > 0)


def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data):
    try:
        write_json(STATE_PATH, data)
    except Exception:
        pass


def _record(source_id, source_name, result):
    row = dict(result or {})
    row["name"] = str(source_name or source_id or "Source")
    row["timestamp"] = int(time.time())
    key = str(source_id or source_name or "unknown")
    def _mut(data):
        if not isinstance(data, dict): data = {}
        data.setdefault("sources", {})[key] = row
        data["version"] = 1
        return data
    try:
        mutate_json(STATE_PATH, _mut, {"version": 1, "sources": {}})
    except Exception:
        pass


def load_state():
    return _load_state()


def _restore_candidate(path, before):
    candidates = [path + ".bak", path + ".lastgood"]
    target_hash = str((before or {}).get("sha256") or "")
    best = None
    for candidate in candidates:
        info = inspect_xml(candidate)
        if not _valid(info):
            continue
        if target_hash and info.get("sha256") == target_hash:
            return candidate, info
        if best is None or int(info.get("programmes") or 0) > int(best[1].get("programmes") or 0):
            best = (candidate, info)
    return best or (None, None)


def evaluate_after_refresh(path, before, config=None, source_id=None, source_name=None):
    """Validate refreshed XMLTV and restore last-known-good on major regression.

    Returns a compact dict with accepted/restored/unchanged/reason and metrics.
    The caller decides whether a restored cache counts as an operational success.
    """
    before = before or inspect_xml(path)
    after = inspect_xml(path)
    safe_enabled = True
    reject_drop = True
    try:
        if config is not None and hasattr(config, "get_safe_source_replacement"):
            safe_enabled = bool(config.get_safe_source_replacement())
        if config is not None and hasattr(config, "get_reject_abnormal_drop"):
            reject_drop = bool(config.get_reject_abnormal_drop())
    except Exception:
        pass

    unchanged = bool(_valid(before) and _valid(after) and before.get("sha256") == after.get("sha256"))
    reason = ""
    reject = False
    if safe_enabled:
        if not _valid(after):
            reject = True
            reason = "empty or invalid XMLTV"
        elif reject_drop and _valid(before):
            old_age = max(0, int(time.time()) - int(before.get("mtime") or 0))
            old_p = int(before.get("programmes") or 0)
            new_p = int(after.get("programmes") or 0)
            old_c = int(before.get("channels") or 0)
            new_c = int(after.get("channels") or 0)
            if old_age <= MAX_OLD_AGE and old_p >= MIN_PROGRAMMES_FOR_RATIO:
                ratio = float(new_p) / float(max(1, old_p))
                if new_p < 5 or ratio < MIN_ACCEPTED_RATIO:
                    reject = True
                    reason = "abnormal programme drop %d -> %d" % (old_p, new_p)
            if (not reject and old_age <= MAX_OLD_AGE and old_c >= 4 and
                    float(new_c) / float(max(1, old_c)) < MIN_CHANNEL_RATIO):
                reject = True
                reason = "abnormal channel drop %d -> %d" % (old_c, new_c)

    restored = False
    restore_from = None
    if reject and _valid(before):
        candidate, candidate_info = _restore_candidate(path, before)
        if candidate:
            try:
                shutil.copy2(candidate, path)
                restored = True
                restore_from = candidate
                after = inspect_xml(path)
                log.warning("Rejected refreshed XMLTV for %s (%s); restored %s",
                            source_name or source_id, reason, candidate)
            except Exception as exc:
                reason = "%s; restore failed: %s" % (reason, exc)

    accepted = bool(_valid(after) and (not reject or restored))
    result = {
        "accepted": accepted,
        "restored": bool(restored),
        "unchanged": bool(unchanged and not restored),
        "reason": reason or ("unchanged" if unchanged else "valid"),
        "programmes": int(after.get("programmes") or 0),
        "channels": int(after.get("channels") or 0),
        "size": int(after.get("size") or 0),
        "sha256": str(after.get("sha256") or ""),
        "restore_from": restore_from,
        "previous_programmes": int(before.get("programmes") or 0),
        "previous_channels": int(before.get("channels") or 0),
    }
    _record(source_id, source_name, result)
    return result
