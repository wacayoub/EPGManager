# -*- coding: utf-8 -*-
"""Explicit, offline-first EPGManager health and timezone audit.

The check is intentionally user-triggered. It never downloads a feed and never
changes a mapping. It validates the exact local state Import All/Smart Mapping
will use: current direct feeds, XMLTV time syntax/future events, SRP/mapping
ownership, duplicate ServiceRef owners and (when eEPGCache is available) NOW
alignment against the receiver clock.
"""
from __future__ import print_function

import json
import os
import re
import time
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

from . import github_direct_sync, source_catalog, channel_mapper, srp_channel_map, channel_registry
from .mapping_store import MappingStore
from .source_preferences import SourcePreferences
from .native_importer import parse_xmltv_time

CACHE_PATH = "/etc/enigma2/epgmanager_system_check.json"
LAST_DIAGNOSTIC = "/etc/enigma2/epgmanager_last_diagnostic.txt"
_TZ_TOKEN = re.compile(r"(?:\s)([+-]\d{4}|Z)\s*$", re.I)


def _atomic_json(path, data):
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _epg_dir(config=None):
    try:
        if config is not None:
            value = str(config.get_epg_output_dir() or "").strip()
            if value:
                return value
    except Exception:
        pass
    return channel_mapper.EPG_DIR


def _source_path(source_id, epg_dir):
    item = (source_catalog.by_catalogue_id() or {}).get(source_id) or {}
    filename = str(item.get("local_filename") or "").strip()
    if not filename:
        stem = github_direct_sync.source_stem(source_id)
        filename = (stem + ".xml") if stem else ""
    return os.path.join(epg_dir, filename) if filename else ""


def _norm(value):
    text = " ".join(str(value or "").casefold().split())
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", text).strip()


def _first_title(programme):
    for node in programme.findall("title"):
        text = " ".join(str(node.text or "").split())
        if text:
            return text
    return ""


def _audit_xml(path, wanted_ids=None, now=None):
    now = int(now or time.time())
    result = {
        "exists": False, "channels": 0, "programmes": 0, "future_programmes": 0,
        "future_ids": 0, "invalid_times": 0, "duplicate_ids": 0, "offsets": {},
        "current": {}, "shift_plus_60": {}, "shift_minus_60": {},
    }
    if not path or not os.path.isfile(path):
        return result
    result["exists"] = True
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        result["error"] = str(exc)
        return result
    ids = []
    for ch in root.findall("channel"):
        cid = str(ch.get("id") or "").strip()
        if cid:
            ids.append(cid)
    result["channels"] = len(ids)
    result["duplicate_ids"] = max(0, len(ids) - len(set(ids)))
    wanted = set(str(x or "").casefold() for x in (wanted_ids or []) if str(x or ""))
    future_ids = set()
    offsets = Counter()
    for p in root.findall("programme"):
        result["programmes"] += 1
        cid = str(p.get("channel") or "").strip()
        start_raw = str(p.get("start") or "")
        stop_raw = str(p.get("stop") or "")
        mt = _TZ_TOKEN.search(start_raw)
        offsets[(mt.group(1).upper() if mt else "NO_OFFSET")] += 1
        start = parse_xmltv_time(start_raw)
        stop = parse_xmltv_time(stop_raw)
        if start is None or stop is None or int(stop) <= int(start):
            result["invalid_times"] += 1
            continue
        if int(stop) > now:
            result["future_programmes"] += 1
            future_ids.add(cid)
        if wanted and cid.casefold() not in wanted:
            continue
        title = _first_title(p)
        if not title:
            continue
        if int(start) <= now < int(stop):
            result["current"][cid.casefold()] = title
        # Which title would be current if the whole source were shifted by one hour.
        if int(start) <= now + 3600 < int(stop):
            result["shift_plus_60"][cid.casefold()] = title
        if int(start) <= now - 3600 < int(stop):
            result["shift_minus_60"][cid.casefold()] = title
    result["future_ids"] = len(future_ids)
    result["offsets"] = dict(offsets)
    return result


def _receiver_now(ref):
    try:
        from enigma import eEPGCache, eServiceReference
        epg = eEPGCache.getInstance()
        if epg is None:
            return ""
        sref = eServiceReference(str(ref or ""))
        try:
            event = epg.lookupEventTime(sref, -1, 0)
        except TypeError:
            event = epg.lookupEventTime(sref, -1)
        return " ".join(str(event.getEventName() or "").split()) if event is not None else ""
    except Exception:
        return ""


