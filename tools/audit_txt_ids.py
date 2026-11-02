#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv,re,unicodedata,gzip
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
import xml.etree.ElementTree as ET

FEEDS=("morocco","bein","osn","sport24","elcinema","others")

def clean(s):
    return re.sub(r"\s+"," ",(s or "").strip())

def latin(s):
    s=unicodedata.normalize("NFKD",s or "")
    s="".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()

def norm(s):
    s=latin(s).replace("&"," and ").replace("+"," plus ")
    s=re.sub(r"\b(?:hd|sd|fhd|uhd|4k|1080p|720p|tv|channel|middleeast|mena|arabia|arabic)\b"," ",s)
    s=re.sub(r"\.(?:sa|ae|eg|qa|ma|jo|lb|iq|om|tn|kw|bh|uk|us|ir|sy|dz|ye|net)\b"," ",s)
    s=re.sub(r"[^a-z0-9\u0600-\u06ff]+"," ",s)
    return "".join(s.split())

ALIASES={
 "alaoula":"alaoula",
 "الأولىالمغربية":"alaoula",
 "rotanacinemaksa":"rotanacinemaksa",
 "روتاناسينما":"rotanacinemaksa",
 "rotanacinemaegypt":"rotanacinemaegypt",
 "روتاناسينمامصر":"rotanacinemaegypt",
 "rotanaclassic":"rotanaclassic",
 "روتاناكلاسيك":"rotanaclassic",
 "rotanacomedy":"rotanacomedy",
 "روتاناكوميدي":"rotanacomedy",
 "rotanadrama":"rotanadrama",
 "روتانادراما":"rotanadrama",
 "rotanakhalijia":"rotanakhalijia",
 "روتاناخليجية":"rotanakhalijia",
 "alresalah":"alresalah",
 "الرسالة":"alresalah",
 "omantv":"omantv",
 "عمان":"omantv",
 "echorouktv":"echorouktv",
 "الشروقالجزائرية":"echorouktv",
 "hadhramauttv":"hadhramauttv",
 "تلفزيونحضرموت":"hadhramauttv",
 "aliraqia":"iraqiatv",
 "iraqiatv":"iraqiatv",
 "العراقية":"iraqiatv",
 "nessma":"nessma",
 "نسمة":"nessma",
 "ten":"ten",
 "تن":"ten",
 "utv":"utv",
 "يوتيڤي":"utv",
 "يوتي في":"utv",
 "bloomberg":"bloomberg",
 "bloomberg1":"bloomberg",
 "hgtv":"hgtv",
 "foodnetwork":"foodnetwork",
 "bbcarabic":"bbcarabic",
 "bbcnewsarabic":"bbcarabic",
 "cnninternational":"cnn",
 "cnnhd":"cnn",
 "cnn":"cnn",
 "cartoonnetworkarabic":"cartoonnetworkarabic",
 "cnarabia":"cartoonnetworkarabic",
 "discoveryid":"investigationdiscovery",
 "investigationdiscovery":"investigationdiscovery",
}

def canon(name,cid):
    vals=[norm(name),norm(cid)]
    for v in vals:
        if v in ALIASES:return ALIASES[v]
    # prefer non-generic name key
    n=vals[0]
    if len(n)>=4 and n not in {"news","sport","sports","cinema","movies","movie","one","drama","classic"}:
        return n
    return vals[1]

def read_txt(path,source):
    rows=[]
    if not path.exists():return rows
    for line in path.read_text(encoding="utf-8",errors="ignore").splitlines():
        if not line.strip():continue
        parts=[x.strip() for x in line.split("|")]
        cid=parts[0]
        name=parts[1] if len(parts)>1 else cid
        origin=parts[2] if len(parts)>2 else source
        rows.append({"source":source,"origin":origin,"id":cid,"name":name,"canon":canon(name,cid)})
    return rows

def parse_dt(v):
    raw=(v or "").strip()
    m=re.match(r"^(\d{12}|\d{14})",raw)
    if not m:return None
    s=m.group(1)
    fmt="%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M"
    try:return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
    except Exception:return None

def load_programme_meta(source):
    p=Path("feeds")/f"{source}.xml.gz"
    out={}
    if not p.exists():return out
    try:
        root=ET.fromstring(gzip.decompress(p.read_bytes()))
    except Exception:
        return out
    now=datetime.now(timezone.utc)
    by=defaultdict(list)
    for pr in root.findall("programme"):
        cid=(pr.get("channel") or "").strip()
        if not cid:continue
        st=parse_dt(pr.get("start"));sp=parse_dt(pr.get("stop"))
        if not st or not sp or sp<=now:continue
        title=""
        title_lang=""
        for t in pr.findall("title"):
            txt=clean(t.text or "")
            if txt:
                title=txt;title_lang=t.get("lang") or "";break
        desc=""
        desc_lang=""
        for d in pr.findall("desc"):
            txt=clean(d.text or "")
            if txt:
                desc=txt;desc_lang=d.get("lang") or "";break
        by[cid].append((st,sp,title,desc,title_lang,desc_lang))
    for cid,events in by.items():
        events.sort(key=lambda x:x[0])
        current=next((e for e in events if e[0]<=now<e[1]),None)
        chosen=current or next((e for e in events if e[0]>=now),events[0])
        last=max(e[1] for e in events)
        out[cid]={
            "programme":chosen[2],
            "description":chosen[3],
            "title_lang":chosen[4],
            "desc_lang":chosen[5],
            "start":chosen[0].isoformat(),
            "stop":chosen[1].isoformat(),
            "future_programmes":len(events),
            "future_hours":round(max(0.0,(last-now).total_seconds()/3600),1),
        }
    return out

