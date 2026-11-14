# -*- coding: utf-8 -*-
"""Maintained external XMLTV provider backends for EPG Manager v6.3.

Raw feeds are internal provider details.  The normal UI groups them by
country/region and lets the user choose which provider to use.
"""
from __future__ import print_function
import gzip
import hashlib
import json
try:
    import lzma
except ImportError:
    lzma = None
import os
import re
import time
from datetime import datetime, timedelta

from .downloader import Downloader
from .logger import get_logger

log = get_logger(__name__)
DOWNLOAD_COOLDOWN_SECONDS = 12 * 60 * 60
OPENEPG_COOLDOWN_SECONDS = 24 * 60 * 60
EPGSHARE_COOLDOWN_SECONDS = 12 * 60 * 60
RYTEC_COOLDOWN_SECONDS = 12 * 60 * 60
AZMAN_COOLDOWN_SECONDS = 12 * 60 * 60
RYTEC_CHANNEL_MAP_URLS = [
    "http://www.xmltvepg.nl/rytec.channels.xml.xz",
    "http://rytecepg.wanwizard.eu/rytec.channels.xml.xz",
    "http://epg.vuplus-community.net/rytec.channels.xml.xz",
]

# Poland is presented to the user inside the unified RYTEC catalogue.
# Internally it still uses the Azman programme/channel-map endpoints because
# those are the authoritative OE-Alliance files for Poland. This preserves
# compatibility while avoiding a separate Azman category in Smart Sources.
AZMAN_CHANNEL_MAP_URLS = [
    "http://www.topolowa4.pl/azman.channels.xml.xz",
    "http://www.xmltvepg.nl/azman.channels.xml.xz",
    "http://rytecepg.wanwizard.eu/azman.channels.xml.xz",
    "http://epg.vuplus-community.net/azman.channels.xml.xz",
]
_AZMAN_POLAND = [
    ("Poland Rytec Basic", "azman_basic.xml.xz"),
    ("Poland Rytec Additional", "azman_misc.xml.xz"),
]

# Spain is presented to the user inside the unified RYTEC catalogue.
# Internally it keeps the Koala endpoints used by OE-Alliance for Spain; no
# separate Koala category is exposed in Smart Sources.
_KOALA_SPAIN = [
    ("Spain 7 days",
     "http://tropical.jungle-team.online/epg/koala7.xml.gz",
     "http://tropical.jungle-team.online/epg/koala7.channels.xml.gz"),
    ("Spain 15 days",
     "http://tropical.jungle-team.online/epg/koala15.xml.xz",
     "http://tropical.jungle-team.online/epg/koala_epg.channels.xml.xz"),
]


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return s or hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:12]


def _src(provider, name, url, region, mirrors=None, countries=None, language=None):
    urls = [url] + list(mirrors or [])
    return dict(id="ext_%s_%s" % (_slug(provider), _slug(name)), provider=provider,
                name=name, url=url, urls=urls, region=region,
                countries=list(countries or []), language=(language or "").lower())


# beta31: OpenEPG exposes tiny channel-list endpoints for these high-value
# MENA feeds.  They are used ONLY to discover XMLTV IDs/names for mapping;
# programme import still points directly at the normal .xml/.xml.gz feed.
_OPENEPG_ID_CATALOGS = {
    "Saudi Arabia 1": "https://www.open-epg.com/files/saudiarabia1.xml.txt",
    "Saudi Arabia 3": "https://www.open-epg.com/files/saudiarabia3.xml.txt",
    "Saudi Arabia 5": "https://www.open-epg.com/files/saudiarabia5.xml.txt",
    "Saudi Arabia 6": "https://www.open-epg.com/files/saudiarabia6.xml.txt",
    "UAE 6": "https://www.open-epg.com/files/uae6.xml.txt",
    "Egypt 1": "https://www.open-epg.com/files/egypt1.xml.txt",
    "Egypt 2": "https://www.open-epg.com/files/egypt2.xml.txt",
}

