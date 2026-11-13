# -*- coding: utf-8 -*-
"""Fast receiver registry for EPG Manager v6.3.1.

Satellite services come from Enigma2's own ``TV -> Reception Lists`` query
(``FROM SATELLITES ORDER BY satellitePosition``), exactly like OpenATV's
ChannelSelection screen.  Raw lamedb parsing is only a fallback for tests or
images where the Enigma2 service API is unavailable.

IPTV services still come from bouquets.  Both registries are cached separately
so a single-source sync never has to rebuild the whole receiver inventory.
"""
from __future__ import print_function
import csv
import json
import os
import re

from . import channel_mapper, fast_name_key
from .performance_cache import file_signature
from .logger import get_logger

log = get_logger(__name__)

PATH = "/etc/enigma2/epgmanager_channel_registry.json"  # compatibility summary
SAT_PATH = "/etc/enigma2/epgmanager_sat_registry.json"
IPTV_PATH = "/etc/enigma2/epgmanager_iptv_registry.json"

PREFIX_TO_REGION = {
    "FR":"FR","FRA":"FR","AR":"MENA","ARA":"MENA","MENA":"MENA","MA":"MA","MAR":"MA",
    "DZ":"DZ","TN":"TN","EG":"EG","SA":"SA","KSA":"SA","QA":"QA","AE":"AE","UAE":"AE",
    "ES":"ES","IT":"IT","DE":"DE","PT":"PT","UK":"UK","GB":"UK","US":"US","USA":"US",
    "BE":"BE","NL":"NL","CH":"CH","AT":"AT","GR":"GR","TR":"TR","RO":"RO","BG":"BG",
    "HR":"HR","RS":"RS","SI":"SI","SK":"SK","CZ":"CZ","DK":"DK","SE":"SE","NO":"NO",
    "FI":"FI","PL":"PL","IE":"IE"
}
EUROPE_CODES = set("FR ES IT DE PT UK IE BE NL CH AT GR TR RO BG HR RS SI SK CZ DK SE NO FI PL".split())
MENA_CODES = set("MENA MA DZ TN EG SA QA AE PS".split())
TV_SERVICE_TYPES = set([1, 17, 22, 25, 31, 134, 195, 211])
LAMEDB_NAMES = ("lamedb", "lamedb5", "lamedb.5")


def is_tv_service(row):
    """Strict Mapping/Repair inventory gate: television only, no radio/data."""
    row = row or {}
    ref = str(row.get("ref") or "")
    if channel_mapper.classify_service_ref(ref) == "IPTV":
        return channel_mapper.is_probable_tv_name(row.get("name") or row.get("clean_name") or "")
    st = row.get("service_type_id")
    if st is None:
        st = channel_mapper.service_type_id_from_ref(ref)
    try:
        if st is not None and int(st) not in TV_SERVICE_TYPES:
            return False
    except Exception:
        pass
    return channel_mapper.is_probable_tv_name(row.get("name") or row.get("clean_name") or "")


# rc84: canonical_service_ref() is a pure string function, called ~27k times
# when Smart Mapping opens (12k receiver services, mapped-view keys, summary).
# Memoise it; the 50k bound keeps memory small (IPTV refs can be long).
_CANON_CACHE = {}
_CANON_CACHE_MAX = 50000


def canonical_service_ref(ref):
    cached = _CANON_CACHE.get(ref) if isinstance(ref, str) else None
    if cached is not None:
        return cached
    value = _canonical_service_ref_uncached(ref)
    if isinstance(ref, str):
        if len(_CANON_CACHE) >= _CANON_CACHE_MAX:
            _CANON_CACHE.clear()
        _CANON_CACHE[ref] = value
    return value


def _canonical_service_ref_uncached(ref):
    raw = str(ref or "").strip()
    if not raw:
        return ""
    if channel_mapper.classify_service_ref(raw) == "IPTV":
        return raw.rstrip(":")
    parts = raw.split(":")
    if len(parts) >= 7:
        return ":".join(parts[:10]).upper().rstrip(":")
    return raw.upper().rstrip(":")


def orbital_position_from_namespace(namespace):
    try:
        value = int(namespace)
    except Exception:
        return None
    raw = (value >> 16) & 0xFFFF
    if raw <= 0 or raw > 3600:
        return None
    if raw > 1800:
        return round(-(3600 - raw) / 10.0, 1)
    return round(raw / 10.0, 1)


def orbital_position_from_ref(ref):
    return orbital_position_from_namespace(channel_mapper.parse_dvb_ids(ref).get("namespace"))


