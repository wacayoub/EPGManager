# -*- coding: utf-8 -*-
"""Low-cost source ownership and curated MENA coverage overlay.

This module does not change the frozen beta1/beta12 mapper.  It only resolves
cases where more than one already-selected source maps the same receiver
Service Reference, and adds a very small set of exact, known provider IDs for
well-known services that the generic name matcher cannot recognise.

No network access and no receiver scan are performed here.  The optional
coverage overlay reads the already cached SAT registry once and persists its
result. Manual mappings always win.
"""
from __future__ import print_function
import json
import os
import re
from xml.etree import ElementTree as ET

from . import channel_registry, source_catalog, source_channel_cache, source_quality
from .logger import get_logger

log = get_logger(__name__)
_MENA = set("MA DZ TN LY EG SA AE QA KW BH OM JO LB PS IQ SY YE MENA".split())
_EUROPE = set("FR BE NL DE AT CH IT ES PT UK IE GR TR RO BG HR RS SI SK CZ DK SE NO FI PL".split())
OVERLAY_PATH = "/etc/enigma2/epgmanager_mena_overlay.json"
OVERLAY_VERSION = 4

# IDs verified against the corresponding provider catalogues.  Arabic IDs are
# preferred where the provider publishes parallel AR/EN variants.
_OVERLAY_SPECS = {
    "ext_epgshare_bein": [
        (("beinsportsnews", "beinsportsnewshd"), ("NEWS_DIGITAL_Mono_AR.bein",)),
        (("beinsports", "beinsportsfta"), ("bein_SPORTS_FTA_DIGITAL_Mono_AR.bein",)),
        (("beinsportsxtra1",), ("beIN_SPORTS_XTRA1_Digital_Mono_AR.bein",)),
        (("beinsportsxtra2",), ("beIN_SPORTS_XTRA2_Digital_Mono_AR.bein",)),
        (("beinsportsxtra3",), ("logos-_beINSPORTSXTRA3_AR.bein",)),
        (("beinsportsxtra4",), ("beIN_SPORTS_XTRA_04_bein.com_AR.bein",)),
        (("beinsportsxtra5",), ("beIN_SPORTS_XTRA_05_bein.com_AR.bein",)),
        (("beinsportsxtra6",), ("beIN_SPORTS_XTRA_06_bein.com_AR.bein",)),
        (("beinsportsxtra7",), ("beIN_SPORTS_XTRA_07_bein.com_AR.bein",)),
        (("beinsportsxtra8",), ("beIN_SPORTS_XTRA_08_Bein.com_AR.bein",)),
        (("beinsportsxtra9",), ("beIN_SPORTS_XTRA_Digital_09_Bein.com_AR.bein",)),
        (("bein4k", "bein4kuhd", "beinsports4k", "beinsports4khdr"), ("4k_DIGITAL_Mono_AR.bein",)),
    ],
    "ext_epgshare_united_arab_emirates": [
        # Keep the Arabic Al Jazeera service distinct from English.  This is
        # especially important for receiver labels such as "Al Jazeera HD".
        (("aljazeera", "aljazeerahd", "aljazeeraarabic"), ("Al.Jazeera.HD.ae",)),
        (("aljazeeradocumentary",), ("Al.Jazeera.Documentary.HD.ae",)),
        (("aljazeeramubasher", "aljazeeramobasher"), ("Al.Jazeera.Mobasher.HD.ae",)),
        (("aljazeeramubasher2", "aljazeeramobasher2"), ("Al.Jazeera.Mobasher.2.HD.ae",)),
        (("attaqafia", "arrabiaa", "arrabia"), ("AlThaqafeya.HD.ae",)),
        (("abudhabi", "abudhabitv", "abudhabitvhd"), ("Abu.Dhabi.HD.ae",)),
        (("adsport1", "adsports1", "abudhabisport1", "abudhabisports1"), ("AD.Sports.1.HD.ae",)),
        (("adsport2", "adsports2", "abudhabisport2", "abudhabisports2"), ("AD.Sports.2.HD.ae",)),
        (("dubaitv", "dubai", "dubaitvhd"), ("Dubai.HD.ae", "Dubai.ae")),
        (("dubaione", "dubaionehd"), ("Dubai.One.HD.ae",)),
        (("dubaisports1", "dubaisport1"), ("Dubai.Sports.1.HD.ae", "Dubai.Sports.1.ae")),
        (("dubaisports2", "dubaisport2"), ("Dubai.Sports.2.ae",)),
        (("alhayat", "elhayat", "alhayattv"), ("Al.Hayat.ae", "El.Hayat.TV.ae")),
        (("alkahirawalnas", "alqahirawalnas", "cairowalnas"), ("Al.Kahera.Wal.Nas.ae",)),
        (("alkahirawalnas2", "alqahirawalnas2", "cairowalnas2"), ("Al.Kahera.Wal.Nas.2.ae",)),
        (("alrasheed", "alrasheedtv"), ("Al.Rasheed.TV.HD.ae",)),
        (("cbc", "cbchd"), ("CBC.ae",)),
        (("cbcdrama",), ("CBC.Drama.ae",)),
        (("cbcsofra",), ("CBC.Sofra.ae",)),
        (("dubairacing1",), ("Dubai.Racing.1.HD.ae",)),
        (("dubairacing2",), ("Dubai.Racing.2.ae",)),
        (("dwarabia", "dwarabic", "dwarabiya"), ("DW.Arabia.HD.ae",)),
        (("trtarabi", "trtarabiya", "trtarabic"), ("TRT.Arabiya.ae",)),
        (("tunisia nat1", "tunisianat1", "watania1"), ("Tunisia.Nat.1.ae",)),
        (("tunisia nat2", "tunisianat2", "watania2"), ("Tunisia.Nat.2.ae",)),
        (("sudantv", "sudan"), ("Sudan.TV.ae",)),
        (("syriatv", "syria"), ("Syria.TV.HD.ae",)),
        (("duachannel", "duaachannel"), ("Dua.Channel.ae",)),
        (("echorouknews",), ("Echorouk.News.ae",)),
        (("echorouktv",), ("Echorouk.TV.HD.ae",)),
        (("afaqtv",), ("Afaq.TV.ae",)),
        (("aghapytv",), ("Aghapy.TV.ae",)),
        (("ajmantv", "ajman"), ("Ajman.TV.HD.ae",)),
        (("adentv",), ("Aden.TV.ae",)),
        (("adenalmustakilla", "adenalmustakillah"), ("Aden.Almustakillah.HD.ae",)),
        (("cima",), ("Cima.ae",)),
        (("cnarabia", "cnarabic"), ("CN.Arabic.ae",)),
        (("taha",), ("Taha.ae",)),
        (("tanasuhtv", "altanasuhtv"), ("Al.Tanasuh.TV.ae",)),
        (("spacetoon", "spacetoontv"), ("Space.Toon.ae",)),
        (("toyoraljanah", "toyoraljanahtv"), ("Toyor.Aljanah.TV.ae",)),
        (("taybatv",), ("Tayba.TV.ae",)),
        (("tchadtv", "teletchad"), (u"Télé.Tchad.ae",)),
    ],
    # France is intentionally fallback-only. These two services are useful when
    # no selected MENA/local source owns the same receiver reference.
    "ext_epgshare_france": [
        # France is always lower priority than local/MENA. These Moroccan IDs
        # are therefore used only when the selected Arabic/local source has no
        # actual programme rows for the same receiver service.
        (("telemaroc",), (u"Télé.Maroc.fr",)),
        (("alaoula", "alaoulainter", "alaoulainterhd"), ("Al.Aoula.fr",)),
        (("almaghribia", "almaghribiya"), ("Al.Maghribia.fr",)),
        (("attaqafia", "arrabiaa", "arrabia"), (u"Arrabiâ.fr",)),
        (("assadisa", "assadissa"), ("Assadissa.fr",)),
        (("arryadia", "arryadiahd", "arryadiatnt"), ("Arryadia.fr",)),
        (("2mmaroc", "2mmonde", "2mnational", "2m"), ("2M.Maroc.fr",)),
        (("medi1tv",), ("Medi1TV.fr",)),
        (("dreamworks",), ("DREAMWORKS.fr",)),
    ],
    "snrt": [
        (("tamazight",), ("Tamazight",)),
        (("almaghribia", "almaghribiya"), ("AlMaghribiya",)),
        (("attaqafia", "arrabiaa", "arrabia"), ("Arrabiaa",)),
        (("assadisa", "assadissa"), ("Assadisa",)),
        (("alaoula",), ("AlAoula",)),
    ],
}