_OPENEPG = [
    # rc34: Arabic/MENA OpenEPG feeds are deliberately removed from Smart
    # Sources.  MENA now comes from our validated EPG-Scrapers / EPGManager
    # feeds, while OpenEPG remains available only for non-Arab catalogues.
    ("Turkey 1", "https://www.open-epg.com/files/turkey1.xml.gz", "Europe / Asia", ["TR"], "tr"),
    ("Turkey 2", "https://www.open-epg.com/files/turkey2.xml.gz", "Europe / Asia", ["TR"], "tr"),
    ("Turkey 3", "https://www.open-epg.com/files/turkey3.xml.gz", "Europe / Asia", ["TR"], "tr"),
    ("Turkey 4", "https://www.open-epg.com/files/turkey4.xml.gz", "Europe / Asia", ["TR"], "tr"),
    ("Turkey 5", "https://www.open-epg.com/files/turkey5.xml.gz", "Europe / Asia", ["TR"], "tr"),
    ("France", "https://www.open-epg.com/files/france.xml.gz", "Europe", ["FR"], "fr"),
    ("France 1", "https://www.open-epg.com/files/france1.xml.gz", "Europe", ["FR"], "fr"),
    ("France 2", "https://www.open-epg.com/files/france2.xml.gz", "Europe", ["FR"], "fr"),
    ("France 3", "https://www.open-epg.com/files/france3.xml.gz", "Europe", ["FR"], "fr"),
    ("Spain", "https://www.open-epg.com/files/spain1.xml", "Europe", ["ES"], None),
]

# rc34: EPGShare is international/non-Arab only.  All Arabic/MENA
# EPGShare entries are removed so they cannot collide with the validated
# direct MENA feeds prepared in EPG-Scrapers.
_EPGSHARE_CODES = [
    ("France", "FR1", "Europe", ["FR"]), ("Spain", "ES1", "Europe", ["ES"]),
    ("Portugal", "PT1", "Europe", ["PT"]), ("UK", "UK1", "Europe", ["UK"]),
    ("Ireland", "IE1", "Europe", ["IE"]), ("Belgium", "BE2", "Europe", ["BE"]),
    ("Netherlands", "NL1", "Europe", ["NL"]), ("Germany", "DE1", "Europe", ["DE"]),
    ("Switzerland", "CH1", "Europe", ["CH"]), ("Italy", "IT1", "Europe", ["IT"]),
    ("Austria", "AT1", "Europe", ["AT"]), ("Poland", "PL1", "Europe", ["PL"]),
    ("Greece", "GR1", "Europe", ["GR"]), ("Turkey", "TR1", "Europe / Asia", ["TR"]),
    ("Romania", "RO1", "Europe", ["RO"]), ("Bulgaria", "BG1", "Europe", ["BG"]),
    ("Croatia", "HR1", "Europe", ["HR"]), ("Serbia", "RS1", "Europe", ["RS"]),
    ("Slovakia", "SK1", "Europe", ["SK"]),
    ("Czech Republic", "CZ1", "Europe", ["CZ"]), ("Denmark", "DK1", "Europe", ["DK"]),
    ("Sweden", "SE1", "Europe", ["SE"]), ("Norway", "NO1", "Europe", ["NO"]),
    ("Finland", "FI1", "Europe", ["FI"]), ("USA", "US2", "North America", ["US"]),
    ("Canada", "CA2", "North America", ["CA"]), ("Brazil", "BR1", "South America", ["BR"]),
    ("Argentina", "AR1", "South America", ["AR_COUNTRY"]), ("Australia", "AU1", "Oceania", ["AU"]),
    ("New Zealand", "NZ1", "Oceania", ["NZ"]),
]


