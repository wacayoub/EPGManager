#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accuracy layer for mena_cloud_shards.

EPGShare AE/SA packs are broad regional catalogues: their .ae/.sa suffixes are
not reliable country identity. This wrapper keeps the provider-first sharder,
but replaces country detection with conservative name/official-source rules.
Uncertain channels go to MENA Other instead of a wrong country.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import mena_cloud_shards as base

TRUSTED_CACHE_COUNTRY = {
    "openepg-egypt1": "EG", "openepg-egypt2": "EG",
    "openepg-palestine1": "PS",
    "openepg-qatar1": "QA", "openepg-qatar2": "QA", "openepg-qatar3": "QA",
    "openepg-qatar4": "QA", "openepg-qatar5": "QA", "openepg-qatar6": "QA",
    "openepg-saudi1": "SA", "openepg-saudi2": "SA", "openepg-saudi3": "SA",
    "openepg-saudi4": "SA", "openepg-saudi5": "SA", "openepg-saudi6": "SA",
    "openepg-uae6": "AE",
    "epgshare-aljazeera1": "QA",
}

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


def pop_arg(name):
    try:
        i = sys.argv.index(name)
    except ValueError:
        return None
    if i + 1 >= len(sys.argv):
        raise SystemExit("%s requires a path" % name)
    value = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    return value


def trusted_memberships(cache_dir):
    out = {}
    if not cache_dir:
        return out
    root_dir = Path(cache_dir)
    if not root_dir.is_dir():
        return out
    for source, code in TRUSTED_CACHE_COUNTRY.items():
        path = root_dir / (source + ".xml.gz")
        if not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            root = base.read_xml(path)
        except Exception:
            continue
        for c in root.findall("channel"):
            cid = (c.get("id") or "").strip()
            if cid:
                out.setdefault(cid, set()).add(code)
    return out


def main():
    cache_dir = pop_arg("--source-cache-dir")
    memberships = trusted_memberships(cache_dir)

    def safe_country_code(cid, name, meta):
        p = base.probe(cid, name, meta)
        if any(base.has_word(p, word) for word in MOROCCO_WORDS) or re.search(r"\.ma(?:@|$)", cid or "", re.I):
            return "MA"
        for code, words in COUNTRY_KEYWORDS:
            if any(base.has_word(p, word) for word in words):
                return code

        trusted = memberships.get(cid) or set()
        if len(trusted) == 1:
            code = next(iter(trusted))
            if code in base.COUNTRY_BY_CODE:
                return code

        # Only catalogue-backed primary IDs may use their country suffix.
        # Raw EPGShare aliases such as *.ae/*.sa are intentionally NOT trusted.
        if meta:
            meta_id = str(meta.get("xmltv_id") or cid or "")
            m = re.search(r"\.([a-z]{2})(?:@|$)", meta_id, re.I)
            if m and m.group(1).upper() in base.COUNTRY_BY_CODE:
                return m.group(1).upper()
        return None

    base.country_code = safe_country_code
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
