# -*- coding: utf-8 -*-
"""Provider-aware SAT coverage/routing layer for beta17.

The proven beta12/beta1 mapper remains untouched.  This module runs *after*
that mapper and only enriches/routs generated channels.xml files when it can
identify the same channel with very high confidence.

Goals:
- cover service-name aliases the beta1 normalizer cannot infer (Athaqafia,
  Al Maghribia, numbered Almajd services, etc.);
- prefer Arabic/MENA EPG on MENA orbital positions (7W/8W, 25.5/26E,
  30.5E) while preferring European guides on Astra 19.2E;
- never touch explicit/manual mappings;
- never rebuild Reception Lists merely because programme data changed.
"""
from __future__ import print_function

import hashlib
import json
import os
import re
import shutil
import time
from collections import defaultdict
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from . import channel_mapper, channel_registry, source_catalog, source_metadata, srp_channel_map, service_variant_guard, programme_feed_policy, precision_match_engine
from .mapping_store import MappingStore
from .logger import get_logger

log = get_logger(__name__)
STATE_PATH = "/etc/enigma2/epgmanager_sat_coverage_state.json"
ALGORITHM_VERSION = 20

_MENA_COUNTRIES = set(("MA","DZ","TN","LY","EG","SA","AE","QA","KW","BH","OM","JO","LB","PS","IQ","SY","YE","MENA"))
_EUROPE_COUNTRIES = set(("FR","BE","NL","DE","AT","CH","IT","ES","PT","UK","IE","GR","TR","RO","BG","HR","RS","SI","SK","CZ","DK","SE","NO","FI","PL"))
_LOCAL_LANG = {
    "medi1tv":"ar", "chada_2m":"ar", "snrt":"ar", "arryadia":"ar",
    "bein_sports":"ar", "almajd":"ar",
}

# The Almajd local generator uses its own stable channel IDs.  For the local
# feed only, keep those IDs aligned with the receiver labels Almajd 01..14.
# This is deliberately source-scoped so it cannot alter unrelated Almajd/
# satellite naming discovered by the frozen beta1 mapper.
_ALMAJD_LOCAL_IDS = {
    "almajd":"almajd_01",
    "masah":"almajd_02",
    "basma":"almajd_03",
    "taghreed":"almajd_04",
    "majd":"almajd_05",
    "rawda":"almajd_06",
    "almajd-islamic-science":"almajd_07",
    "almajd-holy-hadeeth":"almajd_08",
    "almajd-holy-quran":"almajd_09",
    "elmajd-news":"almajd_10",
    "almajd-documentary":"almajd_11",
    "almajd-nature":"almajd_12",
    "dal-radio":"almajd_13",
    "al-ajaweed":"almajd_14",
}

