#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate live EPG candidates and filter discovery-only IDs.

Consumes reports/arab-epg-gaps.json plus raw XMLTV grabbed from candidate sites.
Produces a strict valid-only inventory. It never publishes feeds.
"""
from __future__ import annotations
import argparse, json, re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

PLACEHOLDER_RE=re.compile(
    r"\b(tba|tbd|to be announced|to be confirmed|no information|no program(?:me)?|channel)\b|"
    r"(لم يحدد|لم يُحدد|سيتم تحديد|لا توجد معلومات|تغطية إقليمية متميزة)",
    re.I
)
AR=re.compile(r"[\u0600-\u06FF]")

def parse_dt(raw):
    raw=(raw or "").strip()
    if len(raw)<14: return None
    try:
        return datetime.strptime(raw[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def txt(node,tag):
    for x in node.findall(tag):
        t=(x.text or "").strip()
        if t: return t
    return ""

def norm(cid):
    return (cid or "").strip().split("@",1)[0].casefold()

def metrics(root,cid):
    now=datetime.now(timezone.utc)
    rows=[p for p in root.findall("programme") if (p.get("channel") or "")==cid]
    good=[]; invalid=0
    for p in rows:
        s=parse_dt(p.get("start")); e=parse_dt(p.get("stop"))
        if not s or not e or not s<e:
            invalid+=1; continue
        if e <= now: continue
        good.append((p,s,e))
    if not good:
        return {"programmes":0,"future_hours":0.0,"desc_pct":0.0,"arabic_title_pct":0.0,
                "arabic_desc_pct":0.0,"placeholder_pct":0.0,"invalid_time":invalid}
    latest=max(e for _,_,e in good)
    titles=[txt(p,"title") for p,_,_ in good]
    descs=[txt(p,"desc") for p,_,_ in good]
    placeholders=sum(1 for t in titles if PLACEHOLDER_RE.search(t or ""))
    def pct(vals,pred):
        vals=[v for v in vals if v]
        return round(100*sum(1 for v in vals if pred(v))/len(vals),1) if vals else 0.0
    return {
        "programmes":len(good),
        "future_hours":round(max(0,(latest-now).total_seconds()/3600),1),
        "desc_pct":round(100*sum(1 for d in descs if d)/len(good),1),
        "arabic_title_pct":pct(titles,lambda s:bool(AR.search(s))),
        "arabic_desc_pct":pct(descs,lambda s:bool(AR.search(s))),
        "placeholder_pct":round(100*placeholders/len(good),1),
        "invalid_time":invalid,
    }

def strict_valid(m):
    return (
        m["programmes"] >= 4
        and m["future_hours"] >= 24
        and m["invalid_time"] == 0
        and m["placeholder_pct"] <= 20
    )

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--gaps",default="reports/arab-epg-gaps.json")
    ap.add_argument("--raw-dir",default="output/arab-validation")
    ap.add_argument("--json",default="reports/arab-epg-valid.json")
    ap.add_argument("--md",default="reports/arab-epg-valid.md")
    a=ap.parse_args()

    gaps=json.loads(Path(a.gaps).read_text(encoding="utf-8"))
    by_site=defaultdict(dict)
    for item in gaps.get("missing",[]):
        for c in item.get("candidates",[]):
            site=c["site"]
            by_site[site][norm(c["xmltv_id"])] = c

    results=[]
    rawdir=Path(a.raw_dir)
    for site,cands in by_site.items():
        raw=rawdir/f"{site}.xml"
        if not raw.exists() or raw.stat().st_size<20:
            for c in cands.values():
                results.append({"status":"INVALID","reason":"NO_LIVE_XML","site":site,**c})
            continue
        try:
            root=ET.fromstring(raw.read_bytes())
        except Exception as exc:
            for c in cands.values():
                results.append({"status":"INVALID","reason":"BAD_XML","site":site,"error":str(exc)[:100],**c})
            continue
        ids={ (ch.get("id") or "").strip():ch for ch in root.findall("channel") }
        # Grabber output channel IDs should be xmltv IDs from our catalogue.
        for key,c in cands.items():
            xid=c["xmltv_id"]
            matched=None
            for cid in ids:
                if norm(cid)==key:
                    matched=cid; break
            if not matched:
                # Some grabbers omit <channel> but still emit programmes.
                for p in root.findall("programme"):
                    cid=(p.get("channel") or "").strip()
                    if norm(cid)==key:
                        matched=cid; break
            if not matched:
                results.append({"status":"INVALID","reason":"NO_EPG","site":site,**c})
                continue
            m=metrics(root,matched)
            if strict_valid(m):
                results.append({"status":"VALID","reason":"PASS","site":site,**c,"metrics":m})
            else:
                reason=[]
                if m["programmes"]<4: reason.append("TOO_FEW_PROGRAMMES")
                if m["future_hours"]<24: reason.append("SHORT_COVERAGE")
                if m["invalid_time"]>0: reason.append("INVALID_TIME")
                if m["placeholder_pct"]>20: reason.append("PLACEHOLDER_HEAVY")
                results.append({"status":"INVALID","reason":"+".join(reason) or "FAIL","site":site,**c,"metrics":m})

    # Keep a channel if any candidate site passed.
    grouped=defaultdict(list)
    for r in results:
        grouped[norm(r.get("xmltv_id"))].append(r)
    valid=[]; invalid=[]
    for key,rows in grouped.items():
        passed=[r for r in rows if r["status"]=="VALID"]
        if passed:
            passed.sort(key=lambda r:(r["metrics"]["future_hours"],r["metrics"]["programmes"],r["metrics"]["desc_pct"]),reverse=True)
            valid.append({"canonical":key,"winner":passed[0],"valid_candidates":passed})
        else:
            invalid.append({"canonical":key,"candidates":rows})

    valid.sort(key=lambda x:(x["winner"]["country"],x["winner"]["name"].casefold()))
    invalid.sort(key=lambda x:(x["candidates"][0]["country"],x["candidates"][0]["name"].casefold()))

    report={
        "validated_utc":datetime.now(timezone.utc).isoformat(),
        "strict_policy":{"min_programmes":4,"min_future_hours":24,"max_placeholder_pct":20,"invalid_time":0},
        "valid_channels":len(valid),
        "invalid_channels":len(invalid),
        "valid":valid,
        "invalid":invalid,
    }
    Path(a.json).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# Strict live EPG validation","",
           f"Valid: **{len(valid)}**  ","",
           f"Rejected: **{len(invalid)}**","",
           "| Country | Channel | Winner site | Programmes | Future h | Desc % |",
           "|---|---|---|---:|---:|---:|"]
    for x in valid:
        w=x["winner"]; m=w["metrics"]
        lines.append(f'| {w["country"]} | {w["name"].replace("|","/")} | {w["site"]} | {m["programmes"]} | {m["future_hours"]} | {m["desc_pct"]} |')
    Path(a.md).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"valid_channels":len(valid),"invalid_channels":len(invalid),
                      "valid":[{"country":x["winner"]["country"],"name":x["winner"]["name"],"site":x["winner"]["site"],"metrics":x["winner"]["metrics"]} for x in valid]},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