def _mapping_summary(store=None):
    store = store or MappingStore()
    mappings = store.all_readonly() or {}
    owners = defaultdict(list)
    manual = auto = 0
    direct = set(str(x).lower() for x in github_direct_sync.DIRECT_IDS)
    direct_rows = []
    for key, rec in mappings.items():
        sid, sep, cid = str(key or "").partition("::")
        mode = str((rec or {}).get("mode") or "").lower()
        if mode == "manual":
            manual += 1
        else:
            auto += 1
        refs = [str(x or "").strip() for x in ((rec or {}).get("refs") or []) if str(x or "").strip()]
        for ref in refs:
            try:
                canon = channel_registry.canonical_service_ref(ref) or ref.rstrip(":")
            except Exception:
                canon = ref.rstrip(":")
            owners[str(canon).casefold()].append((sid, cid, mode))
        if sep and sid.lower() in direct:
            direct_rows.append({"source_id": sid.lower(), "channel_id": cid, "refs": refs,
                                "mode": mode, "display_name": str((rec or {}).get("display_name") or ""),
                                "confidence": int((rec or {}).get("confidence") or 0),
                                "reason": str((rec or {}).get("reason") or "")})
    dup = {ref: rows for ref, rows in owners.items() if len(rows) > 1}
    return {"manual": manual, "auto": auto, "records": len(mappings),
            "owned_refs": len(owners), "duplicate_owner_refs": len(dup),
            "duplicates": dup, "direct_rows": direct_rows}


def _timezone_audit(xml_by_source, mapping):
    direct_rows = mapping.get("direct_rows") or []
    direct = exact = plus = minus = samples = 0
    by_source = {}
    for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES:
        rows = [x for x in direct_rows if str(x.get("source_id") or "").lower() == sid.lower()]
        xa = xml_by_source.get(sid) or {}
        e = p = m = s = 0
        for row in rows:
            cid = str(row.get("channel_id") or "").casefold()
            xml_now = str((xa.get("current") or {}).get(cid) or "")
            xml_plus = str((xa.get("shift_plus_60") or {}).get(cid) or "")
            xml_minus = str((xa.get("shift_minus_60") or {}).get(cid) or "")
            for ref in row.get("refs") or []:
                receiver = _receiver_now(ref)
                if not receiver:
                    continue
                s += 1; samples += 1
                nr = _norm(receiver)
                if xml_now and _norm(xml_now) == nr:
                    e += 1; exact += 1
                if xml_plus and _norm(xml_plus) == nr:
                    p += 1; plus += 1
                if xml_minus and _norm(xml_minus) == nr:
                    m += 1; minus += 1
        state = "UNKNOWN"
        detail = "no receiver NOW samples"
        if s:
            best_shift = max(p, m)
            if e >= best_shift:
                state = "PASS"; detail = "%d/%d NOW titles aligned" % (e, s)
            elif s >= 2 and best_shift >= 2 and best_shift > e:
                state = "WARN"; detail = "TIME OFFSET SUSPECT %s60m • exact %d/%d" % ("+" if p >= m else "-", e, s)
            else:
                state = "WARN"; detail = "NOW mismatch %d/%d • not enough proof for offset" % (s - e, s)
        by_source[sid] = {"status": state, "samples": s, "exact": e, "plus60": p, "minus60": m, "detail": detail}
    overall = "UNKNOWN"
    if samples:
        shifted = max(plus, minus)
        overall = "PASS" if exact >= shifted else ("WARN" if shifted >= 2 else "WARN")
    return {"status": overall, "samples": samples, "exact": exact,
            "plus60": plus, "minus60": minus, "sources": by_source}


