# -*- coding: utf-8 -*-
"""Build Smart Mapping display ownership off the Enigma2 UI thread.

The beta121 semantic firewall is preserved, but validating thousands of SRP rows
must not block screen opening.  This module returns plain Python structures that
can be swapped into the UI in O(1) time once the worker finishes.
"""
from __future__ import print_function
from . import channel_registry, source_catalog, service_variant_guard, programme_feed_policy, precision_match_engine, deterministic_matcher


def _canon(ref):
    raw=str(ref or "").strip()
    if not raw: return ""
    try: return channel_registry.canonical_service_ref(raw) or raw
    except Exception: return raw


def build(catalog, srp_state, selected_mids, mapping_rows, blocked_refs):
    catalog=list(catalog or [])
    selected=set(str(x or "") for x in (selected_mids or []))
    mappings=dict(mapping_rows or {})
    blocked_raw=set(str(x or "").strip() for x in (blocked_refs or []) if str(x or "").strip())
    blocked=set(blocked_raw)
    blocked.update(_canon(x) for x in blocked_raw)

    mapped_epg_keys=set(); mapped_refs=set(); mapped_info={}
    raw_by_canon={}; service_by_canon={}
    for service in catalog:
        raw=str((service or {}).get("ref") or "").strip(); canon=_canon(raw)
        if raw and canon:
            raw_by_canon[canon]=raw; service_by_canon[canon]=service

    try: source_by_mid=source_catalog.by_mapping_id() or {}
    except Exception: source_by_mid={}

    # Signature sets avoid repeatedly scanning each ref's list.
    info_sigs={}
    def add_ref(ref, info):
        raw=str(ref or "").strip(); canon=_canon(raw); actual=raw_by_canon.get(canon) or raw
        sig=(info.get("source_id"),info.get("channel_id"),info.get("mode"))
        for key in (raw,canon,actual):
            if not key: continue
            mapped_refs.add(key)
            ss=info_sigs.setdefault(key,set())
            if sig in ss: continue
            ss.add(sig); mapped_info.setdefault(key,[]).append(dict(info))

    # Many SRP aliases/duplicate DVB services repeat the same candidate. Cache
    # the PrecisionMatch verdict inside this one build so a cold validation does
    # not recompute the same normalization/provider/feed evidence thousands of
    # times.
    allowed_cache={}
    def allowed(canon_ref, sid, cid, display):
        key=(str(canon_ref or ""),str(sid or ""),str(cid or ""),str(display or cid or ""))
        if key in allowed_cache:
            return allowed_cache[key]
        service=service_by_canon.get(canon_ref) or {}
        src=source_by_mid.get(str(sid or ""))
        if not service or not src:
            allowed_cache[key]=False
            return False
        try:
            verdict=precision_match_engine.evaluate(
                service,src,{"source_id":str(sid or ""),"channel_id":cid,"display_name":display or cid}, balanced=True)
            value=bool(verdict.get("auto"))
        except Exception:
            value=False
        allowed_cache[key]=value
        return value

    def hard_semantic_ok(canon_ref, cid, display, sid=""):
        """Manual source choice may use a native/local Arabic edition fallback."""
        service=service_by_canon.get(canon_ref) or {}
        src=source_by_mid.get(str(sid or "")) or {}
        if not service:
            return False
        try:
            ok, _reason, local_fb = programme_feed_policy.feed_compatible_with_source(
                service, cid, display or cid, source=src)
            if not ok:
                return False
            sname=str(service.get("clean_name") or service.get("name") or "")
            if local_fb:
                if not programme_feed_policy._same_family_presentation_safe(sname, display or cid):
                    return False
            elif not deterministic_matcher.compatible_variant(sname, display or cid):
                return False
        except Exception:
            return False
        return True

    manual_owner={}
    for key,saved in mappings.items():
        if not saved or not saved.get("refs"): continue
        try: sid,cid=key.split("::",1)
        except ValueError: sid,cid="",key
        mode=str(saved.get("mode") or "manual")
        info={"source_id":sid,"source_name":sid,"channel_id":cid,
              "display_name":saved.get("display_name") or cid,"mode":mode}
        is_manual=mode.lower()=="manual"; accepted=False
        for ref in saved.get("refs") or []:
            raw=str(ref or "").strip(); canon=_canon(raw)
            if is_manual:
                # Manual source choice remains authoritative, but absolute feed
                # boundaries (2M National/Monde, Al Aoula Inter/base, +1, etc.)
                # can never be overridden by stale legacy rows.
                if not hard_semantic_ok(canon,cid,info["display_name"],sid):
                    continue
                if raw: manual_owner.setdefault(raw,set()).add(str(sid).lower())
                if canon: manual_owner.setdefault(canon,set()).add(str(sid).lower())
            elif not allowed(canon,sid,cid,info["display_name"]):
                continue
            add_ref(ref,info); accepted=True
        if accepted: mapped_epg_keys.add(str(key))

    service_map=(srp_state or {}).get("service_map") or {}
    # SRP Master v8 already runs service-variant, programme-feed AND
    # PrecisionMatch safety before a ref is written to ``service_map``. Re-running
    # the same PrecisionMatch evaluation here for ~1k receiver refs can cost
    # minutes on a Zero 4K. Trust current v8+ state for display ownership; keep
    # the old validator only for legacy state files.
    try:
        trusted_srp = int((srp_state or {}).get("version") or 0) >= 8
    except Exception:
        trusted_srp = False
    for cref,infos in service_map.items():
        raw_ref=str(cref or "").strip(); canon_ref=_canon(raw_ref)
        if raw_ref in blocked or canon_ref in blocked: continue
        # Never paint a stale SRP ref that is no longer present in the live SAT
        # registry, even when the state version itself is trusted.
        if trusted_srp and canon_ref not in service_by_canon:
            continue
        owners=manual_owner.get(raw_ref) or manual_owner.get(canon_ref) or set()
        for row in infos or []:
            sid=str((row or {}).get("source_id") or "")
            if sid not in selected: continue
            if owners and sid.lower() not in owners: continue
            cid=str((row or {}).get("channel_id") or "")
            display=str((row or {}).get("display_name") or cid)
            if not trusted_srp and not allowed(canon_ref,sid,cid,display): continue
            info={"source_id":sid,"source_name":str((row or {}).get("source_name") or sid),
                  "channel_id":cid,"display_name":display,
                  "mode":str((row or {}).get("method") or "srp")}
            if sid and cid: mapped_epg_keys.add((sid+"::"+cid).lower())
            add_ref(cref,info)

    return {"mapped_epg_keys":mapped_epg_keys,"mapped_refs":mapped_refs,
            "mapped_info_by_ref":mapped_info}
