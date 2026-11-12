# -*- coding: utf-8 -*-
"""Bidirectional Channel-ID <-> receiver SRP coverage audit for beta41.

The audit separates real matching failures from IDs that simply have no
corresponding DVB-TV service in the receiver lamedb.  It also reports receiver
services for which a known EPG identity exists but no persistent SRP map was
created.  No network access and no mapping writes occur here.
"""
from __future__ import print_function

import json
import os
import time

from .logger import get_logger
from . import (
    channel_registry, channel_mapper, source_catalog, source_channel_cache,
    srp_channel_map, source_id_harvester, canonical_bridge, deterministic_matcher,
    service_variant_guard, programme_feed_policy,
)
from .source_preferences import SourcePreferences
from .channel_identity_resolver import (
    canonical_key, keys as identity_keys, label_from_id,
)

log = get_logger(__name__)
JSON_PATH = "/etc/enigma2/epgmanager_id_audit.json"
TEXT_PATH = "/etc/epgimport/epgmanager-id-audit.txt"
VERSION = 4
PRIORITY_SATS = set((-8.0, -7.0, 25.5, 26.0))


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _epg_dir(config=None):
    try:
        if config is not None:
            return str(config.get_epg_output_dir())
    except Exception:
        pass
    return channel_mapper.EPG_DIR


def _merge_rows(*groups):
    out, seen = [], set()
    for rows in groups:
        for row in rows or []:
            cid = str((row or {}).get("channel_id") or "").strip()
            if not cid or cid.lower() in seen:
                continue
            seen.add(cid.lower())
            out.append({
                "channel_id": cid,
                "display_name": str((row or {}).get("display_name") or cid).strip() or cid,
            })
    return out


def _local_rows(item, epg_dir):
    mid = source_catalog.mapping_source_id(item)
    dynamic = []
    filename = str((item or {}).get("output_file") or "")
    path = os.path.join(epg_dir, filename) if filename else ""
    try:
        if path and os.path.isfile(path) and os.path.getsize(path) > 32:
            dynamic = channel_mapper.read_xmltv_channel_ids_fast(path, mid, item.get("name") or mid)
    except Exception:
        dynamic = []
    return _merge_rows(dynamic, source_catalog.local_channel_hints(mid) or [])


def _source_rows(item, epg_dir):
    if str((item or {}).get("kind") or "").lower() == "local":
        return _local_rows(item, epg_dir)
    return _merge_rows(source_channel_cache.get(item.get("id")) or [])


def _candidate_names(row, item=None):
    out = []
    def add(v):
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)
    add((row or {}).get("display_name"))
    add(label_from_id((row or {}).get("channel_id")))
    add((row or {}).get("channel_id"))
    for value in canonical_bridge.aliases((row or {}).get("channel_id"), (row or {}).get("display_name")):
        add(value)
    # Keep diagnostics aligned with the actual SRP builder for local feeds whose
    # stable XMLTV IDs intentionally differ from DVB presentation names
    # (provider-numbered and beIN logo-derived IDs).
    if item is not None:
        try:
            from . import srp_master_engine
            for value in srp_master_engine._local_special_aliases(item, row):
                add(value)
        except Exception:
            pass
    return out


def _source_mapping(index, mid):
    row = ((index.get("sources") or {}).get(str(mid))) or ((index.get("sources") or {}).get(str(mid).lower())) or {}
    path = str((row or {}).get("map_path") or "")
    if not path or not os.path.isfile(path):
        return {}, path
    try:
        return srp_channel_map._parse_channels_xml_preserve(path) or {}, path
    except Exception:
        return {}, path


def _identity_conflict(refs, by_ref):
    explicit = set()
    names = []
    for raw in refs or []:
        svc = by_ref.get(channel_registry.canonical_service_ref(raw)) or {}
        name = str(svc.get("name") or "").strip()
        if not name:
            continue
        names.append(name)
        key = canonical_key(name)
        if key.startswith("id:"):
            explicit.add(key)
    return len(explicit) > 1, names


def _sat_label(orbital):
    try:
        x = float(orbital)
        return ("%.1fE" % x) if x >= 0 else ("%.1fW" % abs(x))
    except Exception:
        return "?"


