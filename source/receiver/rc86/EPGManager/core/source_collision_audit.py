# -*- coding: utf-8 -*-
"""Offline duplicate XMLTV Channel-ID inspector for selected Smart Sources.

The audit is intentionally cache-only: it reads the tiny per-source channel-ID
shards already used by Smart Mapping and never downloads/parses programme XML.
It helps diagnose the exact class of overwrite problem seen with parallel AR/EN
provider feeds where the same XMLTV ID exists in more than one selected source.
"""
from __future__ import print_function

from . import source_catalog, source_channel_cache, source_variant_policy


def _source_row(source_id):
    sid = str(source_id or "")
    item = source_catalog.by_mapping_id().get(sid) or source_catalog.by_catalogue_id().get(sid) or {}
    return {
        "source_id": sid,
        "name": str(item.get("name") or item.get("source_name") or sid),
        "provider": str(item.get("provider") or ""),
        "language": str(item.get("language") or ""),
    }


def scan(source_ids, max_groups=200):
    ids = source_variant_policy.normalize_selected([str(x or "") for x in (source_ids or []) if str(x or "")])
    ownership = {}
    missing = []
    scanned = 0
    total_rows = 0
    for sid in ids:
        try:
            rows = source_channel_cache.get(sid) or []
        except Exception:
            rows = []
        if not rows:
            try:
                rows = source_catalog.local_channel_hints(sid) or []
            except Exception:
                rows = []
        if not rows:
            missing.append(sid)
            continue
        scanned += 1
        source = _source_row(sid)
        seen = set()
        for row in rows:
            cid = str((row or {}).get("channel_id") or "").strip()
            key = cid.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            total_rows += 1
            ownership.setdefault(key, {"channel_id": cid, "owners": []})["owners"].append(source)

    collisions = []
    known_parallel = 0
    high_risk = 0
    for group in ownership.values():
        owners = group.get("owners") or []
        unique_ids = sorted(set(str(x.get("source_id") or "") for x in owners if x.get("source_id")))
        if len(unique_ids) < 2:
            continue
        pairs = set()
        for sid in unique_ids:
            sib = source_variant_policy.sibling(sid)
            if sib and sib in unique_ids:
                pairs.add(tuple(sorted((sid, sib))))
        is_parallel = bool(pairs)
        if is_parallel:
            known_parallel += 1
        else:
            high_risk += 1
        collisions.append({
            "channel_id": group.get("channel_id") or "",
            "owners": owners,
            "known_parallel_language": is_parallel,
        })

    collisions.sort(key=lambda x: (0 if not x.get("known_parallel_language") else 1,
                                   str(x.get("channel_id") or "").casefold()))
    if max_groups:
        collisions = collisions[:int(max_groups)]
    return {
        "selected": len(ids),
        "scanned": scanned,
        "channel_rows": total_rows,
        "collisions": collisions,
        "collision_count": high_risk + known_parallel,
        "high_risk": high_risk,
        "known_parallel": known_parallel,
        "missing": missing,
    }