def _compact(value):
    raw = str(value or "").lower()
    raw = raw.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    # receiver quality suffixes are presentation, not channel identity
    raw = re.sub(r"(?:fullhd|fhd|uhd|hd|sd|hdr|hevc|h265)$", "", raw)
    return raw


def _countries(item):
    return set(str(x or "").upper() for x in ((item or {}).get("countries") or []) if x)


def source_tier(item):
    """Higher wins only when two selected sources claim the same service ref."""
    item = item or {}
    countries = _countries(item)
    lang = str(item.get("language") or "").lower()
    region = str(item.get("region") or "").upper()
    kind = str(item.get("kind") or "").lower()
    provider = str(item.get("provider") or "").upper()
    # Rytec claims already come from provider-supplied exact Service References.
    # Treat them as a strong neutral fallback: below preferred local/MENA Arabic
    # guides, but above generic regional guesses.
    if provider in ("RYTEC", "KOALA"):
        return 700
    mena = bool(countries & _MENA or "MENA" in region or "ARAB" in region)
    europe = bool(countries & _EUROPE or "EUROPE" in region)
    # RC31 Arabic-first MENA ownership.  The cloud generator already chooses
    # native Arabic timelines before English fallbacks; mirror that policy on
    # the receiver when multiple *selected* sources claim the same service.
    # Premium beIN/OSN cloud feeds intentionally have blank language metadata
    # because their visible titles can be hybrid EN/AR while descriptions are AR.
    if item.get("cloud_feed") and mena:
        if lang.startswith("ar"):
            return 1120
        if not lang:
            return 1060
        if lang.startswith("fr"):
            return 1010
        if lang.startswith("en"):
            return 980
        return 960
    if kind == "local" and mena:
        return 1000
    if mena and lang.startswith("ar"):
        return 940
    if mena and not lang:
        return 860
    if mena and lang.startswith("fr"):
        return 820
    if mena and lang.startswith("en"):
        return 780
    if kind == "local":
        return 700
    if europe:
        return 300
    return 500