def satellite_zone(orbital):
    if orbital is None:
        return ""
    # Main MENA positions used by Nilesat/Eutelsat 7W, Badr/Arabsat 26E and 30W.
    if (-9.5 <= orbital <= -6.0) or (24.0 <= orbital <= 26.8) or (29.0 <= orbital <= 31.5):
        return "MENA"
    # Use only strong regional hints. Hotbird 13E is deliberately left MIXED
    # because it carries both European and MENA services.
    if ((18.5 <= orbital <= 20.0) or (22.5 <= orbital <= 24.0) or (27.5 <= orbital <= 29.0)
            or (-31.5 <= orbital <= -29.0)):
        return "EUROPE"
    return ""


def _prefix_region(text):
    """Extract IPTV country/language tags without polluting channel identity.

    Common provider labels include ``|FR| TF1``, ``[AR] MBC 1``, ``IT | Rai 1``
    or the older ``FR: TF1`` form.  These tags are strong Smart Mapping context
    and must be removed before name scoring.
    """
    text = str(text or "").strip()
    patterns = (
        r"^\s*\|\s*([A-Za-z]{2,5})\s*\|\s*",
        r"^\s*\[\s*([A-Za-z]{2,5})\s*\]\s*",
        r"^\s*\(\s*([A-Za-z]{2,5})\s*\)\s*",
        r"^\s*([A-Za-z]{2,5})\s*[:|]\s*",
    )
    for pattern in patterns:
        m = re.match(pattern, text)
        if not m:
            continue
        region = PREFIX_TO_REGION.get(m.group(1).upper(), "")
        if region:
            return region, text[m.end():].strip()
    return "", text


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        log.exception("Could not save receiver registry %s", path)
        return False


def _make_sat_ref(sid, namespace, tsid, onid, service_type=1):
    try:
        return "1:0:%X:%X:%X:%X:%X:0:0:0:" % (
            int(service_type), int(sid), int(tsid), int(onid), int(namespace))
    except Exception:
        return ""




