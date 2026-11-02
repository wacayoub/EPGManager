#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,html,json,re
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import xml.etree.ElementTree as ET

DT_RE=re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")

def parse_dt(v):
    """Parse XMLTV timestamps to UTC without discarding provider offsets."""
    m=DT_RE.match((v or "").strip())
    if not m:return None
    s=m.group(1);fmt="%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M"
    try:
        dt=datetime.strptime(s,fmt)
        off=m.group(2)
        if not off or off=="Z":
            return dt.replace(tzinfo=timezone.utc)
        sign=1 if off[0]=="+" else -1
        mins=sign*(int(off[1:3])*60+int(off[3:5]))
        return dt.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)
    except Exception:
        return None

def read_root(path):
    raw=Path(path).read_bytes()
    if str(path).endswith(".gz") or raw[:2]==b"\x1f\x8b":raw=gzip.decompress(raw)
    return ET.fromstring(raw)

def feed_rows(path):
    root=read_root(path);now=datetime.now(timezone.utc)
    programmes=Counter();future=Counter();latest={};desc=Counter();exact=Counter();bad=Counter()
    channels=[]
    channel_count=Counter()
    for ch in root.findall("channel"):
        cid=(ch.get("id") or "").strip()
        if not cid:continue
        channel_count[cid]+=1
        names=[(x.text or "").strip() for x in ch.findall("display-name") if (x.text or "").strip()]
        channels.append((cid,names[0] if names else cid))
    for p in root.findall("programme"):
        cid=(p.get("channel") or "").strip();programmes[cid]+=1
        st=parse_dt(p.get("start"));sp=parse_dt(p.get("stop"))
        title="|".join((t.text or "").strip() for t in p.findall("title"))
        exact[(cid,p.get("start") or "",p.get("stop") or "",title)]+=1
        if not st or not sp or sp<=st:
            bad[cid]+=1;continue
        if sp>now:
            future[cid]+=1;latest[cid]=max(latest.get(cid,sp),sp)
            if any((d.text or "").strip() for d in p.findall("desc")):desc[cid]+=1
    rows=[];feed=Path(path).name.replace(".xml.gz","")
    for cid,name in channels:
        fh=max(0.0,(latest[cid]-now).total_seconds()/3600) if cid in latest else 0.0
        rows.append({
            "provider":"direct","source":feed,"id":cid,"name":name,"country":"",
            "programmes":programmes[cid],"future_programmes":future[cid],"future_hours":round(fh,1),
            "desc_pct":round(100*desc[cid]/future[cid],1) if future[cid] else 0.0,
            "status":"OK" if future[cid]>0 else "NO_EPG",
            "reason":"" if future[cid]>0 else "ZERO_FUTURE_EPG",
            "duplicate_group":"YES" if channel_count[cid]>1 else "",
            "winner":"DIRECT","covered_by":"","integration_status":"DIRECT","url":"",
            "invalid_times":bad[cid]
        })
    return rows,{
        "feed":Path(path).name,"channels":len(channels),"programmes":sum(programmes.values()),
        "dup_ids":sum(1 for v in channel_count.values() if v>1),
        "dup_programmes":sum(v-1 for v in exact.values() if v>1),
        "invalid_times":sum(bad.values()),
        "zero_epg":sum(1 for r in rows if r["future_programmes"]==0)
    }

def load_csv(path):
    p=Path(path)
    if not p.exists():return []
    return list(csv.DictReader(p.open(encoding="utf-8")))

def load_json(path,default):
    p=Path(path)
    if not p.exists():return default
    try:return json.loads(p.read_text(encoding="utf-8"))
    except:return default

def simple_canonical(s):
    s=(s or "").casefold()
    s=re.sub(r"\b(?:hd|sd|fhd|uhd|4k|tv|channel)\b"," ",s)
    s=re.sub(r"[._:/\\-]+"," ",s)
    return " ".join(s.split())