# Compact aliases are deliberately explicit.  They solve known broadcaster
# naming differences without turning the beta1 matcher into a new fuzzy engine.
_COMPACT_ALIAS = {
    # SNRT Morocco
    "alaoula":"snrt_al_aoula", "alaoulainter":"snrt_al_aoula",
    "alaoulainternational":"snrt_al_aoula",
    "almaghribia":"snrt_al_maghribia", "almaghribiya":"snrt_al_maghribia",
    "almaghbiya":"snrt_al_maghribia", "almagrebia":"snrt_al_maghribia",
    "maghribia":"snrt_al_maghribia", "maghribiya":"snrt_al_maghribia",
    "arrabiaa":"snrt_attaqafia", "arrabia":"snrt_attaqafia",
    "attaqafia":"snrt_attaqafia", "athaqafia":"snrt_attaqafia",
    "althaqafia":"snrt_attaqafia", "thaqafia":"snrt_attaqafia",
    "tamazight":"snrt_tamazight", "tamazighttv":"snrt_tamazight",
    "assadisa":"snrt_assadissa", "assadissa":"snrt_assadissa",
    "assadissah":"snrt_assadissa",
    # Al Jazeera family - Arabic, English and Documentary must stay distinct.
    "aljazeera":"aljazeera_ar", "aljazeerachannel":"aljazeera_ar",
    "aljazeeraarabic":"aljazeera_ar", "aljazeeraalarabiya":"aljazeera_ar",
    "aljazeeraalrabya":"aljazeera_ar", "aljazeeraalarabia":"aljazeera_ar",
    "aljazeeraenglish":"aljazeera_en", "aljazeeraeng":"aljazeera_en",
    "aljazeeradocumentary":"aljazeera_doc", "aljazeeradoc":"aljazeera_doc",
    "aljazeradocumentary":"aljazeera_doc", "aljazeradoc":"aljazeera_doc",
    "aljazeeradocumentarychannel":"aljazeera_doc",
    "aljazeeramubasher":"aljazeera_mubasher", "aljazeeramobasher":"aljazeera_mubasher",
    "aljazeeramubasherhd":"aljazeera_mubasher", "aljazeeramobasherhd":"aljazeera_mubasher",
    # Arabic/MENA news channels frequently named differently by XMLTV feeds.
    "asharqnews":"asharq_news", "alsharqnews":"asharq_news",
    "asharqalakhbar":"asharq_news", "asharq":"asharq_news",
    "dwarabic":"dw_arabic", "dwarabia":"dw_arabic", "dwarabiya":"dw_arabic",
    "deutschewellearabic":"dw_arabic", "deutschewellearabia":"dw_arabic",
    "trtarabi":"trt_arabi", "trtarabic":"trt_arabi", "trtalarabiya":"trt_arabi",
    "cnbcarabia":"cnbc_arabia", "cnbcarabiya":"cnbc_arabia", "cnbcarabic":"cnbc_arabia",
    "jordantv":"jrtv_1", "jordantv1":"jrtv_1", "jrtv":"jrtv_1", "jrtv1":"jrtv_1",
    "alordoniya":"jrtv_1", "alordoniyah":"jrtv_1",
    # Saudi Broadcasting Authority Quran channel. Generic 'Holy Quran' is
    # disambiguated later with receiver/source provider context.
    "saudiquran":"saudi_quran", "saudiqurantv":"saudi_quran",
    "saudichforquran":"saudi_quran", "saudichannelquran":"saudi_quran",
    "qurantvsaudiarabia":"saudi_quran", "qurantvksa":"saudi_quran",
    "ksaquran":"saudi_quran", "saudiholyquran":"saudi_quran",
    # Common international brands
    "nationalgeographic":"national_geographic",
    "nationalgeographicabudhabi":"national_geographic",
    "natgeo":"national_geographic", "natgeoabudhabi":"national_geographic",
    "adnatgeo":"national_geographic", "adnationalgeographic":"national_geographic",
    "abudhabinatgeo":"national_geographic",
    "nationalgeographicwild":"national_geographic_wild", "natgeowild":"national_geographic_wild",
    "animalplanet":"animal_planet",
    # TV5 and MBC are intentionally separate. This prevents errors such as
    # TV5 Algérie receiving MBC 5 programmes merely because both contain 5.
    "tv5algerie":"tv5_algerie", "tv5algeria":"tv5_algerie",
    "tv5monde":"tv5_monde", "tv5mondeafrique":"tv5_monde_afrique",
    "mbc5":"mbc_5",
    # Saudi / MENA public channels
    "saudisunnah":"saudi_sunnah", "saudisunnahtv":"saudi_sunnah",
    "saudichforsunnah":"saudi_sunnah", "saudichannelofsunnah":"saudi_sunnah",
    "sunnahchannel":"saudi_sunnah", "sunnatv":"saudi_sunnah",
    "alekhbariya":"saudi_ekhbaria", "alekhbaria":"saudi_ekhbaria",
    "saudialekhbaria":"saudi_ekhbaria", "saudinews":"saudi_ekhbaria",
    "spacetoon":"spacetoon", "spacetoontv":"spacetoon",
    # Abu Dhabi / Dubai sports and MENA entertainment
    "abudhabisports1":"ad_sports_1", "abudhabisport1":"ad_sports_1", "adsports1":"ad_sports_1",
    "abudhabisports2":"ad_sports_2", "abudhabisport2":"ad_sports_2", "adsports2":"ad_sports_2",
    "dubaisports1":"dubai_sports_1", "dubaisport1":"dubai_sports_1",
    "rotanacinemaegy":"rotana_cinema_egy", "rotanacinemaegypt":"rotana_cinema_egy",
    "osncinema1":"osn_cinema_1", "cinema1osn":"osn_cinema_1",
    "osncinema2":"osn_cinema_2", "cinema2osn":"osn_cinema_2",
    # beIN entertainment / UHD
    "beinmovies":"bein_movies", "beinmovie":"bein_movies",
    "beindrama":"bein_drama", "beindramatv":"bein_drama",
    "bein4k":"bein_4k", "bein4kuhd":"bein_4k", "beinuhd":"bein_4k",
    "beinsportsnews":"bein_sports_news",
    "beinsportsxtra1":"bein_sports_xtra_1", "beinsportsxtra2":"bein_sports_xtra_2",
    "beinsportsxtra3":"bein_sports_xtra_3", "beinsportsxtra4":"bein_sports_xtra_4",
    "beinsportsxtra5":"bein_sports_xtra_5", "beinsportsxtra6":"bein_sports_xtra_6",
    "beinsportsxtra7":"bein_sports_xtra_7", "beinsportsxtra8":"bein_sports_xtra_8",
    "beinsportsxtra9":"bein_sports_xtra_9",
    # 2M variants
    "2m":"2m_ma", "2mmaroc":"2m_ma", "2mmonde":"2m_ma",
    "2mnational":"2m_ma", "2mnat":"2m_ma",
    # Almajd semantic names -> Badr service slots
    "almajd":"almajd_01", "almajdgeneral":"almajd_01", "almajdspacechannel":"almajd_01",
    "masatalmajd":"almajd_02", "masah":"almajd_02", "masa":"almajd_02",
    "basmah":"almajd_03", "basma":"almajd_03",
    "taghreed":"almajd_04", "taghareed":"almajd_04", "almajdtaghreed":"almajd_04", "almajdtaghareed":"almajd_04",
    "almajdkids":"almajd_05", "majdkids":"almajd_05", "majd":"almajd_05",
    "rodachannel":"almajd_06", "rawda":"almajd_06", "roda":"almajd_06",
    "almajdislamicscience":"almajd_07", "almajdscientific":"almajd_07", "elmiachannel":"almajd_07",
    "almajdholyhadeeth":"almajd_08", "almajdprophetichadeeth":"almajd_08", "prophetichadeeth":"almajd_08",
    "almajdholyquran":"almajd_09", "almajdquran":"almajd_09", "almajdqurankareem":"almajd_09", "holyquran":"almajd_09",
    "almajdnews":"almajd_10", "elmajdnews":"almajd_10", "majdalakhbar":"almajd_10",
    "almajddocumentary":"almajd_11", "alwathaeqya":"almajd_11", "wathaeqya":"almajd_11",
    "almajdnature":"almajd_12", "taabeaachannel":"almajd_12", "taabeaa":"almajd_12",
    "dalradio":"almajd_13", "radiodal":"almajd_13", "ezaetdal":"almajd_13",
    "alajaweed":"almajd_14", "alajaweeed":"almajd_14",
}


def _norm(value):
    return channel_mapper.normalize_name(str(value or ""))