# beta23: provider/satellite-aware guard.  The golden mapper is unchanged;
# this layer only rejects a source claim that is incompatible with the DVB
# provider/orbital position of the already-known receiver service.
_EUROPE_PROVIDER_TOKENS = (
    "sky italia", "sky deutschland", "sky uk", "cyfrowy", "polsat", "platforma canal",
    "canal+", "canalsat", "nc+", "tivusat", "rai", "mediaset", "m7", "hd+",
    "movistar", "orange france", "orange polska", "tvn", "telenor", "viasat",
    "nova", "total tv", "upc", "freeview", "freesat"
)
_MENA_PROVIDER_TOKENS = (
    "snrt", "bein", "be in", "osn", "mbc", "rotana", "abu dhabi", "dubai",
    "al jazeera", "alkass", "ksa", "saudi", "qatar", "nilesat", "arabsat",
    "shahid", "ssc", "etisalat", "ooredoo"
)
# Only strong DVB-provider signatures are used for European country routing.
# Generic names such as "CANAL+" are deliberately not classified here unless
# the satellite/provider string makes the country unambiguous.
_PROVIDER_COUNTRY_HINTS = (
    (("cyfrowy polsat", "polsat", "platforma canal+", "nc+", "tvn"), {"PL"}),
    (("sky italia", "tivusat", "tivùsat", "mediaset", "rai"), {"IT"}),
    (("sky deutschland", "hd+", "austriasat"), {"DE", "AT"}),
    (("movistar", "digital+ esp", "canal+ esp"), {"ES"}),
    (("canal digitaal", "canaldigitaal"), {"NL"}),
    (("telesat belg", "tv vlaanderen"), {"BE"}),
    (("freesat", "freeview", "sky uk", "bbc sat"), {"UK", "IE"}),
)
_AMBIGUOUS_GLOBAL_BRANDS = (
    "disney", "discovery", "national geographic", "nat geo", "bein sports",
    "cartoon network", "nickelodeon", "cnn", "bbc", "sky news", "animal planet"
)
_EXPLICIT_MENA_NAMES = (
    "al jazeera", "mbc", "rotana", "abu dhabi", "dubai sports", "dubai racing",
    "dw arabia", "trt arabi", "sky news arabia", "cnbc arabia", "2m", "al aoula",
    "tamazight", "al maghribia", "assadissa", "attaqafia", "arryadia", "medi1"
)
_REGISTRY_CACHE_SIG = None
_REGISTRY_CACHE = {}
_LOCAL_EVENT_CACHE = {}

# Small, high-value remote catalogues that can safely contribute exact-name
# mappings from the already-synchronised Channel-ID cache. No network is used
# here; if a cache is absent, the source simply contributes nothing.
_CACHE_EXACT_SOURCE_IDS = set((
    "ext_openepg_morocco_1", "ext_openepg_morocco_2",
    "ext_openepg_egypt_1", "ext_openepg_egypt_2",
    "ext_openepg_saudi_arabia_1", "ext_openepg_saudi_arabia_3", "ext_openepg_saudi_arabia_5",
    "ext_openepg_saudi_arabia_6",
    "ext_openepg_qatar_4", "ext_openepg_uae_6",
    "ext_epgshare_bein", "ext_epgshare_al_jazeera", "ext_epgshare_united_arab_emirates",
    "ext_epgshare_egypt",
))
_SAUDI_PRIORITY_IDS = set(("ext_openepg_saudi_arabia_1", "ext_openepg_saudi_arabia_3", "ext_openepg_saudi_arabia_5"))
_EGYPT_PRIORITY_IDS = set(("ext_openepg_egypt_1", "ext_openepg_egypt_2", "ext_epgshare_egypt"))
_SAUDI_SERVICE_TOKENS = ("saudi", "ksa", "ssc", "quran", "sunnah", "alekhb", "al ekhbar", "thikrayat", "sbc")
_EGYPT_SERVICE_TOKENS = ("egypt", "masr", "maspero", "cbc", "dmc", "on time", "on sport", "al nahar", "el nahar", "al hayat", "cairo", "sada el balad", "mehwar")
_UAE_PRIORITY_IDS = set(("ext_epgscrapers_dubaiplus", "ext_openepg_uae_6", "ext_epgshare_united_arab_emirates"))
_UAE_SERVICE_TOKENS = ("abu dhabi", "abudhabi", "ad sport", "ad sports", "dubai", "ajman", "sharjah", "yas tv", "yas sports")
_EGYPT_TITLE_RESCUE_TOKENS = ("al hayat", "el hayat", "al kahera wal nas", "al qahera wal nas", "al rasheed", "cbc")

