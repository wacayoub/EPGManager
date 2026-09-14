#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, csv, gzip, json, re, urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

URL = "https://sbauae.faulio.com/api/v1/programgrid"
UA = "EPGManager-Sharjah-Official-Audit/1.0"


def fetch_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode("utf-8","replace"))


def norm(v):
    return " ".join(re.sub(r"[^0-9a-z\u0600-\u06ff]+"," ",(v or "").casefold()).split())


def scalar_summary(obj, depth=0):
    if depth>3: return {}
    if isinstance(obj,dict):
        out={}
        for k,v in obj.items():
            if isinstance(v,(str,int,float,bool)) or v is None:
                out[k]=v
            elif isinstance(v,dict) and depth<2:
                x=scalar_summary(v,depth+1)
                if x: out[k]=x
        return out
    return {}


def likely_event_lists(obj, path=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=(path+"."+k).strip(".")
            if isinstance(v,list) and v and isinstance(v[0],dict):
                score=0
                keys=set().union(*(set(x.keys()) for x in v[:5] if isinstance(x,dict)))
                low={str(x).casefold() for x in keys}
                for marker in ("title","name","program","programme","start","end","date","time","from","to"):
                    if any(marker in x for x in low): score+=1
                if score>=2: out.append((p,v,score))
            out.extend(likely_event_lists(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:10]): out.extend(likely_event_lists(v,f"{path}[{i}]"))
    return out


def event_title(e):
    for k in ("title","name","program_title","programme_title","program","programme","project_title"):
        v=e.get(k)
        if isinstance(v,str) and v.strip(): return v.strip()
        if isinstance(v,dict):
            for kk in ("title","name"):
                vv=v.get(kk)
                if isinstance(vv,str) and vv.strip(): return vv.strip()
    # nested project/program
    for k,v in e.items():
        if isinstance(v,dict) and any(x in k.casefold() for x in ("program","project","episode","vod")):
            for kk in ("title","name"):
                vv=v.get(kk)
                if isinstance(vv,str) and vv.strip(): return vv.strip()
    return ""


def event_time(e):
    vals=[]
    for k,v in e.items():
        lk=k.casefold()
        if isinstance(v,(str,int,float)) and any(x in lk for x in ("start","from","time","date","end","to")):
            vals.append(f"{k}={v}")
    return ", ".join(vals[:5])


def load_cloud_ids(csv_path):
    rows=[]
    with Path(csv_path).open("r",encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def sim(a,b):
    aa=set(norm(a).split()); bb=set(norm(b).split())
    if not aa or not bb: return 0.0
    return len(aa&bb)/len(aa|bb)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--all-id-csv",required=True)
    ap.add_argument("--json",required=True)
    ap.add_argument("--text",required=True)
    a=ap.parse_args()
    data=fetch_json(URL)
    cloud=load_cloud_ids(a.all_id_csv)
    channels=data.get("channels",[]) if isinstance(data,dict) else []
    results=[]
    for ch in channels:
        title=str(ch.get("title") or ch.get("name") or "").strip()
        lists=likely_event_lists(ch)
        lists.sort(key=lambda x:(x[2],len(x[1])),reverse=True)
        path,events,score=(lists[0] if lists else ("",[],0))
        samples=[]
        for e in events[:8]:
            samples.append({"title":event_title(e),"time":event_time(e),"raw":scalar_summary(e)})
        best=[]
        for r in cloud:
            s=max(sim(title,r.get("name","")),sim(title,r.get("id","")))
            if s>=0.45:
                best.append((s,r))
        best.sort(key=lambda x:x[0],reverse=True)
        results.append({
            "api_id":ch.get("id"),"title":title,"url":ch.get("url"),"has_grid":ch.get("has_grid"),
            "grid_field":path,"events":len(events),"samples":samples,
            "cloud_matches":[{"similarity":round(s,3),"id":r.get("id"),"name":r.get("name"),"verdict":r.get("verdict"),"shard":r.get("shard")} for s,r in best[:5]],
            "channel_keys":sorted(ch.keys()),
        })
    out={"schema":1,"source":URL,"top_keys":sorted(data.keys()) if isinstance(data,dict) else [],"channels":results}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["SHARJAH OFFICIAL API AUDIT",f"source={URL} channels={len(results)}",""]
    for x in results:
        lines.append(f"- API#{x['api_id']} {x['title']} | has_grid={x['has_grid']} events={x['events']} field={x['grid_field']}")
        for s in x['samples'][:5]:
            lines.append(f"    PROGRAM {s['time']} | {s['title']}")
        for m in x['cloud_matches'][:3]:
            lines.append(f"    CLOUD {m['similarity']:.3f} | {m['id']} | {m['verdict']} | {m['shard']}")
        if not x['samples']:
            lines.append("    NO GRID EVENTS DETECTED")
        lines.append("")
    Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))

if __name__=="__main__": main()
