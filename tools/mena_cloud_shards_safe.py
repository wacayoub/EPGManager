#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accuracy layer for MENA country/provider shards.

Provider ownership comes from channel identity, never from the EPG guide site.
Country order: iptv-org canonical metadata -> unique name/alias metadata ->
explicit conservative identity rules -> canonical catalogue suffix -> Other.
Regional pack labels and raw .ae/.sa/.eg aliases are never country evidence.

MBC programme policy: season/episode markers are removed from programme titles
and moved to the description. This is applied only to the provider-mbc shard.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
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


BEIN_LEGACY_PACKAGE_KEYS = {
    "4kdigitalqa",
    "movies1premieredigitalqa",
    "movies2actiondigitalqa",
    "movies3dramadigitalqa",
    "movies4familydigitalqa",
    "series1digitalqa",
    "series2digitalqa",
    "newsdigitalqa",
}


def safe_provider_group(cid, name, meta):
    if cid in base.PREMIUM_INTERNATIONAL_IDS:
        return "international"
    p = identity_probe(cid, name, meta)
    c_id = compact(cid)
    c_name = compact(name)
    c_meta = compact((meta or {}).get("name") or "")
    tokens = (c_id, c_name)

    if {c_id, c_name, c_meta} & BEIN_LEGACY_PACKAGE_KEYS:
        return "bein"
    if has_word(p, "alkass", "al kass") or any(x.startswith("alkass") for x in tokens):
        return "alkass"
    if has_word(p, "bein", "be in", "bein sports", "beinsports") or any(x.startswith("bein") for x in tokens):
        return "bein"
    if has_word(p, "osn", "osntv", "osn tv") or any(x.startswith("osn") for x in tokens):
        return "osn"

    hadath_exact = {"alhadath", "alhadathlive", "alhadathtv", "alhadathhd"}
    if (re.search(r"(?:^| )mbc(?: |[0-9]|$)", p)
            or any(x.startswith("mbc") for x in tokens)
            or any(x.startswith("alarabiya") for x in tokens)
            or any(x in hadath_exact for x in tokens)
            or any(x.startswith("wanasah") for x in tokens)):
        return "mbc"
    if has_word(p, "rotana") or any(x.startswith("rotana") for x in tokens):
        return "rotana"
    if (has_word(p, "abu dhabi", "abudhabi", "ad sports", "ad sport", "yas sports",
                 "yas tv", "majid", "national geographic abu dhabi", "al emarat", "baynounah")
            or any(x.startswith(("abudhabi", "adsports", "yassports", "yastv", "majid", "baynounah")) for x in tokens)):
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


_AR_NUM = r"[0-9٠-٩]+"
_MBC_META_PATTERNS = [
    re.compile(r"\s*(?:[-–—|:•]\s*)?(?:الموسم|موسم)\s*(%s)\s*(?:[-–—|:•]\s*)?(?:الحلقة|حلقة)\s*(%s)\s*$" % (_AR_NUM, _AR_NUM), re.I),
    re.compile(r"\s*(?:[-–—|:•]\s*)?(?:الحلقة|حلقة)\s*(%s)\s*(?:[-–—|:•]\s*)?(?:الموسم|موسم)\s*(%s)\s*$" % (_AR_NUM, _AR_NUM), re.I),
    re.compile(r"\s*(?:[-–—|:•]\s*)?S(?:eason)?\s*0*([0-9]+)\s*[-–—|:• ]*E(?:p(?:isode)?)?\.?\s*0*([0-9]+)\s*$", re.I),
    re.compile(r"\s*(?:[-–—|:•]\s*)?Season\s*0*([0-9]+)\s*[-–—|:• ]*(?:Episode|Ep\.?)\s*0*([0-9]+)\s*$", re.I),
]
_MBC_EP_AR = re.compile(r"\s*(?:[-–—|:•]\s*)?(?:الحلقة|حلقة)\s*(%s)\s*$" % _AR_NUM, re.I)
_MBC_SEASON_AR = re.compile(r"\s*(?:[-–—|:•]\s*)?(?:الموسم|موسم)\s*(%s)\s*$" % _AR_NUM, re.I)
_MBC_EP_EN = re.compile(r"\s*(?:[-–—|:•]\s*)?(?:Episode|Ep\.?)\s*0*([0-9]+)\s*$", re.I)
_MBC_SEASON_EN = re.compile(r"\s*(?:[-–—|:•]\s*)?Season\s*0*([0-9]+)\s*$", re.I)


def _clean_title_tail(value):
    return re.sub(r"\s*[-–—|:•]+\s*$", "", re.sub(r"\s+", " ", value or "")).strip()


def _extract_mbc_meta(value):
    """Return (clean_title, season, episode) for conservative end-of-title markers."""
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return text, None, None

    for idx, pattern in enumerate(_MBC_META_PATTERNS):
        m = pattern.search(text)
        if m:
            if idx == 1:  # Arabic episode then season.
                episode, season = m.group(1), m.group(2)
            else:
                season, episode = m.group(1), m.group(2)
            return _clean_title_tail(text[:m.start()]), season, episode

    season = None
    episode = None
    work = text
    # Handle a trailing episode then a trailing season, or either marker alone.
    m = _MBC_EP_AR.search(work) or _MBC_EP_EN.search(work)
    if m:
        episode = m.group(1)
        work = _clean_title_tail(work[:m.start()])
    m = _MBC_SEASON_AR.search(work) or _MBC_SEASON_EN.search(work)
    if m:
        season = m.group(1)
        work = _clean_title_tail(work[:m.start()])
    if season or episode:
        return work, season, episode
    return text, None, None