PROGRAMME_META={s:load_programme_meta(s) for s in FEEDS}

def enrich(r):
    m=PROGRAMME_META.get(r["source"],{}).get(r["id"],{})
    x=dict(r)
    x.update({
        "programme":m.get("programme",""),
        "description":m.get("description",""),
        "title_lang":m.get("title_lang",""),
        "desc_lang":m.get("desc_lang",""),
        "programme_start":m.get("start",""),
        "programme_stop":m.get("stop",""),
        "future_programmes":m.get("future_programmes",0),
        "future_hours":m.get("future_hours",0.0),
    })
    return x

def similarity(a,b):
    # token-lite compact similarity; exact canonical handled separately
    a=norm(a);b=norm(b)
    if not a or not b:return 0.0
    if a==b:return 1.0
    # containment only for reasonably long names; avoids "MBC Drama" vs "+" false collapse
    if len(a)>=7 and len(b)>=7 and (a in b or b in a):
        short=min(len(a),len(b));long=max(len(a),len(b))
        return short/long
    aset=set(re.findall(r"[a-z]+|[0-9]+|[\u0600-\u06ff]+",latin(a)))
    bset=set(re.findall(r"[a-z]+|[0-9]+|[\u0600-\u06ff]+",latin(b)))
    return len(aset&bset)/len(aset|bset) if aset and bset else 0.0

rows=[]
for s in FEEDS: rows+=[enrich(x) for x in read_txt(Path("feeds")/f"{s}.txt",s)]

by=defaultdict(list)
for r in rows:by[r["canon"]].append(r)

certain=[]
for key,opts in sorted(by.items()):
    if len(opts)<2 or len({x["source"] for x in opts})<2:continue
    certain.append((key,opts))

# Additional fuzzy review pairs not already exact canonical matches
review=[]
for i,a in enumerate(rows):
    for b in rows[i+1:]:
        if a["source"]==b["source"]:continue
        if a["canon"]==b["canon"]:continue
        sim=max(similarity(a["name"],b["name"]),similarity(a["id"],b["id"]))
        if sim>=0.72:
            review.append((sim,a,b))

review.sort(key=lambda x:x[0],reverse=True)

out=Path("reports");out.mkdir(exist_ok=True)
with (out/"txt-id-duplicate-audit.csv").open("w",newline="",encoding="utf-8") as f:
    fields=["type","canonical","similarity","source","origin","id","name","programme","description","title_lang","desc_lang","programme_start","programme_stop","future_programmes","future_hours","other_source","other_origin","other_id","other_name","other_programme","other_description","other_title_lang","other_desc_lang","other_programme_start","other_programme_stop","other_future_programmes","other_future_hours"]
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for key,opts in certain:
        for a in opts:
            others=[x for x in opts if x is not a]
            for b in others[:1]:
                w.writerow({"type":"CERTAIN","canonical":key,"similarity":"1.00",
                  "source":a["source"],"origin":a["origin"],"id":a["id"],"name":a["name"],
                  "programme":a["programme"],"description":a["description"],"title_lang":a["title_lang"],"desc_lang":a["desc_lang"],
                  "programme_start":a["programme_start"],"programme_stop":a["programme_stop"],"future_programmes":a["future_programmes"],"future_hours":a["future_hours"],
                  "other_source":b["source"],"other_origin":b["origin"],"other_id":b["id"],"other_name":b["name"],
                  "other_programme":b["programme"],"other_description":b["description"],"other_title_lang":b["title_lang"],"other_desc_lang":b["desc_lang"],
                  "other_programme_start":b["programme_start"],"other_programme_stop":b["programme_stop"],"other_future_programmes":b["future_programmes"],"other_future_hours":b["future_hours"]})
    for sim,a,b in review:
        w.writerow({"type":"REVIEW","canonical":"","similarity":f"{sim:.2f}",
          "source":a["source"],"origin":a["origin"],"id":a["id"],"name":a["name"],
          "programme":a["programme"],"description":a["description"],"title_lang":a["title_lang"],"desc_lang":a["desc_lang"],
          "programme_start":a["programme_start"],"programme_stop":a["programme_stop"],"future_programmes":a["future_programmes"],"future_hours":a["future_hours"],
          "other_source":b["source"],"other_origin":b["origin"],"other_id":b["id"],"other_name":b["name"],
          "other_programme":b["programme"],"other_description":b["description"],"other_title_lang":b["title_lang"],"other_desc_lang":b["desc_lang"],
          "other_programme_start":b["programme_start"],"other_programme_stop":b["programme_stop"],"other_future_programmes":b["future_programmes"],"other_future_hours":b["future_hours"]})

md=["# TXT ID duplicate audit","",f"TXT IDs scanned: **{len(rows)}**",f"Certain cross-source duplicate groups: **{len(certain)}**",f"Review pairs: **{len(review)}**","",
"## Certain duplicate groups","",
"| Canonical | Entries |","|---|---|"]
for key,opts in certain:
    md.append("| "+key+" | "+" ; ".join(f'{x["source"]}:{x["id"]} ({x["name"]})' for x in opts)+" |")
md+=["","## Top review candidates","","| Similarity | A | B |","|---:|---|---|"]
for sim,a,b in review[:100]:
    md.append(f'| {sim:.2f} | {a["source"]}:{a["id"]} — {a["name"]} | {b["source"]}:{b["id"]} — {b["name"]} |')
(out/"txt-id-duplicate-audit.md").write_text("\n".join(md)+"\n",encoding="utf-8")
print({"ids":len(rows),"certain_groups":len(certain),"review_pairs":len(review)})
