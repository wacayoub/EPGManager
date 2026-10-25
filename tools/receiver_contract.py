#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single receiver-facing canonical contract for MENA publication.

Every provider namespace, required core ID, optional/event ID and premium
ownership rule lives here. Production tools import this module instead of
maintaining drifting copies.
"""
from __future__ import annotations

BEIN_CORE = {
    "beIN.4K.qa", "beIN.Drama.qa", "beIN.Movies.Action.qa", "beIN.Movies.Drama.qa",
    "beIN.Movies.Family.qa", "beIN.Movies.Premiere.qa", "beIN.Series.1.qa",
    "beIN.Series.2.qa", "beIN.Sports.FTA.qa", "beIN.Sports.News.qa", "beIN.Sports.qa",
} | {"beIN.Sports.%d.qa" % n for n in range(1, 10)} \
  | {"beIN.Sports.EN%d.qa" % n for n in range(1, 3)} \
  | {"beIN.Sports.FR%d.qa" % n for n in range(1, 3)}
BEIN_EVENTS = {"beIN.Sports.MAX%d.qa" % n for n in range(1, 7)} \
  | {"beIN.Sports.XTRA%d.qa" % n for n in range(1, 10)}
BEIN_OPTIONAL = BEIN_EVENTS | {"beIN.Gourmet.qa"}

OSN = {
    "OSN.Comedy.ae", "OSN.Kids.ae", "OSN.Mezze.ae", "OSN.Movies.Action.ae",
    "OSN.Movies.Hollywood.ae", "OSN.Movies.Premiere.ae", "OSN.Showcase.ae",
    "OSN.Crime.ae", "OSN.Documentary.ae", "OSN.iQIYI.ae", "OSN.Movies.Comedy.ae",
    "OSN.Movies.Family.ae", "OSN.Movies.Horror.ae", "OSN.Now.ae", "OSN.One.ae",
    "OSN.Pop.Up.ae", "OSN.Showcase.Classics.ae", "OSN.Yahala.ae",
    "OSN.Yahala.Aflam.ae", "OSN.Yahala.Bil.Arabi.ae",
}
MBC = {
    "MBC.Alarabiya.mena", "MBC.Al.Hadath.mena", "MBC.1.mena", "MBC.2.mena",
    "MBC.3.mena", "MBC.4.mena", "MBC.5.mena", "MBC.Action.mena",
    "MBC.Bollywood.mena", "MBC.Drama.mena", "MBC.Iraq.mena", "MBC.Masr.mena",
    "MBC.Masr2.mena", "MBC.Masr.Drama.mena", "MBC.MAX.mena", "MBC.Persia.mena",
    "MBC.Plus.Drama.mena",
}
ROTANA = {
    "Rotana.Plus.mena", "Rotana.Aflam.Plus.mena", "Rotana.Kids.mena",
    "Rotana.M.Plus.mena", "Rotana.Music.mena", "Rotana.Cinema.Egypt.mena",
    "Rotana.Cinema.KSA.mena", "Rotana.Classic.mena", "Rotana.Comedy.mena",
    "Rotana.Drama.mena", "Rotana.Khalijia.mena",
}
ROTANA_OPTIONAL = {"Rotana.Clip.mena"}
DMI = {
    "DMI.Dubai.One.ae", "DMI.Dubai.Sports.2.ae", "DMI.Dubai.Sports.ae",
    "DMI.Dubai.Zaman.ae", "DMI.Dubai.Racing.1.ae", "DMI.Dubai.Racing.2.ae",
    "DMI.Dubai.TV.ae", "DMI.Noor.Dubai.TV.ae", "DMI.Sama.Dubai.ae",
}
DMI_OPTIONAL_REAL_EPG_ONLY = {"DMI.Dubai.Racing.1.ae"}
ADM = {
    "AbuDhabiEmirates.ae", "AbuDhabiSports1.ae", "AbuDhabiSports2.ae",
    "AbuDhabiTV.ae", "ADSportsExtra.ae", "ADSportsPremium1.ae",
    "ADSportsPremium2.ae", "Majid.ae", "NationalGeographicAbuDhabi.ae",
    "YasTV.ae", "YasTVExtra.ae",
}
PREMIUM_INTERNATIONAL = {
    "AnimalPlanetEurope.uk@SD", "DiscoveryChannelMiddleEastAfrica.us@SD",
    "InvestigationDiscovery.uk@SD", "HistoryMiddleEast.us@SD",
    "History2MiddleEast.us@SD", "TLCArabia.us@SD", "CartoonNetworkMENA.uk@SD",
    "NickelodeonArabia.ae@SD", "NickJrArabia.ae@SD", "NicktoonsArabia.ae@SD",
    "CartoonNetworkArabic.ae@SD",
}

PROVIDERS = {
    "provider-bein": (("beIN.",), BEIN_CORE, BEIN_OPTIONAL),
    "provider-osn": (("OSN.",), OSN, set()),
    "provider-mbc": (("MBC.",), MBC, set()),
    "provider-rotana": (("Rotana.",), ROTANA, ROTANA_OPTIONAL),
    "provider-dmi": (("DMI.",), DMI - DMI_OPTIONAL_REAL_EPG_ONLY, DMI_OPTIONAL_REAL_EPG_ONLY),
    "provider-adm": (None, ADM, set()),
}
RESERVED_PREFIXES = ("beIN.", "OSN.", "MBC.", "Rotana.", "DMI.")
PREMIUM_IDS = frozenset(BEIN_CORE | BEIN_OPTIONAL | OSN)
ALLOWED_RESERVED_IDS = frozenset(
    BEIN_CORE | BEIN_OPTIONAL | OSN | MBC | ROTANA | ROTANA_OPTIONAL | DMI | ADM
)

def owner_for_id(cid: str):
    if cid in BEIN_CORE or cid in BEIN_OPTIONAL:
        return "provider-bein"
    if cid in OSN:
        return "provider-osn"
    if cid in MBC:
        return "provider-mbc"
    if cid in ROTANA or cid in ROTANA_OPTIONAL:
        return "provider-rotana"
    if cid in DMI:
        return "provider-dmi"
    if cid in ADM:
        return "provider-adm"
    return None

def is_unknown_reserved(cid: str) -> bool:
    return cid.startswith(RESERVED_PREFIXES) and cid not in ALLOWED_RESERVED_IDS

def validate_contract():
    errors = []
    owners = {}
    for stem, (_, required, optional) in PROVIDERS.items():
        if required & optional:
            errors.append(f"{stem}: required/optional overlap")
        for cid in required | optional:
            previous = owners.get(cid)
            if previous and previous != stem:
                errors.append(f"{cid}: owner collision {previous}/{stem}")
            owners[cid] = stem
    if set(BEIN_CORE) & set(OSN):
        errors.append("premium provider overlap")
    return errors
