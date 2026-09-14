#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, csv, json, re, urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

COUNTRIES = ["ae","sa","qa","kw","bh","om","jo","lb","iq","ye","eg","dz","tn","ly","sd","sy","ps","mr"]
URL_PATTERNS = [
    "https://iptv-org.github.io/epg/guides/{cc}/osn.com.xml",
    "https://iptv-org.github.io/epg/guides/{cc}/osn.com.epg.xml",
]
PLACEHOLDER = re.compile(r"^(?:tv guide is not available|the schedule is not available|schedule unavailable|programme schedule unavailable|program schedule unavailable|no information|no info|tba|جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$", re.I)

def parse_dt(v):
    m=re.match(r"^(\d{12}|\d{14})",(v or "").strip())
    if not m: return None
    s=m.group(1)
    try: return datetime.strptime(s,"%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M")
    except Exception: return None

def text(p,tag="title"):
    n=p.find(tag)
    return ((n.text or "").strip() if n is not None else "")

def norm(v):
    return " ".join(re.sub(r"[^0-9a-z\u0600-\u06ff]+"," ",(v or "").casefold()).split())

def load_no_epg(path):
    out={}
    with Path(path).open("r",encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("verdict") or "") == "NO_EPG" and r.get("id"):
                out[r["id"]]=r
    return out

def fetch_xml(url):
    req=urllib.request.Request(url,headers={"User-Agent":"EPGManager-Audit/1.0"})
    with urllib.request.urlopen(req,timeout=20) as resp:
        return ET.fromstring(resp.read())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--all-id-csv",required=True)
    ap.add_argument("--json",required=True)
    ap.add_argument("--text",required=True)
    a=ap.parse_args()
    missing=load_no_epg(a.all_id_csv)
    hits=defaultdict(list)
    sources=[]
    for cc in COUNTRIES:
        root=None; chosen=None; errors=[]
        for pat in URL_PATTERNS:
            url=pat.format(cc=cc)
            try:
                root=fetch_xml(url); chosen=url; break
            except Exception as exc:
                errors.append(f"{url}: {str(exc)[:100]}")
        if root is None:
            sources.append({"country":cc,"url":None,"status":"ERROR","error":" | ".join(errors)[:320]})
            continue
        by=defaultdict(list)
        for p in root.findall("programme"):
            cid=(p.get("channel") or "").strip()
            if cid in missing:
                by[cid].append(p)
        sources.append({"country":cc,"url":chosen,"status":"OK","channels_with_missing_ids":len(by),"programmes":sum(len(x) for x in by.values())})
        for cid,rows in by.items():
            rows.sort(key=lambda p:p.get("start") or "")
            titles=[text(p) for p in rows if text(p)]
            ph=sum(1 for t in titles if PLACEHOLDER.match(t))
            valid=0; coverage=0.0
            for p in rows:
                st,sp=parse_dt(p.get("start")),parse_dt(p.get("stop"))
                if st and sp and sp>st:
                    valid+=1; coverage += (sp-st).total_seconds()/3600.0
            sig=tuple((p.get("start") or "",p.get("stop") or "",norm(text(p))) for p in rows)
            hits[cid].append({"country":cc,"events":len(rows),"valid":valid,"coverage_h":round(coverage,1),"placeholders":ph,"unique_titles":len(set(norm(t) for t in titles if norm(t))),"samples":titles[:3],"signature":hash(sig)})
    results=[]
    for cid in sorted(hits,key=str.casefold):
        variants=hits[cid]
        best=max(variants,key=lambda x:(x["events"]-x["placeholders"],x["coverage_h"],x["unique_titles"]))
        if best["events"]>=3 and best["valid"]==best["events"] and best["placeholders"]==0 and best["coverage_h"]>=6:
            verdict="CANDIDATE_EXACT_ID"
        elif best["events"]:
            verdict="REVIEW"
        else:
            verdict="EMPTY"
        results.append({"id":cid,"name":missing[cid].get("name",""),"shard":missing[cid].get("shard",""),"verdict":verdict,"best":best,"countries":[x["country"] for x in variants],"distinct_schedules":len(set(x["signature"] for x in variants))})
    out={"schema":2,"no_epg_input":len(missing),"sources":sources,"results":results,"counts":{k:sum(1 for x in results if x["verdict"]==k) for k in ["CANDIDATE_EXACT_ID","REVIEW","EMPTY"]}}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["OSN COUNTRY-GUIDE EXACT-ID RESCUE AUDIT",f"NO_EPG input={len(missing)} matched_ids={len(results)} candidates={out['counts']['CANDIDATE_EXACT_ID']} review={out['counts']['REVIEW']}","","GUIDES"]
    for s in sources:
        lines.append(f"- {s['country']}: {s['status']} matched={s.get('channels_with_missing_ids',0)} programmes={s.get('programmes',0)} url={s.get('url') or '-'}"+(f" error={s.get('error')}" if s['status']!='OK' else ""))
    lines += ["","MATCHED MISSING IDS"]
    for x in results:
        b=x["best"]
        lines.append(f"- [{x['verdict']}] {x['id']} | {x['shard']} | countries={','.join(x['countries'])} | events={b['events']} cov={b['coverage_h']}h ph={b['placeholders']} unique={b['unique_titles']}")
        if b["samples"]: lines.append("    "+" | ".join(b["samples"]))
    Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(lines[1])

if __name__=="__main__": main()
