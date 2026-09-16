# -*- coding: utf-8 -*-
"""External XMLTV source catalogue for standalone Smart Mapping.

Sources are only downloaded on explicit user request (YELLOW in Smart Mapping).
Downloaded files are decompressed to XML in the configured EPG output directory.
"""
from __future__ import print_function
import gzip
import hashlib
try:
    import lzma
except ImportError:
    lzma = None
import os
import re

from .downloader import Downloader
from .logger import get_logger

log = get_logger(__name__)

# (name, url, region)
_RAW = [
("PALESTINE1","https://www.open-epg.com/files/palestine1.xml.gz","Middle East"),
("EGYPT","https://www.open-epg.com/files/egypt1.xml.gz","Middle East"),
("QATAR1","https://www.open-epg.com/files/qatar1.xml.gz","Middle East"),
("QATAR2","https://www.open-epg.com/files/qatar2.xml.gz","Middle East"),
("QATAR3","https://www.open-epg.com/files/qatar3.xml.gz","Middle East"),
("QATAR4","https://www.open-epg.com/files/qatar4.xml.gz","Middle East"),
("QATAR5","https://www.open-epg.com/files/qatar5.xml.gz","Middle East"),
("QATAR6","https://www.open-epg.com/files/qatar6.xml.gz","Middle East"),
("ALJAZEERA","https://epgshare01.online/epgshare01/epg_ripper_ALJAZEERA1.xml.gz","Middle East"),
("SAUDI1","https://www.open-epg.com/files/saudiarabia1.xml.gz","Middle East"),
("SAUDI2","https://www.open-epg.com/files/saudiarabia2.xml.gz","Middle East"),
("SAUDI3","https://www.open-epg.com/files/saudiarabia3.xml.gz","Middle East"),
("SAUDI4","https://www.open-epg.com/files/saudiarabia4.xml.gz","Middle East"),
("SAUDI5","https://www.open-epg.com/files/saudiarabia5.xml.gz","Middle East"),
("UAE6","https://www.open-epg.com/files/uae6.xml.gz","Middle East"),
("FRANCE","https://www.open-epg.com/files/france.xml.gz","Europe"),
("MOROCCO","https://www.open-epg.com/files/morocco1.xml.gz","Africa"),
("Rytec-News","http://www.xmltvepg.nl/rytecNWS.xz","Rytec"),
("Rytec-IPTV","http://www.xmltvepg.nl/rytecIPTV.xz","Rytec"),
("Rytec-Miscellaneous","http://www.xmltvepg.nl/rytecMisc.xz","Rytec"),
("Rytec-Vlaanderen-Basic","http://www.xmltvepg.nl/rytecBE_VL_Basic.xz","Rytec"),
("Rytec-Vlaanderen-Nederland","http://www.xmltvepg.nl/rytecBE_NL_Common.xz","Rytec"),
("Rytec-Nederland-Basis","http://www.xmltvepg.nl/rytecNL_Basic.xz","Rytec"),
("Rytec-Nederland-Sport-Movies","http://www.xmltvepg.nl/rytecNL_Extra.xz","Rytec"),
("Rytec-Wallonie-Telesat-Base","http://www.xmltvepg.nl/rytecBE_FR_Basic.xz","Rytec"),
("Rytec-France-TNT","http://www.xmltvepg.nl/rytecTNT_Basic.xz","Rytec"),
("Rytec-France-Wallonie-Commun","http://www.xmltvepg.nl/rytecBE_FR_Common.xz","Rytec"),
("Rytec-France-Mixte","http://www.xmltvepg.nl/rytecFR_Mixte.xz","Rytec"),
("Rytec-France-Sport-Cinema","http://www.xmltvepg.nl/rytecFR_SportMovies.xz","Rytec"),
("Rytec-Bulgaria","http://www.xmltvepg.nl/rytecBG.xz","Rytec"),
("Rytec-Slovensko-Bazicky","http://www.xmltvepg.nl/rytecSK_Basic.xz","Rytec"),
("Rytec-Ceska-Bazicky","http://www.xmltvepg.nl/rytecCZ_Basic.xz","Rytec"),
("Rytec-Ceska-Slovensko-Rozdeleny","http://www.xmltvepg.nl/rytecCZ_SK_Common.xz","Rytec"),
("Rytec-Ceska-Slovensko-Sportove-Filmy","http://www.xmltvepg.nl/rytecCZ_SK_SportMovies.xz","Rytec"),
("Rytec-Danmark-Grundleggende","http://www.xmltvepg.nl/rytecDK_Basic.xz","Rytec"),
("Rytec-Danmark-Delt","http://www.xmltvepg.nl/rytecDK_Misc.xz","Rytec"),
("Rytec-Danmark-Sport-Movies","http://www.xmltvepg.nl/rytecDK_SportMovies.xz","Rytec"),
("Rytec-Deutschland-Basis","http://www.xmltvepg.nl/rytecDE_Basic.xz","Rytec"),
("Rytec-Osterreich-Basis","http://www.xmltvepg.nl/rytecAT_Basic.xz","Rytec"),
("Rytec-Switzerland-Basis","http://www.xmltvepg.nl/rytecCH_Basic.xz","Rytec"),
("Rytec-Deutsch-Osterreich-Switz-Gemeinsam","http://www.xmltvepg.nl/rytecDE_Common.xz","Rytec"),
("Rytec-Deutsch-Osterreich-Switz-Sports-Film","http://www.xmltvepg.nl/rytecDE_SportMovies.xz","Rytec"),
("Rytec-Ellada-Genikos","http://www.xmltvepg.nl/rytecGR_Basic.xz","Rytec"),
("Rytec-Ellada-Athlitismos-Kinimatografou","http://www.xmltvepg.nl/rytecGR_SportMovies.xz","Rytec"),
("Rytec-Italia-Basis","http://www.xmltvepg.nl/rytecIT_Basic.xz","Rytec"),
("Rytec-Italia-Sky","http://www.xmltvepg.nl/rytecIT_Sky.xz","Rytec"),
("Rytec-Italia-Sports-Film-Premium","http://www.xmltvepg.nl/rytecIT_SportMovies.xz","Rytec"),
("Rytec-Portugal","http://www.xmltvepg.nl/rytecPT.xz","Rytec"),
("Rytec-UK-Basic","http://www.xmltvepg.nl/rytecUK_Basic.xz","Rytec"),
("Rytec-UK-Sports-Movies","http://www.xmltvepg.nl/rytecUK_SportMovies.xz","Rytec"),
("Albania","https://epgshare01.online/epgshare01/epg_ripper_AL1.xml.gz","Europe"),
("Austria","https://epgshare01.online/epgshare01/epg_ripper_AT1.xml.gz","Europe"),
("Belgium","https://epgshare01.online/epgshare01/epg_ripper_BE1.xml.gz","Europe"),
("Bosnia_Herzegovina","https://epgshare01.online/epgshare01/epg_ripper_BA1.xml.gz","Europe"),
("Bulgaria","https://epgshare01.online/epgshare01/epg_ripper_BG1.xml.gz","Europe"),
("Croatia","https://epgshare01.online/epgshare01/epg_ripper_HR1.xml.gz","Europe"),
("Cyprus","https://epgshare01.online/epgshare01/epg_ripper_CY1.xml.gz","Europe"),
("Czech_Republic","https://epgshare01.online/epgshare01/epg_ripper_CZ1.xml.gz","Europe"),
("Denmark","https://epgshare01.online/epgshare01/epg_ripper_DK1.xml.gz","Europe"),
("Estonia","https://epgshare01.online/epgshare01/epg_ripper_EE1.xml.gz","Europe"),
("Finland","https://epgshare01.online/epgshare01/epg_ripper_FI1.xml.gz","Europe"),
("France-EPGShare","https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz","Europe"),
("France1","https://www.open-epg.com/files/france1.xml.gz","Europe"),
("France2","https://www.open-epg.com/files/france2.xml.gz","Europe"),
("France3","https://www.open-epg.com/files/france3.xml.gz","Europe"),
("Germany","https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz","Europe"),
("Greece","https://epgshare01.online/epgshare01/epg_ripper_GR1.xml.gz","Europe"),
("Hungary","https://epgshare01.online/epgshare01/epg_ripper_HU1.xml.gz","Europe"),
("Iceland","https://epgshare01.online/epgshare01/epg_ripper_IS1.xml.gz","Europe"),
("Ireland","https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz","Europe"),
("Italy","https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz","Europe"),
("Latvia","https://epgshare01.online/epgshare01/epg_ripper_LV1.xml.gz","Europe"),
("Lithuania","https://epgshare01.online/epgshare01/epg_ripper_LT1.xml.gz","Europe"),
("Netherlands","https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz","Europe"),
("Norway","https://epgshare01.online/epgshare01/epg_ripper_NO1.xml.gz","Europe"),
("Poland","https://epgshare01.online/epgshare01/epg_ripper_PL1.xml.gz","Europe"),
("Portugal","https://epgshare01.online/epgshare01/epg_ripper_PT1.xml.gz","Europe"),
("Romania","https://epgshare01.online/epgshare01/epg_ripper_RO1.xml.gz","Europe"),
("Serbia","https://epgshare01.online/epgshare01/epg_ripper_RS1.xml.gz","Europe"),
("Slovakia","https://epgshare01.online/epgshare01/epg_ripper_SK1.xml.gz","Europe"),
("Slovenia","https://epgshare01.online/epgshare01/epg_ripper_SI1.xml.gz","Europe"),
("Spain","https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz","Europe"),
("Spain2","https://www.open-epg.com/files/spain1.xml","Europe"),
("Sweden","https://epgshare01.online/epgshare01/epg_ripper_SE1.xml.gz","Europe"),
("Switzerland","https://epgshare01.online/epgshare01/epg_ripper_CH1.xml.gz","Europe"),
("UK","https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz","Europe"),
("Egypt-EPGShare","https://epgshare01.online/epgshare01/epg_ripper_EG1.xml.gz","Africa"),
("Kenya","https://epgshare01.online/epgshare01/epg_ripper_KE1.xml.gz","Africa"),
("Nigeria","https://epgshare01.online/epgshare01/epg_ripper_NG1.xml.gz","Africa"),
("South_Africa","https://epgshare01.online/epgshare01/epg_ripper_ZA1.xml.gz","Africa"),
("China","https://epgshare01.online/epgshare01/epg_ripper_CN1.xml.gz","Asia"),
("India","https://epgshare01.online/epgshare01/epg_ripper_IN1.xml.gz","Asia"),
("Indonesia","https://epgshare01.online/epgshare01/epg_ripper_ID1.xml.gz","Asia"),
("Israel","https://epgshare01.online/epgshare01/epg_ripper_IL1.xml.gz","Asia"),
("Hong_Kong","https://epgshare01.online/epgshare01/epg_ripper_HK1.xml.gz","Asia"),
("Japan","https://epgshare01.online/epgshare01/epg_ripper_JP1.xml.gz","Asia"),
("South_Korea","https://epgshare01.online/epgshare01/epg_ripper_KR1.xml.gz","Asia"),
("Malaysia","https://epgshare01.online/epgshare01/epg_ripper_MY1.xml.gz","Asia"),
("Pakistan","https://epgshare01.online/epgshare01/epg_ripper_PK1.xml.gz","Asia"),
("Philippines","https://epgshare01.online/epgshare01/epg_ripper_PH1.xml.gz","Asia"),
("Singapore","https://epgshare01.online/epgshare01/epg_ripper_SG1.xml.gz","Asia"),
("Thailand","https://epgshare01.online/epgshare01/epg_ripper_TH1.xml.gz","Asia"),
("Turkey","https://epgshare01.online/epgshare01/epg_ripper_TR1.xml.gz","Asia"),
("Vietnam","https://epgshare01.online/epgshare01/epg_ripper_VN1.xml.gz","Asia"),
("Canada","https://epgshare01.online/epgshare01/epg_ripper_CA1.xml.gz","North America"),
("Mexico","https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz","North America"),
("USA","https://epgshare01.online/epgshare01/epg_ripper_US1.xml.gz","North America"),
("Argentina","https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz","South America"),
("Brazil","https://epgshare01.online/epgshare01/epg_ripper_BR1.xml.gz","South America"),
("Chile","https://epgshare01.online/epgshare01/epg_ripper_CL1.xml.gz","South America"),
("Colombia","https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz","South America"),
("Ecuador","https://epgshare01.online/epgshare01/epg_ripper_EC1.xml.gz","South America"),
("Peru","https://epgshare01.online/epgshare01/epg_ripper_PE1.xml.gz","South America"),
("Uruguay","https://epgshare01.online/epgshare01/epg_ripper_UY1.xml.gz","South America"),
("Venezuela","https://epgshare01.online/epgshare01/epg_ripper_VE1.xml.gz","South America"),
("Australia","https://epgshare01.online/epgshare01/epg_ripper_AU1.xml.gz","Oceania"),
("New_Zealand","https://epgshare01.online/epgshare01/epg_ripper_NZ1.xml.gz","Oceania"),
]


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


