# -*- coding: utf-8 -*-
"""EPGManager beta41 deterministic SRP Master Engine.

Builds Rytec-style XMLTV-id -> Enigma2 Service Reference maps for OpenEPG and
EPGManager-local sources.  The receiver's real SAT registry/lamedb is the only
source of automatically generated DVB SRPs: this module never fabricates
SID/TSID/ONID/namespace values.

The engine is deliberately outside the frozen beta1/beta12 mapper files.  It
writes the same *.channels.xml contract consumed by Native EPGImport and updates
only the derived /etc/enigma2/epgmanager_srp_maps.json index.
"""
from __future__ import print_function

import hashlib
import json
import os
import re
import threading
import time

from .logger import get_logger
from . import channel_registry, channel_mapper, source_catalog, source_channel_cache, source_priority
from . import srp_channel_map, deterministic_matcher, canonical_bridge, service_variant_guard, programme_feed_policy, precision_match_engine
from .channel_identity_resolver import keys as identity_keys, label_from_id, resolve as resolve_identity
from .mapping_store import MappingStore

log = get_logger(__name__)

STATE_PATH = "/etc/enigma2/epgmanager_srp_master.json"
VERSION = 11
VERIFY_DIR = "/etc/epgimport"
OPENEPG_VERIFY_PATH = os.path.join(VERIFY_DIR, "epgmanager-openepg.channels.xml")
LOCAL_VERIFY_PATH = os.path.join(VERIFY_DIR, "epgmanager-local.channels.xml")
REPORT_PATH = os.path.join(VERIFY_DIR, "epgmanager-srp-report.txt")
SAT_MIN = -31.5
SAT_MAX = 31.5

_BUILD_LOCK = threading.Lock()
_MEM_LOCK = threading.RLock()
_ASYNC_RUNNING = False

_BILINGUAL_PAIRS = {
    "ext_openepg_egypt_1": "ext_openepg_egypt_2",
    "ext_openepg_saudi_arabia_1": "ext_openepg_saudi_arabia_2",
    "ext_openepg_saudi_arabia_3": "ext_openepg_saudi_arabia_4",
    "ext_openepg_saudi_arabia_5": "ext_openepg_saudi_arabia_6",
    "ext_openepg_qatar_4": "ext_openepg_qatar_5",
}


def _safe(value):
    return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_") or "source"


def _sig(path):
    try:
        st = os.stat(path)
        return [int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size)]
    except Exception:
        return []


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


def _epg_dir(config=None, epg_dir=None):
    if epg_dir:
        return str(epg_dir)
    try:
        if config is not None:
            return str(config.get_epg_output_dir())
    except Exception:
        pass
    return channel_mapper.EPG_DIR


def _registry_services(force=False):
    rows, _rebuilt = channel_registry.load_satellite_registry(force=force)
    out = []
    for service in rows or []:
        try:
            orbital = float(service.get("orbital"))
        except Exception:
            continue
        if orbital < SAT_MIN or orbital > SAT_MAX:
            continue
        ref = str(service.get("ref") or "").strip()
        if not ref:
            continue
        x = dict(service)
        x["orbital"] = orbital
        out.append(x)
    return out


def _service_index(services):
    by_key = {}
    by_ref = {}
    token_index = {}
    for service in services or []:
        ref = channel_registry.canonical_service_ref(service.get("ref"))
        if not ref:
            continue
        by_ref[ref] = service
        for key in identity_keys(service.get("name")):
            bucket = by_key.setdefault(key, [])
            if all(channel_registry.canonical_service_ref(x.get("ref")) != ref for x in bucket):
                bucket.append(service)
        for token in deterministic_matcher.token_keys(service.get("name")):
            bucket = token_index.setdefault(token, [])
            if all(channel_registry.canonical_service_ref(x.get("ref")) != ref for x in bucket):
                bucket.append(service)
    return by_key, by_ref, token_index


def _merge_rows(*groups):
    out = []
    seen = set()
    for rows in groups:
        for row in rows or []:
            cid = str((row or {}).get("channel_id") or "").strip()
            if not cid or cid.lower() in seen:
                continue
            seen.add(cid.lower())
            out.append({"channel_id": cid, "display_name": str((row or {}).get("display_name") or cid)})
    return out


def _local_rows(item, epg_dir):
    mid = source_catalog.mapping_source_id(item)
    hints = source_catalog.local_channel_hints(mid)
    dynamic = []
    filename = str((item or {}).get("output_file") or "")
    path = os.path.join(epg_dir, filename) if filename else ""
    try:
        if path and os.path.isfile(path) and os.path.getsize(path) > 32:
            dynamic = channel_mapper.read_xmltv_channel_ids_fast(path, mid, item.get("name") or mid)
    except Exception:
        dynamic = []
    # Real generated IDs win because local generators such as beIN discover IDs
    # dynamically. Static hints fill only IDs not present in the XML header.
    return _merge_rows(dynamic, hints), path


def _source_rows(item, epg_dir):
    if str((item or {}).get("kind") or "").lower() == "local":
        rows = _local_rows(item, epg_dir)[0]
    else:
        rows = list(source_channel_cache.get(item.get("id")) or [])

    # rc39 Direct-ID Health Gate: GitHub monitoring is authoritative only for
    # IDs it has actually measured.  Exclude an ID from automatic SRP ownership
    # only when the monitor proves it has ZERO future programmes.  UNKNOWN or
    # short-horizon IDs stay available, so a temporary monitoring outage can
    # never erase working mappings.
    try:
        from . import github_direct_sync
        sid = str((item or {}).get("id") or "")
        if sid in github_direct_sync.DIRECT_IDS:
            rows = github_direct_sync.filter_usable_rows(sid, rows)
    except Exception:
        pass
    return rows