def _compact(value):
    value = _norm(value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _raw_compact(value):
    """Brand-preserving compact form (beta1 normalizer may drop words like TV)."""
    raw = str(value or "").lower()
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    # Quality/codec decorations are not part of broadcaster identity.
    raw = re.sub(r"(?:fullhd|fhd|uhd|hd|sd|4k|hevc|h265)$", "", raw)
    return raw


def _strip_xmltv_suffix(value):
    """Drop common country/domain suffixes used in XMLTV ids, not channel names."""
    text = str(value or "").strip()
    # Examples seen in provider ids: Tamazight.ma, AlJazeera.qa, QuranTV.sa.
    return re.sub(r"(?i)(?:[._-](?:ma|sa|qa|ae|eg|mena|arab|arabic|tv))+$", "", text)


def _provider_family(value):
    c = _raw_compact(value) or _compact(value)
    if not c:
        return ""
    if "bein" in c: return "bein"
    if "snrt" in c or "societenationalederadiodiffusion" in c: return "snrt"
    if "almajd" in c or "almaj" in c: return "almajd"
    if c.startswith("mbc") or "mbcgroup" in c: return "mbc"
    if "aljazeera" in c or c == "jazeera": return "aljazeera"
    if "asharq" in c or "alsharq" in c: return "asharq"
    if c.startswith("trt") or "turkishradio" in c: return "trt"
    if c == "dw" or "deutschewelle" in c: return "dw"
    if "cnbc" in c: return "cnbc"
    if c.startswith("bbc"): return "bbc"
    if c.startswith("tv5"): return "tv5"
    if "jrtv" in c or "jordanradio" in c or "jordantelevision" in c: return "jrtv"
    if ("saudibroadcast" in c or "saudiradio" in c or c in ("sba","ksa") or
            c.startswith("sauditv") or "saudibroadcastingauthority" in c):
        return "saudi"
    if "abudhabi" in c or c.startswith("admedia"): return "ad_sports"
    if "dubaimedia" in c or c == "dmi": return "dubai_sports"
    if "osn" in c: return "osn"
    if "rotana" in c: return "rotana"
    return ""


def _channel_family(canon):
    c = str(canon or "")
    if c.startswith("bein_"): return "bein"
    if c.startswith("snrt_"): return "snrt"
    if c.startswith("almajd_"): return "almajd"
    if c.startswith("mbc_"): return "mbc"
    if c.startswith("aljazeera_"): return "aljazeera"
    if c.startswith("asharq_"): return "asharq"
    if c.startswith("trt_"): return "trt"
    if c.startswith("dw_"): return "dw"
    if c.startswith("cnbc_"): return "cnbc"
    if c.startswith("bbc_"): return "bbc"
    if c.startswith("tv5_"): return "tv5"
    if c.startswith("jrtv_"): return "jrtv"
    if c.startswith("saudi_"): return "saudi"
    if c.startswith("ad_sports_"): return "ad_sports"
    if c.startswith("dubai_sports_"): return "dubai_sports"
    if c.startswith("osn_"): return "osn"
    if c.startswith("rotana_"): return "rotana"
    if c.startswith("bein_"): return "bein"
    if c == "spacetoon": return "spacetoon"
    if c.startswith("watania_"): return "tunisia"
    return ""


def canonical_name(value):
    """Return a conservative cross-provider identity key."""
    raw = str(value or "")
    # Explicit Arabic broadcaster labels that disappear in the ASCII beta1
    # normalizer. Keep this small and deterministic.
    if u"الوطنية" in raw:
        if u"الثانية" in raw or re.search(r"(?:^|\D)2(?:\D|$)", raw):
            return "watania_2"
        return "watania_1"
    norm = _norm(raw)
    # 4K/UHD is part of the service identity for beIN. _raw_compact() normally
    # strips quality suffixes, so preserve this explicit channel before that
    # generic cleanup turns "beIN 4K" into just "bein".
    raw_identity = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if raw_identity in ("bein4k", "bein4kuhd", "beinuhd", "beinuhd4k"):
        return "bein_4k"
    raw_compact = _raw_compact(raw)
    compact = _compact(raw)
    if raw_compact in _COMPACT_ALIAS:
        return _COMPACT_ALIAS[raw_compact]
    if compact in _COMPACT_ALIAS:
        return _COMPACT_ALIAS[compact]

    # Numbered Almajd service names on Badr (01..14, optional SD copies).
    m = re.search(r"\bal\s*majd\s*0?(\d{1,2})\b", norm)
    if not m:
        m = re.search(r"\balmajd\s*0?(\d{1,2})\b", norm)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 15:
            return "almajd_%02d" % n

    # beIN names are particularly inconsistent across XMLTV providers.
    c = compact
    m = re.search(r"beinsportsmax0?(\d{1,2})", c)
    if m:
        return "bein_sports_max_%d" % int(m.group(1))
    if "beinsportsnews" in c or c == "beinnews":
        return "bein_sports_news"
    # Explicit language variants must be classified before the generic
    # numbered beIN rule, otherwise FR 1 / EN 1 collapse into MENA 1.
    m = re.search(r"beinsports(?:fr|french)0?(\d{1,2})", c)
    if not m:
        m = re.search(r"beinsports0?(\d{1,2}).*?(?:fr|french)", c)
    if m:
        return "bein_sports_fr_%d" % int(m.group(1))
    m = re.search(r"beinsports(?:en|english)0?(\d{1,2})", c)
    if not m:
        m = re.search(r"beinsports0?(\d{1,2}).*?(?:en|english)", c)
    if m:
        return "bein_sports_en_%d" % int(m.group(1))
    m = re.search(r"beinsports0?(\d{1,2})", c)
    if m:
        return "bein_sports_%d" % int(m.group(1))
    # Some satellite lists omit the final 's'.
    m = re.search(r"beinsport0?(\d{1,2})", c)
    if m:
        return "bein_sports_%d" % int(m.group(1))

    m = re.search(r"beinmovies?0?(\d{1,2})", c)
    if m:
        return "bein_movies_%d" % int(m.group(1))
    if c in ("beinmovies", "beinmovie"):
        return "bein_movies"
    m = re.search(r"beindrama0?(\d{1,2})", c)
    if m:
        return "bein_drama_%d" % int(m.group(1))
    if c == "beindrama":
        return "bein_drama"
    if c in ("bein4k", "bein4kuhd", "beinuhd"):
        return "bein_4k"

    # MBC and TV5 need a hard brand identity because number-only fuzzy matches
    # are unsafe (TV5 Algérie must never become MBC 5).
    m = re.search(r"^mbc0?(\d{1,2})$", raw_compact or compact)
    if m:
        return "mbc_%d" % int(m.group(1))
    tv5c = raw_compact or compact
    if tv5c.startswith("tv5"):
        return "tv5_" + (tv5c[3:] or "monde")

    # BBC: keep each service distinct, but normalize common compact spelling.
    bbcc = raw_compact or compact
    if bbcc.startswith("bbc") and len(bbcc) > 3:
        return "bbc_" + bbcc[3:]

    # Try a cleaned XMLTV id only when the original string looked like an id.
    stripped = _strip_xmltv_suffix(raw)
    if stripped != raw:
        sc = _compact(stripped)
        if sc in _COMPACT_ALIAS:
            return _COMPACT_ALIAS[sc]

    return norm


def _service_canonical(service):
    """Canonical receiver identity, enriched by the DVB provider when useful."""
    service = service or {}
    name = str(service.get("clean_name") or service.get("name") or "")
    base = canonical_name(name)
    fam = _provider_family(service.get("provider_name"))
    c = _compact(name)
    # 'Quran' is inherently ambiguous. The broadcast provider disambiguates
    # Saudi Quran from the separate Almajd Quran service.
    if ("quran" in c or "koran" in c):
        if fam == "saudi": return "saudi_quran"
        if fam == "almajd": return "almajd_09"
    return base


def _channel_canonical(name, channel_id, profile):
    """Canonical XMLTV identity using feed/source ownership for ambiguity."""
    value = str(name or channel_id or "")
    base = canonical_name(value)
    c = _compact(value)
    if not c and channel_id:
        c = _compact(_strip_xmltv_suffix(channel_id))
    profile = profile or {}
    mid = str(profile.get("mid") or "")

    # The local Almajd generator exposes semantic channel IDs while the
    # receiver commonly labels the services Almajd 01..14.  Align them by the
    # generator's own stable ID table.  This fixes 01/04/05/08/12 without
    # changing the global beta1 name matcher.
    if mid == "almajd":
        local_key = str(channel_id or "").strip().lower()
        if local_key in _ALMAJD_LOCAL_IDS:
            return _ALMAJD_LOCAL_IDS[local_key]
        name_key = re.sub(r"\s+", "-", str(name or "").strip().lower())
        if name_key in _ALMAJD_LOCAL_IDS:
            return _ALMAJD_LOCAL_IDS[name_key]

    # Generic Quran labels exist in several networks. Use source ownership to
    # distinguish Saudi SBA from Almajd without relying on a fuzzy name score.
    generic_quran = ("holyquran","holyquranchannel","qurantv","quranchannel",
                     "alquranalkareem","alqurankareem","qurankareem",
                     "saudichannelquran","saudichforquran")
    if c in generic_quran or ("quran" in c and "saudi" in c):
        if mid == "almajd":
            return "almajd_09"
        if "SA" in (profile.get("countries") or set()) or "saudi" in str(profile.get("name") or "").lower():
            return "saudi_quran"

    # Channel ids often carry better identity than a generic display name.
    cid_base = canonical_name(_strip_xmltv_suffix(channel_id)) if channel_id else ""
    if _channel_family(cid_base) and not _channel_family(base):
        return cid_base
    return base


def _service_family(service, canon=None):
    # An explicit broadcaster identity in the service name is strongest; DVB
    # provider is the extra signal for generic/ambiguous names (Quran, News...).
    name_family = _channel_family(canon or _service_canonical(service))
    return name_family or _provider_family((service or {}).get("provider_name"))


def _family_compatible(service, channel):
    sf = _service_family(service)
    cf = _channel_family((channel or {}).get("canonical"))
    # Only reject when both sides expose an unambiguous broadcaster family.
    return not (sf and cf and sf != cf)


def _arabic_intent(service):
    service = service or {}
    if _is_mena_orbital(service.get("orbital")):
        return True
    text = _norm(service.get("name")) + " " + str(service.get("name") or "")
    return bool("arabic" in text or "arabi" in text or "arabiya" in text or
                re.search(u"[\u0600-\u06ff]", text))


def _is_mena_orbital(orbital):
    try:
        o = float(orbital)
    except Exception:
        return False
    # Nilesat/Eutelsat 7/8W, Es'hail/Suhail/Badr 25.5/26E, Arabsat 30.5E.
    return ((-9.5 <= o <= -5.5) or (24.0 <= o <= 27.0) or (29.0 <= o <= 31.5))


def _is_astra_19(orbital):
    try:
        o = float(orbital)
    except Exception:
        return False
    return 18.5 <= o <= 20.0


def _local_bein_service_allowed(service):
    """Hard SAT firewall for the local Arabic/MENA beIN feed.

    beIN carries plain, FR and EN services on the same MENA transponders.
    Old generated maps could therefore retain FR/EN refs even after the source
    UUID firewall was fixed.  Keep local MENA ownership restricted to MENA
    orbitals and reject services that explicitly identify themselves as
    FR/FRENCH or EN/ENGLISH.
    """
    service = service or {}
    if not _is_mena_orbital(service.get("orbital")):
        return False
    text = " %s " % str(service.get("clean_name") or service.get("name") or "").lower()
    if re.search(r"\b(?:fr|french|en|english)\b", text, flags=re.I):
        return False
    return True


def _source_language(item, epg_dir):
    lang = str((item or {}).get("language") or "").lower().strip()
    if not lang:
        mid = source_catalog.mapping_source_id(item)
        lang = _LOCAL_LANG.get(str(mid), "")
    if not lang:
        try:
            lang = str((source_metadata.get(item, epg_dir) or {}).get("language") or "").lower()
        except Exception:
            lang = ""
    if lang.startswith("ar"): return "ar"
    if lang.startswith("en"): return "en"
    if lang.startswith("fr"): return "fr"
    return ""


def _source_profile(item, epg_dir):
    countries = set(str(x).upper() for x in ((item or {}).get("countries") or []) if x)
    region = str((item or {}).get("region") or "").upper()
    provider = str((item or {}).get("provider") or "").upper()
    mid = source_catalog.mapping_source_id(item)
    name = str((item or {}).get("name") or mid)
    language = _source_language(item, epg_dir)
    is_mena = bool(countries.intersection(_MENA_COUNTRIES) or "MENA" in region or "ARAB" in region)
    is_europe = bool(countries.intersection(_EUROPE_COUNTRIES) or "EUROPE" in region)
    return {"mid": str(mid), "language": language, "countries": countries,
            "region": region, "provider": provider, "name": name,
            "is_mena": is_mena, "is_europe": is_europe,
            "item": item}


def _brand_bonus(canon, profile):
    mid = profile.get("mid", "").lower()
    name = (profile.get("name", "") + " " + profile.get("provider", "")).lower()
    if canon.startswith("snrt_"):
        return 80 if mid == "snrt" else (35 if "morocco" in name else 0)
    if canon.startswith("almajd_"):
        return 85 if mid == "almajd" else (30 if "saudi" in name else 0)
    if canon.startswith("bein_sports"):
        if mid == "bein_sports": return 80
        if "bein" in name: return 55
    if canon.startswith("aljazeera_"):
        if "al jazeera" in name or "aljazeera" in name: return 75
        if profile.get("is_mena"): return 35
    if canon in ("saudi_quran", "saudi_sunnah", "saudi_ekhbaria"):
        if "SA" in (profile.get("countries") or set()): return 70
        if profile.get("is_mena"): return 30
    if canon in ("asharq_news", "dw_arabic", "trt_arabi", "cnbc_arabia", "jrtv_1",
                 "spacetoon", "ad_sports_1", "ad_sports_2", "dubai_sports_1",
                 "rotana_cinema_egy", "watania_1", "watania_2"):
        if profile.get("is_mena"): return 45
        if profile.get("language") == "ar": return 35
    if canon == "2m_ma":
        return 75 if mid == "chada_2m" else (35 if "morocco" in name else 0)
    return 0


def _candidate_score(service, channel, profile, match_strength):
    canon = channel.get("canonical") or ""
    score = int(match_strength) * 2 + _brand_bonus(canon, profile)
    orbital = service.get("orbital")
    mena_sat = _is_mena_orbital(orbital)
    astra = _is_astra_19(orbital)
    lang = profile.get("language") or ""

    # Receiver DVB provider is an extra identity signal, not a replacement for
    # name matching. A matching known family is strongly rewarded.
    sf = _service_family(service)
    cf = _channel_family(canon)
    if sf and cf:
        if sf == cf:
            score += 70
        else:
            score -= 180

    # Region/language preference. On MENA satellites an Arabic/MENA guide wins
    # over a European/international copy for the same channel identity.
    if mena_sat:
        if profile.get("is_mena"): score += 50
        elif profile.get("is_europe"): score -= 35
        if lang == "ar": score += 55
        elif lang in ("en", "fr"): score -= 25
    elif astra:
        if profile.get("is_europe"): score += 90
        elif profile.get("is_mena"): score -= 60
        if lang in ("fr", "en"): score += 25
        elif lang == "ar": score -= 30

    # Arabic-service intent also applies on mixed orbital positions such as
    # Hotbird. It only decides between already-identical candidates.
    if _arabic_intent(service):
        if lang == "ar": score += 35
        elif lang in ("en", "fr"): score -= 20
        if profile.get("is_mena"): score += 20

    return score


def _read_map(path):
    out = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        for _ev, elem in ET.iterparse(path, events=("end",)):
            tag = str(elem.tag).split("}", 1)[-1]
            if tag != "channel":
                continue
            cid = str(elem.get("id") or "").strip()
            ref = str(elem.text or "").strip()
            if cid and ref:
                out.setdefault(cid, []).append(ref)
            elem.clear()
    except Exception as exc:
        log.warning("SAT coverage could not read map %s: %s", path, exc)
    # dedupe while preserving order
    for cid, refs in list(out.items()):
        seen = set(); clean = []
        for ref in refs:
            if ref not in seen:
                seen.add(ref); clean.append(ref)
        out[cid] = clean
    return out


def _write_map(path, mapping):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".coverage.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="utf-8"?>\n<channels>\n')
        for cid in sorted(mapping, key=lambda x: str(x).lower()):
            for ref in mapping.get(cid) or []:
                fh.write('  <channel id="%s">%s</channel>\n' % (escape(str(cid), {'"':'&quot;'}), escape(str(ref))))
        fh.write('</channels>\n')
    os.replace(tmp, path)


def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data):
    try:
        parent = os.path.dirname(STATE_PATH)
        if parent and not os.path.isdir(parent): os.makedirs(parent)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def _channel_sig(channels):
    h = hashlib.sha1()
    for row in sorted(channels, key=lambda x: (str(x.get("channel_id")), str(x.get("display_name")))):
        h.update(("%s\0%s\n" % (row.get("channel_id") or "", row.get("display_name") or "")).encode("utf-8", "ignore"))
    return h.hexdigest()


