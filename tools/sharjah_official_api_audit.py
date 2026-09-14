#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, csv, json, re, urllib.request
from pathlib import Path

URL = "https://sbauae.faulio.com/api/v1/programgrid"
UA = "EPGManager-Sharjah-Official-Audit/1.1"


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


def shape(obj, depth=0):
    if depth>=4:
        return type(obj).__name__
    if isinstance(obj,dict):
        return {str(k):shape(v,depth+1) for k,v in list(obj.items())[:12]}
    if isinstance(obj,list):
        return {"type":"list","len":len(obj),"first":shape(obj[0],depth+1) if obj else None}
    return type(obj).__name__


def likely_event_lists(obj, path=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=(path+"."+str(k)).strip(".")
            if isinstance(v,list) and v and isinstance(v[0],dict):
                keys=set().union(*(set(x.keys()) for x in v[:5] if isinstance(x,dict)))
                low={str(x).casefold() for x in keys}
                score=sum(1 for marker in ("title","name","program","programme","start","end","date","time","from","to") if any(marker in x for x in low))
                if score>=1: out.append((p,v,score))
            out.extend(likely_event_lists(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:100]): out.extend(likely_event_lists(v,f"{path}[{i}]"))
    return out


def event_title(e):
    for k in ("title","name","program_title","programme_title","program","programme","project_title"):
        v=e.get(k)
        if isinstance(v,str) and v.strip(): return v.strip()
        if isinstance(v,dict):
            for kk in ("title","name"):
                vv=v.get(kk)
                if isinstance(vv,str) and vv.strip(): return vv.strip()
    for k,v in e.items():
        if isinstance(v,dict) and any(x in str(k).casefold() for x in ("program","project","episode","vod")):
            for kk in ("title","name"):
                vv=v.get(kk)
                if isinstance(vv,str) and vv.strip(): return vv.strip()
    return ""


def event_time(e):
    vals=[]
    for k,v in e.items():
        lk=str(k).casefold()
        if isinstance(v,(str,int,float)) and any(x in lk for x in ("start","from","time","date","end","to")):
            vals.append(f"{k}={v}")
    return ", ".join(vals[:6])


def load_cloud_ids(csv_path):
    rows=[]
    with Path(csv_path).open("r",encoding="utf-8-sig",newline="") as f:
        rows.extend(csv.DictReader(f))
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
        grid=ch.get("grid")
        lists=likely_event_lists(grid,"grid")
        lists.sort(key=lambda x:(x[2],len(x[1])),reverse=True)
        path,events,score=(lists[0] if lists else ("",[],0))
        samples=[]
        for e in events[:12]:
            samples.append({"title":event_title(e),"time":event_time(e),"raw":scalar_summary(e)})
        best=[]
        for r in cloud:
            s=max(sim(title,r.get("name","")),sim(title,r.get("id","")))
            if s>=0.45: best.append((s,r))
        best.sort(key=lambda x:x[0],reverse=True)
        preview=json.dumps(grid,ensure_ascii=False)[:5000] if grid is not None else "null"
        results.append({
            "api_id":ch.get("id"),"title":title,"url":ch.get("url"),"has_grid":ch.get("has_grid"),
            "grid_type":type(grid).__name__,"grid_len":len(grid) if isinstance(grid,(list,dict)) else None,
            "grid_shape":shape(grid),"grid_preview":preview,
            "grid_field":path,"events":len(events),"samples":samples,
            "cloud_matches":[{"similarity":round(s,3),"id":r.get("id"),"name":r.get("name"),"verdict":r.get("verdict"),"shard":r.get("shard")} for s,r in best[:5]],
        })
    out={"schema":2,"source":URL,"ts_start":data.get("ts_start"),"ts_end":data.get("ts_end"),"channels":results}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["SHARJAH OFFICIAL API AUDIT V2",f"source={URL} channels={len(results)} ts_start={out['ts_start']} ts_end={out['ts_end']}",""]
    for x in results:
        lines.append(f"- API#{x['api_id']} {x['title']} | has_grid={x['has_grid']} grid={x['grid_type']} len={x['grid_len']} events={x['events']} field={x['grid_field']}")
        lines.append(f"    SHAPE {json.dumps(x['grid_shape'],ensure_ascii=False)[:1000]}")
        lines.append(f"    PREVIEW {x['grid_preview'][:1200]}")
        for s in x['samples'][:5]: lines.append(f"    PROGRAM {s['time']} | {s['title']}")
        for m in x['cloud_matches'][:3]: lines.append(f"    CLOUD {m['similarity']:.3f} | {m['id']} | {m['verdict']} | {m['shard']}")
        lines.append("")
    Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))

if __name__=="__main__": main()
