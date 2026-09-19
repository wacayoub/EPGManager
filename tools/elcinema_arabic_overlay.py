#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and apply a throttled ElCinema Arabic donor without stealing official clocks.

Modes:
  build-channels: intersect upstream ElCinema Arabic channels with the active MENA catalogue.
  overlay: for ElCinema-primary channels replace/extend their raw timetable from the 2-day donor;
           for official-primary channels keep start/stop untouched and only enrich exact matching
           slots with Arabic title/description. Official-first protected services never have titles
           replaced; they may only receive a missing/non-Arabic description.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")

# Verified OSN/ElCinema duplicate audit (2026-09-19). These identities keep
# the winning non-ElCinema timeline and are removed entirely from the ElCinema
# donor to avoid duplicate/competing programme grids.
AUDITED_ELCINEMA_LOSER_IDS = {
    "AbuDhabiTV.ae@SD",
    "AlArabyTV2.qa@SD",
    "BahrainTV.bh@SD",
    "CartoonNetworkArabic.ae@SD",
    "DiscoveryChannelMiddleEastAfrica.us@SD",
    "DMC.eg@SD",
    "DubaiTV.ae@SD",
    "MBC1.ae@SD",
    "MBC3.ae@SD",
    "MBC5.ae@SD",
    "MBCDrama.ae@SD",
    "MBCIraq.iq@SD",
    "MBCMasr.eg@SD",
    "MBCMasr2.eg@SD",
    "MBCPlusDrama.sa@SD",
    "OmanTV.om@SD",
    "RoyaTV.jo@SD",
}


def has_ar(text: str) -> bool:
    return len(AR.findall(text or "")) >= 2


def read_root(path: str) -> ET.Element:
    return ET.parse(path).getroot()


def first_text(node: ET.Element, tag: str):
    for el in node.findall(tag):
        text = (el.text or "").strip()
        if text:
            return el, text
    return None, ""


def slot(node: ET.Element):
    return ((node.get("channel") or "").strip(), (node.get("start") or "").strip(), (node.get("stop") or "").strip())


def build_channels(args) -> int:
    manifest = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    active = {row.get("xmltv_id", "") for row in manifest.get("channels", []) if row.get("xmltv_id")}
    src = read_root(args.upstream_channels)
    out = ET.Element("channels")
    kept = 0
    for ch in src.findall("channel"):
        cid = (ch.get("xmltv_id") or "").strip()
        if cid and cid in active and cid not in AUDITED_ELCINEMA_LOSER_IDS:
            out.append(copy.deepcopy(ch))
            kept += 1
    ET.indent(out, space="  ")
    Path(args.output).write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))
    print(
        f"ELCINEMA_DONOR_CHANNELS kept={kept} active={len(active)} "
        f"duplicate_losers_excluded={len(AUDITED_ELCINEMA_LOSER_IDS & active)}"
    )
    return 0


def overlay(args) -> int:
    manifest = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    rows = {row.get("xmltv_id", ""): row for row in manifest.get("channels", [])}
    primary = read_root(args.primary)
    donor = read_root(args.donor)

    donor_by_slot = {slot(p): p for p in donor.findall("programme")}
    donor_by_channel = defaultdict(list)
    for p in donor.findall("programme"):
        donor_by_channel[(p.get("channel") or "").strip()].append(p)

    primary_programmes = primary.findall("programme")
    primary_by_channel = defaultdict(list)
    for p in primary_programmes:
        primary_by_channel[(p.get("channel") or "").strip()].append(p)

    donor_primary_ids = {cid for cid, row in rows.items() if row.get("site") == "elcinema.com"}
    replaced_channels = 0
    added_programmes = 0
    title_enriched = 0
    desc_enriched = 0

    # ElCinema-primary identities use the separately throttled 3-day donor so a Cairo
    # midnight boundary does not collapse the receiver horizon to ~24 hours.
    if donor_primary_ids:
        for p in list(primary.findall("programme")):
            if (p.get("channel") or "").strip() in donor_primary_ids:
                primary.remove(p)
        for cid in sorted(donor_primary_ids):
            events = donor_by_channel.get(cid, [])
            if events:
                replaced_channels += 1
                for p in events:
                    primary.append(copy.deepcopy(p))
                    added_programmes += 1

    # Official timelines keep their exact clocks. ElCinema can only enrich matching slots.
    for p in primary.findall("programme"):
        cid = (p.get("channel") or "").strip()
        row = rows.get(cid) or {}
        if cid in donor_primary_ids:
            continue
        d = donor_by_slot.get(slot(p))
        if d is None:
            continue
        protected = bool(row.get("official_first_protected"))
        p_title, p_title_text = first_text(p, "title")
        d_title, d_title_text = first_text(d, "title")
        if (not protected and d_title is not None and has_ar(d_title_text)
                and (p_title is None or not has_ar(p_title_text))):
            if p_title is None:
                p_title = ET.SubElement(p, "title")
            p_title.text = d_title_text
            p_title.set("lang", "ar")
            title_enriched += 1
        p_desc, p_desc_text = first_text(p, "desc")
        d_desc, d_desc_text = first_text(d, "desc")
        if d_desc is not None and has_ar(d_desc_text) and (p_desc is None or not has_ar(p_desc_text)):
            if p_desc is None:
                p_desc = ET.SubElement(p, "desc")
            p_desc.text = d_desc_text
            p_desc.set("lang", "ar")
            desc_enriched += 1

    # Deterministic order: channels first, then programme start/channel.
    channels = [x for x in primary.findall("channel")]
    programmes = [x for x in primary.findall("programme")]
    for child in list(primary):
        primary.remove(child)
    for ch in channels:
        primary.append(ch)
    programmes.sort(key=lambda p: ((p.get("start") or ""), (p.get("channel") or ""), (p.get("stop") or "")))
    seen = set()
    deduped = 0
    for p in programmes:
        key = slot(p)
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        primary.append(p)

    ET.indent(primary, space="  ")
    Path(args.output).write_bytes(ET.tostring(primary, encoding="utf-8", xml_declaration=True))
    report = {
        "schema": 1,
        "policy": "official clocks preserved; ElCinema 2-day throttled donor for Arabic metadata and ElCinema-primary horizon",
        "donor_primary_ids": len(donor_primary_ids),
        "replaced_channels": replaced_channels,
        "donor_programmes_added": added_programmes,
        "official_exact_title_enriched": title_enriched,
        "official_exact_desc_enriched": desc_enriched,
        "duplicate_slots_removed": deduped,
    }
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ELCINEMA_OVERLAY " + " ".join(f"{k}={v}" for k, v in report.items() if isinstance(v, int)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-channels")
    b.add_argument("--catalog-manifest", required=True)
    b.add_argument("--upstream-channels", required=True)
    b.add_argument("--output", required=True)
    o = sub.add_parser("overlay")
    o.add_argument("--catalog-manifest", required=True)
    o.add_argument("--primary", required=True)
    o.add_argument("--donor", required=True)
    o.add_argument("--output", required=True)
    o.add_argument("--report")
    args = ap.parse_args()
    return build_channels(args) if args.command == "build-channels" else overlay(args)


if __name__ == "__main__":
    raise SystemExit(main())
