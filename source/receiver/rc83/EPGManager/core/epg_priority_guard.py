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
# Feeds refresh daily; two missed days means the service lost its XMLTV guide.
STALE_AFTER_SECONDS = 48 * 3600


def _owned_with_timestamps(state, now):
    """Return (owned refs, {ref: last written}) from a schema 1 or 2 journal.

    Schema 1 journals have no timestamps; their refs start counting from now,
    so upgrading never releases anything immediately.
    """
    owned = set(str(x) for x in (state.get("protected_refs") or []) if x)
    raw = state.get("last_written") if isinstance(state.get("last_written"), dict) else {}
    last_written = {}
    for ref in owned:
        try:
            last_written[ref] = int(raw.get(ref, now))
        except (TypeError, ValueError):
            last_written[ref] = now
    return owned, last_written


def _state_payload(owned, last_written, now):
    return {
        "schema": 2,
        "flag": DX_NO_EIT,
        "updated_at": int(now),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "protected_refs": sorted(owned),
        "last_written": dict((ref, int(last_written.get(ref, now))) for ref in sorted(owned)),
    }


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


def protect_imported_services(service_refs, state_path=STATE_FILE, past_only_refs=()):
    """Persist OpenATV's native no-EIT flag for successfully imported DVB refs.

    Refs absent from this import are not released immediately, because this
    function is also used for single-source imports. rc82 records when each
    ref was last written and releases refs older than STALE_AFTER_SECONDS,
    so a service whose feed stopped covering it gets SAT EIT back instead of
    keeping an empty guide. Nothing is released when this import wrote no
    DVB service (failed or offline run).

    rc83: ``past_only_refs`` received only programmes that had already ended.
    The native import still blocks their SAT EIT, so they are released at
    once instead of staying blank for 48 hours.
    """
    refs = sorted(set(str(x or "").strip() for x in (service_refs or ()) if _is_dvb_service_ref(x)))
    protected_set = set(refs)
    past_only = sorted(set(str(x or "").strip() for x in (past_only_refs or ())
                           if _is_dvb_service_ref(x)) - protected_set)
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
    now = int(time.time())
    stale = []
    try:
        state = _load_state(state_path)
        owned, last_written = _owned_with_timestamps(state, now)
        owned.update(refs)
        for raw in refs:
            last_written[raw] = now
        # rc82: a service EPGManager has not written for STALE_AFTER_SECONDS
        # gets SAT EIT back. Ages are per service, so a single-source import
        # never releases services that a recent full import still covers.
        stale = sorted(r for r in owned if now - int(last_written.get(r, now)) > STALE_AFTER_SECONDS)
        _atomic_json(state_path, _state_payload(owned, last_written, now))
        result["journal_services"] = len(owned)
    except Exception as exc:
        result["journal_error"] = str(exc)
        log.warning("Could not write EPG priority journal: %s", exc)

    if stale:
        released = release_refs(stale, state_path=state_path)
        result["stale_released"] = int(released.get("released", 0) or 0)
        result["stale_release_ok"] = bool(released.get("ok"))
        log.info("Returned SAT EIT to %d service(s) without EPGManager events for %d h",
                 result["stale_released"], STALE_AFTER_SECONDS // 3600)

    if past_only:
        released = release_refs(past_only, state_path=state_path)
        result["past_only_released"] = int(released.get("released", 0) or 0)
        log.info("Returned SAT EIT to %d service(s) whose feed had only finished programmes",
                 result["past_only_released"])

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
        now = int(time.time())
        state = _load_state(state_path)
        owned, last_written = _owned_with_timestamps(state, now)
        owned.difference_update(refs)
        for raw in refs:
            last_written.pop(raw, None)
        _atomic_json(state_path, _state_payload(owned, last_written, now))
    except Exception:
        pass
    return result
