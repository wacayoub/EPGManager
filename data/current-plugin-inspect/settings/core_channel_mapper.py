# -*- coding: utf-8 -*-
"""
core/channel_mapper.py

"Review XML channels and automatically assign channels based on name" -
this module:

  1. Reads the <channel id="..."><display-name>...</display-name></channel>
     entries directly out of each source's ALREADY-GENERATED XMLTV file in
     /etc/epgimport/jedi_epg/ (core func: list_epg_channels()). This is
     deliberately not a hardcoded per-source channel list - it reflects
     whatever actually got written on the last successful run, so it also
     covers beIN Sports' dynamically-discovered channel IDs.

  2. Scans every Enigma2 bouquet file (/etc/enigma2/userbouquet.*.tv) to
     build a catalog of every real service the receiver knows about, with
     its satellite/bouquet label (e.g. "Nilesat 7W", "Astra 19E", "Hotbird
     13E", "Badr 26E" - whatever your bouquets are actually named).

  3. Matches EPG channel names against that catalog by normalized name
     (case/diacritic/suffix-insensitive), so e.g. an EPG channel named "2M"
     gets matched against every service across every satellite whose name
     normalizes to "2m" - exactly the "found in Nilesat, Astra, Hotbird,
     Badr" behavior asked for.

  4. core/epgimport_export.py then turns the resulting mapping into an
     EPG-Importer-compatible sources.xml, so the physical service
     references get wired to the right XMLTV channel id automatically.

This is entirely offline/text-based (bouquet files and XMLTV files are
just text), so it's fully unit-testable without a running Enigma2.
"""

import os
import re
import unicodedata
import json
from xml.etree import ElementTree as ET

from .logger import get_logger
from . import external_sources
from .performance_cache import file_signature, load_cache, load_cache_fast, save_cache

log = get_logger(__name__)

EPG_DIR = "/etc/epgimport/jedi_epg"
BOUQUET_DIR = "/etc/enigma2"

# Known EPG Manager output files -> (source_id, source_name)
SOURCE_OUTPUT_FILES = {
    "medi1tv_ar.xml": ("medi1tv", "Medi1 TV"),
    "2M_Chada.xml": ("chada_2m", "2M / Chada"),
    "snrt.xml": ("snrt", "SNRT"),
    "bein.xml": ("bein_sports", "beIN Sports"),
    "almajdtv.xml": ("almajd", "Almajd"),
    "arryadia_snrt.xml": ("arryadia", "Arryadia"),
}

# Suffixes/qualifiers stripped for fuzzy matching - these commonly differ
# between an XMLTV channel's display name and how a satellite feed names
# the same channel (e.g. EPG "Arryadia HD" vs bouquet "ARRYADIA HD FHD").
_STRIP_WORDS = [
    "hd", "sd", "fhd", "uhd", "4k", "tv", "hevc", "h265", "backup",
    "feed", "channel",
]




def classify_service_ref(ref):
    """Return IPTV, SAT or DVB for a service reference without receiver APIs."""
    raw = str(ref or '')
    low = raw.lower()
    if 'http://' in low or 'https://' in low or '%3a//' in low or '%3a%2f%2f' in low:
        return 'IPTV'
    parts = raw.split(':')
    # IPTV service types commonly used by Enigma2 players even when the URL is encoded.
    if parts and parts[0] in ('4097', '5001', '5002', '8193'):
        return 'IPTV'
    # Standard DVB references have SID/TSID/ONID/namespace fields.
    if len(parts) >= 7 and parts[0] in ('1', '17', '22', '25', '31'):
        return 'SAT'
    return 'DVB'


