# -*- coding: utf-8 -*-
"""Lightweight Mapping Repair strategy helpers.

The mapping engine must discover the right XMLTV ID before applying any
country/provider preference.  This module contains only cheap deterministic
routing hints for high-value MENA broadcaster families.  It never downloads or
parses programme XMLTV.
"""
from __future__ import print_function
import re


def _norm(value):
    text = re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold())
    return " ".join(text.split())


def is_external_source(source):
    source = source or {}
    return bool(source.get("external")) or str(source.get("kind") or "").casefold() == "external"


def has_id_catalog(source):
    """Whether Smart Mapping can obtain Channel IDs without a full EPG download.

    7.0.6 accepts a tiny catalogue URL, an authoritative channels map, or the
    source XMLTV URL itself because the direct reader streams only the <channel>
    declaration header and closes the connection before programme data.
    """
    source = source or {}
    if not is_external_source(source):
        return False
    if str(source.get("catalog_url") or "").strip():
        return True
    if source.get("channels_urls"):
        return True
    if str(source.get("url") or "").strip():
        return True
    return bool(source.get("urls"))


def family(channel_name, provider=""):
    n = _norm(channel_name)
    p = _norm(provider)
    joined = (n + " " + p).strip()
    if n.startswith("mbc ") or n == "mbc" or " mbc " in (" " + p + " "):
        if "masr" in n or "masr" in p or "egypt" in n:
            return "MBC_MASR"
        if "iraq" in n:
            return "MBC_IRAQ"
        return "MBC"
    if n.startswith("trt ") or n == "trt" or p == "trt":
        return "TRT"
    if "bein" in n or "be in" in n:
        if re.search(r"(?:^| )(?:fr|french|francais)(?: |$)", n):
            return "BEIN_FR"
        if re.search(r"(?:^| )(?:en|eng|english)(?: |$)", n):
            return "BEIN_EN"
        return "BEIN"
    if "osn" in joined:
        return "OSN"
    if "rotana" in joined:
        return "ROTANA"
    if any(x in joined for x in ("abu dhabi", "abudhabi", "ad sports", "ad sport", "yas sports", "yas tv")):
        return "ADM"
    if any(x in joined for x in ("dubai tv", "dubai sports", "dubai racing", "dubai one", "sama dubai", "noor dubai", "dubai zaman")):
        return "DMI"
    if "sharjah" in joined:
        return "UAE"
    if any(x in joined for x in ("alkass", "al kass")):
        return "ALKASS"
    if re.search(r"(?:^| )ssc(?: |[0-9]|$)", joined):
        return "SSC"
    if any(x in joined for x in ("art aflam", "art cinema", "art hekayat", "alfa cinema", "alfa drama")):
        return "ART"
    if "starz" in joined:
        return "STARZ"
    if "al jazeera" in joined or "aljazeera" in joined:
        return "QATAR"
    return ""


def strategic_source_ids(channel_name, provider="", country=""):
    fam = family(channel_name, provider)
    country = str(country or "").upper().strip()
    out = []

    def add(values):
        for sid in values:
            if sid and sid not in out:
                out.append(sid)

    shard = lambda code: "ext_epgmanager_mena_" + str(code).lower()
    provider_shard = lambda name: "ext_epgmanager_provider_" + str(name).lower()
    sa = [shard("sa")] + ["ext_openepg_saudi_arabia_%d" % i for i in range(1, 7)]
    qa = [shard("qa")] + ["ext_openepg_qatar_%d" % i for i in range(1, 7)]
    eg = [shard("eg"), "ext_openepg_egypt_1", "ext_openepg_egypt_2"]
    ae = [shard("ae"), "ext_openepg_uae_6"]
    tr = ["ext_epgshare_turkey", "ext_openepg_turkey_1", "ext_openepg_turkey_2",
          "ext_openepg_turkey_3", "ext_openepg_turkey_4", "ext_openepg_turkey_5"]
    fr = ["ext_epgshare_france", "ext_openepg_france", "ext_openepg_france_1",
          "ext_openepg_france_2", "ext_openepg_france_3", "ext_rytec_france_sport_cinema"]

    if fam == "MBC_MASR":
        add([provider_shard("mbc")]); add(eg); add(sa)
    elif fam in ("MBC", "MBC_IRAQ"):
        add([provider_shard("mbc")]); add(sa)
        if fam == "MBC_IRAQ": add([shard("iq")])
    elif fam == "TRT": add(tr)
    elif fam == "BEIN_FR":
        add(fr); add([provider_shard("bein"), "local_bein_sports"]); add(qa)
    elif fam == "BEIN_EN":
        add([provider_shard("bein"), "local_bein_sports", "ext_openepg_qatar_5",
             "ext_openepg_qatar_2", "ext_openepg_qatar_6"]); add(qa)
    elif fam == "BEIN":
        add([provider_shard("bein"), "local_bein_sports"]); add(qa)
    elif fam == "OSN": add([provider_shard("osn")])
    elif fam == "ROTANA": add([provider_shard("rotana")]); add(sa)
    elif fam == "ADM": add([provider_shard("adm")]); add(ae)
    elif fam == "DMI": add([provider_shard("dmi")]); add(ae)
    elif fam == "UAE": add(ae)
    elif fam == "ALKASS": add(["ext_epgscrapers_alkass", provider_shard("alkass")]); add(qa)
    elif fam == "SSC": add([provider_shard("ssc")]); add(sa)
    elif fam == "ART": add([provider_shard("art")])
    elif fam == "STARZ": add([provider_shard("starz")])
    elif fam == "QATAR":
        add(qa)
        if "al jazeera" in _norm(channel_name) or "aljazeera" in _norm(channel_name):
            add(["ext_epgshare_al_jazeera"])

    # Country context is a fallback discovery hint, not identity proof.
    country_shards = {
        "AE":"ae", "SA":"sa", "EG":"eg", "QA":"qa", "DZ":"dz", "TN":"tn",
        "LY":"ly", "KW":"kw", "BH":"bh", "OM":"om", "JO":"jo", "LB":"lb",
        "PS":"ps", "IQ":"iq", "SY":"sy", "YE":"ye", "SD":"sd", "MR":"mr",
    }
    if country in country_shards:
        add([shard(country_shards[country])])
        if country == "AE": add(["ext_openepg_uae_6"])
        elif country == "SA": add(["ext_openepg_saudi_arabia_%d" % i for i in range(1,7)])
        elif country == "EG": add(["ext_openepg_egypt_1", "ext_openepg_egypt_2"])
        elif country == "QA": add(["ext_openepg_qatar_%d" % i for i in range(1,7)])
    elif country == "MENA": add(["ext_epgmanager_mena_other"])
    elif country == "TR": add(tr)
    elif country == "FR": add(fr)
    return out

