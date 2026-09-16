#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split the legacy combined MENA Cloud XMLTV into exclusive country/provider shards.

One channel is published in exactly one receiver shard: premium/provider group first,
then a conservatively detected home country. Unclassified MENA identities remain
internal-only for audit/recovery and are not exposed as an auto-mapping source.
Morocco is excluded because EPGManager publishes a dedicated Morocco Cloud feed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

COUNTRY_SHARDS = [
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
PROVIDER_SHARDS = [
    ("alkass", "Al Kass", "provider-alkass"),
    ("bein", "beIN", "provider-bein"),
    ("osn", "OSN", "provider-osn"),
    ("mbc", "MBC Group", "provider-mbc"),
    ("rotana", "Rotana", "provider-rotana"),
    ("adm", "Abu Dhabi Media", "provider-adm"),
    ("dmi", "Dubai Media", "provider-dmi"),
    ("art", "ART / Alfa", "provider-art"),
    ("ssc", "SSC", "provider-ssc"),
    ("starz", "STARZPLAY", "provider-starz"),
    ("international", "Premium International", "provider-international"),
]
COUNTRY_BY_CODE = {code: (label, stem) for code, label, stem in COUNTRY_SHARDS}
ALL_STEMS = [x[2] for x in PROVIDER_SHARDS] + [x[2] for x in COUNTRY_SHARDS] + ["mena-other"]

PREMIUM_INTERNATIONAL_IDS = {
    "AnimalPlanetEurope.uk@SD",
    "DiscoveryChannelMiddleEastAfrica.us@SD",
    "InvestigationDiscovery.uk@SD",
    "HistoryMiddleEast.us@SD",
    "History2MiddleEast.us@SD",
    "TLCArabia.us@SD",
    "CartoonNetworkMENA.uk@SD",
    "NickelodeonArabia.ae@SD",
    "NickJrArabia.ae@SD",
    "NicktoonsArabia.ae@SD",
    "CartoonNetworkArabic.ae@SD",
}

# Receiver-facing Rotana identities frozen after the provider audit. Alternate
# HD/generic aliases remain in the internal combined feed, but must not create
# duplicate Safe EPG Match candidates on the receiver.
ROTANA_RECEIVER_ALLOWED_IDS = {
    "Rotana + HD.sa",
    "Rotana Aflam +.sa",
    "Rotana Kids.sa",
    "RotanaCinemaEgypt.eg@SD",
    "RotanaCinemaKSA.sa@SD",
    "RotanaClassic.sa@SD",
    "RotanaComedy.sa@SD",
    "RotanaDrama.sa@SD",
    "RotanaKhalijia.sa@SD",
    "Rotana M+ HD.sa",
    "Rotana Music HD.sa",
    "RotanaClip.sa@SD",
}


def read_xml(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def display_name(channel: ET.Element) -> str:
    for n in channel.findall("display-name"):
        if (n.text or "").strip():
            return (n.text or "").strip()
    return channel.get("id") or ""


def probe(cid: str, name: str, meta: dict) -> str:
    return norm("%s %s %s %s" % (cid, name, meta.get("name") or "", meta.get("site") or ""))


def has_word(text: str, *words: str) -> bool:
    padded = " " + text + " "
    return any((" " + norm(word) + " ") in padded for word in words)


def provider_group(cid: str, name: str, meta: dict):
    if cid in PREMIUM_INTERNATIONAL_IDS:
        return "international"
    p = probe(cid, name, meta)
    site = str(meta.get("site") or "").casefold()
    # Order matters: Al Kass IDs can originate from a beIN catalogue.
    if has_word(p, "alkass", "al kass"):
        return "alkass"
    if has_word(p, "bein", "be in", "bein sports", "beinsports") or (cid or "").casefold().endswith(".bein"):
        return "bein"
    if has_word(p, "osn", "osntv", "osn tv") or site == "osn.com":
        return "osn"
    if site == "shahid.mbc.net" or re.search(r"(?:^| )mbc(?: |[0-9]|$)", p) or has_word(p, "al arabiya", "alarabiya", "al hadath", "alhadath"):
        return "mbc"
    if site == "rotana.net" or has_word(p, "rotana"):
        return "rotana"
    if has_word(p, "abu dhabi", "abudhabi", "ad sports", "ad sport", "yas sports", "yas", "majid", "national geographic abu dhabi", "al emarat"):
        return "adm"
    if site == "dmi.gov.ae" or has_word(p, "dubai tv", "dubai sports", "dubai one", "dubai racing", "sama dubai"):
        return "dmi"
    if site == "artonline.tv" or has_word(p, "art aflam", "art cinema", "art hekayat", "art movies", "art sport", "alfa cinema", "alfa drama", "alfa hekayat", "alfa series", "alfa music", "alfa fann", "alfa al safwa", "alfa al yawm") or re.search(r"(?:^| )art(?: |[0-9]|$)", p):
        return "art"
    if re.search(r"(?:^| )ssc(?: |[0-9]|$)", p) or has_word(p, "saudi sports company"):
        return "ssc"
    if has_word(p, "starzplay", "starz play", "starz"):
        return "starz"
    return None


COUNTRY_KEYWORDS = [
    ("MR", ("mauritania", "mauritanie", "almouritania")),
    ("SD", ("sudan", "sudania", "blue nile")),
    ("LY", ("libya", "libyan", "jamahiriya")),
    ("TN", ("tunisia", "tunisie", "wataniya", "ettounsi", "nessma", "attessia", "hannibal")),
    ("DZ", ("algeria", "algerie", "entv", "echorouk", "ennahar", "el bilad", "dzair", "samira tv")),
    ("YE", ("yemen", "yemeni", "aden", "belqees", "al masirah", "almasirah", "suhail", "saeedah", "alsaeedah")),
    ("PS", ("palestine", "palestinian", "al quds", "quds", "wattan", "maan tv")),
    ("IQ", ("iraq", "iraqi", "iraqiya", "al sharqiya", "sharqiya", "alsumaria", "al sumaria", "al rasheed", "alrasheed", "al forat", "al anbar", "afaq tv", "al kafeel", "al najaf", "alahad", "alghadeer")),
    ("LB", ("lebanon", "lebanese", "lbc", "lbci", "mtv lebanon", "al jadeed", "aljadeed", "tele liban", "nbn", "otv lebanon", "al mayadeen", "mayadeen", "aghani aghani")),
    ("JO", ("jordan", "jordanian", "roya tv", "royaa", "al mamlaka", "mamlaka")),
    ("OM", ("oman", "omani", "majan")),
    ("BH", ("bahrain", "bahraini")),
    ("KW", ("kuwait", "kuwaiti", "ktv", "al rai tv", "alrai")),
    ("QA", ("qatar", "qatari", "al rayyan", "rayyan", "al jazeera", "aljazeera")),
    ("AE", ("uae", "united arab emirates", "emirates", "sharjah", "ajman", "fujairah", "al dhafra", "aldafrah")),
    ("SA", ("saudi", "saudiya", "ksa", "al ekhbaria", "alekhbaria", "quran al kareem", "sunnah nabawiyah", "al resalah")),
    ("SY", ("syria", "syrian", "souriya", "al souriya", "sama tv", "lana tv")),
    ("EG", ("egypt", "egyptian", "masr", "masriya", "al masriyah", "cairo", "qahera", "kahera", "nile", "cbc", "dmc", "sada el balad", "mehwar", "al nahar", "alnahar", "al hayat", "alhayat")),
]
MOROCCO_WORDS = ("morocco", "maroc", "maghribiya", "al aoula", "alaoula", "arryadia", "arrabiaa", "assadissa", "tamazight", "2m monde", "2m national", "medi1", "medi 1")


def country_code(cid: str, name: str, meta: dict):
    p = probe(cid, name, meta)
    if any(has_word(p, word) for word in MOROCCO_WORDS) or re.search(r"\.ma(?:@|$)", cid or "", re.I):
        return "MA"
    for code, words in COUNTRY_KEYWORDS:
        if any(has_word(p, word) for word in words):
            return code
    meta_id = str(meta.get("xmltv_id") or "")
    m = re.search(r"\.([a-z]{2})(?:@|$)", meta_id, re.I)
    if m and m.group(1).upper() in COUNTRY_BY_CODE:
        return m.group(1).upper()
    m = re.search(r"\.([a-z]{2})(?:@|$)", cid or "", re.I)
    if m and m.group(1).upper() in COUNTRY_BY_CODE:
        return m.group(1).upper()
    return None


def copy_element(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def event_key(node: ET.Element):
    return ((node.get("channel") or "").strip(), (node.get("start") or "").strip(), (node.get("stop") or "").strip())


def write_shard(out_dir: Path, stem: str, ids, channels, programmes, label: str):
    root = ET.Element("tv", {
        "generator-info-name": "EPGManager MENA Cloud - %s" % label,
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    txt = []
    ids = sorted(set(ids), key=str.casefold)
    for cid in ids:
        c = channels[cid]
        root.append(copy_element(c))
        txt.append("%s|%s" % (cid, display_name(c)))
    seen = set()
    event_count = 0
    for cid in ids:
        for p in programmes.get(cid, []):
            cp = copy_element(p)
            key = event_key(cp)
            if not key[1] or key in seen:
                continue
            seen.add(key)
            root.append(cp)
            event_count += 1
    ET.indent(root, space="  ")
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    gz_bytes = gzip.compress(xml_bytes, compresslevel=9, mtime=0)
    (out_dir / (stem + ".xml.gz")).write_bytes(gz_bytes)
    (out_dir / (stem + ".txt")).write_text("\n".join(txt) + ("\n" if txt else ""), encoding="utf-8")
    return {
        "label": label, "channels": len(ids), "programmes": event_count,
        "size_bytes": len(gz_bytes), "sha256": hashlib.sha256(gz_bytes).hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    root = read_xml(Path(args.input))
    catalog = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    source_by_id = {x.get("xmltv_id"): x for x in catalog.get("channels", []) if x.get("xmltv_id")}
    channels = {}
    programmes = defaultdict(list)
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            channels[cid] = c
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid in channels:
            programmes[cid].append(p)

    provider_stem = {key: stem for key, _label, stem in PROVIDER_SHARDS}
    country_stem = {code: stem for code, _label, stem in COUNTRY_SHARDS}
    buckets = {stem: set() for stem in ALL_STEMS}
    excluded_morocco = set()

    for cid, c in channels.items():
        name = display_name(c)
        meta = dict(source_by_id.get(cid) or {})
        meta.setdefault("xmltv_id", cid if cid in source_by_id else "")
        group = provider_group(cid, name, meta)
        if group:
            buckets[provider_stem[group]].add(cid)
            continue
        code = country_code(cid, name, meta)
        if code == "MA":
            excluded_morocco.add(cid)
            continue
        stem = country_stem.get(code) if code else None
        if stem:
            buckets[stem].add(cid)
        else:
            buckets["mena-other"].add(cid)

    seen = set()
    for stem in ALL_STEMS:
        overlap = seen & buckets[stem]
        if overlap:
            raise SystemExit("Shard overlap in %s: %s" % (stem, sorted(overlap)[:5]))
        seen.update(buckets[stem])
    if seen & excluded_morocco:
        raise SystemExit("Morocco exclusion overlaps published shards")
    if (seen | excluded_morocco) != set(channels):
        missing = set(channels) - (seen | excluded_morocco)
        raise SystemExit("Shard coverage mismatch: %d missing" % len(missing))

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for key, label, stem in PROVIDER_SHARDS:
        publish_ids = set(buckets[stem])
        internal_only_ids = []
        if key == "rotana":
            internal_only_ids = sorted(publish_ids - ROTANA_RECEIVER_ALLOWED_IDS, key=str.casefold)
            publish_ids &= ROTANA_RECEIVER_ALLOWED_IDS
        stats[stem] = write_shard(out_dir, stem, publish_ids, channels, programmes, label)
        stats[stem].update({"kind": "provider", "key": key})
        if internal_only_ids:
            stats[stem]["internal_only_ids"] = internal_only_ids
            stats[stem]["internal_only_count"] = len(internal_only_ids)
            stats[stem]["receiver_policy"] = "canonical-only; duplicate/legacy Rotana identities stay internal"
            print("Rotana receiver cleanup: canonical=%d internal-only=%d" % (
                len(publish_ids), len(internal_only_ids)))
    for code, label, stem in COUNTRY_SHARDS:
        stats[stem] = write_shard(out_dir, stem, buckets[stem], channels, programmes, label)
        stats[stem].update({"kind": "country", "key": code})
    unclassified_internal_ids = sorted(buckets["mena-other"], key=str.casefold)

    provider_counts = {key: stats[stem]["channels"] for key, _label, stem in PROVIDER_SHARDS}
    country_counts = {code: stats[stem]["channels"] for code, _label, stem in COUNTRY_SHARDS}

    # write_shard can apply a strict receiver-facing canonical filter (for
    # example DMI/MBC in the strict layer and Rotana here) after the initial
    # exclusive buckets were built. The manifest must describe the XML files
    # actually written, not the pre-filter bucket size, otherwise a valid feed
    # can be rejected before publication.
    actual_published = sum(int(row.get("channels", 0)) for row in stats.values())
    receiver_internal_only_ids = sorted(
        set(unclassified_internal_ids) | {
            cid
            for row in stats.values()
            for cid in (row.get("internal_only_ids") or [])
        },
        key=str.casefold,
    )
    receiver_internal_only = max(len(receiver_internal_only_ids), len(seen) - actual_published)
    total_receiver_excluded = len(excluded_morocco) + receiver_internal_only

    manifest = {
        "schema": 2,
        "policy": "exclusive provider-first, then country; unclassified MENA identities are internal-only; Morocco excluded to dedicated Morocco Cloud; provider internal-only identities excluded from receiver shards",
        "input_channels": len(channels),
        "published_channels": actual_published,
        # Legacy invariant field: total channels intentionally not emitted into
        # receiver shards. Explicit fields below separate Morocco from provider
        # internal-only identities for diagnostics.
        "excluded_morocco_channels": total_receiver_excluded,
        "morocco_channels": len(excluded_morocco),
        "receiver_internal_only_channels": receiver_internal_only,
        "receiver_internal_only_ids": receiver_internal_only_ids,
        "other_channels": len(unclassified_internal_ids),
        "other_receiver_published": False,
        "unclassified_internal_channels": len(unclassified_internal_ids),
        "unclassified_internal_ids": unclassified_internal_ids,
        "provider_counts": provider_counts,
        "country_counts": country_counts,
        "shards": stats,
    }
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("MENA shards: input=%d published=%d Morocco=%d InternalOnly=%d Other=%d providers=%s" % (
        len(channels), actual_published, len(excluded_morocco), receiver_internal_only,
        len(unclassified_internal_ids),
        ",".join("%s:%d" % (k, v) for k, v in provider_counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())