def build_all_rows(feed_paths,reports):
    direct=[];summaries=[]
    for p in feed_paths:
        if Path(p).exists():
            r,s=feed_rows(p);direct+=r;summaries.append(s)

    zero={(r.get("provider",""),r.get("source",""),r.get("id","")):r.get("reason","ZERO_FUTURE_EPG")
          for r in load_csv(reports/"arab-fallback-zero-epg-blacklist.csv")}
    source_black={(r.get("provider",""),r.get("source","")):(r.get("reason",""),r.get("detail",""))
                  for r in load_json(reports/"arab-fallback-source-blacklist.json",{}).get("sources",[])}
    duplicate_rows=load_csv(reports/"arab-fallback-duplicate-decisions.csv")
    dup_by_id={}
    for d in duplicate_rows:
        wk=(d.get("winner_provider",""),d.get("winner_source",""),d.get("winner_id",""))
        dup_by_id[wk]=("WINNER",d)
        for alt in (d.get("alternatives") or "").split(";"):
            bits=alt.split(":")
            if len(bits)>=3:
                dup_by_id[(bits[0],bits[1],bits[2])]=("DUPLICATE",d)

    covered={(r.get("provider",""),r.get("source",""),r.get("id","")):r.get("covered_by","")
             for r in load_csv(reports/"arab-fallback-already-covered.csv")}
    new_rows=load_csv(reports/"new-arab-epg-ids.csv")
    compare={(r.get("provider",""),r.get("source",""),r.get("id","")):r
             for r in new_rows if r.get("integration_status")=="COMPARE_ONLY"}

    rows=list(direct)
    all_fallback=load_csv(reports/"arab-fallback-all-channels.csv")
    for r in all_fallback:
        provider=r.get("provider","");source=r.get("source","");cid=r.get("id","");key=(provider,source,cid)
        fp=int(float(r.get("future_programmes") or 0));fh=float(r.get("future_hours") or 0);dp=float(r.get("desc_pct") or 0)
        status="OK";reason="";winner=""
        if (provider,source) in source_black:
            reason,detail=source_black[(provider,source)]
            status="QUARANTINE" if "QUARANTIN" in reason else "INVALID_SOURCE"
            reason=(reason+" "+detail).strip()
        elif key in zero or fp<=0:
            status="BLACKLIST_ZERO";reason=zero.get(key,"ZERO_FUTURE_EPG")
        elif key in covered:
            status="DIRECT_COVERED";reason="Already covered by healthy direct feed"
        elif key in dup_by_id:
            kind,d=dup_by_id[key]
            if kind=="WINNER":
                status="DUPLICATE_WINNER";winner="YES";reason="Winner of duplicate group"
            else:
                status="DUPLICATE";reason="Alternative/duplicate candidate"
        if key in compare:
            status="COMPARE_ONLY";reason="Discovery source awaiting validation"
        rows.append({
            "provider":provider,"source":source,"id":cid,"name":r.get("name",""),"country":r.get("country",""),
            "programmes":fp,"future_programmes":fp,"future_hours":round(fh,1),"desc_pct":round(dp,1),
            "status":status,"reason":reason,"duplicate_group":"YES" if status.startswith("DUPLICATE") else "",
            "winner":winner,"covered_by":covered.get(key,""),
            "integration_status":compare.get(key,{}).get("integration_status","FALLBACK"),
            "url":r.get("url",""),"invalid_times":0
        })

    seen={(x["provider"],x["source"],x["id"]) for x in rows}
    for key,r in compare.items():
        if key in seen:continue
        rows.append({
            "provider":r.get("provider",""),"source":r.get("source",""),"id":r.get("id",""),
            "name":r.get("name",""),"country":r.get("country",""),
            "programmes":int(float(r.get("future_programmes") or 0)),
            "future_programmes":int(float(r.get("future_programmes") or 0)),
            "future_hours":float(r.get("future_hours") or 0),"desc_pct":float(r.get("desc_pct") or 0),
            "status":"COMPARE_ONLY","reason":"Discovery source awaiting validation",
            "duplicate_group":"","winner":"","covered_by":"","integration_status":"COMPARE_ONLY",
            "url":r.get("url",""),"invalid_times":0
        })
    rows.sort(key=lambda x:(x["status"],x["provider"],x["source"],x["name"].casefold(),x["id"].casefold()))
    return rows,summaries

