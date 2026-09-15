#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical receiver-facing XMLTV cleanup.

This post-publish cleanup removes proven legacy/compatibility aliases from the
receiver-facing mena-data branch, prunes zero-programme IDs, and conservatively
collapses exact duplicate identities when both display identity and programme
timeline match. It never invents EPG data and never rewrites programme content.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict

BEIN_ALIAS_TO_CANONICAL = {
    "4k_DIGITAL_Mono_AR.bein": "beIN4K.qa@SD",
    "beIN SPORTS2 DIGITAL.qa": "beINSports2.qa@MENA",
    "beIN_SPORTS3_DIGITAL_Mono_EN.bein": "beINSports3.qa@MENA",
    "beIN SPORTS4 DIGITAL.qa": "beINSports4.qa@MENA",
    "beIN_SPORTS4_DIGITAL_Mono_EN.bein": "beINSports4.qa@MENA",
    "beIN_SPORTS5_DIGITAL_Mono_EN.bein": "beINSports5.qa@MENA",
    "beIN SPORTS6 DIGITAL -d-1.qa": "beINSports6.qa@MENA",
    "beIN SPORTS7 DIGITAL.qa": "beINSports7.qa@MENA",
    "beIN_SPORTS7_DIGITAL_Mono_EN.bein": "beINSports7.qa@MENA",
    "NEWS_DIGITAL_Mono_AR.bein": "beIN.Sports.News.ae",
    "NEWS_DIGITAL_Mono_EN.bein": "beIN.Sports.News.ae",
    "beIN SPORTS XTRA 04 bein.com.qa": "beIN SPORTS XTRA 4.qa",
    "beIN SPORTS XTRA 05 bein.com.qa": "beIN SPORTS XTRA 5.qa",
    "beIN_SPORTS_XTRA_06_bein.com_EN.bein": "beIN SPORTS XTRA 6.qa",
    "beIN_SPORTS_XTRA_07_bein.com_EN.bein": "beIN SPORTS XTRA 7.qa",
    "beIN_SPORTS_XTRA_08_Bein.com_EN.bein": "beIN SPORTS XTRA 8.qa",
    "beIN_SPORTS_XTRA_Digital_09_Bein.com_EN.bein": "beIN SPORTS XTRA 9.qa",
    "beIN SPORTS XTRA 3.qa": "beINSPORTSXTRA3.qa",
    "logos-_beINSPORTSXTRA3_EN.bein": "beINSPORTSXTRA3.qa",
    "بي إن موفيز أكشن.eg": "BEIN MOVIES ACTION.eg",
    "بي إن موفيز دراما.eg": "BEIN MOVIES DRAMA.eg",
    "بي إن موفيز فاميلي.eg": "BEIN MOVIES FAMILY.eg",
    "بي إن موفيز بريمير.eg": "BEIN MOVIES PREMIERE.eg",
    "بي إن سيريس.eg": "BeIn Series HD 1.eg",
    "بي إن سيريس إتش دي 2.eg": "beIN Series HD 2.eg",
    "beINDrama1.qa@SD": "beIN Drama.eg",
    "beINMovies1Premiere.qa@SD": "BEIN MOVIES PREMIERE.eg",
    "beINMovies2Action.qa@SD": "BEIN MOVIES ACTION.eg",
    "beINMovies3Drama.qa@SD": "BEIN MOVIES DRAMA.eg",
    "beINMovies4Family.qa@SD": "BEIN MOVIES FAMILY.eg",
}

OSN_ALIAS_TO_CANONICAL = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}

MBC_CANONICAL = {
    "Alarabiya.ae@SD", "AlHadath.sa@SD", "MBC1.ae@SD", "MBC2.ae@SD",
    "MBC3.ae@SD", "MBC4.ae@SD", "MBC5.ae@SD", "MBCAction.ae@SD",
    "MBCBollywood.ae@SD", "MBCDrama.ae@SD", "MBCIraq.iq@SD",
    "MBCMasr.eg@SD", "MBCMasr2.eg@SD", "MBCMasrDrama.sa@SD",
    "MBCMax.ae@SD", "MBCPersia.ae@SD", "MBCPlusDrama.sa@SD",
}

ROTANA_CANONICAL = {
    "Rotana + HD.sa", "Rotana Aflam +.sa", "Rotana Kids.sa",
    "RotanaCinemaEgypt.eg@SD", "RotanaCinemaKSA.sa@SD",
    "RotanaClassic.sa@SD", "RotanaComedy.sa@SD", "RotanaDrama.sa@SD",
    "RotanaKhalijia.sa@SD", "Rotana M+ HD.sa", "Rotana Music HD.sa",
    "RotanaClip.sa@SD",
}

DMI_CANONICAL = {
    "Dubai One.sa", "Dubai Sports 2.sa", "Dubai Sports HD.sa", "Dubai Zaman.eg",
    "Dubai.Racing.1.HD.ae", "Dubai.Racing.2.ae", "DubaiTV.ae@SD",
    "Noor.DubaiTV.ae", "Sama Dubai.sa",
}