def _parse_tv_service_type(value):
    """Accept lamedb service-type tokens written in decimal or hexadecimal.

    Classic /4/ settings commonly store decimal 25 for an HD TV service while
    /5/ writers may emit the ServiceReference-style hexadecimal 19.  Prefer a
    representation that lands in Enigma2's known TV service types.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty service type")
    candidates = []
    try:
        candidates.append(int(raw, 10))
    except Exception:
        pass
    try:
        hx = int(raw, 16)
        if hx not in candidates:
            candidates.append(hx)
    except Exception:
        pass
    for candidate in candidates:
        if candidate in TV_SERVICE_TYPES:
            return candidate
    if candidates:
        return candidates[0]
    raise ValueError("invalid service type")

def _clean_provider(value):
    value = str(value or "").strip()
    if value.lower().startswith("p:"):
        value = value[2:]
    return value.split(",", 1)[0].strip()


def _parse_lamedb_v4(path):
    """Stream every scanned DVB-TV service from classic lamedb /4/.

    Do not read the full lamedb into a Python list. Large satellite databases
    can contain tens of thousands of lines and the old implementation doubled
    memory temporarily just to split them.
    """
    out = []
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except Exception:
        return out
    try:
        in_services = False
        while True:
            raw = fh.readline()
            if not raw:
                break
            line = raw.strip()
            if line == "services":
                in_services = True
                continue
            if not in_services:
                continue
            if line == "end":
                break
            parts = line.split(":")
            if len(parts) < 5:
                continue
            try:
                sid = int(parts[0], 16)
                namespace = int(parts[1], 16)
                tsid = int(parts[2], 16)
                onid = int(parts[3], 16)
                service_type = _parse_tv_service_type(parts[4])
            except Exception:
                # Keep stream alignment: this wasn't a service header, so do
                # not consume the following name/provider lines.
                continue
            name = (fh.readline() or "").strip()
            provider = _clean_provider((fh.readline() or "").strip())
            if service_type not in TV_SERVICE_TYPES or not name:
                continue
            ref = _make_sat_ref(sid, namespace, tsid, onid, service_type)
            if ref:
                out.append({"ref": ref, "name": name, "provider_name": provider,
                            "dvb": {"sid": sid, "namespace": namespace, "tsid": tsid, "onid": onid},
                            "service_type": "SAT", "service_type_id": service_type, "registry_origin": "lamedb"})
    finally:
        try: fh.close()
        except Exception: pass
    return out


def _parse_lamedb_v5(path):
    """Stream the official Enigma2 lamedb /5/ service-line format."""
    out = []
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except Exception:
        return out
    try:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw.startswith("s:"):
                continue
            try:
                ref_part, meta_part = raw[2:].split(",", 1)
            except ValueError:
                continue
            fields = ref_part.split(":")
            if len(fields) < 7:
                continue
            try:
                sid = int(fields[0], 16)
                namespace = int(fields[1], 16)
                tsid = int(fields[2], 16)
                onid = int(fields[3], 16)
                service_type = _parse_tv_service_type(fields[4])
                int(fields[5], 10) if str(fields[5]).isdigit() else int(fields[5], 16)
                int(fields[6], 16)
            except Exception:
                continue
            if service_type not in TV_SERVICE_TYPES:
                continue
            try:
                tokens = next(csv.reader([meta_part], skipinitialspace=False))
            except Exception:
                tokens = [meta_part]
            if not tokens:
                continue
            name = str(tokens[0] or "").strip()
            provider = ""
            for token in tokens[1:]:
                token = str(token or "").strip()
                if token.lower().startswith("p:"):
                    provider = _clean_provider(token)
                    break
            if not name:
                continue
            ref = _make_sat_ref(sid, namespace, tsid, onid, service_type)
            if ref:
                out.append({"ref": ref, "name": name, "provider_name": provider,
                            "dvb": {"sid": sid, "namespace": namespace, "tsid": tsid, "onid": onid},
                            "service_type": "SAT", "service_type_id": service_type, "registry_origin": "lamedb"})
    finally:
        try: fh.close()
        except Exception: pass
    return out



def _orbital_label(orbital):
    if orbital is None:
        return "Satellite"
    try:
        value = float(orbital)
    except Exception:
        return "Satellite"
    hemi = "E" if value >= 0 else "W"
    return "%.1f%s" % (abs(value), hemi)


def _scan_reception_lists_api(service_handler, root_ref, directory_flag=0, marker_flag=0, provider_key=None):
    """Enumerate the same satellite service roots shown by OpenATV Reception Lists.

    ``root_ref`` is the standard TV service-type query with
    ``FROM SATELLITES ORDER BY satellitePosition`` appended.  Each child whose
    path contains ``satellitePosition`` is the ``<satellite> - Services`` folder
    visible in ChannelSelection.  We deliberately ignore the parallel Provider
    and New folders and enumerate the services folder itself.

    This helper is separated from the Enigma2 imports so it can be unit-tested
    with tiny fake service-center objects off-box.
    """
    out = []
    seen = set()
    try:
        roots = service_handler.list(root_ref)
    except Exception:
        roots = None
    if roots is None:
        return out

    while True:
        try:
            group = roots.getNext()
        except Exception:
            break
        try:
            if not group.valid():
                break
        except Exception:
            break

        try:
            path = str(group.getPath() or "")
        except Exception:
            path = ""
        # OpenATV's Reception Lists root also exposes Providers/New variants.
        # Only the plain satellite-position Services folder should feed EPG
        # Manager's receiver-wide SAT catalogue.
        if "satellitePosition" not in path:
            continue
        if "FROM PROVIDER" in path or "flags ==" in path:
            continue

        try:
            unsigned_orbpos = int(group.getUnsignedData(4)) >> 16
            if unsigned_orbpos in (0xFFFF, 0xEEEE):  # cable / terrestrial
                continue
        except Exception:
            pass

        try:
            services = service_handler.list(group)
        except Exception:
            services = None
        if services is None:
            continue

        while True:
            try:
                service = services.getNext()
            except Exception:
                break
            try:
                if not service.valid():
                    break
            except Exception:
                break
            try:
                flags = int(getattr(service, "flags", 0) or 0)
                if directory_flag and (flags & directory_flag):
                    continue
                if marker_flag and (flags & marker_flag):
                    continue
            except Exception:
                pass

            try:
                ref = str(service.toString() or "").strip()
            except Exception:
                ref = ""
            if not ref or channel_mapper.classify_service_ref(ref) != "SAT":
                continue
            canon = canonical_service_ref(ref)
            if not canon or canon in seen:
                continue

            info = None
            try:
                info = service_handler.info(service)
            except Exception:
                pass
            name = ""
            provider = ""
            if info is not None:
                try:
                    name = str(info.getName(service) or "").strip()
                except Exception:
                    pass
                if provider_key is not None:
                    try:
                        provider = str(info.getInfoString(service, provider_key) or "").strip()
                    except Exception:
                        pass
            if not name:
                try:
                    name = str(service.getName() or "").strip()
                except Exception:
                    pass
            if not name:
                continue

            dvb = channel_mapper.parse_dvb_ids(ref)
            orbital = orbital_position_from_namespace(dvb.get("namespace"))
            service_type_id = None
            try:
                parts = ref.split(":")
                service_type_id = int(parts[2], 16) if len(parts) > 2 and parts[2] else None
            except Exception:
                pass
            if service_type_id is not None and service_type_id not in TV_SERVICE_TYPES:
                continue
            if not channel_mapper.is_probable_tv_name(name):
                continue
            seen.add(canon)
            out.append({
                "ref": ref, "name": name, "provider_name": provider,
                "dvb": dvb, "service_type": "SAT",
                "service_type_id": service_type_id,
                "registry_origin": "reception_lists",
                "reception_list": _orbital_label(orbital),
            })
    return out


def _scan_reception_lists():
    """Use Enigma2's native Reception Lists API; return [] when unavailable."""
    try:
        from enigma import eServiceCenter, eServiceReference, iServiceInformation
        from ServiceReference import serviceRefAppendPath, service_types_tv_ref
    except Exception:
        return []
    try:
        root = serviceRefAppendPath(service_types_tv_ref, "FROM SATELLITES ORDER BY satellitePosition")
        handler = eServiceCenter.getInstance()
        if handler is None:
            return []
        return _scan_reception_lists_api(
            handler, root,
            directory_flag=getattr(eServiceReference, "isDirectory", 0),
            marker_flag=getattr(eServiceReference, "isMarker", 0),
            provider_key=getattr(iServiceInformation, "sProvider", None),
        )
    except Exception:
        log.exception("Could not enumerate OpenATV Reception Lists")
        return []

