#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, gzip, json, re, urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

URL="https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz"
TARGETS=["Alfa.Cinema.ae","Alfa.Drama.ae","Alfa.Hekayat.ae"]

def parse_dt(v):
    m=re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?",(v or "").strip())
    if not m:return None
    s,off=m.group(1),m.group(2)
    dt=datetime.strptime(s,"%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M")
    if off and off!="Z":
        sign=1 if off[0]=="+" else -1; hh=int(off[1:3]); mm=int(off[3:5])
        tz=timezone(sign*timedelta(hours=hh,minutes=mm))
    else: tz=timezone.utc
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)

def title(p):
    n=p.find("title"); return ((n.text or "").strip() if n is not None else "")

def norm(s): return " ".join(re.sub(r"[^0-9a-z\u0600-\u06ff]+"," ",(s or "").casefold()).split())

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--json",required=True); ap.add_argument("--text",required=True); a=ap.parse_args()
    req=urllib.request.Request(URL,headers={"User-Agent":"EPGManager-Alfa-Audit/1.0"})
    with urllib.request.urlopen(req,timeout=45) as r: data=r.read()
    if data[:2]==b"\x1f\x8b": data=gzip.decompress(data)
    root=ET.fromstring(data)
    now=datetime.now(timezone.utc); end=now+timedelta(hours=48)
    by=defaultdict(list); names={}
    for c in root.findall("channel"):
        cid=(c.get("id") or "").strip(); dn=c.find("display-name"); names[cid]=((dn.text or "").strip() if dn is not None else cid)
    for p in root.findall("programme"):
        st=parse_dt(p.get("start")); sp=parse_dt(p.get("stop"))
        if not st or not sp or sp<=now or st>=end: continue
        by[(p.get("channel") or "").strip()].append(p)
    fingerprints=defaultdict(list)
    stats={}
    for cid,rows in by.items():
        rows.sort(key=lambda p:p.get("start") or "")
        fp=tuple((p.get("start") or "",p.get("stop") or "",norm(title(p))) for p in rows)
        if len(rows)>=3: fingerprints[fp].append(cid)
        if cid in TARGETS:
            titles=[title(p) for p in rows]
            stats[cid]={"name":names.get(cid,cid),"events":len(rows),"unique_titles":len(set(norm(t) for t in titles if norm(t))),"samples":titles[:6],"fingerprint":fp}
    results=[]
    for cid in TARGETS:
        st=stats.get(cid,{"name":names.get(cid,cid),"events":0,"unique_titles":0,"samples":[],"fingerprint":()})
        clones=fingerprints.get(st["fingerprint"],[]) if st["fingerprint"] else []
        unrelated=[x for x in clones if x!=cid]
        verdict="CANDIDATE" if st["events"]>=3 and not unrelated and st["unique_titles"]>=2 else ("REVIEW" if st["events"] else "EMPTY")
        if unrelated: verdict="REJECT_CLONED"
        results.append({"id":cid,"name":st["name"],"events":st["events"],"unique_titles":st["unique_titles"],"samples":st["samples"],"clone_group":clones,"verdict":verdict})
    out={"schema":1,"source":URL,"window_start":now.isoformat(),"window_end":end.isoformat(),"results":results}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["ALFA EPGSHARE AE1 TARGETED AUDIT",f"source={URL}",""]
    for x in results:
        lines.append(f"- [{x['verdict']}] {x['id']} | events={x['events']} unique={x['unique_titles']} clone_group={len(x['clone_group'])}")
        if x['samples']: lines.append("    samples="+" | ".join(x['samples']))
        if len(x['clone_group'])>1: lines.append("    clones="+", ".join(x['clone_group'][:20]))
    Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("; ".join(f"{x['id']}={x['verdict']}({x['events']})" for x in results))

if __name__=="__main__": main()