# Dynamic aliases: resolve the actual XMLTV ID from the current UAE6 lightweight
# catalogue instead of hard-coding provider IDs.  This keeps AD/Dubai sports
# resilient when OpenEPG changes IDs while still mapping exact service families.
_UAE_CATALOG_ALIAS_GROUPS = (
    (("adsport1", "adsports1", "abudhabisport1", "abudhabisports1"),
     ("adsport1", "adsports1", "abudhabisport1", "abudhabisports1")),
    (("adsport2", "adsports2", "abudhabisport2", "abudhabisports2"),
     ("adsport2", "adsports2", "abudhabisport2", "abudhabisports2")),
    (("dubaisports1", "dubaisport1"), ("dubaisports1", "dubaisport1")),
    (("dubaisports2", "dubaisport2"), ("dubaisports2", "dubaisport2")),
    (("dubairacing1",), ("dubairacing1",)),
    (("dubairacing2",), ("dubairacing2",)),
    (("abudhabitv", "abudhabitvhd"), ("abudhabitv", "abudhabitvhd")),
)

def _preferred_source_bonus(item, service):
    sid = str(source_catalog.mapping_source_id(item) or "").lower()
    text = ("%s %s" % (str((service or {}).get("name") or ""), str((service or {}).get("provider_name") or ""))).lower()
    bonus = 0
    # Tiny generic tie-breaker: Arabic OpenEPG variants requested by the user
    # stay useful, but never outrank a provider/country conflict.
    if sid == "ext_openepg_saudi_arabia_1": bonus += 8
    elif sid == "ext_openepg_saudi_arabia_3": bonus += 6
    elif sid == "ext_openepg_saudi_arabia_5": bonus += 4
    elif sid == "ext_openepg_egypt_1": bonus += 8
    elif sid == "ext_openepg_egypt_2": bonus += 2
    if sid in _SAUDI_PRIORITY_IDS and any(tok in text for tok in _SAUDI_SERVICE_TOKENS):
        bonus += 140
    if sid in _EGYPT_PRIORITY_IDS and any(tok in text for tok in _EGYPT_SERVICE_TOKENS):
        bonus += 140
    if sid in _UAE_PRIORITY_IDS and any(tok in text for tok in _UAE_SERVICE_TOKENS):
        bonus += 180
    # rc55: Dubai+ is the official Dubai Media guide. For Dubai-branded TV,
    # prefer it over Sport24 and generic UAE fallbacks when both are healthy.
    if sid == "ext_epgscrapers_dubaiplus" and any(tok in text for tok in (
            "dubai", "sama dubai", "noor dubai")):
        bonus += 220
    # rc57: STC is intentionally a broad fallback catalogue. It only receives
    # a strong ownership bonus for STC-branded channels, so provider-specific
    # feeds (beIN/OSN/Shahid/Rotana/Dubai+/Sport24) keep precedence.
    if sid == "ext_epgscrapers_stctv" and any(tok in text for tok in (
            "stc tv", "stctv", "stc sports", "stc sport", "stc family",
            "stc cinema", "stc drama", "shabab al bomb")):
        bonus += 230
    # rc61: STARZPLAY GCC is a broad union catalogue. Keep provider-specific
    # feeds authoritative on overlaps; grant STARZPLAY ownership only for its
    # own branded services so it behaves as a useful fallback, not a hijacker.
    if sid == "ext_epgscrapers_starzplay" and any(tok in text for tok in (
            "starzplay", "starz play", "starzplay sports", "starz play sports")):
        bonus += 225
    # rc58: the dedicated Al Jazeera feed owns the Al Jazeera family when
    # healthy. Generic Qatar/OpenEPG remains a fallback, never the first owner.
    if sid == "ext_epgscrapers_aljazeera" and any(tok in text for tok in (
            "al jazeera", "aljazeera", "الجزيرة")):
        bonus += 235
    # rc66: Al Kass 1-8 now have their own official direct source. Keep
    # generic Qatar/Rytec/OpenEPG entries as fallbacks only.
    if sid == "ext_epgscrapers_alkass" and any(tok in text for tok in (
            "al kass", "alkass", "الكاس", "الكأس")):
        bonus += 245
    if sid == "ext_epgshare_united_arab_emirates" and any(tok in text for tok in _EGYPT_TITLE_RESCUE_TOKENS):
        bonus += 190
    # 8.2.1 single-owner provider routing. When several country catalogues carry
    # the exact same pan-MENA channel, the broadcaster/provider context should
    # choose the primary owner instead of a generic alphabetical tie-breaker.
    if "bein" in text:
        if sid == "ext_epgshare_bein":
            bonus += 260
        elif sid.startswith("ext_openepg_qatar_"):
            bonus += 220
    if "osn" in text:
        if sid in ("ext_openepg_uae_6", "ext_epgshare_united_arab_emirates"):
            bonus += 220
        elif sid.startswith("ext_openepg_egypt_"):
            bonus += 80
    if sid == "ext_openepg_egypt_1" and any(tok in text for tok in _EGYPT_TITLE_RESCUE_TOKENS):
        bonus -= 40
    # Strong country-branded UAE services must not drift to Saudi/Egypt feeds.
    if (sid in _SAUDI_PRIORITY_IDS or sid in _EGYPT_PRIORITY_IDS) and any(tok in text for tok in _UAE_SERVICE_TOKENS):
        bonus -= 500
    return bonus