def _companion_aliases(item, rows):
    """Return only deterministic aliases from a parallel OpenEPG catalogue.

    Never pair Arabic/English feeds by row position.  Parallel catalogues can
    be sorted differently, so line N == line N is not trustworthy.  A bridge
    is accepted only when both rows already share an exact normalized/canonical
    identity key (for example IDs differing only by AR/EN decoration).
    """
    sid = str((item or {}).get("id") or "")
    companion = _BILINGUAL_PAIRS.get(sid)
    if not companion:
        return {}
    try:
        other = list(source_channel_cache.get(companion) or [])
    except Exception:
        other = []
    if not rows or not other:
        return {}

    other_by_key = {}
    for row in other:
        vals = (str((row or {}).get("channel_id") or ""),
                str((row or {}).get("display_name") or ""),
                label_from_id((row or {}).get("channel_id")))
        keys = set()
        for value in vals:
            keys.update(identity_keys(value))
        for key in keys:
            if key:
                other_by_key.setdefault(key, []).append(row)

    aliases = {}
    for row in rows:
        cid = str((row or {}).get("channel_id") or "").strip()
        if not cid:
            continue
        vals = (cid, str((row or {}).get("display_name") or ""), label_from_id(cid))
        candidates = {}
        for value in vals:
            for key in identity_keys(value):
                for other_row in other_by_key.get(key) or []:
                    ocid = str((other_row or {}).get("channel_id") or "").strip()
                    if ocid:
                        candidates[ocid.lower()] = other_row
        # Only one companion identity is safe. Multiple candidates are an
        # explicit ambiguity and must remain unmapped for manual review.
        if len(candidates) != 1:
            continue
        other_row = list(candidates.values())[0]
        alias = str((other_row or {}).get("display_name") or label_from_id((other_row or {}).get("channel_id")) or "").strip()
        if alias:
            aliases.setdefault(cid.lower(), []).append(alias)
    return aliases

def _candidate_names(row, extra_aliases=None):
    out = []
    cid = str((row or {}).get("channel_id") or "")
    override = canonical_bridge.source_override(cid)
    if override:
        # A full XMLTV-ID correction is authoritative.  Do not also feed the
        # stripped/raw ID into the generic matcher (MEDI1TV_AR.ma would
        # otherwise collapse to the generic "MEDI1TV" identity).
        return [override]
    for raw in (str((row or {}).get("display_name") or ""),
                label_from_id(cid),
                cid):
        if raw and raw not in out:
            out.append(raw)
    for raw in extra_aliases or []:
        if raw and raw not in out:
            out.append(raw)
    # beta41 deterministic language/presentation bridge.  This resolves only
    # channel identity labels (Arabic/Latin and logo-filename decorations); the
    # actual SRP is still copied from the live receiver lamedb.
    for raw in canonical_bridge.aliases((row or {}).get("channel_id"), (row or {}).get("display_name")):
        if raw and raw not in out:
            out.append(raw)
    return out


def _local_special_aliases(item, row):
    """Recover broadcaster identity hidden inside dynamic local XMLTV IDs.

    The local beIN scraper uses logo filenames as XMLTV channel IDs. Some logo
    names are excellent (``beIN_SPORTS_1_*``), while others are presentation
    shorthand such as ``NEWS_*`` or ``4k_*``. Convert only these documented
    beIN patterns to conservative service names before matching against lamedb.
    The final Service Reference still always comes from the real lamedb.
    """
    try:
        mid = str(source_catalog.mapping_source_id(item) or "").lower()
    except Exception:
        mid = ""
    raw = "%s %s" % (str((row or {}).get("channel_id") or ""),
                       str((row or {}).get("display_name") or ""))
    c = re.sub(r"[^a-z0-9]+", "", raw.lower())
    out = []
    def add(value):
        if value and value not in out:
            out.append(value)

    if mid != "bein_sports":
        return out
    if "news" in c:
        add("beIN Sports News")
    if "4k" in c or "uhd" in c:
        add("beIN Sports 4K")
    m = re.search(r"xtra(?:digital)?0*(\d{1,2})", c)
    if m:
        add("beIN Sports XTRA %d" % int(m.group(1)))
    m = re.search(r"max0*(\d{1,2})", c)
    if m:
        add("beIN Sports MAX %d" % int(m.group(1)))
    m = re.search(r"beinsports0*(\d{1,2})", c)
    if m and "xtra" not in c and "max" not in c:
        add("beIN Sports %d" % int(m.group(1)))
    if "beinsportsfta" in c or ("fta" in c and "sports" in c):
        add("beIN Sports")
    return out


