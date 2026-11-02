#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted ElCinema refresh for guides shorter than 40h.

Never manufacture schedule rows and never replace a verified first-pass slot.
Only provider rows that extend past the last existing programme are appended.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import re
import xml.etree.ElementTree as ET
from copy import deepcopy

DATETIME=re.compile(r"^(\d{14}|\d{12})\s+([+-]\d{4}|Z)$")
def time_utc(raw):
    m=DATETIME.match((raw or "").strip())
    if not m:
        return None
    digits,off=m.groups()
    try:
        out=datetime.strptime(digits,"%Y%m%d%H%M%S" if len(digits)==14 else "%Y%m%d%H%M")
        if off and off!="Z":
            sign=1 if off[0]=="+" else -1
            hours,minutes=int(off[1:3]),int(off[3:5])
            if hours>14 or minutes>=60:
                return None
            return out.replace(tzinfo=timezone(sign*timedelta(hours=hours,minutes=minutes))).astimezone(timezone.utc)
        # No timezone should never be silently interpreted as UTC.
        return out.replace(tzinfo=timezone.utc) if off=="Z" else None
    except ValueError:
        return None

def copy(node):
    return deepcopy(node)

def by_id(root):
    out={}
    for p in root.findall("programme"):
        key=(p.get("channel") or "").strip()
        if key:
            out.setdefault(key,[]).append(p)
    return out

def last_stop(rows,now):
    stops=[time_utc(x.get("stop")) for x in rows]
    stops=[x for x in stops if x and x>now]
    return max(stops,default=None)

def build_catalogue(catalogue,raw,fallback=None,now=None,min_hours=40,max_ids=45):
    now=now or datetime.now(timezone.utc)
    source=by_id(raw)
    fallback_by={}
    if fallback is not None:
        fallback_by={(c.get("xmltv_id") or c.get("id") or "").strip():c
                     for c in fallback.findall("channel")}
    requests=[]
    for c in catalogue.findall("channel"):
        cid=(c.get("xmltv_id") or c.get("id") or "").strip()
        if not cid:
            continue
        stop=last_stop(source.get(cid,()),now)
        horizon=(stop-now).total_seconds()/3600 if stop else 0
        if horizon<min_hours:
            requests.append((horizon,cid,copy(fallback_by.get(cid,c))))
    requests.sort(key=lambda x:(x[0],x[1]))
    out=ET.Element("channels")
    for _,_,c in requests[:max_ids]:
        out.append(c)
    return out,{"retryable_total":len(requests),"selected":len(out),
                "selected_ids":[cid for _,cid,_ in requests[:max_ids]],
                "min_hours":min_hours}

def extend_future(primary,retry,now=None):
    now=now or datetime.now(timezone.utc)
    known_channels={(c.get("id") or "").strip() for c in primary.findall("channel")}
    rows=by_id(primary)
    limits={cid:max(now,max(
        (time_utc(p.get("stop")) for p in data if time_utc(p.get("stop"))),
        default=now))
        for cid,data in rows.items()}
    added=0
    recovered=set()
    invalid=0
    seen={(p.get("channel"),p.get("start"),p.get("stop")) for p in primary.findall("programme")}
    # One actual broadcast only: no overlapping alternate title for the same slot.
    for p in sorted(retry.findall("programme"),key=lambda x:(x.get("channel") or "",x.get("start") or "")):
        cid=(p.get("channel") or "").strip()
        start=time_utc(p.get("start"))
        stop=time_utc(p.get("stop"))
        title=(p.findtext("title") or "").strip()
        if cid not in known_channels or not start or not stop or not title or stop<=start or stop-start>timedelta(hours=8):
            invalid+=1
            continue
        if stop<=now or start<limits.get(cid,now):
            continue
        key=(cid,p.get("start"),p.get("stop"))
        if key in seen:
            continue
        primary.append(copy(p))
        seen.add(key)
        limits[cid]=stop
        added+=1
        recovered.add(cid)
    return {"extended_ids":sorted(recovered),"events_added":added,"invalid_retry_rows":invalid}

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)
    b=sub.add_parser("build")
    b.add_argument("--catalogue",required=True)
    b.add_argument("--raw",required=True)
    b.add_argument("--fallback-catalogue")
    b.add_argument("--output",required=True)
    b.add_argument("--report",required=True)
    b.add_argument("--min-hours",type=float,default=40)
    b.add_argument("--max-ids",type=int,default=45)
    e=sub.add_parser("extend")
    e.add_argument("--primary",required=True)
    e.add_argument("--retry",required=True)
    e.add_argument("--output",required=True)
    e.add_argument("--report",required=True)
    args=ap.parse_args()
    if args.cmd=="build":
        cat=ET.parse(args.catalogue).getroot()
        raw=ET.parse(args.raw).getroot()
        fall=ET.parse(args.fallback_catalogue).getroot() if args.fallback_catalogue and Path(args.fallback_catalogue).is_file() else None
        out,report=build_catalogue(cat,raw,fall,min_hours=args.min_hours,max_ids=max(0,min(args.max_ids,45)))
    else:
        out=ET.parse(args.primary).getroot()
        report=extend_future(out,ET.parse(args.retry).getroot())
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    ET.indent(out,space="  ")
    Path(args.output).write_bytes(ET.tostring(out,encoding="utf-8",xml_declaration=True))
    report.update({"mode":args.cmd,"synthetic_programmes_added":0})
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print("ELCINEMA_SHORT_RETRY",json.dumps(report,ensure_ascii=False))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
