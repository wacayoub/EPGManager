#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build lightweight XMLTV feeds grouped for Vu+ / EPGManager import.

The groups are a presentation/import layer only. Programme data always comes
from the already-arbitrated final feeds; this script never changes winner
sources or scraper priority.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

GROUP_ORDER = [
    "OSN","ROTANA","BEIN_SPORTS","BEIN_MEDIA","ART","MBC","ALJAZEERA",
    "EMIRATES","EGYPT","MOROCCO","SAUDI","QATAR","KUWAIT","LEBANON",
    "JORDAN","IRAQ","BAHRAIN","OMAN","TUNISIA","ALGERIA","LIBYA",
    "SYRIA","YEMEN","PALESTINE","SUDAN","MAURITANIA","OTHER",
]
COUNTRY_GROUPS = {
    "ae":"EMIRATES","eg":"EGYPT","ma":"MOROCCO","sa":"SAUDI","qa":"QATAR",
    "kw":"KUWAIT","lb":"LEBANON","jo":"JORDAN","iq":"IRAQ","bh":"BAHRAIN",
    "om":"OMAN","tn":"TUNISIA","dz":"ALGERIA","ly":"LIBYA","sy":"SYRIA",
    "ye":"YEMEN","ps":"PALESTINE","sd":"SUDAN","mr":"MAURITANIA",
}

def read_xml(path: Path):
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def fallback_group(cid: str) -> str:
    probe=(cid or "").casefold()
    if "rotana" in probe:
        return "ROTANA"
    if probe.startswith("bein") or re.search(r"\bbein\b", probe):
        return "BEIN_SPORTS" if "sport" in probe else "BEIN_MEDIA"
    if probe.startswith("art.") or probe.startswith("art_"):
        return "ART"
    if probe.startswith("osn.") or probe.startswith("osn_"):
        return "OSN"
    if probe.startswith("mbc.") or probe.startswith("mbc_"):
        return "MBC"
    if "aljazeera" in probe or "al.jazeera" in probe:
        return "ALJAZEERA"
    m=re.search(r"\.([a-z]{2})(?:@[^.]*)?$", cid or "", re.I)
    return COUNTRY_GROUPS.get(m.group(1).lower(), "OTHER") if m else "OTHER"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--mena", required=True)
    ap.add_argument("--morocco")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--base-url", required=True)
    args=ap.parse_args()

    out=Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    group_by_id={}
    with Path(args.master).open("r", encoding="utf-8-sig", newline="") as fh:
        rows=list(csv.DictReader(fh))
    for r in rows:
        raw=(r.get("xmltv_id") or "").strip()
        src=(r.get("source") or "").strip()
        winner=(r.get("winner_source") or "").strip()
        if not raw or not src or src != winner:
            continue
        canonical=(r.get("receiver_canonical_id") or raw).strip()
        group=(r.get("id_group") or fallback_group(raw)).strip() or "OTHER"
        group_by_id[raw]=group
        group_by_id[canonical]=group

    roots=[read_xml(Path(args.mena))]
    if args.morocco and Path(args.morocco).exists():
        roots.append(read_xml(Path(args.morocco)))

    channels={}
    programmes=[]
    for root in roots:
        for ch in root.findall("channel"):
            cid=(ch.get("id") or "").strip()
            if cid:
                channels[cid]=ch
        programmes.extend(root.findall("programme"))

    ids_by_group={g:set() for g in GROUP_ORDER}
    for cid in channels:
        g=group_by_id.get(cid) or fallback_group(cid)
        if g not in ids_by_group:
            g="OTHER"
        ids_by_group[g].add(cid)

    prog_by_group={g:[] for g in GROUP_ORDER}
    for p in programmes:
        cid=(p.get("channel") or "").strip()
        g=group_by_id.get(cid) or fallback_group(cid)
        if g not in prog_by_group:
            g="OTHER"
        if cid in ids_by_group[g]:
            prog_by_group[g].append(p)

    registry={"schema":1,"policy":"grouped import views over final arbitrated EPG; source/winner priority unchanged","groups":[]}
    total_channels=total_programmes=0
    for group in GROUP_ORDER:
        tv=ET.Element("tv", {
            "generator-info-name":"EPGManager Group Builder",
            "source-info-name":"EPGManager final arbitrated feeds",
        })
        for cid in sorted(ids_by_group[group], key=str.casefold):
            tv.append(channels[cid])
        for p in prog_by_group[group]:
            tv.append(p)
        xml=ET.tostring(tv, encoding="utf-8", xml_declaration=True)
        fn="group-%s.xml.gz" % group.lower().replace("_","-")
        with gzip.open(out/fn, "wb", compresslevel=9) as gz:
            gz.write(xml)
        chn=len(ids_by_group[group]); prg=len(prog_by_group[group])
        total_channels += chn; total_programmes += prg
        registry["groups"].append({
            "key":group,
            "label":group.replace("_"," "),
            "xml":fn,
            "url":args.base_url.rstrip("/")+"/"+fn,
            "channels":chn,
            "programmes":prg,
            "enabled":True,
        })

    registry["totals"]={"channels":total_channels,"programmes":total_programmes}
    (out/"group-sources.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("GROUP_FEEDS PASS groups=%d channels=%d programmes=%d" % (len(GROUP_ORDER), total_channels, total_programmes))

if __name__ == "__main__":
    main()
