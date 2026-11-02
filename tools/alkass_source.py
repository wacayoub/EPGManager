#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct Al Kass 1-8 XMLTV scraper from the official broadcast guide."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE="https://dirorigin.alkass.net/tvguide"
TZ=ZoneInfo("Asia/Qatar")
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-AlKass/1.0"
CHANNELS=[
 ("AlkassOne.qa@SD","Al Kass 1"),
 ("AlkassTwo.qa@SD","Al Kass 2"),
 ("AlkassThree.qa@SD","Al Kass 3"),
 ("AlkassFour.qa@SD","Al Kass 4"),
 ("AlkassFive.qa@SD","Al Kass 5"),
 ("AlkassSix.qa@SD","Al Kass 6"),
 ("AlkassSeven.qa@SD","Al Kass 7"),
 ("AlkassEight.qa@SD","Al Kass 8"),
]
TIME_RE=re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def lang_for(text:str)->str:
    return "ar" if re.search(r"[\u0600-\u06ff]",text or "") else "en"


def parse_page(session, url, date):
    r=session.get(url,timeout=30,headers={"Referer":"https://dirorigin.alkass.net/"})
    r.raise_for_status()
    s=BeautifulSoup(r.text,"html.parser")
    tables=s.select("table.team-result")
    if len(tables)<8:
        raise RuntimeError(f"Al Kass expected >=8 schedule tables, got {len(tables)}")
    out={}
    for idx,(cid,name) in enumerate(CHANNELS):
        items=[]
        for tr in tables[idx].select("tr"):
            tnode=tr.select_one(".tv-prog-time")
            if not tnode:
                continue
            time_txt=" ".join(tnode.stripped_strings).strip()
            if not TIME_RE.match(time_txt):
                continue
            # Title is the row text minus the dedicated time cell.
            title_parts=[]
            for cell in tr.find_all(["td","th"],recursive=False):
                if "tv-prog-time" in (cell.get("class") or []):
                    continue
                txt=" ".join(cell.stripped_strings).strip()
                if txt:
                    title_parts.append(txt)
            title=" ".join(title_parts).strip()
            if not title:
                full=" ".join(tr.stripped_strings)
                title=full.replace(time_txt,"").strip(" |-")
            if not title or title.lower()=="schedule not available":
                continue
            hh,mm=map(int,time_txt.split(":"))
            start=datetime(date.year,date.month,date.day,hh,mm,tzinfo=TZ)
            items.append({"title":title,"start":start})
        out[cid]=items
    return out


def main()->int:
    now=datetime.now(TZ)
    today=now.date()
    tomorrow=today+timedelta(days=1)
    sess=requests.Session()
    sess.headers.update({"User-Agent":UA,"Accept-Language":"ar,en;q=0.8"})

    day0=parse_page(sess,BASE,today)
    day1=parse_page(sess,BASE+"?day=next",tomorrow)

    root=ET.Element("tv",{"generator-info-name":"EPGManager Al Kass direct","generator-info-url":BASE})
    cat=ET.Element("channels")
    for cid,name in CHANNELS:
        c=ET.SubElement(root,"channel",{"id":cid})
        ET.SubElement(c,"display-name",{"lang":"en"}).text=name
        cc=ET.SubElement(cat,"channel",{"site":"alkass.net","site_id":name.split()[-1],"lang":"ar","xmltv_id":cid})
        cc.text=name

    report={"generated_qatar":now.isoformat(),"channels":{},"programmes":0,
            "provider_date_options":["today","tomorrow"],
            "provider_official_schedule_days":2,
            "third_day_not_published_by_official_guide":True,
            "synthetic_programmes_added":0,
            "last_programme_stop_is_inferred_if_no_next_start":True}
    for cid,name in CHANNELS:
        rows=(day0.get(cid) or [])+(day1.get(cid) or [])
        rows.sort(key=lambda x:x["start"])
        # remove exact duplicates
        ded=[]
        seen=set()
        for x in rows:
            k=(x["start"],x["title"])
            if k in seen: continue
            seen.add(k); ded.append(x)
        kept=0
        for i,x in enumerate(ded):
            start=x["start"]
            if i+1<len(ded):
                stop=ded[i+1]["start"]
            else:
                stop=min(start+timedelta(hours=3),datetime.combine(tomorrow+timedelta(days=1),datetime.min.time(),tzinfo=TZ))
            if stop<=start:
                continue
            p=ET.SubElement(root,"programme",{
                "channel":cid,
                "start":start.strftime("%Y%m%d%H%M%S %z"),
                "stop":stop.strftime("%Y%m%d%H%M%S %z"),
            })
            ET.SubElement(p,"title",{"lang":lang_for(x["title"])}).text=x["title"]
            kept+=1; report["programmes"]+=1
        verified_last_start = max((x["start"] for x in ded),default=None)
        report["channels"][cid]={
            "name":name,"programmes":kept,
            "today":len(day0.get(cid) or []),
            "tomorrow":len(day1.get(cid) or []),
            "last_verified_start_qatar":verified_last_start.isoformat() if verified_last_start else None,
            "native_last_start_horizon_hours":(
                round(max(0.0,(verified_last_start-now).total_seconds()/3600.0),2)
                if verified_last_start else 0.0
            ),
            "provider_third_day_unknown":True,
        }

    Path("output/source-build").mkdir(parents=True,exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    ET.indent(root,space="  "); ET.indent(cat,space="  ")
    Path("output/source-build/alkass.raw.xml").write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    Path("output/source-build/alkass.channels.xml").write_bytes(ET.tostring(cat,encoding="utf-8",xml_declaration=True))
    Path("reports/alkass-direct.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report["programmes"] else 3

if __name__=="__main__":
    raise SystemExit(main())