# Current maintained Rytec catalogue. One source can belong to multiple countries.
_RYTEC = [
    ("News Channels", "rytecNWS.xz", "International", ["INT"]),
    ("Erotic", "rytecERO.xz", "International", ["INT"]),
    ("IPTV", "rytecIPTV.xz", "International", ["INT"]),
    ("Miscellaneous", "rytecMisc.xz", "International", ["INT"]),
    ("Netherlands Basic", "rytecNL_Basic.xz", "Europe", ["NL"]),
    ("Flanders Basic", "rytecBE_VL_Basic.xz", "Europe", ["BE"]),
    ("Belgium/Netherlands Common", "rytecBE_NL_Common.xz", "Europe", ["BE", "NL"]),
    ("Netherlands Sport/Movies", "rytecNL_Extra.xz", "Europe", ["NL"]),
    ("Belgium FR Basic", "rytecBE_FR_Basic.xz", "Europe", ["BE"]),
    ("France TNT", "rytecTNT_Basic.xz", "Europe", ["FR"]),
    ("France/Belgium Common", "rytecBE_FR_Common.xz", "Europe", ["FR", "BE"]),
    ("France Mixte", "rytecFR_Mixte.xz", "Europe", ["FR"]),
    ("France Sport/Cinema", "rytecFR_SportMovies.xz", "Europe", ["FR"]),
    ("Bulgaria", "rytecBG.xz", "Europe", ["BG"]),
    ("Slovakia Basic", "rytecSK_Basic.xz", "Europe", ["SK"]),
    ("Czech Basic", "rytecCZ_Basic.xz", "Europe", ["CZ"]),
    ("Czech/Slovak Common", "rytecCZ_SK_Common.xz", "Europe", ["CZ", "SK"]),
    ("Czech/Slovak Sport/Movies", "rytecCZ_SK_SportMovies.xz", "Europe", ["CZ", "SK"]),
    ("Denmark Basic", "rytecDK_Basic.xz", "Europe", ["DK"]),
    ("Denmark Misc", "rytecDK_Misc.xz", "Europe", ["DK"]),
    ("Denmark Sport/Movies", "rytecDK_SportMovies.xz", "Europe", ["DK"]),
    ("Germany Basic", "rytecDE_Basic.xz", "Europe", ["DE"]),
    ("Austria Basic", "rytecAT_Basic.xz", "Europe", ["AT"]),
    ("Switzerland Basic", "rytecCH_Basic.xz", "Europe", ["CH"]),
    ("Germany/Austria/Switzerland Common", "rytecDE_Common.xz", "Europe", ["DE", "AT", "CH"]),
    ("Germany/Austria/Switzerland Sport/Movies", "rytecDE_SportMovies.xz", "Europe", ["DE", "AT", "CH"]),
    ("Greece Basic", "rytecGR_Basic.xz", "Europe", ["GR"]),
    ("Greece Sport/Movies", "rytecGR_SportMovies.xz", "Europe", ["GR"]),
    ("Italy Basic", "rytecIT_Basic.xz", "Europe", ["IT"]),
    ("Italy Sky", "rytecIT_Sky.xz", "Europe", ["IT"]),
    ("Italy Sport/Movies", "rytecIT_SportMovies.xz", "Europe", ["IT"]),
    ("Portugal", "rytecPT.xz", "Europe", ["PT"]),
    ("Norway Basic", "rytecNO_Basic.xz", "Europe", ["NO"]),
    ("Norway Misc", "rytecNO_Misc.xz", "Europe", ["NO"]),
    ("Norway Sport/Movies", "rytecNO_SportMovies.xz", "Europe", ["NO"]),
    ("Romania Basic", "rytecRO_Basic.xz", "Europe", ["RO"]),
    ("Romania Sport/Movies", "rytecRO_SportMovies.xz", "Europe", ["RO"]),
    ("Croatia Basic", "rytecHRV_Basic.xz", "Europe", ["HR"]),
    ("Croatia Misc", "rytecHRV_Misc.xz", "Europe", ["HR"]),
    ("Croatia Sport/Movies", "rytecHRV_SportMovies.xz", "Europe", ["HR"]),
    ("Serbia/Montenegro Basic", "rytecSRB_Basic.xz", "Europe", ["RS"]),
    ("Serbia/Montenegro Misc", "rytecSRB_Misc.xz", "Europe", ["RS"]),
    # Maintained source currently points this entry to HRV Sport/Movies.
    ("Serbia/Montenegro Sport/Movies", "rytecHRV_SportMovies.xz", "Europe", ["RS"]),
    ("Slovenia Basic", "rytecSVN_Basic.xz", "Europe", ["SI"]),
    ("Slovenia Misc", "rytecSVN_Misc.xz", "Europe", ["SI"]),
    ("Slovenia Sport/Movies", "rytecSVN_SportMovies.xz", "Europe", ["SI"]),
    ("Sweden Basic", "rytecSE_Basic.xz", "Europe", ["SE"]),
    ("Sweden Misc", "rytecSE_Misc.xz", "Europe", ["SE"]),
    ("Sweden Sport/Movies", "rytecSE_SportMovies.xz", "Europe", ["SE"]),
    ("Finland Basic", "rytecFI_Basic.xz", "Europe", ["FI"]),
    ("Finland Misc", "rytecFI_Misc.xz", "Europe", ["FI"]),
    ("Finland Sport/Movies", "rytecFI_SportMovies.xz", "Europe", ["FI"]),
    ("UK/Ireland FreeSat/FTA", "rytecUK_Basic.xz", "Europe", ["UK", "IE"]),
    ("UK/Ireland Sky", "rytecUK_SkyLive.xz", "Europe", ["UK", "IE"]),
    ("UK/Ireland Sport/Movies", "rytecUK_SportMovies.xz", "Europe", ["UK", "IE"]),
]

