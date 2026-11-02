#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional STC *verified* exact-channel fallback for two empty Rotana guides.

Never invent programme times, translate metadata, or overwrite a real Rotana
schedule. This is strictly a donor guide on two documented cross-provider
channel identities, not automatic fuzzy mapping or receiver configuration.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.stctv_per_id_readiness import ready_report
    from tools.audit_source_quality import parse_time
except ImportError:
    from stctv_per_id_readiness import ready_report
    from audit_source_quality import parse_time

CROSS_PROVIDER = {
    "AlResalah.sa@SD": ("stctv.41a7b5dc5c24", "Al Resalah"),
    "RotanaClip.sa@SD": ("stctv.05dc964253e8", "Rotana Clip"),
}

def add_verified_donor(root, feed_dir, now=None):
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if root.tag!="tv":
        raise ValueError("ROTANA_ROOT_NOT_XMLTV")
    existing_ids=[c.get("id") for c in root.findall("channel")]
    if len(set(existing_ids))!=len(existing_ids):
        raise ValueError("ROTANA_DUPLICATE_CHANNELS")
    report={"mode":"strict_verified_STC_fallback_only_when_Rotana_direct_empty",
            "generated_utc":now.isoformat(),"source":"rotana",
            "donor":"stctv", "added":0,"synthetic_programmes_added":0,
            "ids":{},"status":"SKIPPED_UNVERIFIED_DONOR"}
    try:
        index=ready_report(Path(feed_dir)/"stctv.xml.gz",
                           Path(feed_dir)/"stctv.json", now=now)
        verified={x["xmltv_id"] for x in index["ids"]
                  if x["status"]=="VERIFIED" and x["best_match_green_allowed"]}
        data=(Path(feed_dir)/"stctv.xml.gz").read_bytes()
        donor=ET.fromstring(gzip.decompress(data))
        donor_channels={c.get("id"):c for c in donor.findall("channel")}
        programmes={}
        for p in donor.findall("programme"):
            programmes.setdefault(p.get("channel"),[]).append(p)
    except Exception as exc:
        report["status"]="DONOR_UNAVAILABLE_OR_FAILED_PROVENANCE_OR_QUALITY"
        report["donor_error"]=str(exc)[:160]
        return report

    original={cid:sum(p.get("channel")==cid for p in root.findall("programme"))
              for cid in CROSS_PROVIDER}
    for dest,(sid,required_english_name) in CROSS_PROVIDER.items():
        result={"stc_id":sid,"added":0,"original_rotana_programmes":original[dest]}
        report["ids"][dest]=result
        if dest not in existing_ids:
            result["reason"]="ROTANA_CHANNEL_ID_NOT_IN_OFFICIAL_CATALOGUE"
            continue
        if original[dest]>0:
            result["reason"]="ROTANA_PRIMARY_PRESENT_UNTOUCHED"
            continue
        if sid not in verified:
            result["reason"]="STC_DONOR_NOT_VERIFIED_PER_ID"
            continue
        c=donor_channels.get(sid)
        names={(n.text or "").strip().casefold() for n in
               c.findall("display-name")} if c is not None else set()
        if required_english_name.casefold() not in names or not any(
                x.get("system")=="stctv-id" and (x.text or "").strip()
                for x in c.findall("url")):
            result["reason"]="STC_DONOR_CHANNEL_IDENTITY_NOT_VERIFIED"
            continue
        for p in sorted(programmes.get(sid,[]),key=lambda n:n.get("start") or ""):
            start=parse_time(p.get("start"))
            stop=parse_time(p.get("stop"))
            if (start is None or stop is None or stop<=start or
                    stop<=now-timedelta(hours=2) or
                    start>=now+timedelta(hours=48) or
                    not any((t.text or "").strip() for t in p.findall("title"))):
                continue
            copied=ET.fromstring(ET.tostring(p,encoding="utf-8"))
            copied.set("channel",dest)
            copied.set("source-info-url","https://web.stctv.com/")
            root.append(copied)
            result["added"]+=1
            report["added"]+=1
        result["reason"]="STC_REAL_BROADCAST_PROGRAMMES_COPIED" if result["added"] else "NO_STC_EVENTS_IN_WINDOW"
    report["status"]="VERIFIED_REAL_FALLBACK_USED" if report["added"] else "NO_VERIFIED_MISSING_GUIDE_TO_COPY"
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--feed-dir",default="feeds")
    p.add_argument("--report",required=True)
    args=p.parse_args()
    root=ET.parse(args.input).getroot()
    rep=add_verified_donor(root,args.feed_dir)
    ET.indent(root,space="  ")
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",
                                encoding="utf-8")
    print("ROTANA_VERIFIED_DONOR",json.dumps(rep,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