MBC_INTERNAL_ONLY = {
    "Al Arabiya.sa", "Al Arabiya Business.sa", "AlArabiyaBusiness.ae@SD",
    "AlarabiyaPortrait.ae@SD", "EN:.MBC1.Iraq.sa", "EN:.MBC1.Masr.sa",
    "MBC Egypt.eg", "MBC Maser 2.sa", "MBC Maser.sa", "MBC MASR 2.sa",
    "MBC Masr Drama.eg", "MBC.eg", "MBC1Egypt.eg@HD", "MBC1USA.us@SD",
    "MBC3USA.us@SD", "MBCDramaUSA.us@SD", "MBCMasrUSA.us@SD",
    "MBC Plus eLife HD.sa", "MBC Plus Variety HD.sa", "MBC VARIETY.sa",
    "MBCMood.sa@HD", "Wanasah.sa",
}

ROTANA_INTERNAL_ONLY = {
    "Rotana Cinema + US.sa", "Rotana Cinema HD.sa", "Rotana Cinema Masr.sa",
    "Rotana Classic.eg", "Rotana Clip.sa", "Rotana Comedy.eg", "Rotana Drama.eg",
    "Rotana Khalejia.eg", "Rotana Khalijia HD.sa", "Rotana.Cinema.Egypt.ae",
    "Rotana.Cinema.KSA.ae",
}

ADM_INTERNAL_ONLY = {
    "Abu Dhabi.sa", "AbuDhabiSports1.ae@SD", "AbuDhabiTV.ae@SD",
    "AD Sports 2.sa", "AD Sports Premium 1.sa", "AD Sports Premium 2.sa",
    "Al Emarat.sa", "en:.AD.Sports.Extra.ae", "en:.YAS.TV.Extra.ae",
    "Majid.sa", "Nat.Geo.Abu.Dhabi.HD.ae", "Yas.TV.HD.ae", "Emarat.HD.ae",
}

BEIN_NON_RECOMMENDED = {
    "beIN SPORTS66 DIGITAL -01.qa", "beIN_SPORTS66_DIGITAL_Mono-01_EN.bein",
    "bein.com-05.qa", "bein.com-06.qa", "bein.com-07.qa", "bein.com-08.qa",
}

DROP_EXPLICIT = (
    set(BEIN_ALIAS_TO_CANONICAL) | set(OSN_ALIAS_TO_CANONICAL) |
    MBC_INTERNAL_ONLY | ROTANA_INTERNAL_ONLY | ADM_INTERNAL_ONLY |
    BEIN_NON_RECOMMENDED | {"AlSharqiya.eg", "الشرقية.eg"}
)

PROVIDER_ALLOWED = {
    "provider-mbc": MBC_CANONICAL,
    "provider-rotana": ROTANA_CANONICAL,
    "provider-dmi": DMI_CANONICAL,
}

CANONICAL_TARGETS = set(BEIN_ALIAS_TO_CANONICAL.values()) | set(OSN_ALIAS_TO_CANONICAL.values()) | MBC_CANONICAL | ROTANA_CANONICAL | DMI_CANONICAL


def read_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def display_name(channel: ET.Element) -> str:
    for node in channel.findall("display-name"):
        if (node.text or "").strip():
            return (node.text or "").strip()
    return (channel.get("id") or "").strip()


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\b(?:uhd|fhd|hd|sd|digital|mono)\b", " ", value)
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def event_title(p: ET.Element) -> str:
    for n in p.findall("title"):
        if (n.text or "").strip():
            return (n.text or "").strip()
    return ""


def timeline_fingerprint(rows):
    if not rows:
        return None
    return tuple(
        ((p.get("start") or "").strip(), (p.get("stop") or "").strip(), event_title(p))
        for p in rows
    )


def canonical_score(cid: str) -> tuple:
    score = 0
    low = cid.casefold()
    if cid in CANONICAL_TARGETS:
        score += 1000
    if "@mena" in low:
        score += 80
    if "@sd" in low:
        score += 30
    if ".ae" in low or ".qa" in low or ".sa" in low or ".eg" in low:
        score += 10
    if any(x in low for x in ("digital_mono", "logos-", "usa", "legacy")):
        score -= 100
    return (score, -len(cid), cid.casefold())


