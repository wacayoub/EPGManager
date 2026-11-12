# -*- coding: utf-8 -*-
"""One-shot, cache-only SAFE EXACT SRP enrichment for beta126.

This is deliberately O(receiver services + cached channel IDs). It never
performs fuzzy search, never downloads a source and never rebuilds the whole
SRP Master. Existing maps are preserved and only PROVEN/SAFE EXACT claims are
added after the same PrecisionMatch hard guards used by Import All.
"""
from __future__ import print_function

import json
import os
import time

from . import (channel_registry, precision_match_engine, source_catalog,
               source_priority, srp_channel_map, srp_master_engine, programme_feed_policy)
from .logger import get_logger

log = get_logger(__name__)
STATE_PATH = "/etc/enigma2/epgmanager_balanced_exact_state.json"
POLICY = "beta127-native-arabic-coverage-v1"


def _safe(value):
    import re
    return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"


def _atomic_json(path, payload):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _existing_map(index, mid):
    row = ((index or {}).get("sources") or {}).get(str(mid)) or {}
    path = str(row.get("map_path") or "")
    if path and os.path.isfile(path):
        return path, srp_channel_map._parse_channels_xml_preserve(path) or {}
    return "", {}


def _claim_rank(claim):
    grade = 2 if claim.get("grade") == "PROVEN" else 1
    return (-grade, -int(claim.get("confidence") or 0), -int(claim.get("tier") or 0),
            source_catalog.source_sort_key(claim.get("item") or {}),
            str(claim.get("mid") or ""), str(claim.get("cid") or ""))


def _choose_claims(claims_by_ref):
    winners = []
    ambiguous = 0
    suppressed = 0
    for _cref, claims in claims_by_ref.items():
        rows = sorted(list(claims or []), key=_claim_rank)
        if not rows:
            continue
        best = rows[0]
        competitor = None
        for row in rows[1:]:
            if str(row.get("target_key") or "") != str(best.get("target_key") or ""):
                competitor = row
                break
        if competitor is not None:
            bw = 2 if best.get("grade") == "PROVEN" else 1
            cw = 2 if competitor.get("grade") == "PROVEN" else 1
            margin = precision_match_engine.AUTO_MARGIN if bw == 2 else precision_match_engine.SAFE_MARGIN
            if bw == cw and int(best.get("confidence") or 0) - int(competitor.get("confidence") or 0) < int(margin):
                ambiguous += 1
                suppressed += len(rows)
                continue
        winners.append(best)
        suppressed += max(0, len(rows) - 1)
    return winners, ambiguous, suppressed