def parse_dvb_ids(ref):
    """Best-effort DVB identifiers from an Enigma2 service reference."""
    parts = str(ref or '').split(':')
    out = {'sid': None, 'tsid': None, 'onid': None, 'namespace': None}
    try:
        if len(parts) >= 7:
            out['sid'] = int(parts[3] or '0', 16)
            out['tsid'] = int(parts[4] or '0', 16)
            out['onid'] = int(parts[5] or '0', 16)
            out['namespace'] = int(parts[6] or '0', 16)
    except Exception:
        pass
    return out

# --------------------------------------------------------------------------
# Step 1: read EPG channel ids/names out of our own generated XMLTV files
# --------------------------------------------------------------------------
def _read_xmltv_channels(path, source_id, source_name):
    channels = []
    if not os.path.exists(path):
        return channels
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        log.warning("Could not parse %s for channel review: %s", path, e)
        return channels
    except Exception as e:
        log.warning("Could not read %s for channel review: %s", path, e)
        return channels
    for ch_el in tree.getroot().findall("channel"):
        ch_id = ch_el.get("id")
        if not ch_id:
            continue
        name_el = ch_el.find("display-name")
        display_name = (name_el.text or ch_id) if name_el is not None else ch_id
        channels.append({
            "source_id": source_id,
            "source_name": source_name,
            "epg_xml_path": path,
            "channel_id": ch_id,
            "display_name": display_name.strip(),
        })
    return channels


def list_epg_channels(epg_dir=EPG_DIR):
    """Return channels from built-in outputs plus cached external XMLTV sources."""
    channels = []
    for filename, (source_id, source_name) in SOURCE_OUTPUT_FILES.items():
        channels.extend(_read_xmltv_channels(os.path.join(epg_dir, filename), source_id, source_name))
    for src in external_sources.cached_sources(epg_dir):
        channels.extend(_read_xmltv_channels(src["path"], src["id"], src["name"]))
    return channels


def iter_source_xml_files(epg_dir=EPG_DIR, source_ids=None):
    """Yield XMLTV files, optionally restricted to Native Import source selection.

    source_ids uses the IDs from the unified Native Sources catalogue. Local
    generator aliases (local_snrt, local_chada_2m, ...) are accepted.
    """
    selected = set(source_ids or [])
    local_alias = {
        "medi1tv": "local_medi1tv", "chada_2m": "local_chada_2m",
        "snrt": "local_snrt", "bein_sports": "local_bein_sports",
        "almajd": "local_almajd", "arryadia": "local_arryadia",
    }
    def wanted(source_id):
        return not selected or source_id in selected or local_alias.get(source_id) in selected
    seen=set()
    for filename,(source_id,source_name) in SOURCE_OUTPUT_FILES.items():
        path=os.path.join(epg_dir,filename)
        if os.path.exists(path) and wanted(source_id):
            seen.add(os.path.realpath(path)); yield source_id,source_name,path
    # Unified catalogue cache/download paths, including installed EPG-Importer providers.
    try:
        from . import source_catalog
        for src in source_catalog.all_sources():
            sid=src.get("id")
            if src.get("kind") == "local" or not wanted(sid):
                continue
            path=external_sources.local_xml_path(src,epg_dir)
            if os.path.exists(path) and os.path.realpath(path) not in seen:
                seen.add(os.path.realpath(path)); yield sid,src.get("name",sid),path
    except Exception:
        pass


# --------------------------------------------------------------------------
# Step 2: scan Enigma2 bouquets for real services
# --------------------------------------------------------------------------
def normalize_name(name):
    """Case/diacritic/suffix-insensitive normalization shared by both sides
    of the match (EPG display name and bouquet service name)."""
    if not name:
        return ""
    name = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)  # punctuation -> space
    words = name.split()
    words = [w for w in words if w not in _STRIP_WORDS]
    return " ".join(words).strip()


