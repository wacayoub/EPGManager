#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct XMLTV scraper for Télévision Tunisienne (Watania 1/2)."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import quote
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ=ZoneInfo("Africa/Tunis")
BASE="https://tunisiatv.tn/ar/programme"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-TunisiaTV/1.0"
CHANNELS=[
    ("ElWatania1.tn@SD","69c58aac8d6cac7a55cd2e09","الوطنية 1"),
    ("ElWatania2.tn@SD","69c593d18d6cac7a55cd575c","الوطنية 2"),
]
RANGE=re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def _text(tag):
    return " ".join(tag.stripped_strings).strip() if tag else ""


def _desc_from(box, title, timerange):
    # Prefer explicit paragraph-like text near the programme title.
    chunks=[]
    for node in box.find_all(["p","div"], recursive=True):
        txt=_text(node)
        if not txt or txt==title or txt==timerange:
            continue
        if RANGE.match(txt):
            continue
        # Avoid huge parent containers / navigation text.
        if len(txt) < 25 or len(txt) > 1800:
            continue
        if title and title in txt and len(txt) <= len(title)+40:
            continue
        if txt not in chunks:
            chunks.append(txt)
        if len(chunks)>=2:
            break
    if not chunks:
        return ""
    # Pick the most descriptive candidate, not a wrapper containing all rows.
    chunks.sort(key=len)
    return chunks[-1][:1800]


def parse_schedule(html:str, target:date):
    soup=BeautifulSoup(html,"html.parser")
    rows=[]
    seen=set()
    for node in soup.find_all(string=RANGE):
        timerange=str(node).strip()
        m=RANGE.match(timerange)
        if not m:
            continue

        box=node.parent
        chosen=None
        for _ in range(8):
            if not box:
                break
            h=box.find(["h2","h3","h4"])
            if h and _text(h):
                chosen=box
                break
            box=box.parent
        if not chosen:
            continue

        heading=chosen.find(["h2","h3","h4"])
        title=_text(heading)
        if not title:
            continue
        # Some headings include the clock; remove only a leading clock/range.
        title=re.sub(r"^\s*\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?\s*","",title).strip(" -|")
        if not title:
            continue

        sh,sm,eh,em=map(int,m.groups())
        start=datetime.combine(target,dtime(sh,sm),tzinfo=TZ)
        stop=datetime.combine(target,dtime(eh,em),tzinfo=TZ)
        if stop <= start:
            stop += timedelta(days=1)
        duration=stop-start
        if duration <= timedelta(0) or duration > timedelta(hours=12):
            continue
        key=(start,stop,title)
        if key in seen:
            continue
        seen.add(key)
        desc=_desc_from(chosen,title,timerange)
        rows.append({"start":start,"stop":stop,"title":title,"desc":desc})

    # Fallback for the compact homepage/programme markup where <time> holds
    # only the start and the end is implied by the next row.
    if len(rows) < 5:
        starts=[]
        for t in soup.find_all("time"):
            raw=_text(t)
            if not re.fullmatch(r"\d{1,2}:\d{2}",raw):
                continue
            holder=t.parent
            title=""
            if holder:
                txt=_text(holder)
                title=re.sub(r"^\s*"+re.escape(raw)+r"\s*","",txt).strip()
            if not title:
                continue
            hh,mm=map(int,raw.split(":"))
            starts.append((datetime.combine(target,dtime(hh,mm),tzinfo=TZ),title,holder))
        # The official day may continue after midnight; detect the wrap.
        fixed=[]
        dayoff=0
        last_minutes=None
        for st,title,holder in starts:
            mins=st.hour*60+st.minute
            if last_minutes is not None and mins+360 < last_minutes:
                dayoff+=1
            fixed.append((st+timedelta(days=dayoff),title,holder))
            last_minutes=mins
        for i,(st,title,holder) in enumerate(fixed):
            stop=fixed[i+1][0] if i+1<len(fixed) else st+timedelta(minutes=30)
            if stop<=st or stop-st>timedelta(hours=12):
                continue
            key=(st,stop,title)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"start":st,"stop":stop,"title":title,"desc":""})

    rows.sort(key=lambda x:x["start"])
    return rows


def fetch_day(session, site_id, name, target):
    weekday=target.isoweekday()  # official URLs use 1=Monday ... 7=Sunday
    url=f"{BASE}/{weekday}/{site_id}/{quote(name, safe='')}"
    r=session.get(url,timeout=30,headers={"Referer":"https://tunisiatv.tn/ar/programme"})
    r.raise_for_status()
    return url,parse_schedule(r.text,target)


def main()->int:
    now=datetime.now(TZ)
    targets=[now.date()+timedelta(days=i) for i in range(3)]
    sess=requests.Session()
    sess.headers.update({"User-Agent":UA,"Accept-Language":"ar,en;q=0.8"})

    root=ET.Element("tv",{"generator-info-name":"EPGManager TunisiaTV direct","generator-info-url":"https://tunisiatv.tn/ar/programme"})
    cat=ET.Element("channels")
    report={"generated_tunis":now.isoformat(),"channels":{},"programmes":0}

    for cid,site_id,name in CHANNELS:
        c=ET.SubElement(root,"channel",{"id":cid})
        ET.SubElement(c,"display-name",{"lang":"ar"}).text=name
        cc=ET.SubElement(cat,"channel",{"site":"tunisiatv.tn","site_id":site_id,"lang":"ar","xmltv_id":cid})
        cc.text=name

        allrows=[]
        days={}
        for target in targets:
            try:
                url,rows=fetch_day(sess,site_id,name,target)
                days[target.isoformat()]={"url":url,"programmes":len(rows),"status":"OK"}
                allrows.extend(rows)
            except Exception as exc:
                days[target.isoformat()]={"programmes":0,"status":"ERROR","error":str(exc)}

        seen=set()
        kept=0
        for row in sorted(allrows,key=lambda x:(x["start"],x["stop"],x["title"])):
            key=(row["start"],row["stop"],row["title"])
            if key in seen:
                continue
            seen.add(key)
            p=ET.SubElement(root,"programme",{
                "channel":cid,
                "start":row["start"].strftime("%Y%m%d%H%M%S %z"),
                "stop":row["stop"].strftime("%Y%m%d%H%M%S %z"),
            })
            ET.SubElement(p,"title",{"lang":"ar"}).text=row["title"]
            if row["desc"] and row["desc"]!=row["title"]:
                ET.SubElement(p,"desc",{"lang":"ar"}).text=row["desc"]
            kept+=1
            report["programmes"]+=1
        report["channels"][cid]={"name":name,"programmes":kept,"days":days}

    Path("output/source-build").mkdir(parents=True,exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    ET.indent(root,space=" "); ET.indent(cat,space="  ")
    Path("output/source-build/tunisiatv.raw.xml").write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    Path("output/source-build/tunisiatv.channels.xml").write_bytes(ET.tostring(cat,encoding="utf-8",xml_declaration=True))
    Path("reports/tunisiatv-direct.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report["programmes"] else 3


if __name__=="__main__":
    raise SystemExit(main())
