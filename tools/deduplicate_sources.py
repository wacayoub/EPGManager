#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-source duplicate arbitration for published EPG feeds.

Works only on existing feeds/*.xml.gz. It never scrapes upstream sites.
For each logical channel appearing in more than one source, it compares:
- source speciality/directness;
- Arabic title/description quality;
- description coverage;
- future coverage and event count;
then keeps one winner and removes the losers from lower-quality feeds.

A full JSON/Markdown audit is written before publication.
"""
from __future__ import annotations
import argparse, gzip, json, re, shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

SOURCES=("morocco","bein","osn","sport24","elcinema","others")
AR=re.compile(r"[\u0600-\u06FF]")

def read_root(path:Path):
    b=path.read_bytes()
    if b[:2]==b"\x1f\x8b": b=gzip.decompress(b)
    return ET.fromstring(b)

def write_root(path:Path,root):
    ET.indent(root,space="  ")
    b=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(gzip.compress(b,9,mtime=0))

def text_of(node,tag):
    for x in node.findall(tag):
        t=(x.text or "").strip()
        if t: return t
    return ""

def norm_name(s):
    s=(s or "").casefold().replace("+"," plus ")
    s=re.sub(r"\b(hd|sd|uhd|4k|tv|channel)\b"," ",s)
    s=re.sub(r"[^a-z0-9\u0600-\u06ff]+","",s)
    return s

def canonical_id(cid,name):
    # Cross-source duplicate key: prefer normalized channel name so different
    # XMLTV IDs for the same real channel collapse into one group.
    n=norm_name(name)
    aliases={
        "adsport":"abudhabisport",
        "abudhabisports":"abudhabisport",
        "bbcarabicnews":"bbcarabic",
        "bbcnewsarabic":"bbcarabic",
        "blomberg1":"bloomberg",
        "bloomberg1":"bloomberg",
        "cnnhd":"cnn",
        "cnninternational":"cnn",
        "cartoonnetworkarabic1":"cartoonnetworkarabic",
        "movies1premier":"beinmovies1",
        "beinmovies1premier":"beinmovies1",
        "movies2action":"beinmovies2",
        "beinmovies2action":"beinmovies2",
        "beinsports1english":"beinsportsen1",
        "beinsportsen1":"beinsportsen1",
    }
    n=aliases.get(n,n)
    if len(n)>=4 and n not in {"news","sport","sports","movie","movies","cinema","arabic","english"}:
        return "name:"+n
    base=(cid or "").split("@",1)[0].casefold()
    base=re.sub(r"\.(?:sa|ae|eg|qa|ma|net|mena)$","",base)
    base=norm_name(base)
    return "id:"+aliases.get(base,base)

def language_profile(canon,name):
    """User-approved language policy.

    arabic_native: Arabic title + Arabic description
    foreign_subtitled: English/original title + Arabic description
    international: English title + Arabic description
    """
    c=(canon or "").casefold()
    n=(name or "").casefold()

    foreign_subtitled_tokens=(
        "beinmovies","beinseries","beindrama",
        "mbc2","mbcmax","mbcaction","mbcbollywood",
        "dubai1","dubaione","osnmovies","osnshowcase","osncomedy",
        "osnkids","cartoonnetworkarabic"
    )
    if any(x in c for x in foreign_subtitled_tokens):
        return "foreign_subtitled"

    international_tokens=(
        "cnn","bloomberg","animalplanet","discoverychannel","history",
        "tlc","ginx","motorvision","mfmtv","rfmtv","rt.ru",
        "foodnetwork","hgtv","mtv80s","mtv90s","clubmtv"
    )
    if any(x in c for x in international_tokens):
        return "international"

    return "arabic_native"


def specialty_bonus(source,canon):
    # Direct/official providers must beat generic fallback when both represent
    # the same real channel.
    c=canon.casefold()
    if source=="others":
        return 1
    if source=="morocco" and any(x in c for x in ("alaoula","arryadia","2m","medi1","tamazight","assadisa","almaghribiya","arrabiaa","aflam","chada")):
        return 6
    if source=="bein" and c.startswith("beinsports"):
        return 6
    if source=="osn" and c.startswith("osn"):
        return 6
    if source=="sport24" and any(x in c for x in ("abudhabisports","dubaisports","thmanyah")):
        return 6
    return 0

def metrics(root,cid):
    now=datetime.now(timezone.utc)
    rows=[p for p in root.findall("programme") if (p.get("channel") or "")==cid]
    titles=[text_of(p,"title") for p in rows]
    descs=[text_of(p,"desc") for p in rows]
    def pct(vals,pred):
        vals=[v for v in vals if v]
        return round(100*sum(1 for v in vals if pred(v))/len(vals),1) if vals else 0.0
    future=0
    last=None
    for p in rows:
        raw=(p.get("stop") or p.get("start") or "")[:14]
        try:
            dt=datetime.strptime(raw,"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            if dt>now: future+=1
            if last is None or dt>last: last=dt
        except Exception: pass
    future_hours=max(0.0,(last-now).total_seconds()/3600) if last else 0.0
    return {
        "programmes":len(rows),
        "future_programmes":future,
        "future_hours":round(future_hours,1),
        "desc_pct":round(100*sum(1 for d in descs if d)/len(rows),1) if rows else 0.0,
        "arabic_title_pct":pct(titles,lambda x: bool(AR.search(x))),
        "arabic_desc_pct":pct(descs,lambda x: bool(AR.search(x))),
    }

def language_fit(profile,m):
    title_ar=m["arabic_title_pct"]
    desc_ar=m["arabic_desc_pct"]
    if profile=="foreign_subtitled":
        # Original/English title + Arabic description.
        title_fit=100-title_ar
        desc_fit=desc_ar
    elif profile=="international":
        # Multinational/international channel: English title + Arabic description.
        title_fit=100-title_ar
        desc_fit=desc_ar
    else:
        # Pure Arabic channel.
        title_fit=title_ar
        desc_fit=desc_ar
    return round((title_fit+desc_fit)/2,1)


def score(source,canon,name,m):
    profile=language_profile(canon,name)
    fit=language_fit(profile,m)
    # User rule: language correctness first, then description completeness and
    # future coverage. Source directness is only a tie-breaker.
    return round(
        fit/100*44
        + m["desc_pct"]/100*20
        + min(m["future_hours"],48)/48*18
        + min(m["future_programmes"],40)/40*12
        + specialty_bonus(source,canon),
        2
    )

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--feeds-dir",default="feeds")
    ap.add_argument("--output-dir",default="output/dedup")
    ap.add_argument("--report-json",default="reports/source-winners.json")
    ap.add_argument("--report-md",default="reports/source-winners.md")
    a=ap.parse_args()
    feeds=Path(a.feeds_dir); out=Path(a.output_dir)
    roots={}
    channel_nodes={}
    entries=defaultdict(list)

    for source in SOURCES:
        p=feeds/f"{source}.xml.gz"
        if not p.exists(): continue
        root=read_root(p); roots[source]=root
        channel_nodes[source]={}
        for ch in root.findall("channel"):
            cid=(ch.get("id") or "").strip()
            if not cid: continue
            name=text_of(ch,"display-name") or cid
            channel_nodes[source][cid]=ch
            canon=canonical_id(cid,name)
            m=metrics(root,cid)
            profile=language_profile(canon,name)
            entries[canon].append({
                "source":source,"id":cid,"name":name,
                "profile":profile,
                "language_fit_pct":language_fit(profile,m),
                "metrics":m,"score":score(source,canon,name,m),
                "specialty_bonus":specialty_bonus(source,canon),
            })

    duplicates=[]
    losers=defaultdict(set)
    winners={}
    for canon,opts in sorted(entries.items()):
        if len(opts)<2: continue
        # Same-source lookalikes (for example MBC Drama vs MBC Drama Plus)
        # are not cross-source duplicates and must remain separate.
        if len({x["source"] for x in opts})<2:
            continue
        priority={"morocco":100,"bein":100,"osn":100,"sport24":100,"elcinema":80,"others":50}
        ranked=sorted(opts,key=lambda x:(priority.get(x["source"],0),x["score"],x["metrics"]["future_programmes"],x["metrics"]["programmes"]),reverse=True)
        winner=ranked[0]
        winners[canon]=winner
        for x in ranked[1:]: losers[x["source"]].add(x["id"])
        duplicates.append({"canonical":canon,"winner":winner,"candidates":ranked})

    # Write pruned copies of every existing source feed.
    for source,root in roots.items():
        drop=losers[source]
        if drop:
            for ch in list(root.findall("channel")):
                if (ch.get("id") or "") in drop: root.remove(ch)
            for p in list(root.findall("programme")):
                if (p.get("channel") or "") in drop: root.remove(p)
        write_root(out/f"{source}.xml.gz",root)

    report={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "duplicate_groups":len(duplicates),
        "losers_removed":{s:len(v) for s,v in losers.items() if v},
        "duplicates":duplicates,
    }
    Path(a.report_json).parent.mkdir(parents=True,exist_ok=True)
    Path(a.report_json).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    lines=["# Cross-source EPG winners","",f"Duplicate groups: **{len(duplicates)}**","",
           "| Channel | Winner | Score | Other sources |","|---|---|---:|---|"]
    for d in duplicates:
        w=d["winner"]
        others=", ".join(f'{x["source"]} ({x["score"]})' for x in d["candidates"][1:])
        lines.append(f'| {w["name"]} | **{w["source"]}** | {w["score"]} | {others} |')
    Path(a.report_md).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({
        "duplicate_groups":len(duplicates),
        "losers_removed":report["losers_removed"],
        "winners_by_source":dict((s,sum(1 for x in winners.values() if x["source"]==s)) for s in SOURCES)
    },ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
