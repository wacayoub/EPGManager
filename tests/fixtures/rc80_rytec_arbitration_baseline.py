def _fast_single_owner_maps(items, parsed, registry_index, store=None, store_rows=None):
    """Keep one exact source+XMLTV owner per receiver service, CPU-bounded.

    RC20 removes the RC19 quadratic persisted-owner fallback and avoids running
    source-priority scoring for services that already have a valid persisted
    MANUAL/Auto Repair/SmartMatch target.  With mapping health around 97%, this
    eliminates almost all expensive ranking work while preserving identical
    ownership semantics.
    """
    rows = list(items or [])
    item_by_mid = {}
    for item in rows:
        mid = str(source_catalog.mapping_source_id(item) or "").lower()
        if mid:
            item_by_mid[mid] = item
    receiver_by_ref = (registry_index or ({}, {}))[0] or {}
    rank_cache = {}

    def owner_score(mid, cref):
        key = (str(mid or "").lower(), cref)
        cached = rank_cache.get(key)
        if cached is not None:
            return cached
        item = item_by_mid.get(key[0]) or {}
        service = receiver_by_ref.get(cref) or {}
        try:
            value = source_priority.ownership_rank(item, service)
        except Exception:
            value = (-1, ())
        rank_cache[key] = value
        return value

    # rc37: within one provider/source, prefer the XMLTV identity that best
    # matches the receiver service.  This prevents generic catch-all IDs such as
    # beINSports.qa@MENA from stealing beIN SPORTS 1/2/3 services.
    display_index = {}
    for mid, item in item_by_mid.items():
        sid = str((item or {}).get("id") or "")
        try:
            for row in source_channel_cache.get(sid) or []:
                cid = str((row or {}).get("channel_id") or "").strip().lower()
                if cid:
                    display_index[(mid, cid)] = str((row or {}).get("display_name") or cid)
        except Exception:
            pass
    claim_rank_cache = {}
    def claim_score(mid, cid, cref):
        key = (str(mid or "").lower(), str(cid or "").lower(), cref)
        cached = claim_rank_cache.get(key)
        if cached is not None:
            return cached
        service = receiver_by_ref.get(cref) or {}
        service_name = str(service.get("clean_name") or service.get("name") or "")
        label = display_index.get((key[0], key[1])) or str(cid or "")
        try:
            identity_score = int(channel_mapper._token_score(label, service_name) or 0)
        except Exception:
            identity_score = 0
        value = (owner_score(key[0], cref), identity_score)
        claim_rank_cache[key] = value
        return value

    mapping_rows = store_rows if isinstance(store_rows, dict) else _store_rows(store)
    persisted = _persisted_ref_targets(store, store_rows=mapping_rows)
    # Canonical lookup is enough: _persisted_ref_targets stores both raw and
    # canonical keys, so RC19's per-service full scan of every persisted row was
    # redundant and could become O(receiver_refs * mappings).
    persisted_exact = {}
    for ref_key, candidates in (persisted or {}).items():
        cref = _canon_ref(ref_key)
        bucket = persisted_exact.setdefault(cref, {})
        for cand in candidates or []:
            sid = str((cand or {}).get("source_id") or "").lower()
            cid = str((cand or {}).get("channel_id") or "").lower()
            if not sid or not cid:
                continue
            sig = (sid, cid)
            prev = bucket.get(sig)
            if prev is None or _persisted_target_rank(cand, claim_score(sid, cid, cref)) > \
                    _persisted_target_rank(prev, claim_score(sid, cid, cref)):
                bucket[sig] = cand

    auto_winner = {}
    locked_target = {}
    unresolved_persisted = set()
    claims = 0

    # Pass 1: validate exact persisted targets with dictionary lookups. Services
    # with no persisted candidates can choose their fallback winner immediately.
    for mid, channels in (parsed or {}).items():
        lmid = str(mid or "").lower()
        for cid, refs in (channels or {}).items():
            lcid = str(cid or "").lower()
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                claims += 1
                cref = _canon_ref(raw)
                exact = persisted_exact.get(cref)
                if exact:
                    cand = exact.get((lmid, lcid))
                    if cand is not None:
                        prev_target = locked_target.get(cref)
                        if prev_target is None:
                            locked_target[cref] = (lmid, lcid, cand)
                        else:
                            prev = prev_target[2]
                            if _persisted_target_rank(cand, claim_score(lmid, lcid, cref)) > \
                                    _persisted_target_rank(prev, claim_score(prev_target[0], prev_target[1], cref)):
                                locked_target[cref] = (lmid, lcid, cand)
                    else:
                        unresolved_persisted.add(cref)
                    continue
                prev = auto_winner.get(cref)
                if prev is None or claim_score(lmid, lcid, cref) > claim_score(prev[0], prev[1], cref):
                    auto_winner[cref] = (lmid, lcid)

    # Persisted candidates can all be stale/disabled. Rank only that small subset
    # as fallback instead of scoring every claim up front.
    need_fallback = set(cref for cref in unresolved_persisted if cref not in locked_target)
    if need_fallback:
        for mid, channels in (parsed or {}).items():
            lmid = str(mid or "").lower()
            for cid, refs in (channels or {}).items():
                lcid = str(cid or "").lower()
                for ref in refs or []:
                    raw = str(ref or "").strip()
                    if not raw:
                        continue
                    cref = _canon_ref(raw)
                    if cref not in need_fallback:
                        continue
                    prev = auto_winner.get(cref)
                    if prev is None or claim_score(lmid, lcid, cref) > claim_score(prev[0], prev[1], cref):
                        auto_winner[cref] = (lmid, lcid)

    locked_simple = dict((cref, (row[0], row[1])) for cref, row in locked_target.items())
    out = {}
    suppressed = 0
    kept_refs = set()
    for mid, channels in (parsed or {}).items():
        lmid = str(mid or "").lower()
        dst = {}
        for cid, refs in (channels or {}).items():
            lcid = str(cid or "").lower()
            keep = []
            seen = set()
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                cref = _canon_ref(raw)
                target = locked_simple.get(cref) or auto_winner.get(cref)
                if target != (lmid, lcid):
                    suppressed += 1
                    continue
                if raw not in seen:
                    seen.add(raw)
                    keep.append(raw)
                    kept_refs.add(cref)
            if keep:
                dst[str(cid)] = keep
        if dst:
            out[lmid] = dst
    return out, {
        "ownership_claims": int(claims),
        "single_owner_services": int(len(kept_refs)),
        "persisted_locked_services": int(len(set(cref for cref in kept_refs if cref in locked_simple))),
        "suppressed_duplicate_refs": int(suppressed),
        "rank_cache_entries": int(len(rank_cache)),
        "stale_persisted_fallback_services": int(len(need_fallback)),
    }