def _desc_contains_numbered_meta(text, season, episode):
    text = text or ""
    season_ok = not season or bool(re.search(r"(?:الموسم|موسم|season)\s*[:#-]?\s*0*%s\b" % re.escape(str(season)), text, re.I))
    episode_ok = not episode or bool(re.search(r"(?:الحلقة|حلقة|episode|ep\.?)\s*[:#-]?\s*0*%s\b" % re.escape(str(episode)), text, re.I))
    return season_ok and episode_ok


def normalize_mbc_programme(programme):
    season = None
    episode = None
    meta_lang = None
    changed = 0
    for title in programme.findall("title"):
        original = (title.text or "").strip()
        cleaned, s, e = _extract_mbc_meta(original)
        if (s or e) and cleaned and cleaned != original:
            title.text = cleaned
            changed += 1
            if season is None and s:
                season = s
            if episode is None and e:
                episode = e
            if meta_lang is None:
                meta_lang = (title.get("lang") or "").lower()

    if not (season or episode):
        return changed

    descs = programme.findall("desc")
    target = None
    # MBC prefers an Arabic description whenever one is present.
    for desc in descs:
        if (desc.get("lang") or "").lower().startswith("ar"):
            target = desc
            break
    if target is None and meta_lang:
        for desc in descs:
            if (desc.get("lang") or "").lower() == meta_lang:
                target = desc
                break
    if target is None and descs:
        target = descs[0]
    if target is None:
        lang = "ar" if (meta_lang or "").startswith("ar") else (meta_lang or "ar")
        target = base.ET.SubElement(programme, "desc", {"lang": lang})

    old_desc = (target.text or "").strip()
    if _desc_contains_numbered_meta(old_desc, season, episode):
        return changed

    is_ar = (target.get("lang") or "").lower().startswith("ar") or (meta_lang or "").startswith("ar")
    bits = []
    if is_ar:
        if season:
            bits.append("الموسم %s" % season)
        if episode:
            bits.append("الحلقة %s" % episode)
    else:
        if season:
            bits.append("Season %s" % season)
        if episode:
            bits.append("Episode %s" % episode)
    prefix = " • ".join(bits)
    target.text = prefix + (("\n" + old_desc) if old_desc else "")
    return changed


def install_mbc_title_policy():
    original_write_shard = base.write_shard

    def write_shard(out_dir, stem, ids, channels, programmes, label):
        if stem != "provider-mbc":
            return original_write_shard(out_dir, stem, ids, channels, programmes, label)
        normalized = dict(programmes)
        changed = 0
        for cid in set(ids):
            rows = []
            for programme in programmes.get(cid, []):
                cp = base.copy_element(programme)
                changed += normalize_mbc_programme(cp)
                rows.append(cp)
            normalized[cid] = rows
        print("MBC title policy: cleaned %d title(s); season/episode moved to description" % changed)
        return original_write_shard(out_dir, stem, ids, channels, normalized, label)

    base.write_shard = write_shard


def load_channel_metadata(path):
    by_id = {}
    by_name_sets = {}
    if not path:
        return by_id, {}
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        print("MENA shard metadata unavailable: %s" % exc)
        return by_id, {}
    valid = set(base.COUNTRY_BY_CODE) | {"MA"}
    for row in rows if isinstance(rows, list) else []:
        code = str(row.get("country") or "").upper()
        if code not in valid:
            continue
        rid = str(row.get("id") or "").strip()
        if rid:
            by_id[rid.casefold()] = code
        names = [row.get("name") or ""] + list(row.get("alt_names") or [])
        for value in names:
            key = base.norm(value)
            if key:
                by_name_sets.setdefault(key, set()).add(code)
    by_name = {key: next(iter(codes)) for key, codes in by_name_sets.items() if len(codes) == 1}
    print("MENA shard metadata: ids=%d unique_names=%d" % (len(by_id), len(by_name)))
    return by_id, by_name


def main():
    pop_arg("--source-cache-dir")
    metadata_path = pop_arg("--channel-metadata")
    by_id, by_name = load_channel_metadata(metadata_path)

    def safe_country_code(cid, name, meta):
        raw_id = str(cid or "").split("@", 1)[0].strip().casefold()
        canonical_id = str((meta or {}).get("xmltv_id") or "").split("@", 1)[0].strip().casefold()
        for key in (canonical_id, raw_id):
            if key and key in by_id:
                return by_id[key]

        for value in (name, (meta or {}).get("name") or ""):
            key = base.norm(value)
            if key and key in by_name:
                return by_name[key]

        p = identity_probe(cid, name, meta)
        if any(has_word(p, word) for word in MOROCCO_WORDS) or re.search(r"\.ma(?:@|$)", cid or "", re.I):
            return "MA"
        for code, words in COUNTRY_KEYWORDS:
            if any(has_word(p, word) for word in words):
                return code

        if canonical_id:
            m = re.search(r"\.([a-z]{2})$", canonical_id, re.I)
            if m and m.group(1).upper() in base.COUNTRY_BY_CODE:
                return m.group(1).upper()
        return None

    base.provider_group = safe_provider_group
    base.country_code = safe_country_code
    install_mbc_title_policy()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())