def _catalog_identity_keys(row):
    """Safe exact-name keys from OpenEPG display name + dotted XMLTV ID.

    This never fuzzy-maps. It only creates equivalent textual identities such
    as ``Saudi.Quran.HD.sa`` -> ``saudiquran`` so the existing provider/satellite
    safety gate can validate the final claim.
    """
    row = row or {}
    values = []
    display = str(row.get("display_name") or "").strip()
    cid = str(row.get("channel_id") or "").strip()
    if display:
        values.append(display)
    if cid:
        parts = [x for x in re.split(r"[._]+", cid) if x]
        country = set(("sa", "ae", "eg", "qa", "ma", "ar", "en", "fr", "mena"))
        quality = set(("hd", "fhd", "uhd", "4k", "sd", "hdr", "hevc", "h265"))
        while parts and parts[-1].lower() in country:
            parts.pop()
        while parts and parts[-1].lower() in quality:
            parts.pop()
        if parts:
            values.append(" ".join(parts))
    out = []
    for value in values:
        for key in _identity_variants(value):
            if key and key not in out:
                out.append(key)
    return out


def _identity_variants(value):
    """Return conservative identity variants for provider catalogue labels.

    EPGShare IDs often contain a presentation token before a numeric service
    variant (for example ``Al.Kahera.Wal.Nas.TV.2.eg``) while the receiver
    service is named ``Al Kahera Wal Nas 2``.  Removing only the standalone
    presentation words TV/CHANNEL keeps this exact-name overlay deterministic
    without turning it into fuzzy matching.
    """
    key = _compact(value)
    out = [key] if key else []
    if not key:
        return out
    for suffix in ("tv", "channel"):
        if key.endswith(suffix) and len(key) > len(suffix) + 3:
            short = key[:-len(suffix)]
            if short and short not in out:
                out.append(short)
    # Presentation token immediately before a numeric variant: tv2 -> 2.
    for token in ("tv", "channel"):
        alt = re.sub(r"%s(?=[0-9]+$)" % token, "", key)
        if alt and alt != key and alt not in out:
            out.append(alt)
    return out


def _receiver_identity_keys(name):
    return _identity_variants(name)


def _expanded_catalog_rows(mid):
    """Return cache rows plus safe source-specific bilingual aliases.

    OpenEPG Egypt 1/2 are parallel 128-channel variants at the provider level
    (Arabic vs English presentation).  When both lightweight catalogues have
    exactly the same non-trivial length, the English display label at position
    N is used only as an alias for the Egypt-1 channel ID at position N.  The
    imported programmes therefore stay Arabic from Egypt 1.  If the provider
    changes either list length, the positional bridge disables itself rather
    than guessing.
    """
    try:
        rows = list(source_channel_cache.get(mid) or [])
    except Exception:
        rows = []
    if mid != "ext_openepg_egypt_1":
        return rows
    try:
        english = list(source_channel_cache.get("ext_openepg_egypt_2") or [])
    except Exception:
        english = []
    if not rows or len(rows) != len(english) or len(rows) < 20 or len(rows) > 500:
        return rows
    expanded = list(rows)
    for ar_row, en_row in zip(rows, english):
        cid = str((ar_row or {}).get("channel_id") or "").strip()
        alias = str((en_row or {}).get("display_name") or "").strip()
        if not cid or not alias:
            continue
        x = dict(ar_row or {})
        x["channel_id"] = cid
        x["display_name"] = alias
        x["bridge"] = "egypt2-en-name"
        expanded.append(x)
    return expanded