def cleanup_xml(path: Path):
    root = read_root(path)
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

    before_channels = len(channels)
    before_programmes = sum(len(v) for v in programmes.values())
    drop = {cid for cid in channels if cid in DROP_EXPLICIT}

    stem = path.name[:-7] if path.name.endswith(".xml.gz") else path.stem
    allowed = PROVIDER_ALLOWED.get(stem)
    if allowed is not None:
        drop.update(cid for cid in channels if cid not in allowed)

    # Conservative generic dedupe: only collapse IDs when both normalized display
    # identity and the complete start/stop/title timeline are exactly the same.
    groups = defaultdict(list)
    for cid, channel in channels.items():
        if cid in drop or not programmes.get(cid):
            continue
        fp = timeline_fingerprint(programmes[cid])
        if fp:
            groups[(norm(display_name(channel)), fp)].append(cid)
    duplicate_groups = []
    for members in groups.values():
        if len(members) < 2:
            continue
        winner = max(members, key=canonical_score)
        losers = sorted([x for x in members if x != winner], key=str.casefold)
        drop.update(losers)
        duplicate_groups.append({"canonical": winner, "removed": losers})

    # Zero-programme IDs never belong in receiver XML.
    drop.update(cid for cid in channels if not programmes.get(cid))
    keep = sorted(set(channels) - drop, key=str.casefold)

    out = ET.Element("tv", dict(root.attrib))
    for cid in keep:
        out.append(channels[cid])
    seen = set()
    for cid in keep:
        for p in programmes.get(cid, []):
            key = ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append(p)
    ET.indent(out, space="  ")
    xml = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    gz = gzip.compress(xml, compresslevel=9, mtime=0)
    path.write_bytes(gz)

    txt_path = path.with_suffix("").with_suffix(".txt")
    if txt_path.exists():
        txt_path.write_text("".join("%s|%s\n" % (cid, display_name(channels[cid])) for cid in keep), encoding="utf-8")

    after_programmes = len(seen)
    return {
        "file": path.name,
        "channels_before": before_channels,
        "channels_after": len(keep),
        "programmes_before": before_programmes,
        "programmes_after": after_programmes,
        "removed_count": len(drop),
        "removed_ids": sorted(drop, key=str.casefold),
        "duplicate_groups": duplicate_groups,
        "size_bytes": len(gz),
        "sha256": hashlib.sha256(gz).hexdigest(),
        "ids": keep,
    }


def update_manifests(base: Path, reports):
    by_stem = {r["file"][:-7]: r for r in reports if r["file"].endswith(".xml.gz")}
    shards_path = base / "shards.json"
    if shards_path.exists():
        data = json.loads(shards_path.read_text(encoding="utf-8"))
        for stem, row in (data.get("shards") or {}).items():
            rep = by_stem.get(stem)
            if not rep:
                continue
            row["channels"] = rep["channels_after"]
            row["programmes"] = rep["programmes_after"]
            row["size_bytes"] = rep["size_bytes"]
            row["sha256"] = rep["sha256"]
            row.pop("compat_aliases", None)
            row["catalog_preserves_legacy_ids"] = False
            row["receiver_policy"] = "canonical-only; legacy/compat aliases removed"
        shard_rows = data.get("shards") or {}
        data["published_channels"] = sum(int(x.get("channels", 0)) for x in shard_rows.values())
        data["other_channels"] = int((shard_rows.get("mena-other") or {}).get("channels", 0))
        data["receiver_policy"] = "canonical-only; no receiver-facing legacy aliases"
        data["canonical_reset"] = True
        shards_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = base / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        combined = by_stem.get("mena-arabic") or by_stem.get("mena")
        if combined:
            data["channels"] = combined["channels_after"]
            data["programmes"] = combined["programmes_after"]
            data["size_bytes"] = combined["size_bytes"]
            data["sha256"] = combined["sha256"]
        data["canonical_receiver_ids"] = True
        data["legacy_aliases_published"] = False
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prune_catalog(base: Path, published_ids):
    path = base / "catalog.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    rows = data.get("channels") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return 0
    before = len(rows)
    data["channels"] = [r for r in rows if str(r.get("xmltv_id") or "") in published_ids]
    data["canonical_receiver_ids"] = True
    data["legacy_aliases_published"] = False
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return before - len(data["channels"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    base = Path(args.dir)
    reports = []
    for path in sorted(base.glob("*.xml.gz")):
        reports.append(cleanup_xml(path))

    # Use exclusive receiver shards for active catalogue membership when present.
    exclusive = [r for r in reports if r["file"].startswith("provider-") or r["file"].startswith("mena-") and r["file"] not in {"mena-arabic.xml.gz"}]
    published_ids = {cid for r in (exclusive or reports) for cid in r["ids"]}
    catalog_pruned = prune_catalog(base, published_ids)
    update_manifests(base, reports)

    summary = {
        "schema": 1,
        "policy": "canonical-only receiver IDs; explicit legacy aliases removed; exact same-identity+timeline duplicates collapsed",
        "files": [{k: v for k, v in r.items() if k != "ids"} for r in reports],
        "channels_removed_total": sum(r["removed_count"] for r in reports),
        "catalog_rows_pruned": catalog_pruned,
    }
    (base / "canonical-id-reset.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (base / "canonical-id-reset.txt").write_text(
        "CANONICAL ID RESET\nfiles=%d removed=%d catalog_pruned=%d\n" % (
            len(reports), summary["channels_removed_total"], catalog_pruned
        ) + "\n".join(
            "%s: %d -> %d channels; removed=%d" % (
                r["file"], r["channels_before"], r["channels_after"], r["removed_count"]
            ) for r in reports
        ) + "\n",
        encoding="utf-8",
    )
    print("Canonical cleanup: files=%d removed=%d catalog_pruned=%d" % (
        len(reports), summary["channels_removed_total"], catalog_pruned
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