def make_html(rows,summaries,generated):
    counts=Counter(r["status"] for r in rows)
    buttons=["ALL","OK","BLACKLIST_ZERO","DUPLICATE","DUPLICATE_WINNER","NO_EPG","INVALID_SOURCE","QUARANTINE","DIRECT_COVERED","COMPARE_ONLY"]
    cards="".join("<button class=\"chip\" onclick=\"setStatus('"+b+"')\">"+html.escape(b.replace("_"," "))+" <b>"+str(len(rows) if b=="ALL" else counts.get(b,0))+"</b></button>" for b in buttons)
    feedcards="".join('<div class="mini"><b>'+html.escape(s["feed"])+'</b><span>'+str(s["channels"])+' IDs · '+str(s["programmes"])+' programmes · '+str(s["zero_epg"])+' zero</span></div>' for s in summaries)
    trs=[]
    for r in rows:
        url=html.escape(r["url"]);name=html.escape(r["name"]);cid=html.escape(r["id"])
        if url:name='<a href="'+url+'" target="_blank" rel="noopener">'+name+'</a>'
        trs.append(
            '<tr data-status="'+html.escape(r["status"])+'">'+
            '<td><span class="badge '+html.escape(r["status"])+'">'+html.escape(r["status"])+'</span></td>'+
            '<td>'+html.escape(r["country"])+'</td><td>'+html.escape(r["provider"])+'</td><td>'+html.escape(r["source"])+'</td>'+
            '<td>'+name+'</td><td class="mono">'+cid+'</td><td>'+str(r["future_programmes"])+'</td><td>'+str(r["future_hours"])+'</td>'+
            '<td>'+str(r["desc_pct"])+'%</td><td>'+html.escape(r["winner"])+'</td><td>'+html.escape(r["covered_by"])+'</td>'+
            '<td>'+html.escape(r["reason"])+'</td></tr>'
        )
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EPG Source ID Monitoring</title><style>
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#0b1020;color:#e8ecf6}header{padding:22px 28px;background:#111936;position:sticky;top:0;z-index:2}
h1{margin:0 0 4px;font-size:24px}.sub{color:#9eabd0}.wrap{padding:20px 28px}.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.chip{background:#1a2448;color:#fff;border:1px solid #33416f;border-radius:18px;padding:7px 12px;cursor:pointer}.chip:hover{background:#263463}
input{width:min(560px,95%);padding:10px 12px;border-radius:10px;border:1px solid #34436e;background:#111a35;color:#fff}.feeds{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.mini{background:#121a34;border:1px solid #26345e;border-radius:10px;padding:10px 12px}.mini span{display:block;color:#9eabd0;font-size:12px;margin-top:3px}
table{border-collapse:collapse;width:100%;font-size:13px;background:#10172e}th,td{padding:8px;border-bottom:1px solid #222d50;text-align:left;vertical-align:top}th{position:sticky;top:116px;background:#17213f;z-index:1}
.mono{font-family:ui-monospace,monospace;font-size:12px}.badge{padding:3px 7px;border-radius:10px;font-size:11px;white-space:nowrap}.OK,.DUPLICATE_WINNER{background:#153d2b}.BLACKLIST_ZERO,.NO_EPG,.INVALID_SOURCE{background:#5a2028}
.DUPLICATE{background:#594515}.QUARANTINE{background:#61275c}.DIRECT_COVERED{background:#214b61}.COMPARE_ONLY{background:#41356a}a{color:#9ec5ff}.hidden{display:none}</style></head><body>
<header><h1>EPG Source ID Monitoring</h1><div class="sub">Generated """+html.escape(generated)+""" · All direct + fallback + discovery IDs</div>
<div class="chips">"""+cards+"""</div><input id="q" placeholder="Search channel, ID, source, reason…" oninput="apply()"></header>
<div class="wrap"><div class="feeds">"""+feedcards+"""</div><table><thead><tr><th>Status</th><th>Country</th><th>Provider</th><th>Source</th><th>Channel</th><th>ID</th><th>Future</th><th>Hours</th><th>Desc</th><th>Winner</th><th>Covered by</th><th>Reason</th></tr></thead><tbody>"""+"".join(trs)+"""</tbody></table></div>
<script>let status='ALL';function setStatus(s){status=s;apply()}function apply(){let q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>{let ok=(status==='ALL'||r.dataset.status===status)&&(!q||r.innerText.toLowerCase().includes(q));r.classList.toggle('hidden',!ok)})}</script>
</body></html>"""

def main():
    ap=argparse.ArgumentParser();ap.add_argument("feeds",nargs="+");ap.add_argument("--json",required=True);ap.add_argument("--md",required=True)
    ap.add_argument("--html");ap.add_argument("--csv");ap.add_argument("--reports-dir",default="reports")
    a=ap.parse_args();reports=Path(a.reports_dir)
    rows,summaries=build_all_rows(a.feeds,reports);generated=datetime.now(timezone.utc).isoformat()
    out={"generated_utc":generated,"summary":{"total_ids":len(rows),"status_counts":dict(Counter(r["status"] for r in rows)),"feeds":summaries},"ids":rows}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    md=["# EPG Source ID Monitoring","",f"Generated: {generated}","",f"Total monitored IDs: **{len(rows)}**","",
        "| Status | IDs |","|---|---:|"]
    for k,v in sorted(Counter(r["status"] for r in rows).items()):md.append(f"| {k} | {v} |")
    md+=["","## Direct feeds","","| Feed | IDs | Programmes | Dup IDs | Dup programmes | Invalid times | Zero EPG |","|---|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:md.append(f'| {s["feed"]} | {s["channels"]} | {s["programmes"]} | {s["dup_ids"]} | {s["dup_programmes"]} | {s["invalid_times"]} | {s["zero_epg"]} |')
    md+=["","Interactive/filterable page: reports/epg-source-id-monitoring.html"]
    Path(a.md).write_text("\n".join(md)+"\n",encoding="utf-8")
    if a.csv:
        fields=["status","country","provider","source","name","id","future_programmes","future_hours","desc_pct","winner","covered_by","reason","integration_status","url"]
        with Path(a.csv).open("w",newline="",encoding="utf-8") as fh:
            w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
            for r in rows:w.writerow({k:r.get(k,"") for k in fields})
    if a.html:Path(a.html).write_text(make_html(rows,summaries,generated),encoding="utf-8")
    return 0

if __name__=="__main__":raise SystemExit(main())
