#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv,gzip,json
from pathlib import Path
from datetime import datetime,timezone
import xml.etree.ElementTree as ET

REPORTS=Path("reports")
ANALYSIS=Path("analysis/arab-fallback")
FEEDS=Path("feeds")
FEEDS.mkdir(exist_ok=True)

# Explicit IDs removed after manual EPG validation.
OTHERS_EXCLUDE_IDS = {
    "Bloomberg-1.qa",
    "Discovery ID.sa",
    "Nessma.eg",
    "Ten.sa",
    "Utv.eg",
}

# All current Rotana mappings are quarantined until channel IDs are revalidated
# against the live/current programme shown on rotana.net.
ROTANA_QUARANTINE = False

def read_xml_gz(path):
    return ET.fromstring(gzip.decompress(Path(path).read_bytes()))

def valid_winners():
    p=REPORTS/"new-arab-epg-ids.csv"
    rows=[]
    if not p.exists():
        return rows
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("integration_status")!="VALIDATED_MISSING":
                continue
            if r.get("provider") not in ("openepg","epgshare"):
                continue
            if r.get("id") in OTHERS_EXCLUDE_IDS:
                continue
            try:
                if int(float(r.get("future_programmes") or 0))<=0:
                    continue
            except Exception:
                continue
            rows.append(r)
    return rows

def add_fallback(root):
    winners=valid_winners()
    wanted={(r["provider"],r["id"]):r for r in winners}
    source_roots={}
    for provider in ("openepg","epgshare"):
        p=ANALYSIS/f"{provider}.xml.gz"
        if p.exists():
            source_roots[provider]=read_xml_gz(p)

    added_ids=set()
    programmes=0
    origin={}
    for provider,srcroot in source_roots.items():
        ids={cid for (prov,cid) in wanted if prov==provider}
        channels={c.get("id"):c for c in srcroot.findall("channel") if c.get("id") in ids}
        for cid,c in channels.items():
            if cid in added_ids:
                continue
            root.append(c)
            added_ids.add(cid)
            origin[cid]=provider
        for p in srcroot.findall("programme"):
            cid=p.get("channel")
            if cid in added_ids and origin.get(cid)==provider:
                root.append(p)
                programmes+=1
    return len(added_ids),programmes,origin

def add_rotana_art(root):
    p=REPORTS/"rotana-art-programmes.json"
    if not p.exists():
        return 0,0,{}
    data=json.loads(p.read_text(encoding="utf-8"))
    chn=0;prg=0;origin={}
    for row in data.get("channels",[]):
        if row.get("id") in OTHERS_EXCLUDE_IDS:
            continue
        if ROTANA_QUARANTINE and row.get("source")=="rotana":
            continue
        if row.get("status") not in ("VALID_SCHEDULE","PROGRAMME_SAMPLES_FOUND"):
            continue
        if int(row.get("programmes") or 0)<=0:
            continue
        events=row.get("events") or []
        # ART programme samples without aligned start/stop stay compare-only.
        if not events or any(not e.get("start") or not e.get("stop") for e in events):
            continue
        cid=row["id"]
        c=ET.SubElement(root,"channel",{"id":cid})
        ET.SubElement(c,"display-name").text=row.get("name") or cid
        chn+=1;origin[cid]=row.get("source","official")
        for e in events:
            try:
                st=datetime.fromisoformat(e["start"])
                sp=datetime.fromisoformat(e["stop"])
                if sp<=st:
                    continue
            except Exception:
                continue
            fmt=lambda d:d.strftime("%Y%m%d%H%M%S %z")
            x=ET.SubElement(root,"programme",{"channel":cid,"start":fmt(st),"stop":fmt(sp)})
            ET.SubElement(x,"title",{"lang":"ar"}).text=e.get("title") or "برنامج"
            desc=e.get("desc") or row.get("sample_desc") or ""
            if desc:
                ET.SubElement(x,"desc",{"lang":"ar"}).text=desc
            prg+=1
    return chn,prg,origin

def main():
    root=ET.Element("tv",{"generator-info-name":"EPG-Scrapers Others"})
    rc,rp,ro=add_rotana_art(root)
    fc,fp,fo=add_fallback(root)

    # Remove accidental duplicate channel IDs and duplicate programme tuples.
    seen_channels=set()
    for c in list(root.findall("channel")):
        cid=c.get("id")
        if cid in seen_channels:
            root.remove(c)
        else:
            seen_channels.add(cid)
    seen_prog=set()
    for p in list(root.findall("programme")):
        key=(p.get("channel"),p.get("start"),p.get("stop"),
             "|".join((x.text or "") for x in p.findall("title")))
        if key in seen_prog:
            root.remove(p)
        else:
            seen_prog.add(key)

    ET.indent(root,space="  ")
    raw=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    out=FEEDS/"others.xml.gz"
    with gzip.GzipFile(filename="others.xml",mode="wb",fileobj=out.open("wb"),compresslevel=9,mtime=0) as g:
        g.write(raw)

    channels=len(root.findall("channel"))
    programmes=len(root.findall("programme"))
    stats={
        "generated":datetime.now(timezone.utc).isoformat(),
        "channels":channels,
        "programmes":programmes,
        "components":{
            "rotana_art":{"channels":rc,"programmes":rp},
            "validated_fallback":{"channels":fc,"programmes":fp},
        },
        "policy":"Rotana official + ART validated schedules + validated missing winners from OpenEPG/EPGShare",
    }
    (FEEDS/"others.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    origins={**fo,**ro}
    with (FEEDS/"others.txt").open("w",encoding="utf-8") as f:
        for c in root.findall("channel"):
            cid=c.get("id");name=(c.findtext("display-name") or cid)
            f.write(f"{cid} | {name} | {origins.get(cid,'unknown')}\n")
    print(json.dumps(stats,ensure_ascii=False))
    if channels<1 or programmes<1:
        raise SystemExit(2)

if __name__=="__main__":
    main()