SOURCES = [dict(id="ext_" + _slug(n), name=n, url=u, region=r) for n, u, r in _RAW]
BY_ID = {s["id"]: s for s in SOURCES}


def local_xml_path(source, epg_dir):
    return os.path.join(epg_dir, "%s.xml" % source["id"])


def source_status(source, epg_dir):
    path = local_xml_path(source, epg_dir)
    if not os.path.exists(path):
        return "REMOTE"
    try:
        age = max(0, __import__('time').time() - os.path.getmtime(path))
        return "CACHED" if age < 36 * 3600 else "STALE"
    except Exception:
        return "CACHED"


def download_source(source, epg_dir, retries=3, timeout=30):
    """Download and decompress one external source to a stable local XML file."""
    if isinstance(source, str):
        source = BY_ID[source]
    if not os.path.isdir(epg_dir):
        os.makedirs(epg_dir)
    url = source["url"]
    lower = url.lower().split("?", 1)[0]
    # EPG-Importer source definitions may reference an already-local XMLTV
    # file with file:///... . Never send those through requests.
    if lower.startswith("file://"):
        src_path = url[7:]
        if not os.path.exists(src_path):
            raise IOError("Local XMLTV file does not exist: %s" % src_path)
        with open(src_path, "rb") as f:
            payload = f.read()
    elif os.path.isabs(url) and os.path.exists(url):
        with open(url, "rb") as f:
            payload = f.read()
    else:
        d = Downloader(retries=retries, timeout=timeout)
        try:
            resp = d.get(url)
            payload = resp.content
        finally:
            d.close()
    if lower.endswith(".gz"):
        payload = gzip.decompress(payload)
    elif lower.endswith(".xz"):
        if lzma is not None:
            payload = lzma.decompress(payload)
        else:
            # Some compact Enigma2 images omit Python's _lzma module.
            import subprocess
            proc = subprocess.Popen(["xz", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            payload, err = proc.communicate(payload)
            if proc.returncode != 0:
                raise RuntimeError("XZ decompression unavailable: %s" % (err.decode("utf-8", "ignore") or "install xz"))
    if not payload.lstrip().startswith(b"<"):
        raise ValueError("Downloaded source is not XMLTV data")
    path = local_xml_path(source, epg_dir)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(payload)
    os.replace(tmp, path)
    return path


def cached_sources(epg_dir):
    out = []
    for src in SOURCES:
        path = local_xml_path(src, epg_dir)
        if os.path.exists(path):
            item = dict(src)
            item["path"] = path
            out.append(item)
    return out
