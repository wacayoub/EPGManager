# -*- coding: utf-8 -*-
"""Keep EPGManager authoritative on DVB services it successfully imports.

OpenATV's native ``eEPGCache.importEvents`` marks imported DVB services with
``eDVBService::dxNoEIT`` (0x1000) in memory so live SAT EIT cannot overwrite an
external import.  That in-memory flag can be lost after an Enigma2 restart.

This module persists the same native flag to the receiver service database, but
*only* for DVB service references that EPGManager actually wrote successfully.
IPTV is ignored and global EIT remains enabled, so normal SAT EPG continues to
work on services which are not owned by EPGManager.
"""
from __future__ import print_function

import json
import os
import time

from . import channel_mapper
from .logger import get_logger

log = get_logger(__name__)

DX_NO_EIT = 0x1000
STATE_FILE = os.path.join(channel_mapper.EPG_DIR, ".epgmanager_priority.json")


def _atomic_json(path, payload):
    directory = os.path.dirname(path) or "."
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _load_state(path=STATE_FILE):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _is_dvb_service_ref(ref):
    raw = str(ref or "").strip()
    if not raw or channel_mapper.classify_service_ref(raw) == "IPTV":
        return False
    ids = channel_mapper.parse_dvb_ids(raw)
    return bool(ids.get("sid") is not None and ids.get("tsid") is not None and
                ids.get("onid") is not None and len(raw.split(":")) >= 7)


def protect_imported_services(service_refs, state_path=STATE_FILE):
    """Persist OpenATV's native no-EIT flag for successfully imported DVB refs.

    The operation is additive and idempotent.  We intentionally do not remove
    existing flags here because this function is also used for single-source
    imports; removing refs absent from a partial import would incorrectly hand
    them back to SAT EIT.  A later mapping-reset path can use ``release_refs``.
    """
    refs = sorted(set(str(x or "").strip() for x in (service_refs or ()) if _is_dvb_service_ref(x)))
    result = {
        "ok": True,
        "requested": len(service_refs or ()),
        "dvb_services": len(refs),
        "protected": 0,
        "failed": 0,
        "persisted": False,
        "state_file": state_path,
    }
    if not refs:
        return result

    try:
        from enigma import eDVBDB, eServiceReference
        db = eDVBDB.getInstance()
    except Exception as exc:
        result.update({"ok": False, "error": "eDVBDB unavailable: %s" % exc})
        log.warning("EPG priority persistence unavailable: %s", exc)
        return result

    if db is None or not hasattr(db, "addFlag"):
        result.update({"ok": False, "error": "eDVBDB.addFlag unavailable"})
        return result

    for raw in refs:
        try:
            db.addFlag(eServiceReference(raw), DX_NO_EIT)
            result["protected"] += 1
        except Exception:
            result["failed"] += 1
            if result["failed"] <= 4:
                log.exception("Could not persist EPGManager priority for %s", raw)

    if result["protected"]:
        try:
            if hasattr(db, "saveServicelist"):
                db.saveServicelist()
                result["persisted"] = True
            else:
                result["ok"] = False
                result["error"] = "eDVBDB.saveServicelist unavailable"
        except Exception as exc:
            result["ok"] = False
            result["error"] = "saveServicelist failed: %s" % exc
            log.exception("Could not save EPGManager priority flags to lamedb")

    # Keep an ownership journal for diagnostics and safe future Reset handling.
    # Failure to write this journal must never break the actual EPG import.
    try:
        state = _load_state(state_path)
        owned = set(str(x) for x in (state.get("protected_refs") or []) if x)
        owned.update(refs)
        state = {
            "schema": 1,
            "flag": DX_NO_EIT,
            "updated_at": int(time.time()),
            "protected_refs": sorted(owned),
        }
        _atomic_json(state_path, state)
        result["journal_services"] = len(owned)
    except Exception as exc:
        result["journal_error"] = str(exc)
        log.warning("Could not write EPG priority journal: %s", exc)

    return result


def release_refs(service_refs, state_path=STATE_FILE):
    """Remove only EPGManager-owned no-EIT flags for explicit reset/unmapping."""
    refs = sorted(set(str(x or "").strip() for x in (service_refs or ()) if _is_dvb_service_ref(x)))
    result = {"ok": True, "requested": len(refs), "released": 0, "failed": 0}
    if not refs:
        return result
    try:
        from enigma import eDVBDB, eServiceReference
        db = eDVBDB.getInstance()
        if db is None or not hasattr(db, "removeFlag"):
            raise RuntimeError("eDVBDB.removeFlag unavailable")
        for raw in refs:
            try:
                db.removeFlag(eServiceReference(raw), DX_NO_EIT)
                result["released"] += 1
            except Exception:
                result["failed"] += 1
        if result["released"] and hasattr(db, "saveServicelist"):
            db.saveServicelist()
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
        return result

    try:
        state = _load_state(state_path)
        owned = set(str(x) for x in (state.get("protected_refs") or []) if x)
        owned.difference_update(refs)
        state["protected_refs"] = sorted(owned)
        state["updated_at"] = int(time.time())
        state["schema"] = 1
        state["flag"] = DX_NO_EIT
        _atomic_json(state_path, state)
    except Exception:
        pass
    return result