def _manual_sig(store):
    try:
        st = os.stat(store.path)
        return [int(st.st_mtime), int(st.st_size)]
    except Exception:
        return []


def enrich_selected_maps(selected_items, results, epg_dir, progress_cb=None, force=False):
    """Enrich/rout selected SAT maps without modifying the beta1 engine.

    Returns a small report. Failures are intended to be non-fatal to Import All.
    """
    started = time.time()
    store = MappingStore()
    manual_refs = set(store.manual_refs())
    index = srp_channel_map.load_index() or {}
    direct = index.get("sources") or {}

    # Load channel declarations first. This is cheap and also gives a stable
    # signature that ignores daily programme changes.
    source_rows = []
    source_sigs = []
    maps = {}
    profiles = {}
    row_by_mid = {}
    for item in selected_items or []:
        sid = str(item.get("id") or "")
        mid = str(source_catalog.mapping_source_id(item) or "")
        result = (results or {}).get(sid) or {}
        path = str(result.get("path") or "")
        idxrow = direct.get(mid) or direct.get(mid.lower()) or {}
        map_path = str((idxrow or {}).get("map_path") or "")
        if not mid or not path or not os.path.isfile(path) or not map_path or not os.path.isfile(map_path):
            continue
        provider = str((idxrow or {}).get("provider") or item.get("provider") or "").upper()
        profile = _source_profile(item, epg_dir)
        profiles[mid] = profile
        row_by_mid[mid] = item
        try:
            channels = channel_mapper.read_xmltv_channel_ids_fast(path, mid, item.get("name") or mid)
        except Exception:
            channels = []
        if not channels:
            continue
        for ch in channels:
            ch["canonical"] = _channel_canonical(ch.get("display_name"), ch.get("channel_id"), profile)
            ch["normalized"] = _norm(ch.get("display_name") or ch.get("channel_id"))
            ch["mid"] = mid
            ch["family"] = _channel_family(ch.get("canonical"))
        source_rows.extend(channels)
        source_sigs.append([mid, _channel_sig(channels)])

        # Rytec's shared channels.xml is an upstream asset and must never be
        # edited in place.  For receiver-specific coverage (e.g. beIN Sport on
        # Astra) build a tiny derived overlay containing only channel IDs that
        # exist in this selected XMLTV feed.  Native EPGImport then uses the
        # derived path while the original Rytec map stays untouched.
        if provider == "RYTEC":
            official_path = str((idxrow or {}).get("official_map_path") or map_path)
            if not os.path.isfile(official_path):
                official_path = map_path
            wanted = [str(ch.get("channel_id") or "") for ch in channels if ch.get("channel_id")]
            try:
                base_mapping = srp_channel_map.parse_channels_xml(official_path, wanted_ids=wanted)
            except Exception:
                base_mapping = {}
            safe_mid = re.sub(r"[^A-Za-z0-9_.-]+", "_", mid).strip("_") or "rytec"
            overlay_dir = os.path.join(epg_dir, srp_channel_map.MAP_DIRNAME)
            overlay_path = os.path.join(overlay_dir, "%s.coverage.channels.xml" % safe_mid)
            maps[mid] = {"path": overlay_path, "mapping": base_mapping, "index_row": dict(idxrow),
                         "official_map_path": official_path, "provider": provider, "overlay": True}
        else:
            maps[mid] = {"path": map_path, "mapping": _read_map(map_path), "index_row": dict(idxrow),
                         "provider": provider, "overlay": False}

    if not source_rows:
        return {"ok": True, "cache_reused": True, "added_refs": 0, "routed_refs": 0,
                "services_considered": 0, "elapsed": round(time.time()-started, 3)}

    signature_payload = {
        "version": ALGORITHM_VERSION,
        "sat": channel_registry.satellite_registry_signature(),
        "sources": sorted(source_sigs),
        "manual": _manual_sig(store),
    }
    signature = hashlib.sha1(json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    old_state = _load_state()
    if not force and old_state.get("signature") == signature:
        cached = dict(old_state.get("report") or {})
        cached["cache_reused"] = True
        cached["elapsed"] = round(time.time()-started, 3)
        return cached

    if progress_cb:
        progress_cb({"phase":"COVERAGE", "current":0, "total":1,
                     "detail":"Loading cached receiver services"})
    services, registry_rebuilt = channel_registry.load_satellite_registry(force=False)
    services = [x for x in (services or []) if str(x.get("service_type") or "SAT") == "SAT"]

    by_canon = defaultdict(list)
    by_norm = defaultdict(list)
    token_index = defaultdict(list)
    for ch in source_rows:
        if ch.get("canonical"):
            by_canon[ch["canonical"]].append(ch)
        if ch.get("normalized"):
            by_norm[ch["normalized"]].append(ch)
            for tok in set(ch["normalized"].split()[:4]):
                if len(tok) >= 3:
                    token_index[tok].append(ch)

    # Existing selected maps -> reverse reference index.
    reverse = defaultdict(list)
    for mid, holder in maps.items():
        for cid, refs in holder["mapping"].items():
            for ref in refs:
                reverse[ref].append((mid, cid))

    # XMLTV channel lookup lets beta17 audit already-existing beta1 refs. This
    # is essential for hard broadcaster conflicts such as TV5 Algérie -> MBC5.
    channel_lookup = {}
    for ch in source_rows:
        channel_lookup[(str(ch.get("mid") or ""), str(ch.get("channel_id") or ""))] = ch

    changes = defaultdict(lambda: {"add": defaultdict(list), "remove": defaultdict(set)})
    added_refs = routed_refs = considered = aliases = 0
    family_conflicts_removed = provider_matches = arabic_routes = 0
    bein_language_conflicts_removed = 0
    service_variant_conflicts_removed = 0

    for service in services:
        ref = str(service.get("ref") or "").strip()
        if not ref or ref in manual_refs:
            continue
        sname = str(service.get("clean_name") or service.get("name") or "").strip()
        snorm = _norm(sname)
        scanon = _service_canonical(service)
        if not snorm:
            continue
        considered += 1

        # beta96 one-time repair: old cached maps may already contain an FR/EN
        # beIN service ref under the local Arabic/MENA source.  Remove that ref
        # directly before candidate routing. Manual refs were excluded above.
        if not _local_bein_service_allowed(service):
            for old_mid, old_cid in list(reverse.get(ref) or []):
                if str(old_mid or "").lower() == "bein_sports":
                    changes[old_mid]["remove"][str(old_cid)].add(ref)
                    bein_language_conflicts_removed += 1

        # beta113 one-time repair: a base broadcaster identity can still hide
        # distinct programme feeds. Remove old automatic Al Aoula assignments
        # when the receiver says Inter/International or Laayoune but the mapped
        # XMLTV row is the domestic/base feed (and vice versa). Manual refs were
        # excluded above and remain authoritative.
        for old_mid, old_cid in list(reverse.get(ref) or []):
            old_ch = channel_lookup.get((str(old_mid), str(old_cid)))
            if not old_ch:
                continue
            old_item = row_by_mid.get(str(old_mid)) or {}
            try:
                variant_ok, _variant_reason = service_variant_guard.check(
                    service, source=old_item, candidate_name=old_ch.get("channel_id") or old_cid,
                    display_name=old_ch.get("display_name") or "")
            except Exception:
                variant_ok = False
            if not variant_ok:
                changes[old_mid]["remove"][str(old_cid)].add(ref)
                service_variant_conflicts_removed += 1
                continue
            # beta124: legacy automatic refs must still satisfy the new
            # precision contract. Manual refs were excluded at loop entry.
            try:
                old_verdict = precision_match_engine.evaluate(
                    service, old_item, old_ch,
                    text_score=channel_mapper._token_score(
                        sname, old_ch.get("display_name") or old_ch.get("channel_id") or ""),
                    native_proof=bool((old_item or {}).get("native_srp")), balanced=True)
                if not old_verdict.get("auto"):
                    changes[old_mid]["remove"][str(old_cid)].add(ref)
                    service_variant_conflicts_removed += 1
            except Exception:
                changes[old_mid]["remove"][str(old_cid)].add(ref)
                service_variant_conflicts_removed += 1

        # Remove an already-generated automatic ref when receiver provider/name
        # proves it belongs to a different broadcaster family.  Manual refs were
        # filtered above and are never touched.  This fixes number-only false
        # positives such as TV5 Algérie being attached to MBC 5.
        sf = _service_family(service, scanon)
        if sf:
            for old_mid, old_cid in list(reverse.get(ref) or []):
                old_ch = channel_lookup.get((str(old_mid), str(old_cid)))
                if not old_ch:
                    continue
                old_family = _channel_family(old_ch.get("canonical"))
                if old_family and old_family != sf:
                    changes[old_mid]["remove"][str(old_cid)].add(ref)
                    family_conflicts_removed += 1

        candidates = []
        pool = list(by_canon.get(scanon) or [])
        strength = 100
        if not pool:
            pool = list(by_norm.get(snorm) or [])
            strength = 99
        if not pool:
            seen = set(); fuzzy_pool = []
            for tok in snorm.split()[:3]:
                if len(tok) < 3: continue
                for ch in token_index.get(tok, []):
                    key=(ch.get("mid"),ch.get("channel_id"))
                    if key not in seen:
                        seen.add(key); fuzzy_pool.append(ch)
            best_fuzzy = []
            for ch in fuzzy_pool[:300]:
                fs = int(channel_mapper._token_score(sname, ch.get("display_name") or ch.get("channel_id") or ""))
                if fs >= 98:
                    best_fuzzy.append((fs,ch))
            if best_fuzzy:
                top=max(x[0] for x in best_fuzzy)
                pool=[ch for fs,ch in best_fuzzy if fs==top]
                strength=top
        if not pool:
            continue

        for ch in pool:
            mid = ch.get("mid")
            prof = profiles.get(mid)
            if not prof:
                continue
            if str(mid or "").lower() == "bein_sports" and not _local_bein_service_allowed(service):
                continue
            # beta113: same broadcaster brand is insufficient when the suffix
            # denotes a different programme feed (Al Aoula Inter/Laayoune).
            try:
                variant_ok, _variant_reason = service_variant_guard.check(
                    service, source=(row_by_mid.get(str(mid)) or {}),
                    candidate_name=ch.get("channel_id") or "",
                    display_name=ch.get("display_name") or "")
            except Exception:
                variant_ok = False
            if not variant_ok:
                continue
            # Canonical mismatch after fuzzy lookup is allowed only for a
            # near-perfect beta1-compatible textual match.
            ccanon = ch.get("canonical") or ""
            cstrength = strength
            if scanon and ccanon and scanon == ccanon:
                if snorm != ch.get("normalized"):
                    aliases += 1
            elif cstrength < 99:
                continue
            if not _family_compatible(service, ch):
                continue
            source_item = (row_by_mid.get(str(mid)) or prof.get("item") or {})
            route_ok, route_bonus, _route_reason = programme_feed_policy.route(
                service, source_item, ch)
            if not route_ok:
                continue
            try:
                verdict = precision_match_engine.evaluate(
                    service, source_item, ch, text_score=cstrength,
                    native_proof=bool((source_item or {}).get("native_srp")), balanced=True)
            except Exception:
                continue
            if not verdict.get("auto"):
                continue
            # Precision confidence is the primary rank. Existing provider/SAT
            # score is retained as a secondary deterministic tie-break.
            score = int(verdict.get("confidence") or 0) + _candidate_score(service, ch, prof, cstrength) + int(route_bonus or 0)
            if _service_family(service) and _service_family(service) == _channel_family(ccanon):
                provider_matches += 1
            candidates.append((score, mid, ch))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (-x[0], str(x[1]), str(x[2].get("channel_id"))))
        best_score, best_mid, best_ch = candidates[0]
        second_score = candidates[1][0] if len(candidates)>1 else -999
        # Require a strong identity. Exact canonical names easily pass; fuzzy
        # cases need an excellent score and reasonable separation.
        if best_score < 190:
            continue
        if strength < 100 and (best_score-second_score) < 8:
            continue

        best_cid = str(best_ch.get("channel_id") or "")
        existing = list(reverse.get(ref) or [])
        if (best_mid, best_cid) not in existing:
            changes[best_mid]["add"][best_cid].append(ref)
            added_refs += 1

        # Strong language/region routing: remove this ref only from competing
        # candidates for the *same canonical channel*. This never removes a
        # manual mapping and never touches unrelated beta1 matches.
        if _is_mena_orbital(service.get("orbital")) or _is_astra_19(service.get("orbital")):
            best_profile = profiles.get(best_mid) or {}
            for score, mid, ch in candidates[1:]:
                if mid == best_mid and str(ch.get("channel_id")) == best_cid:
                    continue
                if (ch.get("canonical") or "") != (best_ch.get("canonical") or ""):
                    continue
                prof = profiles.get(mid) or {}
                clear_preference = False
                if _is_mena_orbital(service.get("orbital")):
                    # Once channel identity is already the same, an Arabic/MENA
                    # guide has priority over European *and* generic foreign
                    # copies.  This is the dispatch rule required for beIN on
                    # 7W/26E and Arabic news networks.
                    best_arabic = (best_profile.get("language") == "ar" or best_profile.get("is_mena"))
                    other_foreign = (not prof.get("is_mena") and prof.get("language") != "ar")
                    clear_preference = (best_arabic and other_foreign) or (
                        best_profile.get("language") == "ar" and prof.get("language") in ("en","fr"))
                elif _is_astra_19(service.get("orbital")):
                    clear_preference = (best_profile.get("is_europe") and not prof.get("is_europe"))
                if clear_preference and best_score - score >= 18:
                    cid = str(ch.get("channel_id") or "")
                    if ref in (maps.get(mid,{}).get("mapping",{}).get(cid) or []):
                        changes[mid]["remove"][cid].add(ref)
                        routed_refs += 1
                        if _is_mena_orbital(service.get("orbital")):
                            arabic_routes += 1

    changed_files = 0
    # Apply changes atomically and keep a one-time pre-beta17 safety copy.
    for mid, delta in changes.items():
        holder = maps.get(mid)
        if not holder:
            continue
        mapping = holder["mapping"]
        changed = False
        for cid, refs in delta["remove"].items():
            if cid not in mapping: continue
            before = list(mapping[cid])
            mapping[cid] = [r for r in mapping[cid] if r not in refs]
            if not mapping[cid]: mapping.pop(cid, None)
            if before != mapping.get(cid, []): changed = True
        for cid, refs in delta["add"].items():
            bucket = mapping.setdefault(cid, [])
            for ref in refs:
                if ref not in bucket:
                    bucket.append(ref); changed = True
        if not changed:
            continue
        path = holder["path"]
        if not holder.get("overlay"):
            backup = path + ".pre-beta17"
            try:
                if os.path.exists(path) and not os.path.exists(backup):
                    shutil.copy2(path, backup)
            except Exception:
                pass
        _write_map(path, mapping)
        changed_files += 1
        # Keep UI counts useful without changing beta1's schema. Rytec uses a
        # receiver-specific derived map; record the untouched official file so
        # a future coverage refresh always starts from upstream data.
        row = direct.get(mid) or direct.get(mid.lower())
        if isinstance(row, dict):
            if holder.get("overlay"):
                row["official_map_path"] = holder.get("official_map_path")
                row["map_path"] = path
                row["coverage_overlay"] = True
            row["channels"] = len(mapping)
            row["refs"] = sum(len(v) for v in mapping.values())
            row["coverage_beta17"] = True

    if changed_files:
        # Atomic update of the index metadata. Map paths/schema remain beta1.
        try:
            payload = dict(index)
            payload["sources"] = direct
            tmp = srp_channel_map.INDEX_PATH + ".coverage.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, srp_channel_map.INDEX_PATH)
        except Exception:
            log.exception("Could not update SAT coverage metadata")

    report = {
        "ok": True, "cache_reused": False, "services_considered": considered,
        "added_refs": added_refs, "routed_refs": routed_refs,
        "family_conflicts_removed": family_conflicts_removed,
        "bein_language_conflicts_removed": bein_language_conflicts_removed,
        "service_variant_conflicts_removed": service_variant_conflicts_removed,
        "provider_matches": provider_matches, "arabic_routes": arabic_routes,
        "alias_matches": aliases, "changed_maps": changed_files,
        "registry_rebuilt": bool(registry_rebuilt),
        "elapsed": round(time.time()-started, 3),
        "mode": "beta1_plus_provider_aware_sat_coverage",
    }
    _save_state({"version":ALGORITHM_VERSION, "signature":signature, "timestamp":int(time.time()), "report":report})
    log.info("SAT coverage: considered=%d added=%d routed=%d family_removed=%d bein_lang_removed=%d variant_removed=%d arabic_routes=%d maps=%d cache=%s",
             considered, added_refs, routed_refs, family_conflicts_removed, bein_language_conflicts_removed, service_variant_conflicts_removed, arabic_routes, changed_files, False)
    return report
