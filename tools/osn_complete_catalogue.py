#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a complete OSN catalogue from the official Android + Other guides.

The current OSN web TV guide exposes different channel families depending on
the selected box/platform. Android remains the main catalogue; Other carries
additional services (notably Alfa and Pinoy channels). This script normalizes
both live catalogues to stable EPGManager XMLTV IDs, writes per-platform
catalogues for grabbing, then publishes their union as osn.channels.xml.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import xml.etree.ElementTree as ET

ROOT = Path("vendor/iptv-org-epg/sites/osn.com")
OUT = Path("output/source-build")
REPORTS = Path("reports")
OUT.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)

OFFICIAL_ID_OVERRIDES = {
    "204": "OSNOne.ae@SD",
    "208": "OSNShowcaseClassics.ae@SD",
    "221": "OSNIQIYI.ae@SD",
    "225": "OSNMoviesHorror.ae@SD",
    "226": "OSNPopUp2.ae@SD",
    "307": "OSNCrime.ae@SD",
    "314": "OSNDocumentary.ae@SD",
    "4502": "OSNNow.ae@SD",
    "5666": "OSNMoviesFamily.ae@SD",
    "5669": "OSNMoviesComedy.ae@SD",
    "5672": "OSNPopUp.ae@SD",
    "206": "OSNBlippiAndFriends",
    "1101": "OSNKTVChannel1HD",
    "6607": "OSNeClutchAccess",
    "6609": "OSNEsport24",
    "6610": "OSNeClutchLIVE",
    "6611": "OSNPadelTV",
    "6612": "OSNeClutchLIVE2",
    "6613": "OSNeClutchArabic",
    "9957": "OSNSTV1HD",
}

NAME_ID_OVERRIDES = {
    # Alfa / "Other" package
    "alfaseries": "AlfaSeries.ae@SD",
    "alfaserieshd": "AlfaSeries.ae@SD",
    "alfaserieschannel": "AlfaSeries.ae@SD",
    "alfaseries2": "AlfaSeriesPlus2.ae@SD",
    "alfaseries2hd": "AlfaSeriesPlus2.ae@SD",
    "alfaseriesplus2": "AlfaSeriesPlus2.ae@SD",
    "alfaseriesplus2hd": "AlfaSeriesPlus2.ae@SD",
    "alfaalyawm": "AlYawm.ae@SD",
    "alfaalyawmhd": "AlYawm.ae@SD",
    "alyawm": "AlYawm.ae@SD",
    "alfaalsafwa": "AlSafwa.ae@SD",
    "alsafwa": "AlSafwa.ae@SD",
    "alfafann": "Fann.ae@SD",
    "fann": "Fann.ae@SD",
    "alfacinema1": "Cinema1.ae@SD",
    "cinema1": "Cinema1.ae@SD",
    "alfacinema2": "Cinema2.ae@SD",
    "cinema2": "Cinema2.ae@SD",
    "alfamusic": "MusicNow.ae@SD",
    "alfamusichd": "MusicNow.ae@SD",
    "musicnow": "MusicNow.ae@SD",
    # Pinoy / "Other" package
    "cinemaone": "CinemaOne.ph",
    "anc": "ANC.ph",
    "myx": "MyxMiddleEast.ph",
    "myxmiddleeast": "MyxMiddleEast.ph",
    "kapatidtv5": "KapatidTV5.ph",
    "tv5kapatid": "KapatidTV5.ph",
    "cinemo": "CineMo.ph",
    "thefilipinochannel": "TheFilipinoChannelMiddleEast.us",
    "tfc": "TheFilipinoChannelMiddleEast.us",
    "gmalifetv": "GMALifeTV.ph",
    "gmanewstv": "GMANewsTV.ph",
    "gmapinoytv": "GMAPinoyTVMiddleEast.ph",
}