def _allowed(service, item, candidate_name, row=None):
    """Validate one identity match without treating a feed as a country list.

    OpenEPG country feeds contain international channels (for example Tunisia 1
    inside Egypt1).  beta37 applied the *container* country gate before looking
    at the channel identity, which could reject a correct SRP.  beta38 lets a
    known canonical channel country override the container only for validation;
    the actual source/feed remains unchanged.
    """
    # beta113: broadcaster feed variants are identity, not presentation.
    # Al Aoula Inter/International and Al Aoula Laayoune do not share the
    # domestic AlAoula programme grid even though the legacy canonical identity
    # is the same. Prefer unmapped over a confidently wrong EPG assignment.
    try:
        actual = row or {}
        variant_ok, _variant_reason = service_variant_guard.check(
            service, source=item,
            candidate_name=actual.get("channel_id") or candidate_name,
            display_name=actual.get("display_name") or actual.get("channel_id") or candidate_name)
        if not variant_ok:
            return False
    except Exception:
        return False

    # EPGManager local beIN is explicitly the MENA guide. Do not let generic
    # beIN names bind to France/Europe services on Astra/Hotbird.
    try:
        if str(source_catalog.mapping_source_id(item) or "").lower() == "bein_sports":
            orbital = float((service or {}).get("orbital"))
            if not ((-9.5 <= orbital <= -6.0) or (24.0 <= orbital <= 26.8)):
                return False
            sname = " %s " % str((service or {}).get("name") or "").lower()
            cname = " %s " % str(candidate_name or "").lower()
            # Local beIN source is Arabic/MENA only. Plain "beIN SPORTS 1"
            # is allowed; explicit FR/FRENCH and EN/ENGLISH variants are not.
            if re.search(r"\b(?:fr|french|en|english)\b", sname, flags=re.I):
                return False
            if ("4k" in cname) != ("4k" in sname):
                return False
    except Exception:
        pass

    identity = resolve_identity(candidate_name) or {}
    cid = str(identity.get("id") or "")
    service_identity = resolve_identity((service or {}).get("name")) or {}
    if cid and service_identity and str(service_identity.get("id") or "") != cid:
        actual = row or {}
        try:
            local_fb, _fb_reason = programme_feed_policy.local_arabic_feed_fallback(
                service, actual.get("channel_id") or candidate_name,
                actual.get("display_name") or candidate_name, source=item, epg=actual)
        except Exception:
            local_fb = False
        if not local_fb:
            return False

    gate_item = item
    own_country = str(identity.get("country") or "").upper().strip()
    own_countries = [str(x).upper() for x in (identity.get("countries") or []) if x]
    if own_country and own_country not in own_countries:
        own_countries.append(own_country)

    # beta121: do not disguise a foreign *source container* as the channel's
    # native country for country-locked public/national broadcasters.  That old
    # convenience is exactly how 2M inside Egypt 1 and Al Aoula International
    # inside Belgium could become valid-looking automatic SRPs. For ordinary
    # international channels we retain the historical container override.
    try:
        native_lock = str(programme_feed_policy.locked_home_country(service) or "").upper()
    except Exception:
        native_lock = ""
    if own_countries and not native_lock:
        gate_item = dict(item or {})
        gate_item["countries"] = own_countries
        if set(own_countries) & set(("AE", "SA", "EG", "QA", "MA", "PS", "TN", "DZ", "IQ")):
            gate_item["region"] = "MENA"

    try:
        actual = row or {}
        route_ok, _route_score, _route_reason = programme_feed_policy.route(
            service, gate_item, {"display_name": actual.get("display_name") or candidate_name,
                                 "channel_id": actual.get("channel_id") or candidate_name})
        if not route_ok:
            return False
    except Exception:
        return False

    # Deterministic provider/orbital firewall.  No AI/learning is involved.
    # Known channel identity beats the country label of the OpenEPG container
    # (for example Tunisia 1 may legitimately be carried inside Egypt1).
    try:
        if not deterministic_matcher.provider_satellite_allowed(service, gate_item):
            return False
    except Exception:
        return False

    # beta124 PrecisionMatch: every generated SRP must pass the same final
    # multi-proof gate as Smart Mapping.  Crucially use the ORIGINAL source
    # item here, not gate_item whose country may have been rewritten for legacy
    # international-channel compatibility.  This prevents container-country
    # laundering (2M->Egypt, Al Aoula->Belgium, Al Ahly->UAE).
    try:
        actual = dict(row or {})
        actual["match_name"] = str(candidate_name or actual.get("display_name") or actual.get("channel_id") or "")
        actual.setdefault("source_id", str(source_catalog.mapping_source_id(item) or ""))
        text_score = deterministic_matcher.score(
            str((service or {}).get("clean_name") or (service or {}).get("name") or ""),
            str(candidate_name or actual.get("display_name") or actual.get("channel_id") or ""))
        verdict = precision_match_engine.evaluate(
            service, item, actual, text_score=text_score,
            native_proof=bool((item or {}).get("native_srp")), balanced=True)
        if not verdict.get("auto"):
            return False
    except Exception:
        return False
    return True