def _build_uae_catalog_alias_overlay(selected_mids, manual_refs):
    """Resolve AD/Dubai services from the current UAE6 catalogue dynamically."""
    if "ext_openepg_uae_6" not in set(str(x or "").lower() for x in (selected_mids or [])):
        return {}
    try:
        rows = list(source_channel_cache.get("ext_openepg_uae_6") or [])
    except Exception:
        rows = []
    if not rows:
        return {}
    receiver = _receiver_index()
    recv_by_key = {}
    for ref, service in receiver.items():
        if ref in manual_refs:
            continue
        for key in _receiver_identity_keys((service or {}).get("name")):
            recv_by_key.setdefault(key, []).append(ref)

    row_keys = []
    for row in rows:
        keys = set(_catalog_identity_keys(row))
        if keys:
            row_keys.append((row, keys))
    bucket = {}
    for recv_aliases, source_aliases in _UAE_CATALOG_ALIAS_GROUPS:
        refs = []
        for alias in recv_aliases:
            for ref in recv_by_key.get(_compact(alias)) or []:
                if ref not in refs:
                    refs.append(ref)
        if not refs:
            continue
        wanted = set(_compact(x) for x in source_aliases)
        for row, keys in row_keys:
            if not (keys & wanted):
                continue
            cid = str((row or {}).get("channel_id") or "").strip().lower()
            if not cid:
                continue
            dest = bucket.setdefault(cid, [])
            for ref in refs:
                if ref not in dest:
                    dest.append(ref)
    return {"ext_openepg_uae_6": bucket} if bucket else {}


def _build_cached_exact_overlay(selected_mids, manual_refs):
    """Build exact-name mappings from already cached XMLTV channel headers.

    This is deliberately cache-only and exact-name-only. It gives selected
    Morocco/Egypt/Saudi/Qatar/UAE Arabic guides useful coverage without an
    Update Source step or any network/receiver scan in Import All.
    """
    wanted = set(str(x or "").lower() for x in (selected_mids or [])) & _CACHE_EXACT_SOURCE_IDS
    if not wanted:
        return {}
    receiver = _receiver_index()
    by_name = {}
    for ref, service in receiver.items():
        if ref in manual_refs:
            continue
        for key in _receiver_identity_keys((service or {}).get("name")):
            by_name.setdefault(key, []).append(ref)
    out = {}
    for mid in wanted:
        rows = _expanded_catalog_rows(mid)
        if not rows:
            continue
        bucket = {}
        for row in rows:
            cid = str((row or {}).get("channel_id") or "").strip()
            name = str((row or {}).get("display_name") or cid).strip()
            if not cid or not name:
                continue
            refs = []
            for key in _catalog_identity_keys(row):
                for ref in by_name.get(key) or []:
                    if ref not in refs:
                        refs.append(ref)
            if refs:
                bucket[str(cid).lower()] = list(refs)
        if bucket:
            out[mid] = bucket
    return out


def _source_family(item):
    countries = _countries(item)
    region = str((item or {}).get("region") or "").upper()
    if countries & _MENA or "MENA" in region or "ARAB" in region:
        return "MENA"
    if countries & _EUROPE or "EUROPE" in region:
        return "EUROPE"
    return ""


def _provider_family(value):
    text = str(value or "").lower()
    if any(token in text for token in _MENA_PROVIDER_TOKENS):
        return "MENA"
    if any(token in text for token in _EUROPE_PROVIDER_TOKENS):
        return "EUROPE"
    return ""


def _provider_countries(value):
    text = str(value or "").lower()
    out = set()
    for tokens, countries in _PROVIDER_COUNTRY_HINTS:
        if any(token in text for token in tokens):
            out.update(countries)
    return out


def _explicit_mena_service(name):
    text = str(name or "").lower()
    return any(token in text for token in _EXPLICIT_MENA_NAMES)


def _ambiguous_global_service(name):
    text = str(name or "").lower()
    return any(token in text for token in _AMBIGUOUS_GLOBAL_BRANDS)