MOROCCO_CLOUD_ID = "ext_epgmanager_morocco"
MOROCCO_CLOUD_URL = "https://raw.githubusercontent.com/wacayoub/EPGManager/epg-data/morocco.xml.gz"
MOROCCO_CLOUD_CATALOG_URL = "https://raw.githubusercontent.com/wacayoub/EPGManager/epg-data/morocco.txt"

# Stable monolithic MENA id is retained for preference migration and now also
# backs the optional all-in-one Smart Source.
MENA_CLOUD_ID = "ext_epgmanager_mena_arabic"
MENA_CLOUD_BASE = "https://raw.githubusercontent.com/wacayoub/EPGManager/mena-data"

# Receiver-facing MENA Cloud is split into small exclusive shards. Provider
# ownership wins before country, so one XMLTV id belongs to exactly one shard.
_MENA_COUNTRY_SHARDS = [
    ("EG", "Egypt", "mena-eg"), ("SA", "Saudi Arabia", "mena-sa"),
    ("AE", "United Arab Emirates", "mena-ae"), ("QA", "Qatar", "mena-qa"),
    ("KW", "Kuwait", "mena-kw"), ("BH", "Bahrain", "mena-bh"),
    ("OM", "Oman", "mena-om"), ("JO", "Jordan", "mena-jo"),
    ("LB", "Lebanon", "mena-lb"), ("IQ", "Iraq", "mena-iq"),
    ("PS", "Palestine", "mena-ps"), ("YE", "Yemen", "mena-ye"),
    ("DZ", "Algeria", "mena-dz"), ("TN", "Tunisia", "mena-tn"),
    ("LY", "Libya", "mena-ly"), ("SD", "Sudan", "mena-sd"),
    ("SY", "Syria", "mena-sy"), ("MR", "Mauritania", "mena-mr"),
]
_MENA_PROVIDER_SHARDS = [
    ("bein", "beIN", "provider-bein"),
    ("osn", "OSN", "provider-osn"),
    ("mbc", "MBC Group", "provider-mbc"),
    ("rotana", "Rotana", "provider-rotana"),
    ("adm", "Abu Dhabi Media", "provider-adm"),
    ("dmi", "Dubai Media", "provider-dmi"),
    ("alkass", "Al Kass", "provider-alkass"),
    ("art", "ART / Alfa", "provider-art"),
]

def _cloud_shard(source_id, name, stem, countries, region="MENA / Arabic", language="ar"):
    item = _src("EPG MANAGER", name, MENA_CLOUD_BASE + "/" + stem + ".xml.gz",
                region, countries=countries, language=language)
    item["id"] = source_id
    item["catalog_url"] = MENA_CLOUD_BASE + "/" + stem + ".txt"
    item["catalog_kind"] = "xmltv-id-list"
    item["cloud_feed"] = True
    item["schedule"] = "06:00 Africa/Casablanca"
    item["tv_only"] = True
    item["epg_days"] = 2
    return item

EPG_SCRAPERS_BASE = "https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds"


_DIRECT_SOURCE_SCHEDULES = {
    'aljazeera': '04:12 Africa/Casablanca',
    'alkass': '00:17 Africa/Casablanca',
    'bein': '02:22 Africa/Casablanca',
    'dubaiplus': '04:44 Africa/Casablanca',
    'elcinema': '04:20 Africa/Casablanca',
    'morocco': '07:07 Africa/Casablanca',
    'osn': '01:40 Africa/Casablanca',
    'rotana': '03:50 Africa/Casablanca',
    'shahid': '03:39 Africa/Casablanca',
    'sport24': '06:03 Africa/Casablanca',
    'starzplay': '03:07 Africa/Casablanca',
    'stctv': '02:37 Africa/Casablanca',
    'tabie': '00:30 Africa/Casablanca',
    'tunisiatv': '06:35 Africa/Casablanca',
}