def _service_indexes(services):
    by_key, token_index = {}, {}
    for svc in services or []:
        ref = channel_registry.canonical_service_ref((svc or {}).get("ref"))
        if not ref:
            continue
        for key in identity_keys((svc or {}).get("name")):
            if key:
                by_key.setdefault(key, []).append(svc)
        for token in deterministic_matcher.token_keys((svc or {}).get("name")):
            if token:
                token_index.setdefault(token, []).append(svc)
    return by_key, token_index


def _diagnose_unlinked(item, row, service_by_key, token_index):
    hint = canonical_bridge.classify_hint((row or {}).get("channel_id"), (row or {}).get("display_name"))
    if hint:
        return hint, []
    candidates = _candidate_names(row, item=item)
    exact = {}
    for name in candidates:
        for key in identity_keys(name):
            for svc in service_by_key.get(key) or []:
                try:
                    from . import srp_master_engine
                    if not srp_master_engine._allowed(svc, item, name):
                        continue
                except Exception:
                    pass
                ref = channel_registry.canonical_service_ref((svc or {}).get("ref"))
                if ref:
                    exact[ref] = svc
    if exact:
        rows = list(exact.values())
        return "MATCH FAILED", rows
    try:
        rows, score = deterministic_matcher.unique_soft_matches(candidates, item, token_index)
        if rows and int(score or 0) >= 94:
            return "MATCH FAILED", list(rows)
    except Exception:
        pass
    return "NOT PRESENT", []