def _receiver_index():
    global _REGISTRY_CACHE_SIG, _REGISTRY_CACHE
    sig = _registry_signature()
    if sig and sig == _REGISTRY_CACHE_SIG and _REGISTRY_CACHE:
        return _REGISTRY_CACHE
    out = {}
    try:
        with open(channel_registry.SAT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for service in (data.get("services") or [] if isinstance(data, dict) else []):
            ref = channel_registry.canonical_service_ref((service or {}).get("ref"))
            if ref:
                out[ref] = service
    except Exception:
        out = {}
    _REGISTRY_CACHE_SIG = sig
    _REGISTRY_CACHE = out
    return out


def _claim_allowed(item, service):
    """Provider/orbital safety gate for an already-known receiver service.

    This never invents a mapping. It only rejects an existing source claim
    when DVB provider/orbital metadata gives strong evidence that the source
    belongs to the wrong region/country. France/Europe stays available as a
    fallback on MENA satellites when no Arabic/local owner exists.
    """
    if not service:
        return True
    family = _source_family(item)
    source_countries = _countries(item)
    name = str((service or {}).get("name") or "")
    provider = str((service or {}).get("provider_name") or "")
    pf = _provider_family(provider)
    provider_countries = _provider_countries(provider)
    try:
        orbital = float((service or {}).get("orbital"))
    except Exception:
        orbital = None

    explicit_mena = (pf == "MENA" or _explicit_mena_service(name))

    # A MENA guide can never own a service whose DVB provider is explicitly
    # European. This fixes Arabic Disney/NatGeo/beIN on Hotbird/Astra.
    if family == "MENA" and pf == "EUROPE":
        return False

    # On Astra positions, generic/global brands must not receive a MENA guide
    # unless DVB provider/name explicitly identifies the service as MENA.
    if family == "MENA" and orbital is not None and (
            (18.5 <= orbital <= 20.0) or (22.5 <= orbital <= 24.0) or
            (27.5 <= orbital <= 29.0)):
        if not explicit_mena:
            return False

    # Hotbird is mixed. Provider is mandatory for globally duplicated brands
    # (Disney, NatGeo, Discovery, beIN, etc.) before a MENA feed is accepted.
    if family == "MENA" and orbital is not None and 12.0 <= orbital <= 14.0:
        if _ambiguous_global_service(name) and not explicit_mena:
            return False

    # Provider-aware European language/country routing. For ambiguous global
    # brands on Hotbird/Astra, a strongly identified Polish/Italian/etc. DVB
    # provider rejects a different European country's EPG. This prevents, for
    # example, a French Disney guide from replacing a Polish Disney service.
    if family == "EUROPE" and provider_countries and source_countries:
        if not (provider_countries & source_countries):
            if (_ambiguous_global_service(name) or
                    (orbital is not None and (12.0 <= orbital <= 14.0 or
                                               18.5 <= orbital <= 20.0))):
                return False

    # beta32: explicit country-branded MENA services are a hard routing signal.
    # "AD Sports" / "Dubai Sports" are UAE services, so a Saudi/Egypt-specific
    # feed cannot own them. Generic MENA feeds remain allowed as fallback.
    if family == "MENA" and source_countries and "MENA" not in source_countries:
        lname = name.lower()
        brand_country = None
        if any(tok in lname for tok in _UAE_SERVICE_TOKENS):
            brand_country = "AE"
        elif any(tok in lname for tok in _SAUDI_SERVICE_TOKENS):
            brand_country = "SA"
        elif any(tok in lname for tok in _EGYPT_SERVICE_TOKENS):
            brand_country = "EG"
        if brand_country and brand_country not in source_countries:
            return False

    return True



def _prefer_arabic_quality(service):
    """Arabic programme content is preferred on strong MENA satellites/providers."""
    service = service or {}
    if _provider_family(service.get("provider_name")) == "MENA" or _explicit_mena_service(service.get("name")):
        return True
    try:
        orbital = float(service.get("orbital"))
    except Exception:
        return False
    return ((-9.5 <= orbital <= -6.0) or (24.0 <= orbital <= 26.8))

def _local_program_ids(item, epg_dir):
    """Return channel IDs that actually have programme rows in one local XMLTV.

    Used only for local sources involved in duplicate ownership. This prevents
    an empty SNRT channel header from suppressing a working Morocco/France
    fallback while keeping local EPGManager priority when it really has events.
    """
    if not epg_dir or str((item or {}).get("kind") or "").lower() != "local":
        return None
    filename = str((item or {}).get("output_file") or "")
    if not filename:
        return None
    path = os.path.join(epg_dir, filename)
    try:
        st = os.stat(path)
        sig = (int(st.st_mtime), int(st.st_size))
    except Exception:
        return None
    cached = _LOCAL_EVENT_CACHE.get(path)
    if cached and cached[0] == sig:
        return cached[1]
    ids = set()
    try:
        for event, elem in ET.iterparse(path, events=("start", "end")):
            tag = str(elem.tag or "").rsplit("}", 1)[-1]
            if event == "start" and tag == "programme":
                cid = str(elem.get("channel") or "").strip().lower()
                if cid:
                    ids.add(cid)
            if event == "end":
                elem.clear()
    except Exception:
        return None
    _LOCAL_EVENT_CACHE[path] = (sig, ids)
    return ids


def ownership_rank(item, service=None):
    """Deterministic per-service source ownership rank; higher wins.

    Unlike the global import ordering, this adds the already-curated provider /
    country bonus for the concrete receiver service. It is intentionally cheap
    and cache-free so Smart Mapping and Import All can resolve duplicate valid
    source claims to the same single owner without rescanning any EPG data.
    """
    item = item or {}
    return (int(source_tier(item)) + int(_preferred_source_bonus(item, service or {})),
            source_catalog.source_sort_key(item))


def sort_for_import(items):
    """Deterministic merge order: generic fallbacks first, preferred guides last.

    Native EPGImport can merge programme rows from several exact SRP sources.
    Importing higher-priority local/Arabic guides last lets them win overlapping
    events without suppressing useful unique programmes from Rytec/OpenEPG.
    """
    return sorted(list(items or []), key=lambda item: (source_tier(item), source_catalog.source_sort_key(item)))


def _manual_owners(store):
    out = {}
    try:
        rows = store.all() if store is not None else {}
    except Exception:
        rows = {}
    for key, rec in (rows or {}).items():
        if str((rec or {}).get("mode") or "").lower() != "manual":
            continue
        try:
            sid, _cid = str(key).split("::", 1)
        except ValueError:
            continue
        for ref in (rec or {}).get("refs") or []:
            out.setdefault(str(ref), set()).add(str(sid).lower())
    return out


def _registry_signature():
    try:
        st = os.stat(channel_registry.SAT_PATH)
        return [int(st.st_mtime), int(st.st_size)]
    except Exception:
        return []


def _load_overlay_cache(signature):
    try:
        with open(OVERLAY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if (isinstance(data, dict) and int(data.get("version") or 0) == OVERLAY_VERSION
                and data.get("registry_signature") == signature):
            return data.get("sources") or {}
    except Exception:
        pass
    return None


def _save_overlay_cache(signature, sources):
    try:
        tmp = OVERLAY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": OVERLAY_VERSION, "registry_signature": signature,
                       "sources": sources}, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, OVERLAY_PATH)
    except Exception:
        pass