FALLBACK_ALFA = {
    "AlfaSeries.ae@SD": ("web-alfa-series", "Alfa Series HD"),
    "AlfaSeriesPlus2.ae@SD": ("web-alfa-series-plus2", "Alfa Series +2 HD"),
    "AlYawm.ae@SD": ("web-al-yawm", "Alfa Al Yawm HD"),
    "AlSafwa.ae@SD": ("web-al-safwa", "Alfa Al Safwa"),
    "Fann.ae@SD": ("web-alfa-fann", "Alfa Fann"),
    "Cinema1.ae@SD": ("web-alfa-cinema1", "Alfa Cinema 1"),
    "Cinema2.ae@SD": ("web-alfa-cinema2", "Alfa Cinema 2"),
    "MusicNow.ae@SD": ("web-music-now", "Alfa Music HD"),
}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def clone(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def read_root(path: Path) -> ET.Element | None:
    if not path.exists():
        return None
    try:
        return ET.parse(path).getroot()
    except Exception:
        return None


def static_maps() -> tuple[dict[str, str], dict[str, str]]:
    by_sid = dict(OFFICIAL_ID_OVERRIDES)
    by_name = dict(NAME_ID_OVERRIDES)
    for lang in ("ar", "en"):
        root = read_root(ROOT / f"osn.com_{lang}.channels.xml")
        if root is None:
            continue
        for ch in root.findall("channel"):
            sid = (ch.get("site_id") or "").strip()
            cid = (ch.get("xmltv_id") or "").strip()
            name = (ch.text or "").strip()
            if sid and cid:
                by_sid.setdefault(sid, cid)
            if name and cid:
                by_name.setdefault(norm(name), cid)
    return by_sid, by_name


CANONICAL_BY_SID, CANONICAL_BY_NAME = static_maps()


def canonicalize(ch: ET.Element, lang: str) -> ET.Element | None:
    sid = (ch.get("site_id") or "").strip()
    name = (ch.text or "").strip()
    if not sid:
        return None
    node = clone(ch)
    cid = (node.get("xmltv_id") or "").strip()
    if not cid:
        cid = (
            CANONICAL_BY_SID.get(sid)
            or CANONICAL_BY_NAME.get(norm(name))
            or OFFICIAL_ID_OVERRIDES.get(sid)
            or NAME_ID_OVERRIDES.get(norm(name))
        )
    if not cid:
        safe_sid = re.sub(r"[^A-Za-z0-9._-]+", "", sid) or "unknown"
        cid = f"osn.{safe_sid}"
    node.set("site", "osn.com")
    node.set("lang", lang)
    node.set("xmltv_id", cid)
    return node


def write_channels(path: Path, nodes: list[ET.Element]) -> None:
    root = ET.Element("channels")
    for node in nodes:
        root.append(clone(node))
    ET.indent(root, space="  ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def normalize_live(lang: str, platform: str) -> tuple[list[ET.Element], dict]:
    src = OUT / f"osn_live_{platform}_{lang}.channels.xml"
    root = read_root(src)
    nodes: list[ET.Element] = []
    names: list[str] = []
    if root is not None:
        seen: set[str] = set()
        for ch in root.findall("channel"):
            node = canonicalize(ch, lang)
            if node is None:
                continue
            cid = (node.get("xmltv_id") or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            nodes.append(node)
            names.append((node.text or "").strip())
    suffix = "" if lang == "ar" else "_en"
    write_channels(OUT / f"osn_{platform}{suffix}.channels.xml", nodes)
    return nodes, {
        "input": str(src),
        "channels": len(nodes),
        "names": names,
    }


def static_nodes(lang: str) -> list[ET.Element]:
    root = read_root(ROOT / f"osn.com_{lang}.channels.xml")
    out: list[ET.Element] = []
    if root is None:
        return out
    for ch in root.findall("channel"):
        node = canonicalize(ch, lang)
        if node is not None:
            out.append(node)
    return out


def build(lang: str) -> dict:
    android, android_meta = normalize_live(lang, "android")
    other, other_meta = normalize_live(lang, "other")

    by_cid: dict[str, ET.Element] = {}
    source_for: dict[str, str] = {}

    # The live Android + Legacy/Other catalogues define the current OSN
    # lineup.  The checked-in static file is still used above as a canonical
    # site_id -> XMLTV-ID mapping, but static-only services must not be
    # published as current channels (they otherwise create stale 0-EPG IDs).
    for source, nodes in (
        ("android", android),
        ("other", other),
    ):
        for node in nodes:
            cid = (node.get("xmltv_id") or "").strip()
            if not cid:
                continue
            # Live catalogues override static display metadata. Other is applied
            # last so its real provider GUID replaces any synthetic Alfa entry.
            by_cid[cid] = clone(node)
            source_for[cid] = source

    # Preserve known Alfa IDs in the mapping index even if OSN temporarily
    # stops exposing the Other platform. They remain visibly 0-EPG rather than
    # disappearing from EPGManager.
    for cid, (sid, name) in FALLBACK_ALFA.items():
        if cid in by_cid:
            continue
        node = ET.Element(
            "channel",
            {
                "site": "osn.com",
                "site_id": sid,
                "lang": lang,
                "xmltv_id": cid,
            },
        )
        node.text = name
        by_cid[cid] = node
        source_for[cid] = "fallback-index"

    nodes = [by_cid[cid] for cid in sorted(by_cid, key=str.casefold)]
    out_name = "osn.channels.xml" if lang == "ar" else "osn_en.channels.xml"
    write_channels(OUT / out_name, nodes)

    return {
        "lang": lang,
        "total_union": len(nodes),
        "static": len(static_nodes(lang)),
        "android": android_meta,
        "other": other_meta,
        "other_canonical_ids": sorted(
            [
                (n.get("xmltv_id") or "").strip()
                for n in other
                if (n.get("xmltv_id") or "").strip()
            ],
            key=str.casefold,
        ),
        "ownership": {
            key: sum(1 for v in source_for.values() if v == key)
            for key in ("android", "other", "fallback-index")
        },
    }


def main() -> int:
    ar = build("ar")
    en = build("en")
    selected_platform = ""
    p = OUT / "osn_other_platform.txt"
    if p.exists():
        selected_platform = p.read_text(encoding="utf-8").strip()

    report = {
        "mode": "official-osn-android-plus-other",
        "guide": "https://www.osn.com/ar-sa/watch/tv-schedule",
        "other_platform_value": selected_platform,
        "arabic": ar,
        "english": en,
    }
    (REPORTS / "osn-catalogue.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "OSN COMPLETE "
        f"ar_union={ar['total_union']} ar_android={ar['android']['channels']} "
        f"ar_other={ar['other']['channels']} "
        f"en_union={en['total_union']} en_android={en['android']['channels']} "
        f"en_other={en['other']['channels']} other_platform={selected_platform or 'none'}"
    )
    if ar["android"]["channels"] == 0:
        raise SystemExit("OSN Android live catalogue is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
