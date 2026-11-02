#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a receiver-safe Vu+ test feed and source manifest from healthy direct feeds.

Only already-published XMLTV feeds are consumed. No scraping happens here.
A source is exposed to the Vu+ test manifest only when it has real channels,
future programmes, sane timestamps, and a minimum future horizon.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip, hashlib, json, re
import xml.etree.ElementTree as ET

FEEDS=Path("feeds")
REPORTS=Path("reports")
REPORTS.mkdir(exist_ok=True)

# Direct-source order. First owner wins when XMLTV IDs overlap.
PRIORITY=[
    "morocco",
    "bein",
    "dubaiplus",
    "shahid",
    "rotana",
    "sport24",
    "osn",
    "elcinema",
    "stctv",
]

LABELS={
    "morocco":"Morocco",
    "bein":"beIN MENA",
    "dubaiplus":"Dubai+",
    "shahid":"Shahid / MBC",
    "rotana":"Rotana",
    "sport24":"Sport24",
    "osn":"OSN",
    "elcinema":"ElCinema",
    "stctv":"STC TV",
    "starzplay":"STARZPLAY",
    "gobx":"GOBX",
}

# Conservative receiver-readiness gates. These are about safe import, not
# demanding a perfect 48h horizon from every event-driven channel.
POLICY={
    "morocco":   {"min_channels":5,  "min_programmes":50,  "min_future_ratio":0.70, "min_horizon_hours":4},
    "bein":      {"min_channels":8,  "min_programmes":50,  "min_future_ratio":0.65, "min_horizon_hours":4},
    "dubaiplus": {"min_channels":7,  "min_programmes":20,  "min_future_ratio":0.70, "min_horizon_hours":4},
    "shahid":    {"min_channels":20, "min_programmes":100, "min_future_ratio":0.60, "min_horizon_hours":4},
    "rotana":    {"min_channels":5,  "min_programmes":40,  "min_future_ratio":0.60, "min_horizon_hours":4},
    "sport24":   {"min_channels":2,  "min_programmes":10,  "min_future_ratio":0.60, "min_horizon_hours":3},
    "osn":       {"min_channels":20, "min_programmes":100, "min_future_ratio":0.80, "min_horizon_hours":4},
    "elcinema":  {"min_channels":20, "min_programmes":100, "min_future_ratio":0.70, "min_horizon_hours":4},
    "stctv":     {"min_channels":3,  "min_programmes":6,   "min_future_ratio":0.66, "min_horizon_hours":3},
}

DT_RE=re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")

def parse_dt(v):
    m=DT_RE.match((v or "").strip())
    if not m: return None
    s=m.group(1)
    dt=datetime.strptime(s,"%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M")
    off=m.group(2)
    if not off or off=="Z": return dt.replace(tzinfo=timezone.utc)
    sign=1 if off[0]=="+" else -1
    mins=sign*(int(off[1:3])*60+int(off[3:5]))
    return dt.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)

def read_root(path):
    raw=Path(path).read_bytes()
    if str(path).endswith(".gz") or raw[:2]==b"\x1f\x8b":
        raw=gzip.decompress(raw)
    return ET.fromstring(raw)

def clone(x):
    return ET.fromstring(ET.tostring(x,encoding="utf-8"))

def audit(key,path):
    try:
        root=read_root(path)
    except Exception as e:
        return None,[f"xml_error:{e}"]
    channels={}
    duplicate_ids=0
    for c in root.findall("channel"):
        cid=(c.get("id") or "").strip()
        if not cid: continue
        if cid in channels: duplicate_ids+=1
        channels[cid]=c
    now=datetime.now(timezone.utc)
    valid=defaultdict(int)
    future=defaultdict(int)
    latest=None
    programmes=0
    invalid=0
    orphan=0
    for p in root.findall("programme"):
        cid=(p.get("channel") or "").strip()
        if cid not in channels:
            orphan+=1
            continue
        st=parse_dt(p.get("start")); sp=parse_dt(p.get("stop"))
        if not st or not sp or sp<=st:
            invalid+=1
            continue
        programmes+=1
        valid[cid]+=1
        if sp>now:
            future[cid]+=1
            if latest is None or sp>latest: latest=sp
    active_channels=sum(1 for cid in channels if valid[cid]>0)
    future_channels=sum(1 for cid in channels if future[cid]>0)
    ratio=future_channels/active_channels if active_channels else 0.0
    horizon=max(0.0,(latest-now).total_seconds()/3600.0) if latest else 0.0
    stats={
        "channels":len(channels),
        "active_channels":active_channels,
        "zero_epg_channels":max(0,len(channels)-active_channels),
        "programmes":programmes,
        "future_channels":future_channels,
        "future_ratio":round(ratio,4),
        "future_horizon_hours":round(horizon,2),
        "invalid_rows":invalid,
        "orphan_programmes":orphan,
        "duplicate_ids":duplicate_ids,
    }
    p=POLICY[key]
    reasons=[]
    if stats["active_channels"]<p["min_channels"]: reasons.append(f"active_channels<{p['min_channels']}")
    if stats["programmes"]<p["min_programmes"]: reasons.append(f"programmes<{p['min_programmes']}")
    if ratio<p["min_future_ratio"]: reasons.append(f"future_ratio<{p['min_future_ratio']:.2f}")
    if horizon<p["min_horizon_hours"]: reasons.append(f"future_horizon<{p['min_horizon_hours']}h")
    if invalid: reasons.append(f"invalid_rows={invalid}")
    if orphan: reasons.append(f"orphan_programmes={orphan}")
    if duplicate_ids: reasons.append(f"duplicate_ids={duplicate_ids}")
    return (root,channels),reasons,stats