def guess_satellite_label(bouquet_text_header, filename):
    """Best-effort human label for a bouquet, preferring its #NAME line,
    falling back to a cleaned-up filename (e.g.
    'userbouquet.nilesat_7w_sports.tv' -> 'nilesat 7w sports')."""
    m = re.search(r"#NAME\s+(.+)", bouquet_text_header)
    if m:
        return m.group(1).strip()
    base = re.sub(r"^userbouquet\.", "", filename)
    base = re.sub(r"\.tv$", "", base)
    return base.replace("_", " ").replace(".", " ").strip() or filename


def _bouquet_ref_filename(line):
    m = re.search(r'FROM BOUQUET\s+"([^"]+)"', line or "")
    return m.group(1) if m else None


def list_bouquets(bouquet_dir=BOUQUET_DIR, use_cache=True, fast_cache=True):
    """List every TV bouquet known to Enigma2, including nested/sub-bouquets.

    The master bouquets.tv order is honoured first, then any remaining
    userbouquet.*.tv files are appended so provider-created sub-bouquets are
    not hidden from Smart Mapping.
    """
    if not os.path.isdir(bouquet_dir):
        return []
    cache_path = os.path.join(bouquet_dir, ".epgmanager_bouquets_cache.json")
    # Instant-start path: trust a recent bouquet metadata cache instead of
    # reopening every userbouquet file. This is especially important on
    # receivers with provider trees containing hundreds of bouquet files.
    if use_cache and fast_cache:
        cached = load_cache_fast(cache_path, max_age=24 * 3600)
        if cached is not None:
            return cached
    discovered = []
    seen = set()

    def add_file(filename, parent=None, parent_file=None, depth=0):
        if not filename or filename in seen:
            return
        path = os.path.join(bouquet_dir, filename)
        if not os.path.exists(path) or not filename.endswith('.tv'):
            return
        seen.add(filename)
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return
        label = guess_satellite_label(lines[0] if lines else '', filename)
        discovered.append({
            "bouquet_file": filename,
            "bouquet_label": label,
            "parent": parent,
            "parent_file": parent_file,
            "depth": int(depth or 0),
        })
        # Follow nested bouquet links too.  We keep the parent *file* as well
        # as the human label so the UI can build a real collapsible tree even
        # when two bouquets have the same display name.
        for line in lines:
            child = _bouquet_ref_filename(line)
            if child:
                add_file(child, label, filename, depth + 1)

    master = os.path.join(bouquet_dir, 'bouquets.tv')
    if os.path.exists(master):
        try:
            with open(master, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    child = _bouquet_ref_filename(line)
                    if child:
                        add_file(child, None, None, 0)
        except OSError:
            pass
    try:
        for filename in sorted(os.listdir(bouquet_dir)):
            if filename.startswith('userbouquet.') and filename.endswith('.tv'):
                add_file(filename, None, None, 0)
    except OSError:
        pass
    if use_cache:
        # Signature is intentionally cheap here: the metadata cache is only a
        # startup accelerator. Explicit refresh / TTL expiration rebuilds it.
        save_cache(cache_path, [], discovered)
    return discovered


def scan_bouquets(bouquet_dir=BOUQUET_DIR, use_cache=True, fast_cache=True):
    """Parse all TV bouquets into services, using a persistent cache.

    ``fast_cache`` avoids stat'ing every bouquet file during Smart Mapping
    startup. A recent catalog cache is returned immediately and rebuilt only
    after its TTL or an explicit non-fast refresh.
    """
    cache_path = os.path.join(bouquet_dir, ".epgmanager_catalog_cache.json")
    if use_cache and fast_cache:
        cached = load_cache_fast(cache_path, max_age=24 * 3600)
        if cached is not None:
            log.info("Instant-loaded %d bouquet service(s) from trusted cache", len(cached))
            return cached
    bouquet_files = []
    try:
        bouquet_files = [os.path.join(bouquet_dir, x) for x in os.listdir(bouquet_dir) if x.endswith(".tv")]
    except OSError:
        bouquet_files = []
    signature = file_signature(bouquet_files)
    if use_cache:
        cached = load_cache(cache_path, signature)
        if cached is not None:
            log.info("Loaded %d bouquet service(s) from persistent cache", len(cached))
            return cached
    catalog = []
    bouquets = list_bouquets(bouquet_dir, use_cache=use_cache, fast_cache=False)
    for bq in bouquets:
        filename = bq['bouquet_file']
        path = os.path.join(bouquet_dir, filename)
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError as e:
            log.debug("Could not read bouquet %s: %s", path, e)
            continue
        bouquet_label = bq['bouquet_label']
        pending_ref = None
        pending_name = None
        for line in lines:
            line = line.strip()
            if line.startswith('#SERVICE'):
                if 'FROM BOUQUET' in line:
                    pending_ref = None
                    continue
                pending_ref = line[len('#SERVICE'):].strip()
                pending_name = None
                # IPTV/service lines sometimes embed the displayed name after ':'
                if 'http' in pending_ref.lower():
                    try:
                        tail = pending_ref.rsplit(':', 1)[-1].strip()
                        if tail and not tail.lower().startswith(('http', 'https')):
                            pending_name = tail
                    except Exception:
                        pass
            elif line.startswith('#DESCRIPTION') and pending_ref:
                pending_name = line[len('#DESCRIPTION'):].strip()
                if pending_name:
                    catalog.append({
                        'ref': pending_ref,
                        'name': pending_name,
                        'normalized': normalize_name(pending_name),
                        'bouquet_label': bouquet_label,
                        'bouquet_file': filename,
                        'parent_bouquet': bq.get('parent'),
                        'service_type': classify_service_ref(pending_ref),
                        'dvb': parse_dvb_ids(pending_ref),
                    })
                pending_ref = None
                pending_name = None
        # Some generated bouquets omit #DESCRIPTION. Preserve such services when
        # their #SERVICE line carried a trailing label.
        if pending_ref and pending_name:
            catalog.append({
                'ref': pending_ref, 'name': pending_name,
                'normalized': normalize_name(pending_name),
                'bouquet_label': bouquet_label, 'bouquet_file': filename,
                'parent_bouquet': bq.get('parent'),
                'service_type': classify_service_ref(pending_ref),
                'dvb': parse_dvb_ids(pending_ref),
            })
    log.info("Scanned %d bouquet(s), found %d service(s)", len(bouquets), len(catalog))
    if use_cache:
        save_cache(cache_path, signature, catalog)
    return catalog


# --------------------------------------------------------------------------
# Step 3: match
# --------------------------------------------------------------------------
def match_channels(epg_channels, catalog):
    """For each epg channel dict (from list_epg_channels()), find every
    catalog entry whose normalized name matches (exact, or one contains
    the other for short names like "2M" to avoid missing "2M Maroc HD").
    Returns a new list of epg_channel dicts, each with an added "matches"
    key: a de-duplicated list of {ref, name, bouquet_label}, and a
    "satellites" key: the sorted set of distinct bouquet_labels matched."""
    results = []
    for epg_ch in epg_channels:
        norm_target = normalize_name(epg_ch["display_name"])
        matches = []
        seen_refs = set()

        for entry in catalog:
            if not norm_target or not entry["normalized"]:
                continue
            target_words = norm_target.split()
            entry_words = entry["normalized"].split()
            is_match = (
                entry["normalized"] == norm_target
                or norm_target in entry_words       # e.g. target "2m" is one word of "2m maroc"
                or entry["normalized"] in target_words  # symmetric case
            )
            if is_match and entry["ref"] not in seen_refs:
                seen_refs.add(entry["ref"])
                matches.append({
                    "ref": entry["ref"],
                    "name": entry["name"],
                    "bouquet_label": entry["bouquet_label"],
                })

        satellites = sorted(set(m["bouquet_label"] for m in matches))
        results.append(dict(epg_ch, matches=matches, satellites=satellites))

    return results


# --------------------------------------------------------------------------
# v4 standalone smart mapper (Jedi-style matching without Jedi dependency)
# --------------------------------------------------------------------------
def _token_score(a, b):
    """Return a 0..100 similarity score with TV-friendly normalisation."""
    from difflib import SequenceMatcher
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0
    if na == nb:
        return 100
    wa, wb = set(na.split()), set(nb.split())
    if na in wb or nb in wa:
        return 96
    if na in nb or nb in na:
        # Containment is useful for '2m' vs '2m maroc' but should not beat exact.
        return 92
    union = wa | wb
    overlap = (100.0 * len(wa & wb) / len(union)) if union else 0.0
    ratio = 100.0 * SequenceMatcher(None, na, nb).ratio()
    return int(round(max(ratio, overlap * 0.85 + ratio * 0.15)))


def smart_match_channels(epg_channels, catalog, min_score=82, store=None, max_candidates=8):
    """Score every XMLTV channel against bouquet services.

    Manual mappings from MappingStore always win. Otherwise the best candidate is
    auto-selected only when its score is >= min_score.  Candidate scores remain in
    the result so the UI can present Jedi-style alternatives for manual choice.
    """
    results = []
    for epg_ch in epg_channels:
        candidates = []
        for entry in catalog:
            score = _token_score(epg_ch.get("display_name", ""), entry.get("name", ""))
            if score <= 0:
                continue
            candidates.append({
                "ref": entry["ref"],
                "name": entry["name"],
                "bouquet_label": entry["bouquet_label"],
                "service_type": entry.get("service_type", classify_service_ref(entry.get("ref"))),
                "dvb": entry.get("dvb") or parse_dvb_ids(entry.get("ref")),
                "score": score,
            })
        candidates.sort(key=lambda x: (-x["score"], x["name"].lower(), x["bouquet_label"].lower()))
        candidates = candidates[:max_candidates]

        manual = store.get(epg_ch.get("source_id"), epg_ch.get("channel_id")) if store else None
        matches = []
        mode = "unmapped"
        confidence = 0
        if manual and manual.get("refs"):
            refs = set(manual.get("refs") or [])
            by_ref = {c["ref"]: c for c in candidates}
            for ref in refs:
                item = dict(by_ref.get(ref, {"ref": ref, "name": "Manual service", "bouquet_label": "Saved mapping", "score": 100}))
                item["score"] = 100
                matches.append(item)
            mode = "manual"
            confidence = 100
        elif candidates and candidates[0]["score"] >= int(min_score):
            best_score = candidates[0]["score"]
            # Exact/equally-scored duplicates on several bouquets are all useful.
            matches = [c for c in candidates if c["score"] == best_score]
            mode = "exact" if best_score == 100 else "auto"
            confidence = best_score

        satellites = sorted(set(m.get("bouquet_label", "") for m in matches if m.get("bouquet_label")))
        results.append(dict(epg_ch, matches=matches, satellites=satellites,
                            candidates=candidates, match_mode=mode, confidence=confidence))
    return results



def resolve_service(epg_channel, catalog, store=None, auto_threshold=95, max_candidates=5):
    """Resolve one XMLTV channel with a conservative priority chain.

    manual > exact normalized name > high confidence fuzzy. The result keeps
    SAT/IPTV metadata so UI code can make DVB-aware decisions without reparsing.
    """
    rows = smart_match_channels([epg_channel], catalog, min_score=auto_threshold,
                                store=store, max_candidates=max_candidates)
    return rows[0] if rows else dict(epg_channel, matches=[], candidates=[], match_mode='unmapped', confidence=0)


def mapping_coverage(catalog, mapped_refs):
    refs = set(mapped_refs or [])
    total = len(catalog or [])
    mapped = sum(1 for row in (catalog or []) if row.get('ref') in refs)
    return {'total': total, 'mapped': mapped, 'percent': int(round(mapped * 100.0 / total)) if total else 0}
