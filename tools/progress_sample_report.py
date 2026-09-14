#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, csv, gzip, json
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

TARGETS = [
    "beIN SPORTS 1.qa",
    "beIN SPORTS EN 1.qa",
    "2023 Alkass 6.qa",
    "MBC3.ae@SD",
    "MBCPlusDrama.sa@SD",
    "AlQuranAlKareemTV.sa@SD",
    "OSNMoviesPremiere.ae@SD",
    "RotanaCinemaKSA.sa@SD",
    "AlEkhbariya.sa@SD",
    "AlSunnahAlNabawiyahTV.sa@SD",
    "AlJadeed.lb@SD",
    "JordanTV.jo@SD",
]

def load_xml(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)

def title(p):
    n = p.find("title")
    return ((n.text or "").strip() if n is not None else "")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    a=ap.parse_args()
    d=Path(a.data_dir)
    verdicts={}
    csvp=d/"all-id-audit.csv"
    if csvp.exists():
        with csvp.open("r",encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                verdicts[r.get("id","")] = r
    catalog={}
    cp=d/"catalog.json"
    if cp.exists():
        obj=json.loads(cp.read_text(encoding="utf-8"))
        for x in obj.get("channels",[]) or []:
            catalog[x.get("xmltv_id","")] = x.get("site","")
    root=load_xml(d/"mena-arabic.xml.gz")
    events=defaultdict(list)
    for p in root.findall("programme"):
        cid=(p.get("channel") or "").strip()
        if cid in TARGETS:
            events[cid].append(p)
    for rows in events.values(): rows.sort(key=lambda p:p.get("start") or "")
    lines=["EPGMANAGER MENA PROGRESS SAMPLES",""]
    for cid in TARGETS:
        r=verdicts.get(cid,{})
        rows=events.get(cid,[])
        v=r.get("verdict","NOT_IN_AUDIT")
        source=catalog.get(cid,"merged/alias") or "merged/alias"
        shard=r.get("shard","")
        lines.append(f"ID: {cid}")
        lines.append(f"  status={v} shard={shard or '?'} source={source} events={len(rows)}")
        if rows:
            for p in rows[:2]:
                lines.append(f"  {p.get('start','?')} -> {p.get('stop','?')} | {title(p)}")
        else:
            lines.append("  NO PROGRAMME")
        lines.append("")
    Path(a.out).write_text("\n".join(lines),encoding="utf-8")

if __name__=="__main__":
    main()