manifest={
    "generated_utc":datetime.now(timezone.utc).isoformat(),
    "mode":"vuplus-direct-safe-test",
    "repository":"wacayoub/EPGManager",
    "policy":"Keep complete per-source ID catalogues; validate EPG health on active channels; normalize receiver test timestamps to UTC.",
    "sources":[],
    "disabled_sources":[
        {"key":"starzplay","label":LABELS["starzplay"],"enabled":False,"reason":"parser currently returns 0 programmes; keep hidden until a real XMLTV feed passes validation"},
        {"key":"gobx","label":LABELS["gobx"],"enabled":False,"reason":"HTTP 403 / discovery pending; no empty feed exposed"},
    ],
}

healthy={}
for key in PRIORITY:
    path=FEEDS/f"{key}.xml.gz"
    row={
        "key":key,
        "label":LABELS[key],
        "enabled":False,
        "feed_url":f"https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/{key}.xml.gz",
        "id_index_url":f"https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/{key}.txt",
    }
    if not path.exists():
        row["reason"]="published feed missing"
        manifest["sources"].append(row)
        continue
    try:
        data,reasons,stats=audit(key,path)
        row["stats"]=stats
        if reasons:
            row["reason"]="; ".join(reasons)
        else:
            row["enabled"]=True
            row["reason"]="PASS"
            healthy[key]=data
    except Exception as e:
        row["reason"]=f"audit_error:{e}"
    manifest["sources"].append(row)

# Build one merged test feed so Vu+ can validate download/decompression/import/mapping
# in a single source before enabling all individual direct feeds.
channels={}
programmes=defaultdict(list)
owners={}
for key in PRIORITY:
    if key not in healthy: continue
    root,source_channels=healthy[key]
    by_ch=defaultdict(list)
    for p in root.findall("programme"):
        cid=(p.get("channel") or "").strip()
        if cid in source_channels:
            pp=clone(p)
            st=parse_dt(pp.get("start")); sp=parse_dt(pp.get("stop"))
            if st and sp and sp>st:
                pp.set("start",st.strftime("%Y%m%d%H%M%S +0000"))
                pp.set("stop",sp.strftime("%Y%m%d%H%M%S +0000"))
                by_ch[cid].append(pp)
    for cid,c in source_channels.items():
        if cid in channels: continue
        channels[cid]=clone(c)
        programmes[cid]=[clone(p) for p in by_ch.get(cid,[])]
        owners[cid]=key

root=ET.Element("tv",{
    "generator-info-name":"EPGManager Vu+ safe direct test",
    "generator-info-url":"https://github.com/wacayoub/EPGManager",
})
for cid in sorted(channels,key=str.casefold):
    root.append(channels[cid])
count=0
for cid in sorted(programmes,key=str.casefold):
    seen=set()
    for p in sorted(programmes[cid],key=lambda x:((x.get("start") or ""),(x.get("stop") or ""))):
        title="|".join((t.text or "").strip() for t in p.findall("title"))
        k=(p.get("start") or "",p.get("stop") or "",title)
        if k in seen: continue
        seen.add(k)
        root.append(p); count+=1
ET.indent(root,space="  ")
xml=ET.tostring(root,encoding="utf-8",xml_declaration=True)
gz=gzip.compress(xml,compresslevel=9,mtime=0)
(FEEDS/"vuplus-test.xml.gz").write_bytes(gz)
(FEEDS/"vuplus-test.txt").write_text(
    "\n".join(f"{cid}|{next(((d.text or '').strip() for d in channels[cid].findall('display-name') if (d.text or '').strip()),cid)}" for cid in sorted(channels,key=str.casefold))+"\n",
    encoding="utf-8",
)
manifest["merged_test_feed"]={
    "enabled":bool(channels and count),
    "feed_url":"https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/vuplus-test.xml.gz",
    "id_index_url":"https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/vuplus-test.txt",
    "channels":len(channels),
    "programmes":count,
    "owners":owners,
    "size_bytes":len(gz),
    "sha256":hashlib.sha256(gz).hexdigest(),
}
(FEEDS/"vuplus-test.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

enabled=[s for s in manifest["sources"] if s["enabled"]]
disabled=[s for s in manifest["sources"] if not s["enabled"]]
md=[
    "# Vu+ direct EPG test readiness","",
    f"Generated: {manifest['generated_utc']}","",
    f"Ready direct sources: **{len(enabled)}/{len(manifest['sources'])}**  ",
    f"Merged test feed: **{len(channels)} channels / {count} programmes**","",
    "| Source | Ready | IDs | Active | Programmes | Future(active) | Horizon | Reason |",
    "|---|---:|---:|---:|---:|---:|---:|---|",
]
for s in manifest["sources"]:
    st=s.get("stats",{})
    md.append(f"| {s['label']} | {'YES' if s['enabled'] else 'NO'} | {st.get('channels','-')} | {st.get('active_channels','-')} | {st.get('programmes','-')} | {('%0.0f%%'%(100*st.get('future_ratio',0))) if st else '-'} | {st.get('future_horizon_hours','-')}h | {s.get('reason','')} |")
md += ["","## Intentionally disabled for first Vu+ test","",
       "- STARZPLAY — parser currently produces no real programmes.",
       "- GOBX — access/discovery is still unresolved.",
       "","Only sources marked YES are present in the merged Vu+ test feed."]
(REPORTS/"vuplus-test-readiness.md").write_text("\n".join(md)+"\n",encoding="utf-8")
print("\n".join(md))
if not channels or not count:
    raise SystemExit(3)