def run(selected, epg_dir, progress_cb=None):
    started = time.time()
    selected = [dict(x or {}) for x in (selected or []) if x and not bool((x or {}).get("native_srp"))]
    try:
        services, _ = channel_registry.load_satellite_registry(force=False)
    except Exception:
        services = []
    services = [dict(x or {}) for x in (services or []) if (x or {}).get("ref")]
    if not services or not selected:
        return {"ok": True, "added_refs": 0, "sources": 0, "reason": "nothing to enrich"}

    by_key, _by_ref, _token_index = srp_master_engine._service_index(services)
    family_index = {}
    for service in services:
        try:
            fam, _variant = programme_feed_policy._known_family_variant(
                (service or {}).get("clean_name") or (service or {}).get("name") or "")
        except Exception:
            fam = ""
        if fam in programme_feed_policy._LOCAL_ARABIC_FALLBACK_FAMILIES:
            family_index.setdefault(fam, []).append(service)
    claims = {}
    source_rows = {}
    total_rows = 0
    for item in selected:
        try:
            rows = list(srp_master_engine._source_rows(item, epg_dir) or [])
        except Exception:
            rows = []
        if rows:
            mid = str(source_catalog.mapping_source_id(item) or "")
            source_rows[mid] = (item, rows)
            total_rows += len(rows)

    if progress_cb:
        try: progress_cb(2, "Quick exact map: cached channel IDs", "QUICK SAFE MAP")
        except Exception: pass

    done = 0
    for mid, pair in source_rows.items():
        item, rows = pair
        try: tier = int(source_priority.source_tier(item))
        except Exception: tier = 0
        for row in rows:
            done += 1
            cid = str((row or {}).get("channel_id") or "").strip()
            if not cid:
                continue
            names = srp_master_engine._candidate_names(row, (row or {}).get("aliases") or [])
            seen_services = set()
            for name in names:
                service_pool = []
                pool_seen = set()
                for key in srp_master_engine.identity_keys(name):
                    for service in by_key.get(key) or []:
                        cref = channel_registry.canonical_service_ref((service or {}).get("ref")) or str((service or {}).get("ref") or "")
                        if cref and cref not in pool_seen:
                            pool_seen.add(cref); service_pool.append(service)
                # beta127: for a few curated Arabic broadcaster edition families,
                # also consider same-family receiver services. PrecisionMatch still
                # requires the native/local Arabic source before auto-accepting.
                try:
                    fam, _cv = programme_feed_policy._known_family_variant(name)
                except Exception:
                    fam = ""
                if fam in programme_feed_policy._LOCAL_ARABIC_FALLBACK_FAMILIES:
                    for service in family_index.get(fam) or []:
                        cref = channel_registry.canonical_service_ref((service or {}).get("ref")) or str((service or {}).get("ref") or "")
                        if cref and cref not in pool_seen:
                            pool_seen.add(cref); service_pool.append(service)
                for service in service_pool:
                    ref = str((service or {}).get("ref") or "").strip()
                    cref = channel_registry.canonical_service_ref(ref) or ref
                    if not ref or cref in seen_services:
                        continue
                    seen_services.add(cref)
                    try:
                        verdict = precision_match_engine.evaluate(
                            service, item,
                            {"source_id": mid, "channel_id": cid,
                             "display_name": str((row or {}).get("display_name") or cid),
                             "match_name": str(name or "")},
                            balanced=True)
                    except Exception:
                        continue
                    if not verdict.get("auto") or str(verdict.get("grade") or "") not in ("PROVEN", "SAFE"):
                        continue
                    claims.setdefault(cref, []).append({
                        "mid": mid, "cid": cid, "ref": ref, "item": item,
                        "grade": str(verdict.get("grade") or ""),
                        "confidence": int(verdict.get("confidence") or 0),
                        "target_key": str(verdict.get("target_key") or ""),
                        "tier": tier,
                        "display_name": str((row or {}).get("display_name") or cid),
                    })
            if progress_cb and total_rows and done % 400 == 0:
                try:
                    progress_cb(min(80, 2 + int(70.0 * done / total_rows)),
                                "Quick exact map • %d/%d cached IDs" % (done, total_rows),
                                "QUICK SAFE MAP")
                except Exception:
                    pass

    winners, ambiguous, suppressed = _choose_claims(claims)
    by_mid_winners = {}
    for row in winners:
        by_mid_winners.setdefault(row["mid"], {}).setdefault(row["cid"], []).append(row["ref"])

    index = srp_channel_map.load_index() or {}
    index_sources = dict(index.get("sources") or {})
    sat_sig = channel_registry.satellite_registry_signature()
    map_dir = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
    if not os.path.isdir(map_dir):
        os.makedirs(map_dir)

    added_refs = 0
    changed_sources = 0
    for mid, mapping_add in by_mid_winners.items():
        item = source_rows.get(mid, ({}, []))[0]
        path, current = _existing_map(index, mid)
        if not path:
            path = os.path.join(map_dir, "%s.channels.xml" % _safe(mid))
        changed = False
        for cid, refs in mapping_add.items():
            bucket = current.setdefault(str(cid), [])
            for ref in refs or []:
                if ref and ref not in bucket:
                    bucket.append(ref); added_refs += 1; changed = True
        if not changed and os.path.isfile(path):
            continue
        srp_channel_map.write_channels_xml(current, path)
        refs_count = sum(len(v or []) for v in current.values())
        old = dict(index_sources.get(mid) or {})
        old.update({
            "source_id": mid, "map_path": path, "provider": str(item.get("provider") or old.get("provider") or ""),
            "official": False, "channels": len(current), "refs": refs_count,
            "countries": list(item.get("countries") or old.get("countries") or []),
            "name": str(item.get("name") or old.get("name") or mid),
            "mapping_mode": "native_arabic_coverage_v1", "sat_signature": sat_sig,
        })
        index_sources[mid] = old
        changed_sources += 1

    if changed_sources:
        payload = dict(index)
        payload["version"] = max(13, int(index.get("version") or 0))
        payload["timestamp"] = int(time.time())
        payload["sources"] = index_sources
        _atomic_json(srp_channel_map.INDEX_PATH, payload)

    report = {
        "ok": True, "policy": POLICY, "checked": int(time.time()),
        "sources": len(source_rows), "cached_ids": total_rows,
        "claims": sum(len(x) for x in claims.values()), "winners": len(winners),
        "added_refs": added_refs, "changed_sources": changed_sources,
        "ambiguous_refs": ambiguous, "suppressed_claims": suppressed,
        "elapsed": round(time.time() - started, 3),
    }
    try: _atomic_json(STATE_PATH, report)
    except Exception: pass
    if progress_cb:
        try: progress_cb(82, "Quick exact map ready • +%d safe refs" % added_refs, "QUICK SAFE MAP")
        except Exception: pass
    return report