def _lamedb_candidates(bouquet_dir=channel_mapper.BOUQUET_DIR):
    """Return current lamedb-format candidates, newest first.

    Most Enigma2 images use /etc/enigma2/lamedb for both v4 and v5 formats.
    Some settings/images also expose lamedb5/lamedb.5.  beta39 understands
    all three names and follows the most recently updated valid database.
    """
    rows = []
    for name in LAMEDB_NAMES:
        path = os.path.join(bouquet_dir, name)
        try:
            st = os.stat(path)
            if st.st_size > 32:
                # Standard lamedb wins ties; otherwise newest file wins.
                rows.append((float(st.st_mtime), 1 if name == "lamedb" else 0, path))
        except Exception:
            pass
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in rows]


def active_lamedb_path(bouquet_dir=channel_mapper.BOUQUET_DIR):
    candidates = _lamedb_candidates(bouquet_dir)
    return candidates[0] if candidates else os.path.join(bouquet_dir, "lamedb")


def lamedb_signature(bouquet_dir=channel_mapper.BOUQUET_DIR):
    """Signature all supported lamedb filenames so scans/settings invalidate maps."""
    return file_signature(_lamedb_candidates(bouquet_dir))


def _lamedb_is_v5(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "/5/" in fh.readline().lower()
    except Exception:
        return False


def _parse_lamedb(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.readline().lower()
    except Exception:
        return []
    rows = _parse_lamedb_v5(path) if "/5/" in head else _parse_lamedb_v4(path)
    # Some images may announce /5/ but keep classic blocks after conversion.
    if not rows and "/5/" in head:
        rows = _parse_lamedb_v4(path)
    return rows


def _decorate(row):
    row = dict(row or {})
    ref = str(row.get("ref") or "")
    name = str(row.get("name") or "")
    explicit, clean = _prefix_region(name)
    if not explicit:
        bq_region, _ = _prefix_region(row.get("bouquet_label") or "")
        explicit = bq_region
    dvb = row.get("dvb") or channel_mapper.parse_dvb_ids(ref)
    row["dvb"] = dvb
    service_type = str(row.get("service_type") or channel_mapper.classify_service_ref(ref))
    row["service_type"] = service_type
    orbital = orbital_position_from_namespace(dvb.get("namespace")) if service_type == "SAT" else None
    row.update({
        "canonical_ref": canonical_service_ref(ref),
        "clean_name": clean or name,
        "normalized": channel_mapper.normalize_name(clean or name),
        "fast_match_key": fast_name_key.normalize(clean or name),
        "explicit_region": explicit,
        "orbital": orbital,
        "satellite_zone": satellite_zone(orbital),
        "provider_name": str(row.get("provider_name") or "").strip(),
    })
    return row


def _bouquet_signature(bouquet_dir):
    paths = []
    try:
        for name in os.listdir(bouquet_dir):
            if name == "bouquets.tv" or (name.startswith("userbouquet.") and name.endswith(".tv")):
                paths.append(os.path.join(bouquet_dir, name))
    except Exception:
        pass
    return file_signature(paths)


def satellite_registry_signature(bouquet_dir=channel_mapper.BOUQUET_DIR):
    """Cheap signature of the live receiver service database."""
    return lamedb_signature(bouquet_dir)


def load_satellite_registry(bouquet_dir=channel_mapper.BOUQUET_DIR, force=False, path=SAT_PATH):
    candidates = _lamedb_candidates(bouquet_dir)
    sig = file_signature(candidates) if candidates else []
    cached = _read_json(path)
    if (not force and cached.get("version") == 7 and cached.get("signature") == sig
            and isinstance(cached.get("services"), list)):
        rows = [x for x in (cached.get("services") or []) if is_tv_service(x)]
        return rows, False

    raw = []
    discovery = ""
    active = ""
    # beta39: the receiver's current lamedb is the deterministic SRP authority.
    # Try the newest supported file first; if it is malformed, fall back to the
    # next candidate, then to Enigma2's Reception Lists API.
    for dbpath in candidates:
        parsed = _parse_lamedb(dbpath)
        if parsed:
            raw = parsed
            active = dbpath
            discovery = "%s (%s)" % (os.path.basename(dbpath), "v5" if _lamedb_is_v5(dbpath) else "v4")
            break
    if not raw:
        raw = _scan_reception_lists()
        discovery = "reception_lists_fallback"

    rows = [_decorate(x) for x in raw]
    rows = [x for x in rows if is_tv_service(x)]
    _write_json(path, {"version": 7, "signature": sig, "discovery": discovery,
                       "active_lamedb": active, "services": rows})
    log.info("Satellite registry rebuilt from %s: %d TV service(s)", discovery or "unknown", len(rows))
    return rows, True



def load_iptv_registry(bouquet_dir=channel_mapper.BOUQUET_DIR, force=False, path=IPTV_PATH):
    sig = _bouquet_signature(bouquet_dir)
    cached = _read_json(path)
    if (not force and cached.get("version") == 4 and cached.get("signature") == sig
            and isinstance(cached.get("services"), list)):
        return cached.get("services") or [], False
    try:
        catalog = channel_mapper.scan_bouquets(bouquet_dir, use_cache=True, fast_cache=False)
    except Exception:
        catalog = []
    rows = []
    seen = set()
    for item in catalog:
        if channel_mapper.classify_service_ref(item.get("ref")) != "IPTV":
            continue
        decorated = _decorate(item)
        ref = decorated.get("ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        decorated["registry_origin"] = "bouquet"
        rows.append(decorated)
    _write_json(path, {"version": 4, "signature": sig, "services": rows})
    log.info("IPTV registry rebuilt from bouquets: %d service(s)", len(rows))
    return rows, True


def load_or_rebuild(bouquet_dir=channel_mapper.BOUQUET_DIR, force=False, path=PATH):
    sat, sat_rebuilt = load_satellite_registry(bouquet_dir, force=force)
    iptv, iptv_rebuilt = load_iptv_registry(bouquet_dir, force=force)
    services = sat + iptv
    # Keep a tiny compatibility summary for old diagnostics; do not duplicate all registry data.
    _write_json(path, {"version": 3, "satellite_services": len(sat), "iptv_services": len(iptv),
                       "services": services if len(services) < 3000 else []})
    return services, bool(sat_rebuilt or iptv_rebuilt)


def load(path=PATH):
    data = _read_json(path)
    if isinstance(data.get("services"), list) and data.get("services"):
        return data
    sat = _read_json(SAT_PATH).get("services") or []
    iptv = _read_json(IPTV_PATH).get("services") or []
    return {"version": 3, "services": sat + iptv,
            "satellite_services": len(sat), "iptv_services": len(iptv)}


def save(data, path=PATH):
    return _write_json(path, data)


def region_compatible(expected_codes, service):
    expected = set(str(x) for x in (expected_codes or []))
    if not expected:
        return True, "no-country-filter"
    explicit = str((service or {}).get("explicit_region") or "")
    if explicit:
        if explicit == "MENA":
            ok = bool(expected & MENA_CODES)
        elif explicit in MENA_CODES:
            ok = explicit in expected or "MENA" in expected
        else:
            ok = explicit in expected
        return ok, "iptv-prefix:%s" % explicit

    zone = str((service or {}).get("satellite_zone") or "")
    if zone == "MENA" and expected & EUROPE_CODES and not expected & MENA_CODES:
        return False, "satellite-zone:MENA"
    if zone == "EUROPE" and expected & MENA_CODES and not expected & EUROPE_CODES:
        return False, "satellite-zone:EUROPE"
    return True, "compatible"