def _build_overlay(selected_mids, manual_refs):
    signature = _registry_signature()
    if not signature:
        return {}
    cached = _load_overlay_cache(signature)
    if cached is not None:
        return cached
    try:
        with open(channel_registry.SAT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        services = data.get("services") or [] if isinstance(data, dict) else []
    except Exception:
        return {}
    by_name = {}
    for service in services:
        ref = str((service or {}).get("ref") or "").strip()
        if not ref or ref in manual_refs:
            continue
        key = _compact((service or {}).get("name"))
        if key:
            by_name.setdefault(key, []).append(ref)
    out = {}
    for mid in selected_mids:
        specs = _OVERLAY_SPECS.get(mid) or []
        if not specs:
            continue
        bucket = {}
        for aliases, channel_ids in specs:
            refs = []
            for alias in aliases:
                refs.extend(by_name.get(_compact(alias)) or [])
            if not refs:
                continue
            clean = []
            for ref in refs:
                if ref not in clean:
                    clean.append(ref)
            for cid in channel_ids:
                bucket[str(cid).lower()] = clean
        if bucket:
            out[mid] = bucket
    _save_overlay_cache(signature, out)
    return out


def apply(items, parsed_by_mid, store=None, epg_dir=None):
    """Validate exact SRP claims but keep their programme union.

    beta37 chose a single source owner for each Service Reference. That can
    remove programmes which exist only in a second selected source, explaining
    why Import Source could show events that Import All did not. With Rytec-
    style exact channels.xml maps, duplicate *valid* SRP claims are useful and
    Native EPGImport should receive all of them. Only explicit manual ownership
    suppresses a prepared claim; provider/satellite safety happens at map-build
    time, never again during Import All.
    """
    items = list(items or [])
    by_mid = {str(source_catalog.mapping_source_id(x) or "").lower(): x for x in items}
    maps = {str(k).lower(): {str(cid).lower(): list(refs or []) for cid, refs in (v or {}).items()}
            for k, v in (parsed_by_mid or {}).items()}
    manual = _manual_owners(store)
    # Import All consumes only persistent channels.xml SRP maps. Provider/
    # satellite safety was already applied when those maps were built. Do not
    # reinterpret source-container countries here (e.g. Tunisia 1 can validly
    # live inside OpenEPG Egypt1) or a correct exact SRP gets removed again.
    suppressed = 0
    provider_rejected = 0

    for mid, channels in list(maps.items()):
        item = by_mid.get(mid) or {}
        for cid, refs in list(channels.items()):
            keep = []
            for ref in refs or []:
                raw = str(ref or "").strip()
                if not raw:
                    continue
                cref = channel_registry.canonical_service_ref(raw) or raw
                owners = manual.get(raw) or manual.get(cref) or set()
                # Manual mapping is the only single-owner rule.
                if owners and mid not in owners:
                    suppressed += 1
                    continue
                # No provider/country arbitration during Import All. A row in
                # the persistent SRP map is already validated knowledge; this
                # path must only merge programmes, exactly like Native EPGImport.
                if raw not in keep:
                    keep.append(raw)
            if keep:
                channels[cid] = keep
            else:
                channels.pop(cid, None)

    return maps, {
        "suppressed_lower_priority_refs": suppressed,
        "curated_overlay_refs": 0,
        "cached_exact_overlay_refs": 0,
        "uae_catalog_overlay_refs": 0,
        "provider_rejected_refs": provider_rejected,
        "local_empty_fallback_refs": 0,
        "quality_scored_claims": 0,
        "quality_zero_program_claims": 0,
        "quality_placeholder_claims": 0,
    }
