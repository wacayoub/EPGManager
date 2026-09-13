#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accuracy layer for MENA country/provider shards.

Provider ownership comes from channel identity, never from the website/guide
that supplied the EPG. Country comes from explicit identity/name or, for an
iptv-org canonical catalogue row, its canonical country suffix. Regional
OpenEPG/EPGShare pack labels and raw .ae/.sa aliases are never nationality
proof; uncertain services go to MENA Other.
"""
from __future__ import annotations

import re
import sys
import mena_cloud_shards as base


def pop_arg(name):
    try:
        i = sys.argv.index(name)
    except ValueError:
        return None
    if i + 1 >= len(sys.argv):
        raise SystemExit("%s requires a value" % name)
    value = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    return value


def identity_probe(cid, name, meta):
    return base.norm("%s %s %s" % (cid or "", name or "", (meta or {}).get("name") or ""))


def compact(value):
    return re.sub(r"[^a-z0-9]+", "", base.norm(value or ""))


def has_word(text, *words):
    padded = " " + text + " "
    return any((" " + base.norm(word) + " ") in padded for word in words)


def safe_provider_group(cid, name, meta):
    p = identity_probe(cid, name, meta)
    c_id = compact(cid)
    c_name = compact(name)
    tokens = (c_id, c_name)

    if has_word(p, "alkass", "al kass") or any(x.startswith("alkass") for x in tokens):
        return "alkass"
    if has_word(p, "bein", "be in", "bein sports", "beinsports") or any(x.startswith("bein") for x in tokens):
        return "bein"
    if has_word(p, "osn", "osntv", "osn tv") or any(x.startswith("osn") for x in tokens):
        return "osn"
    if (re.search(r"(?:^| )mbc(?: |[0-9]|$)", p)
            or any(x.startswith("mbc") for x in tokens)
            or any(x.startswith("alarabiya") for x in tokens)
            or any(x.startswith("alhadath") for x in tokens)
            or any(x.startswith("wanasah") for x in tokens)):
        return "mbc"
    if has_word(p, "rotana") or any(x.startswith("rotana") for x in tokens):
        return "rotana"
    if (has_word(p, "abu dhabi", "abudhabi", "ad sports", "ad sport", "yas sports",
                 "yas tv", "majid", "national geographic abu dhabi", "al emarat")
            or any(x.startswith(("abudhabi", "adsports", "yassports", "yastv", "majid")) for x in tokens)):
        return "adm"
    if (has_word(p, "dubai tv", "dubai sports", "dubai one", "dubai racing",
                 "sama dubai", "noor dubai", "dubai zaman")
            or any(x.startswith(("dubaitv", "dubaisports", "dubaione", "dubairacing",
                                 "samadubai", "noordubai", "dubaizaman")) for x in tokens)):
        return "dmi"
    if (has_word(p, "art aflam", "art cinema", "art hekayat", "art movies", "art sport",
                 "alfa cinema", "alfa drama", "alfa hekayat", "alfa series", "alfa music",
                 "alfa fann", "alfa al safwa", "alfa al yawm")
            or re.search(r"(?:^| )art(?: |[0-9]|$)", p)
            or any(x.startswith(("artaflam", "artcinema", "arthekayat", "artmovies",
                                 "alfacinema", "alfadrama", "alfahekayat")) for x in tokens)):
        return "art"
    if re.search(r"(?:^| )ssc(?: |[0-9]|$)", p) or has_word(p, "saudi sports company") or any(x.startswith("ssc") for x in tokens):
        return "ssc"
    if has_word(p, "starzplay", "starz play", "starz") or any(x.startswith("starz") for x in tokens):
        return "starz"
    return None


COUNTRY_KEYWORDS = [
    ("MR", ("mauritania", "mauritanie", "almouritania")),
    ("SD", ("sudan", "sudania", "blue nile", "khartoum")),
    ("LY", ("libya", "libyan", "jamahiriya")),
    ("TN", ("tunisia", "tunisie", "wataniya", "ettounsi", "nessma", "attessia", "hannibal", "zaytoona")),
    ("DZ", ("algeria", "algerie", "entv", "echorouk", "ennahar", "el bilad", "dzair", "samira tv", "al djazair", "el fadjr")),
    ("YE", ("yemen", "yemeni", "aden", "belqees", "al masirah", "almasirah", "suhail", "saeedah", "alsaeedah", "sheba tv", "reef alyemen")),
    ("PS", ("palestine", "palestinian", "al quds", "quds", "wattan", "maan tv", "tulkarem", "hebron", "musawa", "falastini", "falestinona")),
    ("IQ", ("iraq", "iraqi", "iraqiya", "baghdad", "fallujah", "dijlah", "kirkuk", "karbala", "kurdistan", "kurdsat", "kurdmax", "rudaw", "nrt", "zagros", "speda", "waar", "turkmeneli", "al sharqiya", "sharqiya", "alsumaria", "al sumaria", "al rasheed", "alrasheed", "al forat", "al anbar", "afaq tv", "al kafeel", "al najaf", "alahad", "alghadeer")),
    ("LB", ("lebanon", "lebanese", "beirut", "lbc", "lbci", "mtv lebanon", "al jadeed", "aljadeed", "tele liban", "nbn", "otv lebanon", "al mayadeen", "mayadeen", "aghani aghani", "noursat", "nour koddass")),
    ("JO", ("jordan", "jordanian", "roya tv", "royaa", "al mamlaka", "mamlaka", "karameesh", "toyor aljanah")),
    ("OM", ("oman", "omani", "majan")),
    ("BH", ("bahrain", "bahraini", "lualua")),
    ("KW", ("kuwait", "kuwaiti", "ktv", "al rai tv", "alrai")),
    ("QA", ("qatar", "qatari", "al rayyan", "rayyan", "al jazeera", "aljazeera", "doha", "sout alkhaleej")),
    ("AE", ("uae", "united arab emirates", "emirates", "emarat", "sharjah", "ajman", "fujairah", "baynounah", "al dhafra", "aldafrah", "zayed quran")),
    ("SA", ("saudi", "saudiya", "ksa", "al ekhbaria", "alekhbaria", "quran al kareem", "sunnah nabawiyah", "al resalah", "al majd", "saudia alaan")),
    ("SY", ("syria", "syrian", "souriya", "al souriya", "halab today", "sama tv", "lana tv")),
    ("EG", ("egypt", "egyptian", "misr", "masr", "masriya", "al masriyah", "cairo", "qahera", "kahera", "alexandaria", "alexandria", "askandria", "aswan", "matrouh", "north sinai", "south sinai", "zamalek", "al ahly", "nile", "cbc", "dmc", "extra news", "on time sports", "on drama", "on e", "mekameleen", "mazzika", "sada el balad", "mehwar", "al nahar", "alnahar", "al hayat", "alhayat")),
]

MOROCCO_WORDS = (
    "morocco", "maroc", "maghribiya", "al aoula", "alaoula", "arryadia",
    "arrabiaa", "assadissa", "2m", "2m monde", "2m national", "medi1",
    "medi 1", "chada tv", "tele maroc", "m24 tv"
)


def safe_country_code(cid, name, meta):
    p = identity_probe(cid, name, meta)
    if any(has_word(p, word) for word in MOROCCO_WORDS) or re.search(r"\.ma(?:@|$)", cid or "", re.I):
        return "MA"
    for code, words in COUNTRY_KEYWORDS:
        if any(has_word(p, word) for word in words):
            return code
    if meta and meta.get("xmltv_id"):
        canonical_id = str(meta.get("xmltv_id") or "")
        m = re.search(r"\.([a-z]{2})(?:@|$)", canonical_id, re.I)
        if m and m.group(1).upper() in base.COUNTRY_BY_CODE:
            return m.group(1).upper()
    return None


def main():
    pop_arg("--source-cache-dir")
    base.provider_group = safe_provider_group
    base.country_code = safe_country_code
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