def run(config=None):
    started = time.time(); now = int(started)
    epg_dir = _epg_dir(config)
    github = github_direct_sync.load() or {}
    source_rows = github.get("sources") or {}
    selected = set(str(x or "") for x in SourcePreferences().selected_catalogue_ids())
    mapping = _mapping_summary()

    wanted_by_source = defaultdict(set)
    for row in mapping.get("direct_rows") or []:
        wanted_by_source[str(row.get("source_id") or "")].add(str(row.get("channel_id") or ""))

    checks = []
    xml_by_source = {}
    healthy = 0; local_ok = 0; total_future = 0; total_ids = 0
    for sid, label, stem in github_direct_sync.DIRECT_SOURCES:
        meta = source_rows.get(sid) or {}
        if meta.get("ok") and not meta.get("degraded"):
            healthy += 1
        path = _source_path(sid, epg_dir)
        xa = _audit_xml(path, wanted_by_source.get(sid), now=now)
        xml_by_source[sid] = xa
        total_ids += int(xa.get("channels") or 0)
        total_future += int(xa.get("future_programmes") or 0)
        status = "PASS"
        notes = []
        if not xa.get("exists"):
            # GitHub-direct sources need not persist XML on the receiver.
            # Cached remote metadata is not proof of native EPG import:
            # report WARN/unknown, never a false local-file FAIL or PASS.
            status = "WARN"
            notes.append("GitHub direct: local XML optional; native EPG not verified")
            if meta.get("ok"):
                notes.append("remote feed metadata available")
            else:
                notes.append("remote feed metadata unavailable")
        elif xa.get("error"):
            status = "FAIL"; notes.append("XML unreadable")
        elif int(xa.get("future_programmes") or 0) <= 0:
            status = "FAIL"; notes.append("no future EPG")
        else:
            local_ok += 1
            notes.append("%d IDs / %d future" % (int(xa.get("channels") or 0), int(xa.get("future_programmes") or 0)))
        if int(xa.get("invalid_times") or 0):
            status = "FAIL"; notes.append("%d invalid times" % int(xa.get("invalid_times") or 0))
        if int(xa.get("duplicate_ids") or 0):
            status = "FAIL"; notes.append("%d duplicate IDs" % int(xa.get("duplicate_ids") or 0))
        if not meta.get("ok") and status == "PASS":
            status = "WARN"; notes.append("GitHub feed offline/unreadable")
        elif meta.get("degraded") and status == "PASS":
            status = "WARN"
            if meta.get("stale"):
                notes.append("GitHub feed stale")
            if int(meta.get("zero_epg_channels") or 0):
                notes.append("%d zero-EPG IDs" % int(meta.get("zero_epg_channels") or 0))
        checks.append({"name": label, "status": status, "detail": " • ".join(notes),
                       "path": path, "source_id": sid, "offsets": xa.get("offsets") or {}})

    # SRP summary uses the already-generated index; no rebuild happens here.
    try:
        srp = srp_channel_map.load_index() or {}
        srp_sources = list((srp.get("sources") or {}).values())
        srp_refs = sum(int((x or {}).get("refs") or (x or {}).get("service_refs") or (x or {}).get("mapped_service_refs") or 0) for x in srp_sources)
        unresolved = sum(int((x or {}).get("unmapped") or (x or {}).get("unmapped_channel_ids") or 0) for x in srp_sources)
    except Exception:
        srp_refs = 0; unresolved = 0

    monitor = github.get("id_monitor") or {}
    green = red = unknown = 0
    for sid, _label, stem in github_direct_sync.DIRECT_SOURCES:
        for row in monitor.get(stem, []) or []:
            health = github_direct_sync.id_health(sid, row.get("id"), data=github)
            if health == "OK": green += 1
            elif health == "NO_EPG": red += 1
            else: unknown += 1

    # Per-ID diagnostic rows: source, XMLTV ID, ServiceRef, confidence, status and reason.
    mapping_rows = []
    mapped_keys = set()
    for row in mapping.get("direct_rows") or []:
        sid = str(row.get("source_id") or "").lower(); cid = str(row.get("channel_id") or "")
        mapped_keys.add((sid, cid.casefold()))
        health = github_direct_sync.id_health(sid, cid, data=github)
        refs = list(row.get("refs") or [])
        mapping_rows.append({"source_id": sid, "channel_id": cid,
                             "service_refs": refs, "mode": str(row.get("mode") or ""),
                             "confidence": int(row.get("confidence") or 0),
                             "status": "NO_EPG" if health == "NO_EPG" else ("MAPPED" if refs else "UNMAPPED"),
                             "health": health, "reason": str(row.get("reason") or "")})
    for sid, _label, stem in github_direct_sync.DIRECT_SOURCES:
        for row in monitor.get(stem, []) or []:
            cid = str(row.get("id") or "")
            key = (sid.lower(), cid.casefold())
            if not cid or key in mapped_keys:
                continue
            health = github_direct_sync.id_health(sid, cid, data=github)
            mapping_rows.append({"source_id": sid, "channel_id": cid, "service_refs": [],
                                 "mode": "", "confidence": 0,
                                 "status": "NO_EPG" if health == "NO_EPG" else "UNMAPPED",
                                 "health": health,
                                 "reason": str(row.get("reason") or ("no ServiceRef owner" if health != "NO_EPG" else "zero future EPG"))})
    mapping_rows.sort(key=lambda x:(str(x.get("status") or ""), str(x.get("source_id") or ""), str(x.get("channel_id") or "").casefold()))

    tz = _timezone_audit(xml_by_source, mapping)
    selected_direct = len(set(github_direct_sync.DIRECT_IDS).intersection(selected))
    checks.extend([
        {"name": "DIRECT SOURCES", "status": "PASS" if healthy == len(github_direct_sync.DIRECT_SOURCES) else "WARN",
         "detail": "%d/%d GitHub healthy • %d/%d selected" % (healthy, len(github_direct_sync.DIRECT_SOURCES), selected_direct, len(github_direct_sync.DIRECT_SOURCES))},
        {"name": "LOCAL XML / GITHUB DIRECT", "status": "PASS" if local_ok == len(github_direct_sync.DIRECT_SOURCES) else "WARN",
         "detail": "%d/%d valid optional local feeds • %d local XMLTV IDs; remote-only feeds require native EPG verification" % (local_ok, len(github_direct_sync.DIRECT_SOURCES), total_ids)},
        {"name": "SRP / OWNERSHIP", "status": "FAIL" if mapping.get("duplicate_owner_refs") else ("WARN" if unresolved else "PASS"),
         "detail": "%d refs • %d unresolved • %d duplicate owners" % (srp_refs or mapping.get("owned_refs") or 0, unresolved, mapping.get("duplicate_owner_refs") or 0)},
        {"name": "ID HEALTH", "status": "WARN" if red or unknown else "PASS",
         "detail": "%d green • %d no EPG • %d unknown" % (green, red, unknown)},
        {"name": "TIMEZONE / NOW", "status": tz.get("status") if tz.get("status") != "UNKNOWN" else "WARN",
         "detail": ("%d receiver samples • %d aligned • +60=%d • -60=%d" % (tz.get("samples",0), tz.get("exact",0), tz.get("plus60",0), tz.get("minus60",0))) if tz.get("samples") else "No live receiver NOW samples; XML offset syntax still validated"},
    ])

    overall = "PASS"
    if any(x.get("status") == "FAIL" for x in checks):
        overall = "FAIL"
    elif any(x.get("status") in ("WARN", "UNKNOWN") for x in checks):
        overall = "WARN"
    report = {
        "version": 1, "checked": now, "elapsed": round(time.time()-started, 2),
        "status": overall, "epg_dir": epg_dir, "healthy_sources": healthy,
        "local_sources": local_ok, "direct_total": len(github_direct_sync.DIRECT_SOURCES),
        "total_xmltv_ids": total_ids, "future_programmes": total_future,
        "mapped_refs": int(srp_refs or mapping.get("owned_refs") or 0), "unresolved": int(unresolved),
        "manual_mappings": int(mapping.get("manual") or 0), "auto_mappings": int(mapping.get("auto") or 0),
        "duplicate_owner_refs": int(mapping.get("duplicate_owner_refs") or 0),
        "green_ids": green, "red_ids": red, "unknown_ids": unknown,
        "timezone": tz, "checks": checks, "mapping_rows": mapping_rows,
    }
    _atomic_json(CACHE_PATH, report)
    return report