def _epgscraper_source(source_id, label, stem, countries, language="", days=7):
    """Validated daily XMLTV feed published by wacayoub/EPGManager."""
    item = _src("EPG SCRAPERS", label, EPG_SCRAPERS_BASE + "/" + stem + ".xml.gz",
                "Direct", countries=countries, language=language)
    item["id"] = source_id
    item["cloud_feed"] = True
    item["direct_feed"] = True
    item["tv_only"] = True
    item["epg_days"] = int(days)
    item["schedule"] = _DIRECT_SOURCE_SCHEDULES[stem]
    item["source_repo"] = "wacayoub/EPGManager"
    # The ElCinema pipeline parses guide wall times as Africa/Casablanca and
    # publishes normalized UTC. Applying another receiver-side +60m would
    # double-shift its schedule. STARZPLAY still needs the verified correction.
    if stem == "starzplay":
        item["time_shift_minutes"] = 60
    if stem in ("elcinema", "starzplay"):
        item["time_policy_version"] = "rc75-source-time-v1"
    # rc36.8: Import All materialises every direct GitHub feed locally first.
    # Stable short filenames make the import directory inspectable and prevent
    # old ext_epgscrapers_* cache names from being mistaken for current feeds.
    item["local_filename"] = "%s.xml" % stem
    item["persistent_xml"] = True
    item["import_local_xml"] = True
    if stem in ("morocco", "bein", "elcinema", "osn", "sport24", "dubaiplus", "starzplay", "shahid", "rotana", "stctv", "aljazeera", "alkass", "tunisiatv", "tabie"):
        item["catalog_url"] = EPG_SCRAPERS_BASE + "/" + stem + ".txt"
        item["catalog_kind"] = "xmltv-id-list"
    return item


SOURCES = []