def _match_row(item, row, aliases, by_key, token_index):
    matches = {}
    method = ""
    confidence = 0
    extra = list(aliases or []) + _local_special_aliases(item, row)
    candidates = _candidate_names(row, extra)
    for name in candidates:
        ident = resolve_identity(name)
        name_method = "canonical-alias" if ident else "exact-normalized"
        name_conf = 99 if ident else 100
        for key in identity_keys(name):
            for service in by_key.get(key) or []:
                if not _allowed(service, item, name, row=row):
                    continue
                ref = str(service.get("ref") or "").strip()
                cref = channel_registry.canonical_service_ref(ref)
                if not cref:
                    continue
                matches[cref] = service
                if name_conf > confidence:
                    confidence = name_conf
                    method = name_method
    if matches:
        return list(matches.values()), method or "exact-normalized", confidence or 100

    # Conservative final fallback for punctuation/spelling decorations not
    # covered by exact normalization or the validated alias dictionary.
    # It accepts only one unique high-confidence candidate and preserves channel
    # numbers, +1 and 4K variants.
    services, score = deterministic_matcher.unique_soft_matches(candidates, item, token_index)
    services = [svc for svc in (services or [])
                if _allowed(svc, item, str((row or {}).get("display_name") or ""), row=row)]
    if services and score >= 94:
        return services, "deterministic-soft", int(score)
    return [], "unmatched", 0


def _merge_mapping(dst, mapping):
    for cid, refs in (mapping or {}).items():
        bucket = dst.setdefault(str(cid), [])
        for ref in refs or []:
            if ref and ref not in bucket:
                bucket.append(ref)