def _diagnostic_lines(report):
    report = report or {}
    lines = [
        "EPGManager diagnostic",
        "checked=%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(report.get("checked") or time.time()))),
        "status=%s" % str(report.get("status") or "UNKNOWN"),
        "sources=%s/%s" % (report.get("healthy_sources",0), report.get("direct_total",0)),
        "xmltv_ids=%s green=%s red=%s unknown=%s" % (report.get("total_xmltv_ids",0), report.get("green_ids",0), report.get("red_ids",0), report.get("unknown_ids",0)),
        "mapped_refs=%s unresolved=%s duplicate_owner_refs=%s" % (report.get("mapped_refs",0), report.get("unresolved",0), report.get("duplicate_owner_refs",0)),
        "manual=%s auto=%s" % (report.get("manual_mappings",0), report.get("auto_mappings",0)),
        "",
        "CHECKS",
    ]
    for item in report.get("checks") or []:
        lines.append("[%s] %s :: %s" % (item.get("status","?"), item.get("name","?"), item.get("detail","")))
    lines += ["", "TIMEZONE SOURCES"]
    for sid, row in sorted(((report.get("timezone") or {}).get("sources") or {}).items()):
        lines.append("[%s] %s :: %s" % (row.get("status","?"), sid, row.get("detail","")))
    lines += ["", "MAPPING / ID DETAIL", "status | source | xmltv_id | score | mode | service_ref | reason"]
    for row in report.get("mapping_rows") or []:
        refs = ";".join(str(x or "") for x in (row.get("service_refs") or [])) or "-"
        lines.append("%s | %s | %s | %s | %s | %s | %s" % (
            row.get("status") or "?", row.get("source_id") or "?", row.get("channel_id") or "?",
            int(row.get("confidence") or 0), row.get("mode") or "-", refs,
            row.get("reason") or row.get("health") or ""))
    return lines


def export_report(report=None):
    report = report or load_cache() or run(None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = "/tmp/epgmanager_diagnostic_%s.txt" % stamp
    text = "\n".join(_diagnostic_lines(report)) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        with open(LAST_DIAGNOSTIC + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(LAST_DIAGNOSTIC + ".tmp", LAST_DIAGNOSTIC)
    except Exception:
        pass
    return path