# rc34 direct validated feeds.  These are independent Smart Sources: no merge,
# no receiver-side scraping, and no Arabic OpenEPG/EPGShare fallback involved.
SOURCES.extend([
    _epgscraper_source("ext_epgscrapers_morocco", "Morocco", "morocco", ["MA"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_bein", "beIN", "bein", ["MENA"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_elcinema", "ElCinema", "elcinema", ["MENA"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_osn", "OSN", "osn", ["MENA"], "", 7),
    _epgscraper_source("ext_epgscrapers_sport24", "Sport24", "sport24", ["AE", "MENA"], "", 7),
    _epgscraper_source("ext_epgscrapers_dubaiplus", "Dubai+", "dubaiplus", ["AE", "MENA"], "", 7),
    _epgscraper_source("ext_epgscrapers_starzplay", "STARZPLAY GCC", "starzplay", ["MENA", "AE", "SA", "KW", "QA", "BH", "OM"], "", 7),
    _epgscraper_source("ext_epgscrapers_shahid", "Shahid / MBC", "shahid", ["MENA", "AE", "SA", "EG", "IQ"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_rotana", "Rotana", "rotana", ["MENA", "SA", "EG"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_stctv", "STC TV", "stctv", ["MENA", "SA", "AE", "EG", "OM", "BH", "JO"], "", 7),
    _epgscraper_source("ext_epgscrapers_aljazeera", "Al Jazeera", "aljazeera", ["MENA", "QA"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_alkass", "Al Kass", "alkass", ["QA", "MENA"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_tunisiatv", "TunisiaTV", "tunisiatv", ["TN"], "ar", 7),
    _epgscraper_source("ext_epgscrapers_tabie", "Tabie QMC", "tabie", ["QA", "MENA"], "ar", 7),
])

# rc44: provider catalogues can contain mixed-language channel feeds.  These
# declarations are channel-guide capabilities, not a request to cross language
# variants; multilingual_guard still rejects explicit contradictions per row.
for _direct_item in SOURCES:
    if _direct_item.get("id") in ("ext_epgscrapers_osn", "ext_epgscrapers_dubaiplus", "ext_epgscrapers_starzplay", "ext_epgscrapers_stctv"):
        _direct_item["languages"] = ["ar", "en"]
    elif _direct_item.get("id") == "ext_epgscrapers_shahid":
        _direct_item["languages"] = ["ar", "en"]

# rc64: retire the complete legacy EPG MANAGER cloud catalogue from runtime.
#
# These 33 entries (Morocco Cloud, MENA aggregates, country/provider shards,
# and the old source-* diagnostic feeds) pre-date the validated daily
# EPG-Scrapers direct feeds and only duplicate them in Smart Engine.  Keep the
# constants/helpers above for migration/backward compatibility, but do not add
# any of these legacy feeds to SOURCES. Existing saved selections are pruned by
# SourcePreferences because only IDs present in source_catalog.all_sources() are
# considered valid.
#
# Active runtime sources after this point are therefore:
#   - EPG SCRAPERS direct feeds
#   - EPGShare fallbacks
#   - OpenEPG fallbacks
#   - unified Rytec family
# No legacy EPG MANAGER/MENA cloud entry is exposed in Smart Sources or Smart
# Mapping.

for name, url, region, countries, language in _OPENEPG:
    mirrors = []
    if str(url).lower().endswith(".xml.gz"):
        mirrors.append(str(url)[:-3])  # plain .xml fallback
    _item = _src("OPENEPG", name, url, region, mirrors=mirrors, countries=countries, language=language)
    cat = _OPENEPG_ID_CATALOGS.get(name)
    mena_catalog = bool(set(countries or []) & set(("MA", "EG", "SA", "QA", "AE", "PS", "MENA")) or "MENA" in str(region or "").upper())
    if not cat and mena_catalog and str(url).lower().endswith(".xml.gz"):
        cat = str(url)[:-3] + ".txt"
    if cat:
        _item["catalog_url"] = cat
        _item["catalog_kind"] = "openepg-id-list"
    SOURCES.append(_item)
# beta65: EPGShare is back as an optional provider category.  The provider's
# own tiny .txt ID list is used by Smart Sources/Smart Mapping whenever
# possible, so merely browsing/matching a source never downloads the full
# programme XMLTV payload.  The full .xml.gz is fetched only for an explicit
# refresh/import path.  Nothing is selected automatically.
for name, code, region, countries in _EPGSHARE_CODES:
    base = "https://epgshare01.online/epgshare01/epg_ripper_%s" % code
    _item = _src("EPGSHARE", name, base + ".xml.gz", region, countries=countries)
    _item["catalog_url"] = base + ".txt"
    _item["catalog_kind"] = "epgshare-id-list"
    SOURCES.append(_item)


for name, filename in _AZMAN_POLAND:
    primary = "http://www.topolowa4.pl/%s" % filename
    mirrors = ["http://www.xmltvepg.nl/%s" % filename,
               "http://rytecepg.wanwizard.eu/%s" % filename,
               "http://epg.vuplus-community.net/%s" % filename]
    _item = _src("AZMAN", name, primary, "Europe", mirrors=mirrors, countries=["PL"], language="pl")
    _item["native_srp"] = True
    _item["channels_name"] = "azman.channels.xml.xz"
    _item["channels_urls"] = list(AZMAN_CHANNEL_MAP_URLS)
    SOURCES.append(_item)

for name, url, channels_url in _KOALA_SPAIN:
    _item = _src("KOALA", name, url, "Europe", countries=["ES"], language="es")
    # OE-Alliance publishes an authoritative channels= map for Koala.  Treat it
    # exactly like Rytec: channel-id -> Service Reference is provider-owned and
    # must never be reconstructed by fuzzy Service Name matching.
    _item["native_srp"] = True
    _item["channels_urls"] = [channels_url]
    _item["channels_name"] = str(channels_url).rsplit("/", 1)[-1]
    SOURCES.append(_item)

for name, filename, region, countries in _RYTEC:
    primary = "http://www.xmltvepg.nl/%s" % filename
    mirrors = ["http://rytecepg.wanwizard.eu/%s" % filename,
               "http://epg.vuplus-community.net/%s" % filename]
    _item = _src("RYTEC", name, primary, region, mirrors, countries=countries)
    # The same official channel map used by OE-Alliance EPGImport.  This is
    # deliberately a provider-supplied SRP map: never fuzzy-remap it.
    _item["native_srp"] = True
    _item["channels_name"] = "rytec.channels.xml.xz"
    _item["channels_urls"] = list(RYTEC_CHANNEL_MAP_URLS)
    SOURCES.append(_item)

# rc62: restore the optional provider catalogues in Smart Sources.
# EPG-Scrapers direct feeds remain the preferred/high-priority path; OpenEPG,
# EPGShare, Rytec/Azman/Koala are fallback catalogues and are never enabled
# automatically.  This restores the pre-direct-only provider pages without
# allowing a fallback source to silently replace a selected direct feed.

BY_ID = {s["id"]: s for s in SOURCES}


def local_xml_path(source, epg_dir):
    filename = str((source or {}).get("local_filename") or "").strip()
    if not filename:
        filename = "%s.xml" % source["id"]
    return os.path.join(epg_dir, filename)


def rytec_channel_map_path(epg_dir):
    return os.path.join(epg_dir, "rytec.channels.xml")


def cache_age_seconds(source, epg_dir):
    path = local_xml_path(source, epg_dir)
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return max(0, int(time.time() - os.path.getmtime(path)))
    except Exception:
        pass
    return None


def cache_ttl_seconds(source):
    """Provider-aware maintenance interval.

    Import never calls this updater; these TTLs are only for background/manual
    source maintenance.  They intentionally trade a little freshness for very
    low CPU/network usage on Vu+ receivers.
    """
    provider = str((source or {}).get("provider") or "").upper()
    if provider == "OPENEPG": return OPENEPG_COOLDOWN_SECONDS
    if provider == "EPGSHARE": return EPGSHARE_COOLDOWN_SECONDS
    if provider == "RYTEC": return RYTEC_COOLDOWN_SECONDS
    if provider == "AZMAN": return AZMAN_COOLDOWN_SECONDS
    if provider == "KOALA": return RYTEC_COOLDOWN_SECONDS
    return DOWNLOAD_COOLDOWN_SECONDS


def download_locked(source, epg_dir):
    age = cache_age_seconds(source, epg_dir)
    return age is not None and age < cache_ttl_seconds(source)


def download_lock_remaining(source, epg_dir):
    age = cache_age_seconds(source, epg_dir)
    ttl = cache_ttl_seconds(source)
    if age is None or age >= ttl:
        return 0
    return max(1, int((ttl - age + 59) / 60))


def source_status(source, epg_dir):
    path = local_xml_path(source, epg_dir)
    if not os.path.exists(path): return "ONLINE"
    try:
        age = max(0, time.time() - os.path.getmtime(path))
        return "READY" if age < max(36 * 3600, cache_ttl_seconds(source) + 12 * 3600) else "STALE"
    except Exception:
        return "READY"


def _download_payload(url, retries, timeout):
    lower = url.lower().split("?", 1)[0]
    if lower.startswith("file://"):
        with open(url[7:], "rb") as f: return f.read(), lower
    if os.path.isabs(url) and os.path.exists(url):
        with open(url, "rb") as f: return f.read(), lower
    d = Downloader(retries=retries, timeout=timeout)
    try:
        resp = d.get(url); return resp.content, lower
    finally:
        d.close()


def _decompress(payload, lower):
    if lower.endswith(".gz"): return gzip.decompress(payload)
    if lower.endswith(".xz"):
        if lzma is not None: return lzma.decompress(payload)
        import subprocess
        proc = subprocess.Popen(["xz", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate(payload)
        if proc.returncode != 0: raise RuntimeError(err.decode("utf-8", "ignore") or "xz unavailable")
        return out
    return payload


def _shift_xmltv_payload_minutes(payload, minutes):
    """Shift XMLTV programme start/stop wall-clock values by a fixed amount.

    This compatibility helper is used only for the STARZPLAY direct feed. The
    ElCinema feed is already normalized from Africa/Casablanca in the scraper.
    Only programme timestamps are shifted; offsets and channel metadata remain.
    """
    try:
        minutes = int(minutes or 0)
    except Exception:
        minutes = 0
    if not payload or not minutes:
        return payload

    pattern = re.compile(br'\b(start|stop)="(\d{12}|\d{14})(\s*(?:[+-]\d{4}|Z))?"')

    def repl(match):
        digits = match.group(2).decode("ascii", "ignore")
        fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
        try:
            dt = datetime.strptime(digits, fmt) + timedelta(minutes=minutes)
            shifted = dt.strftime(fmt).encode("ascii")
            return match.group(1) + b'="' + shifted + (match.group(3) or b'') + b'"'
        except Exception:
            return match.group(0)

    return pattern.sub(repl, payload)


def _ensure_time_policy_cache(source, epg_dir):
    """Drop stale programme snapshots once when a source time policy changes."""
    source_id = str((source or {}).get("id") or "").strip()
    policy = str((source or {}).get("time_policy_version") or "").strip()
    if not source_id or not policy:
        return
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_id) or "source"
    state_path = os.path.join(epg_dir, ".epgmanager_time_policy_%s.json" % safe_id)
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {}
    if isinstance(state, dict) and state.get("version") == policy:
        return

    cached = local_xml_path(source, epg_dir)
    for stale_path in (cached, cached + ".lastgood"):
        try:
            if os.path.isfile(stale_path):
                os.unlink(stale_path)
        except Exception:
            log.warning("Could not invalidate old time-policy cache %s", stale_path)

    tmp = state_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"source_id": source_id, "version": policy}, fh,
                      ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, state_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        log.warning("Could not save source time-policy marker for %s", source_id)
    log.info("Invalidated stale timestamp cache for %s (%s)", source_id, policy)


def download_source(source, epg_dir, retries=1, timeout=12, force=False):
    """Download one XMLTV feed with provider-aware cache and mirror fallback."""
    if isinstance(source, str): source = BY_ID[source]
    if not os.path.isdir(epg_dir): os.makedirs(epg_dir)
    _ensure_time_policy_cache(source, epg_dir)
    cached = local_xml_path(source, epg_dir)
    if not force and download_locked(source, epg_dir):
        log.info("Reusing fresh XMLTV cache for %s (%d min lock remaining)",
                 source.get("name"), download_lock_remaining(source, epg_dir))
        return cached
    errors = []
    for url in source.get("urls") or [source["url"]]:
        try:
            payload, lower = _download_payload(url, retries, timeout)
            payload = _decompress(payload, lower)
            # Apply any source-declared correction before quality audit/import,
            # so Smart Mapping preview and Native Import see the same instants.
            shift_minutes = int(source.get("time_shift_minutes") or 0)
            if shift_minutes:
                payload = _shift_xmltv_payload_minutes(payload, shift_minutes)
            head = payload.lstrip()[:500].lower()
            # XMLTV standard element names are lowercase. Avoid payload.lower()
            # here because that doubles RAM use on large feeds on Vu+ receivers.
            if ((b"<tv" not in head and not head.startswith(b"<?xml")) or
                    payload.find(b"<channel") < 0 or payload.find(b"<programme") < 0):
                raise ValueError("invalid/empty XMLTV payload")
            path = local_xml_path(source, epg_dir); tmp = path + ".tmp"
            with open(tmp, "wb") as f: f.write(payload)
            # Last-good safety: a failed/empty refresh never replaces the
            # previous working XML. Keep one local recovery copy before swap.
            if os.path.exists(path) and os.path.getsize(path) > 32:
                try:
                    import shutil
                    shutil.copy2(path, path + ".lastgood")
                except Exception:
                    pass
            os.replace(tmp, path)
            try:
                from . import source_metadata
                source_metadata.audit_source(source, path, payload=payload)
            except Exception:
                pass
            try:
                from . import source_quality
                source_quality.audit_file(source, path)
            except Exception:
                pass
            return path
        except Exception as exc:
            errors.append("%s: %s" % (url, exc)); log.warning("EPG source failed %s: %s", url, exc)
    raise RuntimeError("All mirrors failed for %s (%s)" % (source.get("name"), "; ".join(errors[-2:])))


def download_rytec_channel_map(epg_dir, retries=1, timeout=12, force=False):
    """Cache Rytec's official XMLTV-id -> Enigma2 service-reference map."""
    if not os.path.isdir(epg_dir): os.makedirs(epg_dir)
    path = rytec_channel_map_path(epg_dir)
    try:
        if (not force and os.path.exists(path) and os.path.getsize(path) > 32 and
                (time.time() - os.path.getmtime(path)) < 86400):
            return path
    except Exception:
        pass
    errors = []
    for url in RYTEC_CHANNEL_MAP_URLS:
        try:
            payload, lower = _download_payload(url, retries, timeout); payload = _decompress(payload, lower)
            if b"<channel" not in payload[:5000].lower() and not payload.lstrip().startswith(b"<?xml"):
                raise ValueError("not a Rytec channels XML")
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh: fh.write(payload)
            os.replace(tmp, path); return path
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
    raise RuntimeError("Rytec channel map download failed (%s)" % "; ".join(errors[-2:]))


def cached_sources(epg_dir):
    out = []
    for src in SOURCES:
        path = local_xml_path(src, epg_dir)
        if os.path.exists(path):
            item = dict(src); item["path"] = path; out.append(item)
    return out