def export_verification_files(config=None):
    """Publish human-verifiable Rytec-style family maps under /etc/epgimport."""
    try:
        if not os.path.isdir(VERIFY_DIR):
            os.makedirs(VERIFY_DIR)
        index = srp_channel_map.load_index() or {}
        by_mid = source_catalog.by_mapping_id()
        from .source_preferences import SourcePreferences
        prefs = SourcePreferences()
        selected_mids = set(prefs.selected_mapping_ids())
        families = {"OPENEPG": {}, "LOCAL": {}}
        report = ["EPGManager SRP verification (selected sources only)",
                  "Generated: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                  "Selected sources: %d" % len(prefs.selected_catalogue_ids()), ""]
        services = _registry_services(force=False)
        by_ref = {channel_registry.canonical_service_ref(x.get("ref")): x for x in services}
        for mid, row in sorted((index.get("sources") or {}).items()):
            if str(mid) not in selected_mids:
                continue
            item = by_mid.get(str(mid).lower()) or by_mid.get(str(mid)) or {}
            provider = str(item.get("provider") or row.get("provider") or "").upper()
            kind = str(item.get("kind") or "").lower()
            family = "OPENEPG" if provider == "OPENEPG" else ("LOCAL" if kind == "local" else "")
            if not family:
                continue
            path = str((row or {}).get("map_path") or "")
            if not path or not os.path.isfile(path):
                continue
            mapping = srp_channel_map._parse_channels_xml_preserve(path) or {}
            _merge_mapping(families[family], mapping)
            report.append("[%s] %s" % (family, str(item.get("name") or mid)))
            for cid in sorted(mapping, key=lambda x: str(x).lower()):
                for ref in mapping.get(cid) or []:
                    svc = by_ref.get(channel_registry.canonical_service_ref(ref)) or {}
                    orb = svc.get("orbital")
                    if orb is None:
                        sat = "?"
                    else:
                        sat = ("%.1fE" % float(orb)) if float(orb) >= 0 else ("%.1fW" % abs(float(orb)))
                    report.append("%s | %s | %s | %s | %s" % (
                        cid, str(svc.get("name") or "?"), str(svc.get("provider_name") or "-"), sat, ref))
            report.append("")
        srp_channel_map.write_channels_xml(families["OPENEPG"], OPENEPG_VERIFY_PATH)
        srp_channel_map.write_channels_xml(families["LOCAL"], LOCAL_VERIFY_PATH)
        tmp = REPORT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
        os.replace(tmp, REPORT_PATH)
        return True
    except Exception:
        log.exception("Could not export SRP verification files")
        return False


def _input_signature(items, epg_dir, selected_ids=None):
    parts = [["sat", channel_registry.satellite_registry_signature()],
             ["selected", sorted(str(x) for x in (selected_ids or []))]]
    try:
        from .channel_identity_resolver import ALIASES_PATH
        parts.append(["aliases", _sig(ALIASES_PATH)])
    except Exception:
        pass
    for item in items or []:
        provider = str((item or {}).get("provider") or "").upper()
        if provider == "OPENEPG":
            try:
                info = source_channel_cache.info(item.get("id"))
                parts.append([str(item.get("id")), int(info.get("updated_at") or 0), int(info.get("channels") or 0)])
            except Exception:
                pass
        elif str((item or {}).get("kind") or "").lower() == "local":
            fn = str((item or {}).get("output_file") or "")
            parts.append([str(source_catalog.mapping_source_id(item)), _sig(os.path.join(epg_dir, fn)) if fn else []])
    return parts


def _write_source_map(mid, mapping, map_dir):
    path = os.path.join(map_dir, "%s.channels.xml" % _safe(mid))
    simple = {}
    for cid, row in (mapping or {}).items():
        refs = row.get("refs") if isinstance(row, dict) else row
        if refs:
            simple[cid] = list(refs)
    srp_channel_map.write_channels_xml(simple, path)
    return path


def _append_claim(service_map, cref, row):
    cref = str(cref or "").strip()
    row = dict(row or {})
    if not cref or not row.get("source_id") or not row.get("channel_id"):
        return False
    bucket = service_map.setdefault(cref, [])
    sig = (str(row.get("source_id") or "").lower(), str(row.get("channel_id") or "").lower())
    for old in bucket:
        if (str((old or {}).get("source_id") or "").lower(), str((old or {}).get("channel_id") or "").lower()) == sig:
            return False
    bucket.append(row)
    return True


def _merge_lkg_claims(service_map, old_state, index_sources, restore_mids, item_by_mid):
    """Restore last-known-good ownership for sources not rebuilt this pass.

    RC10 introduced a time-bounded first-use rebuild, but the partial SRP state
    accidentally published only claims from sources completed before the budget
    expired. Existing source-specific *.channels.xml maps were correctly kept on
    disk, yet their receiver owners disappeared from ``service_map`` and Smart
    Mapping repainted them YELLOW as suggestions. This helper restores those
    exact already-resolved ServiceRefs without any network or fuzzy matching.
    """
    restore = set(str(x or "") for x in (restore_mids or []) if x)
    if not restore:
        return 0, 0
    claims_added = 0
    sources_restored = set()

    # Prefer the previous authoritative rows because they preserve display name,
    # confidence and method metadata.
    for cref, rows in ((old_state or {}).get("service_map") or {}).items():
        for row in rows or []:
            sid = str((row or {}).get("source_id") or "")
            if sid not in restore:
                continue
            if _append_claim(service_map, cref, row):
                claims_added += 1
            sources_restored.add(sid)

    # A previous RC10 partial state may itself have already lost rows. Recover
    # directly from the source-scoped channels XML maps that RC10 intentionally
    # preserved. Parsing these maps is cheap and deterministic: the ServiceRefs
    # were already resolved against lamedb in an earlier successful build.
    for sid in sorted(restore):
        idx = (index_sources or {}).get(sid) or {}
        map_path = str(idx.get("map_path") or "")
        if not map_path or not os.path.exists(map_path):
            continue
        item = (item_by_mid or {}).get(sid) or {}
        try:
            parsed = srp_channel_map.parse_channels_xml(map_path) or {}
        except Exception:
            parsed = {}
        if not parsed:
            continue
        for cid, refs in parsed.items():
            for raw_ref in refs or []:
                cref = channel_registry.canonical_service_ref(raw_ref) or str(raw_ref or "").strip()
                if not cref:
                    continue
                row = {
                    "source_id": sid,
                    "catalogue_id": str(item.get("id") or idx.get("catalogue_id") or ""),
                    "source_name": str(item.get("name") or idx.get("name") or sid),
                    "provider": str(item.get("provider") or idx.get("provider") or ""),
                    "channel_id": str(cid),
                    "display_name": str(cid),
                    "confidence": 0,
                    "method": "lkg-map",
                }
                if _append_claim(service_map, cref, row):
                    claims_added += 1
                sources_restored.add(sid)
    return len(sources_restored), claims_added


def _reduce_service_map(service_map, by_ref=None, manual_store=None):
    """Reduce claims to one active owner per canonical receiver ServiceRef."""
    duplicate_claim_refs = 0
    duplicate_claim_rows = 0
    by_ref = by_ref or {}
    try:
        persisted_by_ref = {}
        if manual_store is not None:
            for pkey, saved in (manual_store.all() or {}).items():
                saved = saved or {}
                try:
                    psid, pcid = str(pkey).split("::", 1)
                except ValueError:
                    continue
                mode = str(saved.get("mode") or "manual").lower()
                mode_class = 3 if mode == "manual" else (2 if mode in ("auto-repair", "smartmatch-iptv", "smartmatch", "auto") else 1)
                pconf = int(saved.get("confidence") or 0)
                for pref in saved.get("refs") or []:
                    pcanon = channel_registry.canonical_service_ref(pref) or str(pref or "").strip()
                    if not pcanon:
                        continue
                    persisted_by_ref.setdefault(pcanon, []).append({
                        "source_id": str(psid or "").lower(),
                        "channel_id": str(pcid or "").lower(),
                        "mode_class": mode_class,
                        "confidence": pconf,
                    })

        source_by_mid = source_catalog.by_mapping_id() or {}
        reduced = {}
        for cref, claims in (service_map or {}).items():
            unique = []
            seen_claims = set()
            for row in claims or []:
                sig = (str((row or {}).get("source_id") or "").lower(),
                       str((row or {}).get("channel_id") or "").lower())
                if not sig[0] or not sig[1] or sig in seen_claims:
                    continue
                seen_claims.add(sig)
                unique.append(row)
            if not unique:
                continue
            if len(unique) > 1:
                duplicate_claim_refs += 1
                duplicate_claim_rows += len(unique) - 1

            persisted = persisted_by_ref.get(str(cref or "")) or []
            exact = []
            for row in unique:
                rsid = str((row or {}).get("source_id") or "").lower()
                rcid = str((row or {}).get("channel_id") or "").lower()
                matches = [q for q in persisted if q.get("source_id") == rsid and q.get("channel_id") == rcid]
                if matches:
                    best_saved = max(matches, key=lambda q: (int(q.get("mode_class") or 0), int(q.get("confidence") or 0)))
                    exact.append((int(best_saved.get("mode_class") or 0), int(best_saved.get("confidence") or 0), row))
            if exact:
                winner = max(exact, key=lambda x: (x[0], x[1]))[2]
            else:
                service = by_ref.get(str(cref or "")) or {}
                def claim_rank(row):
                    sid = str((row or {}).get("source_id") or "").lower()
                    item = source_by_mid.get(sid) or {}
                    try:
                        owner_rank = source_priority.ownership_rank(item, service)
                    except Exception:
                        owner_rank = (-1, ())
                    return (owner_rank, int((row or {}).get("confidence") or 0),
                            str((row or {}).get("source_id") or ""),
                            str((row or {}).get("channel_id") or ""))
                winner = max(unique, key=claim_rank)
            reduced[str(cref)] = [dict(winner or {})]
        return reduced, duplicate_claim_refs, duplicate_claim_rows
    except Exception:
        log.exception("Could not reduce SRP claims to one receiver owner")
        return dict(service_map or {}), duplicate_claim_refs, duplicate_claim_rows


def recover_state_from_index(force=False):
    """One-time cheap repair for RC10 partial-state ownership loss.

    Uses only already-built channels XML maps; no provider network and no fuzzy
    matching. Safe to run from postinst before Enigma2 restarts.
    """
    state = _read_json(STATE_PATH)
    if (not force and int(state.get("version") or 0) >= VERSION and
            not bool(state.get("partial")) and (state.get("service_map") or {})):
        return {"ok": True, "cached": True, "mapped_service_refs": len(state.get("service_map") or {})}
    try:
        from .source_preferences import SourcePreferences
        selected_catalogue_ids = list(SourcePreferences().selected_catalogue_ids())
    except Exception:
        selected_catalogue_ids = []
    by_catalogue = source_catalog.by_catalogue_id() or {}
    selected_items = [by_catalogue.get(sid) or {} for sid in selected_catalogue_ids]
    selected_items = [x for x in selected_items if x]
    mids = set(str(source_catalog.mapping_source_id(x) or "") for x in selected_items if source_catalog.mapping_source_id(x))
    item_by_mid = dict((str(source_catalog.mapping_source_id(x) or ""), x) for x in selected_items if source_catalog.mapping_source_id(x))
    index = srp_channel_map.load_index() or {}
    index_sources = dict(index.get("sources") or {})
    service_map = {}
    restored_sources, restored_claims = _merge_lkg_claims(service_map, state, index_sources, mids, item_by_mid)
    try:
        services = _registry_services(force=False)
        _by_key, by_ref, _token_index = _service_index(services) if services else ({}, {}, {})
    except Exception:
        by_ref = {}
    try:
        store = MappingStore()
    except Exception:
        store = None
    service_map, dup_refs, dup_rows = _reduce_service_map(service_map, by_ref=by_ref, manual_store=store)
    if not service_map:
        return {"ok": False, "reason": "no cached source maps", "restored_sources": restored_sources}
    stats = dict(state.get("stats") or {})
    stats.update({
        "mapped_service_refs": len(service_map),
        "lkg_sources_restored": int(restored_sources),
        "lkg_claims_restored": int(restored_claims),
        "duplicate_claim_refs_resolved": int(dup_refs),
        "duplicate_claim_rows_removed": int(dup_rows),
        "partial": False,
        "time_budget_exhausted": False,
        "recovered_from_index": True,
    })
    payload = dict(state or {})
    payload.update({"version": VERSION, "checked": int(time.time()), "partial": False,
                    "service_map": service_map, "stats": stats})
    _write_json(STATE_PATH, payload)
    return {"ok": True, "mapped_service_refs": len(service_map),
            "restored_sources": restored_sources, "restored_claims": restored_claims}


def rebuild(config=None, epg_dir=None, force=False, progress_cb=None, postprocess=True,
            time_budget=None, allow_partial=False):
    if not _BUILD_LOCK.acquire(False):
        return {"ok": False, "busy": True}
    started = time.time()
    deadline = None
    if allow_partial and time_budget:
        try:
            deadline = started + max(1.0, float(time_budget))
        except Exception:
            deadline = None
    try:
        epg_dir = _epg_dir(config, epg_dir)
        from .source_preferences import SourcePreferences
        prefs = SourcePreferences()
        selected_catalogue_ids = list(prefs.selected_catalogue_ids())
        selected_set = set(selected_catalogue_ids)
        by_catalogue = source_catalog.by_catalogue_id()
        items = []
        for sid in selected_catalogue_ids:
            item = by_catalogue.get(sid) or {}
            # beta116: every selected source that does NOT publish its own exact
            # channels= SRP map is generated from the live receiver registry.
            # This includes OpenEPG and EPGShare; Rytec/Koala remain
            # provider-owned and are registered by provider_srp_map instead.
            if item and not bool((item or {}).get("native_srp")):
                items.append(item)
        sig = _input_signature(items, epg_dir, selected_ids=selected_catalogue_ids)
        old = _read_json(STATE_PATH)
        if (not force and int(old.get("version") or 0) == VERSION and
                not bool(old.get("partial")) and
                old.get("input_signature") == sig and old.get("source_maps")):
            return {"ok": True, "cached": True, "stats": old.get("stats") or {}}

        services = _registry_services(force=False)
        if not services:
            return {"ok": False, "reason": "SAT registry/lamedb is empty"}
        by_key, by_ref, token_index = _service_index(services)

        map_dir = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
        if not os.path.isdir(map_dir):
            os.makedirs(map_dir)

        index = srp_channel_map.load_index() or {}
        index_sources = dict(index.get("sources") or {})
        # Remove only EPGManager-derived rows that are no longer selected.
        # Provider-owned exact maps (Rytec/Koala) are intentionally excluded.
        all_generated_mids = set()
        for candidate in source_catalog.all_sources():
            if candidate and not bool((candidate or {}).get("native_srp")):
                mid = str(source_catalog.mapping_source_id(candidate) or "")
                if mid:
                    all_generated_mids.add(mid)
        selected_generated_mids = set(str(source_catalog.mapping_source_id(x) or "") for x in items)
        for mid in list(index_sources):
            if str(mid) in all_generated_mids and str(mid) not in selected_generated_mids:
                row = index_sources.get(mid) or {}
                if str(row.get("mapping_mode") or "").startswith("srp_master_"):
                    index_sources.pop(mid, None)
        source_maps = {}
        service_map = {}
        fresh_mids = set()
        item_by_mid = dict((str(source_catalog.mapping_source_id(x) or ""), x) for x in items if source_catalog.mapping_source_id(x))
        # beta64 manual-unmap overlay. This is intentionally outside the frozen
        # Golden SRP matcher: it only filters derived output for receiver refs
        # the user explicitly asked to keep unmapped.
        try:
            manual_store = MappingStore()
        except Exception:
            manual_store = None
        mapped_ids = mapped_refs = unresolved_total = 0
        built_sources = 0
        processed_items = 0
        budget_exhausted = False
        current_sat_signature = channel_registry.satellite_registry_signature()

        total_items = max(1, len(items))
        for item_pos, item in enumerate(items):
            if deadline is not None and time.time() >= deadline:
                budget_exhausted = True
                break
            mid = str(source_catalog.mapping_source_id(item) or "")
            if progress_cb:
                try:
                    progress_cb(int(100.0 * item_pos / total_items),
                                "Matching %d/%d: %s" % (item_pos + 1, len(items),
                                str(item.get("name") or mid or "source")))
                except Exception:
                    pass
            if not mid:
                continue
            rows = _source_rows(item, epg_dir)
            if not rows:
                # Transient source/catalogue absence: preserve last-known-good map.
                processed_items += 1
                continue
            aliases_by_cid = _companion_aliases(item, rows)
            mapping = {}
            unresolved = []
            method_counts = {}
            row_total = max(1, len(rows))
            for row_pos, row in enumerate(rows):
                # RC10 first-use turbo: never let a background initial rebuild own
                # the receiver CPU for minutes.  Check the budget frequently and
                # leave this source untouched if the deadline expires mid-source.
                if deadline is not None and (row_pos % 64 == 0) and time.time() >= deadline:
                    budget_exhausted = True
                    break
                if progress_cb and row_pos and (row_pos % 250 == 0):
                    try:
                        overall = 100.0 * (item_pos + (float(row_pos) / row_total)) / total_items
                        progress_cb(int(overall), "Matching %s • %d/%d IDs" % (
                            str(item.get("name") or mid), row_pos, len(rows)))
                    except Exception:
                        pass
                cid = str((row or {}).get("channel_id") or "").strip()
                if not cid:
                    continue
                matches, method, confidence = _match_row(item, row, aliases_by_cid.get(cid.lower()), by_key, token_index)
                refs = []
                for service in matches:
                    raw = str(service.get("ref") or "").strip()
                    if not raw:
                        continue
                    try:
                        if manual_store is not None and manual_store.is_blocked(raw):
                            continue
                    except Exception:
                        pass
                    if raw not in refs:
                        refs.append(raw)
                if refs:
                    mapping[cid] = {"refs": refs, "method": method, "confidence": confidence,
                                    "display_name": str(row.get("display_name") or cid)}
                    method_counts[method] = method_counts.get(method, 0) + 1
                else:
                    unresolved.append(cid)

            if budget_exhausted:
                source_maps[mid] = {"channels": 0, "refs": 0,
                                    "unresolved": max(0, len(rows) - row_pos),
                                    "deferred_budget": True,
                                    "preserved_previous": True}
                break

            if not mapping:
                # Never replace a working previous map with an empty automatic result.
                source_maps[mid] = {"channels": 0, "refs": 0, "unresolved": len(unresolved),
                                    "preserved_previous": True}
                unresolved_total += len(unresolved)
                processed_items += 1
                continue

            map_path = _write_source_map(mid, mapping, map_dir)
            fresh_mids.add(mid)
            refs_count = sum(len((row or {}).get("refs") or []) for row in mapping.values())
            index_sources[mid] = {
                "source_id": mid, "map_path": map_path,
                "provider": str(item.get("provider") or ""), "official": False,
                "channels": len(mapping), "refs": refs_count, "unmapped": len(unresolved),
                "countries": list(item.get("countries") or []), "xml_path": "",
                "name": str(item.get("name") or mid), "mapping_mode": "srp_master_v8",
                "sat_signature": current_sat_signature,
            }
            source_maps[mid] = {"channels": len(mapping), "refs": refs_count,
                                "unresolved": len(unresolved), "methods": method_counts,
                                "map_path": map_path}
            built_sources += 1
            processed_items += 1
            mapped_ids += len(mapping)
            mapped_refs += refs_count
            unresolved_total += len(unresolved)

            for cid, rec in mapping.items():
                for raw_ref in rec.get("refs") or []:
                    cref = channel_registry.canonical_service_ref(raw_ref)
                    if not cref:
                        continue
                    service_map.setdefault(cref, []).append({
                        "source_id": mid, "catalogue_id": str(item.get("id") or ""),
                        "source_name": str(item.get("name") or mid),
                        "provider": str(item.get("provider") or ""),
                        "channel_id": str(cid), "display_name": str(rec.get("display_name") or cid),
                        "confidence": int(rec.get("confidence") or 0), "method": str(rec.get("method") or "exact")
                    })

        # RC11: partial/failed source passes must never downgrade an already
        # mapped receiver service back to YELLOW SUGGESTION. Restore exact
        # last-known-good claims for every selected source that did not finish a
        # fresh map in this pass, then apply the normal single-owner arbitration.
        restore_mids = set(selected_generated_mids) - set(fresh_mids)
        lkg_sources_restored, lkg_claims_restored = _merge_lkg_claims(
            service_map, old, index_sources, restore_mids, item_by_mid)

        # 8.2.1 / SRP Master v11: one receiver ServiceRef has ONE active EPG
        # owner. Persisted manual/auto targets win when present; otherwise the
        # provider/country-aware deterministic owner is selected.
        service_map, duplicate_claim_refs, duplicate_claim_rows = _reduce_service_map(
            service_map, by_ref=by_ref, manual_store=manual_store)

        # Preserve provider-supplied Rytec and any other unrelated index rows.
        index_payload = dict(index)
        index_payload["version"] = max(12, int(index.get("version") or 0))
        index_payload["timestamp"] = int(time.time())
        index_payload["sources"] = index_sources
        # srp_channel_map._write_json is frozen but callable; avoid modifying it.
        tmp_index = srp_channel_map.INDEX_PATH + ".tmp"
        with open(tmp_index, "w", encoding="utf-8") as fh:
            json.dump(index_payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_index, srp_channel_map.INDEX_PATH)

        stats = {
            "sat_services": len(services), "satellites": sorted(set(float(x.get("orbital")) for x in services)),
            "sources_built": built_sources, "mapped_channel_ids": mapped_ids,
            "mapped_service_refs": len(service_map), "raw_source_claim_refs": mapped_refs,
            "duplicate_claim_refs_resolved": duplicate_claim_refs,
            "duplicate_claim_rows_removed": duplicate_claim_rows,
            "unresolved_ids": unresolved_total,
            "duration_ms": int((time.time() - started) * 1000),
            "rule": "Live lamedb SRPs only; ONE receiver ServiceRef = ONE active EPG source/ID owner; persisted owner first, then provider/country-aware deterministic arbitration; never invented",
            "active_lamedb": channel_registry.active_lamedb_path(),
            "selected_sources": len(selected_catalogue_ids),
            "sources_processed": int(processed_items),
            "sources_deferred": max(0, len(items) - int(processed_items)),
            "lkg_sources_restored": int(lkg_sources_restored),
            "lkg_claims_restored": int(lkg_claims_restored),
            "partial": bool(budget_exhausted),
            "time_budget_exhausted": bool(budget_exhausted),
        }
        payload = {"version": VERSION, "checked": int(time.time()), "input_signature": sig,
                   "partial": bool(budget_exhausted),
                   "source_maps": source_maps, "service_map": service_map, "stats": stats}
        _write_json(STATE_PATH, payload)
        if postprocess:
            # Verification export and the full receiver<->EPG coverage audit are
            # diagnostics, not prerequisites for Native EPG import. Keeping them
            # out of the synchronous self-heal path removes minutes of avoidable
            # CPU work on small receivers. Background/explicit audit paths can
            # still refresh them normally.
            export_verification_files(config=config)
            try:
                from . import id_coverage_audit
                id_coverage_audit.build(config=config, services=services)
            except Exception:
                log.exception("Could not build beta43 selected-source ID coverage audit")
        if progress_cb:
            try:
                progress_cb(100 if not budget_exhausted else 99,
                            "Service Reference matching complete" if not budget_exhausted
                            else "Fast initial mapping budget reached; remaining sources deferred")
            except Exception:
                pass
        log.info("SRP Master ready: %d source maps, %d IDs, %d refs, %d unresolved in %.2fs",
                 built_sources, mapped_ids, mapped_refs, unresolved_total, time.time() - started)
        return {"ok": True, "stats": stats}
    except Exception:
        log.exception("SRP Master rebuild failed")
        return {"ok": False, "reason": "exception"}
    finally:
        _BUILD_LOCK.release()


def rebuild_async(config=None, epg_dir=None, force=False):
    global _ASYNC_RUNNING
    with _MEM_LOCK:
        if _ASYNC_RUNNING:
            return False
        _ASYNC_RUNNING = True
    def worker():
        global _ASYNC_RUNNING
        try:
            rebuild(config=config, epg_dir=epg_dir, force=force)
        finally:
            with _MEM_LOCK:
                _ASYNC_RUNNING = False
    threading.Thread(target=worker, daemon=True).start()
    return True


def load_state():
    return _read_json(STATE_PATH)


def stats():
    return dict(load_state().get("stats") or {})


def mappings_for_ref(service_ref, selected_source_ids=None):
    cref = channel_registry.canonical_service_ref(service_ref)
    rows = list(((load_state().get("service_map") or {}).get(cref)) or [])
    if selected_source_ids is not None:
        wanted = set(str(x or "").lower() for x in selected_source_ids)
        rows = [x for x in rows if str((x or {}).get("source_id") or "").lower() in wanted]
    return [dict(x) for x in rows]