def build(config=None, services=None):
    started = time.time()
    epg_dir = _epg_dir(config)
    if services is None:
        services, _ = channel_registry.load_satellite_registry(force=False)
    clean_services = []
    for svc in services or []:
        try:
            orb = float((svc or {}).get("orbital"))
        except Exception:
            continue
        if -30.0 <= orb <= 28.0:
            x = dict(svc)
            x["orbital"] = orb
            clean_services.append(x)
    services = clean_services
    by_ref = {channel_registry.canonical_service_ref(x.get("ref")): x for x in services}
    service_by_key, token_index = _service_indexes(services)
    index = srp_channel_map.load_index() or {}
    sources = {}
    source_rows_by_mid = {}
    global_epg_keys = set()
    global_epg_candidates = {}
    total_ids = linked_ids = total_refs = conflicts_total = 0
    total_match_failed = total_not_present = total_non_tv = total_virtual = total_bad_ids = 0
    mapped_receiver_refs = set()

    # beta42: the audit must reflect exactly what the user selected in Smart
    # Sources. Cached catalogues from previously selected feeds (for example
    # OpenEPG France) must never reappear in the active coverage report.
    prefs = SourcePreferences()
    selected_catalogue_ids = set(prefs.selected_catalogue_ids())
    selected_mapping_ids = set(prefs.selected_mapping_ids())
    by_catalogue = source_catalog.by_catalogue_id()
    items = []
    for sid in prefs.selected_catalogue_ids():
        item = by_catalogue.get(sid) or {}
        provider = str((item or {}).get("provider") or "").upper()
        kind = str((item or {}).get("kind") or "").lower()
        if provider == "OPENEPG" or kind == "local":
            items.append(item)

    # Build the receiver-applicable EPG identity universe once.  This lets the
    # receiver-side audit distinguish "ID exists but map failed" from "no EPG ID".
    for item in items:
        mid = str(source_catalog.mapping_source_id(item) or "")
        if not mid:
            continue
        rows = _source_rows(item, epg_dir)
        source_rows_by_mid[mid] = rows
        for row in rows:
            for name in _candidate_names(row, item=item):
                for key in identity_keys(name):
                    if key:
                        global_epg_keys.add(key)
                        global_epg_candidates.setdefault(key, []).append((item, row, name))

    for item in items:
        provider = str((item or {}).get("provider") or "").upper()
        mid = str(source_catalog.mapping_source_id(item) or "")
        if not mid:
            continue
        rows = source_rows_by_mid.get(mid) or []
        row_by_lower = {str(x.get("channel_id") or "").lower(): x for x in rows if x.get("channel_id")}
        mapping, map_path = _source_mapping(index, mid)
        harvest = source_id_harvester.source_state(item.get("id")) if provider == "OPENEPG" else {}
        catalog_complete = bool(harvest.get("catalog_complete")) if provider == "OPENEPG" else True
        map_by_lower = {str(k).lower(): (k, list(v or [])) for k, v in (mapping or {}).items()}

        linked, conflict_ids = [], []
        buckets = {"MATCH FAILED": [], "NOT PRESENT": [], "NON-TV": [], "ONLINE/VIRTUAL": [], "BAD SOURCE ID": []}
        variant_conflict_details = []
        refs_count = 0
        for low, row in row_by_lower.items():
            cid = str(row.get("channel_id") or "").strip()
            pair = map_by_lower.get(low)
            refs = pair[1] if pair else []
            valid_refs, variant_bad_refs = [], []
            for raw in refs:
                cref = channel_registry.canonical_service_ref(raw)
                svc = by_ref.get(cref) if cref else None
                if not svc:
                    continue
                ok, why = service_variant_guard.check_epg_row(svc, item, row)
                if not ok:
                    variant_bad_refs.append((cref, svc, why))
                    continue
                if cref not in valid_refs:
                    valid_refs.append(cref)
            if valid_refs:
                linked.append(cid)
                refs_count += len(valid_refs)
                mapped_receiver_refs.update(valid_refs)
                bad, _names = _identity_conflict(valid_refs, by_ref)
                if bad or variant_bad_refs:
                    conflict_ids.append(cid)
                if variant_bad_refs:
                    variant_conflict_details.append({
                        "channel_id": cid,
                        "display_name": str(row.get("display_name") or cid),
                        "refs": [
                            {"name": str(x[1].get("name") or ""), "ref": x[0], "reason": x[2]}
                            for x in variant_bad_refs[:12]
                        ],
                    })
                continue
            if variant_bad_refs:
                conflict_ids.append(cid)
                variant_conflict_details.append({
                    "channel_id": cid,
                    "display_name": str(row.get("display_name") or cid),
                    "refs": [
                        {"name": str(x[1].get("name") or ""), "ref": x[0], "reason": x[2]}
                        for x in variant_bad_refs[:12]
                    ],
                })
                # A stale channels.xml link to the wrong programme-feed variant
                # is a semantic conflict, not a successful link and not a generic
                # matcher failure.  Keep it out of mapped_receiver_refs so the
                # receiver-side audit also exposes the affected service.
                continue
            reason, candidates = _diagnose_unlinked(item, row, service_by_key, token_index)
            detail = {"channel_id": cid, "display_name": str(row.get("display_name") or cid)}
            if candidates:
                detail["candidates"] = [
                    {"name": str(x.get("name") or ""), "provider": str(x.get("provider_name") or ""),
                     "sat": _sat_label(x.get("orbital")), "ref": str(x.get("ref") or "")}
                    for x in candidates[:8]
                ]
            buckets.setdefault(reason, []).append(detail)

        match_failed = len(buckets.get("MATCH FAILED") or [])
        not_present = len(buckets.get("NOT PRESENT") or [])
        non_tv = len(buckets.get("NON-TV") or [])
        virtual = len(buckets.get("ONLINE/VIRTUAL") or [])
        bad_ids = len(buckets.get("BAD SOURCE ID") or [])
        # conflict_ids are already part of linked; do not double-count them in
        # the receiver-applicable denominator.  Coverage reports only safe
        # linked IDs as successful.
        conflict_set = set(conflict_ids)
        safe_linked = sum(1 for cid in linked if cid not in conflict_set)
        applicable = safe_linked + match_failed + len(conflict_ids)
        applicable_coverage = round((100.0 * safe_linked / applicable) if applicable else 100.0, 1)

        if conflict_ids:
            status = "CONFLICT"
        elif not rows:
            status = "NO IDS"
        elif provider == "OPENEPG" and not catalog_complete:
            status = "ID CATALOG PARTIAL"
        elif match_failed:
            status = "PARTIAL"
        else:
            status = "RECEIVER COMPLETE"

        row_out = {
            "source_id": mid, "catalogue_id": str(item.get("id") or ""),
            "name": str(item.get("name") or mid), "provider": provider or "EPG MANAGER",
            "total_ids": len(rows), "linked_ids": len(linked),
            "unlinked_ids": max(0, len(rows) - len(linked)), "srp_links": refs_count,
            "conflicts": len(conflict_ids), "match_failed": match_failed,
            "not_present": not_present, "non_tv": non_tv, "online_virtual": virtual,
            "bad_source_ids": bad_ids, "receiver_applicable_ids": applicable,
            "applicable_coverage": applicable_coverage, "status": status,
            "catalog_complete": bool(catalog_complete),
            "header_ids": int(harvest.get("header_channels") or 0),
            "text_ids": int(harvest.get("text_channels") or 0), "map_path": map_path,
            "details": buckets, "conflict_ids": conflict_ids,
            "service_variant_conflicts": len(variant_conflict_details),
            "service_variant_conflict_details": variant_conflict_details,
        }
        sources[mid] = row_out
        total_ids += len(rows); linked_ids += len(linked); total_refs += refs_count
        conflicts_total += len(conflict_ids); total_match_failed += match_failed
        total_not_present += not_present; total_non_tv += non_tv; total_virtual += virtual; total_bad_ids += bad_ids

    # Receiver -> source coverage uses ONLY currently selected source maps.
    # A stale channels.xml from a source deselected yesterday must not inflate
    # today's coverage. Selected Rytec maps are still counted here when present.
    all_mapped_refs = set(mapped_receiver_refs)
    parsed_paths = set()
    for _sid, idx_row in (index.get("sources") or {}).items():
        if str(_sid) not in selected_mapping_ids:
            continue
        path = str((idx_row or {}).get("map_path") or "")
        if not path or path in parsed_paths or not os.path.isfile(path):
            continue
        parsed_paths.add(path)
        try:
            mp = srp_channel_map._parse_channels_xml_preserve(path) or {}
            known_rows = {
                str(x.get("channel_id") or "").lower(): x
                for x in (source_rows_by_mid.get(str(_sid)) or []) if x.get("channel_id")
            }
            item_for_map = None
            for _item in items:
                if str(source_catalog.mapping_source_id(_item) or "") == str(_sid):
                    item_for_map = _item
                    break
            for cid, refs in mp.items():
                known_row = known_rows.get(str(cid or "").lower())
                for raw in refs or []:
                    cref = channel_registry.canonical_service_ref(raw)
                    svc = by_ref.get(cref) if cref else None
                    if not svc:
                        continue
                    if known_row is not None:
                        ok, _why = service_variant_guard.check_epg_row(svc, item_for_map, known_row)
                        if not ok:
                            continue
                    all_mapped_refs.add(cref)
        except Exception:
            pass

    satellites = {}
    receiver_failed_details = {}
    for svc in services:
        sat = _sat_label(svc.get("orbital"))
        out = satellites.setdefault(sat, {"services": 0, "mapped": 0, "match_failed": 0, "no_epg_id": 0})
        out["services"] += 1
        cref = channel_registry.canonical_service_ref(svc.get("ref"))
        if cref in all_mapped_refs:
            out["mapped"] += 1
            continue
        keys = set(identity_keys(svc.get("name")))
        has_compatible_epg = False
        if keys & global_epg_keys:
            for key in keys:
                for item, row, name in global_epg_candidates.get(key) or []:
                    try:
                        from . import srp_master_engine
                        if srp_master_engine._allowed(svc, item, name):
                            ok, _why = service_variant_guard.check_epg_row(svc, item, row)
                            if ok:
                                has_compatible_epg = True
                                break
                    except Exception:
                        continue
                if has_compatible_epg:
                    break
        if has_compatible_epg:
            out["match_failed"] += 1
            if float(svc.get("orbital")) in PRIORITY_SATS:
                receiver_failed_details.setdefault(sat, []).append({
                    "name": str(svc.get("name") or ""), "provider": str(svc.get("provider_name") or ""),
                    "ref": str(svc.get("ref") or ""),
                })
        else:
            out["no_epg_id"] += 1
    for out in satellites.values():
        out["unmapped"] = out["services"] - out["mapped"]
        out["coverage"] = round((100.0 * out["mapped"] / out["services"]) if out["services"] else 0.0, 1)
        capable = out["mapped"] + out["match_failed"]
        out["epg_capable_services"] = capable
        out["epg_capable_coverage"] = round((100.0 * out["mapped"] / capable) if capable else 100.0, 1)

    payload = {
        "version": VERSION, "checked": int(time.time()),
        "active_lamedb": channel_registry.active_lamedb_path(), "sources": sources,
        "selected_catalogue_ids": list(prefs.selected_catalogue_ids()),
        "selected_mapping_ids": list(prefs.selected_mapping_ids()),
        "satellites": satellites, "receiver_match_failed_details": receiver_failed_details,
        "summary": {
            "total_ids": total_ids, "linked_ids": linked_ids,
            "unlinked_ids": max(0, total_ids - linked_ids), "srp_links": total_refs,
            "conflicts": conflicts_total, "match_failed": total_match_failed,
            "not_present": total_not_present, "non_tv": total_non_tv,
            "online_virtual": total_virtual, "bad_source_ids": total_bad_ids,
            "receiver_services": len(services), "receiver_mapped": len(all_mapped_refs),
            "duration_ms": int((time.time() - started) * 1000),
        },
    }
    _write_json(JSON_PATH, payload)

    lines = [
        "EPGManager beta43 Selected-Source ID Coverage Audit",
        "Generated: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "Live lamedb: %s" % payload.get("active_lamedb"),
        "Selected sources: %d" % len(payload.get("selected_catalogue_ids") or []),
        "Scope: CURRENT SMART SOURCES SELECTION ONLY", "",
        "SOURCE -> RECEIVER", "------------------",
    ]
    for mid, row in sorted(sources.items(), key=lambda kv: (kv[1].get("provider"), kv[1].get("name"))):
        catalog_text = "full" if row.get("catalog_complete") else "partial"
        lines.append(
            "[%s] %s | IDs %d | linked %d | match-failed %d | not-present %d | non-tv %d | virtual %d | bad-id %d | SRP %d | conflicts %d | applicable %.1f%% | catalogue %s | %s" % (
                row.get("provider"), row.get("name"), row.get("total_ids"), row.get("linked_ids"),
                row.get("match_failed"), row.get("not_present"), row.get("non_tv"),
                row.get("online_virtual"), row.get("bad_source_ids"), row.get("srp_links"),
                row.get("conflicts"), row.get("applicable_coverage"), catalog_text, row.get("status")))
        for reason in ("MATCH FAILED", "NOT PRESENT", "NON-TV", "ONLINE/VIRTUAL", "BAD SOURCE ID"):
            for detail in (row.get("details") or {}).get(reason) or []:
                lines.append("  %s | %s" % (reason, detail.get("channel_id")))
                for cand in detail.get("candidates") or []:
                    lines.append("    CANDIDATE | %s | %s | %s | %s" % (
                        cand.get("name"), cand.get("provider"), cand.get("sat"), cand.get("ref")))
        for cid in row.get("conflict_ids") or []:
            lines.append("  CONFLICT | %s" % cid)
        for detail in row.get("service_variant_conflict_details") or []:
            for refrow in detail.get("refs") or []:
                lines.append("  SERVICE VARIANT CONFLICT | %s | %s | %s | %s" % (
                    detail.get("channel_id"), refrow.get("name"), refrow.get("ref"), refrow.get("reason")))
        lines.append("")

    lines.extend(["RECEIVER -> EPG", "---------------"])
    for sat, row in sorted(satellites.items()):
        lines.append(
            "%s | services %d | mapped %d | match-failed %d | no-epg-id %d | coverage %.1f%% | EPG-capable %.1f%%" % (
                sat, row.get("services"), row.get("mapped"), row.get("match_failed"),
                row.get("no_epg_id"), row.get("coverage"), row.get("epg_capable_coverage")))
        for detail in receiver_failed_details.get(sat) or []:
            lines.append("  MATCH FAILED RECEIVER | %s | %s | %s" % (
                detail.get("name"), detail.get("provider"), detail.get("ref")))
    parent = os.path.dirname(TEXT_PATH)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = TEXT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, TEXT_PATH)
    return payload


def load():
    return _read_json(JSON_PATH)


def source_stats(source_id):
    return dict(((load().get("sources") or {}).get(str(source_id))) or {})


def summary():
    return dict(load().get("summary") or {})
