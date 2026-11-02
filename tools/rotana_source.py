#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rotana official EPG scraper resilient to Rotana's false HTTP 403.

rotana.net currently returns the real schedule HTML body to GitHub Actions while
setting status=403. The generic upstream grabber rejects that response before its
parser can see it. This scraper validates the HTML body instead of trusting the
status code alone, then parses the same official interactive guide.

No programme is invented. Every configured Rotana channel ID stays in the XMLTV
catalogue even when the current guide is N/A; zero-EPG status is reported
separately so EPGManager can let the user choose.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

# Rotana's /ar/streams endpoint, when queried without a tz override, exposes
# schedule clock values in UTC. Treating those values as Asia/Riyadh shifted
# every event three hours early on the receiver.
TZ = timezone.utc
BASE = "https://www.rotana.net/ar/streams"

CHANNELS = {
    "431": ("RotanaCinemaKSA.sa@SD", "روتانا سينما السعودية"),
    "434": ("LBC.sa@SD", "إل بي سي"),
    "439": ("RotanaCinemaEgypt.eg@SD", "روتانا سينما مصر"),
    "438": ("RotanaClassic.sa@SD", "روتانا كلاسيك"),
    "443": ("RotanaClip.sa@SD", "روتانا كليب"),
    "437": ("RotanaComedy.sa@SD", "روتانا كوميدي"),
    "436": ("RotanaDrama.sa@SD", "روتانا دراما"),
    "435": ("RotanaKhalijia.sa@SD", "روتانا خليجية"),
    "446": ("AlResalah.sa@SD", "الرسالة"),
}

def clean(text):
    return " ".join((text or "").split()).strip()

def parse_date(raw):
    raw=clean(raw)
    if not raw:
        return None
    # Rotana uses separator ids such as <prefix>-DD-MM-YYYY.
    m=re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})$",raw)
    if m:
        d,mn,y=map(int,m.groups())
        try:
            return datetime(y,mn,d).date()
        except ValueError:
            return None
    # Keep ISO support in case the official markup changes.
    m=re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})$",raw)
    if m:
        y,mn,d=map(int,m.groups())
        try:
            return datetime(y,mn,d).date()
        except ValueError:
            return None
    return None
def parse_time(raw):
    raw=clean(raw)
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(raw, fmt).time()
        except Exception:
            pass
    return None

def fetch_page(session, site_id):
    url=BASE
    r=session.get(url, params={"channel":site_id}, timeout=25, allow_redirects=True)
    body=r.text or ""
    # Rotana's WAF currently sends status 403 together with the full valid HTML.
    # Accept it only when the expected official guide structure/text is present.
    valid=("iq-accordion" in body or "دليل القنوات" in body or "TV Interactive" in body) and len(body)>20000
    if r.status_code not in (200,403) or not valid:
        raise RuntimeError(f"Rotana HTTP {r.status_code}, invalid body size={len(body)}")
    return body, r.status_code

def parse_events(html, cid):
    soup=BeautifulSoup(html,"lxml")
    rows=[]
    day=None
    for div in soup.select(".hour > div"):
        classes=set(div.get("class") or [])
        if "bg" in classes and div.get("id"):
            parsed=parse_date(div.get("id"))
            if parsed:
                day=parsed
            continue
        block=div.select_one(".iq-accordion-block")
        if block is None or day is None:
            continue
        heading=block.select_one(".iq-accordion-title .big-title")
        if heading is None:
            continue
        spans=heading.find_all("span")
        if len(spans)<2:
            continue
        tm=parse_time(spans[0].get_text(" ",strip=True))
        title=clean(spans[1].get_text(" ",strip=True))
        if tm is None or not title:
            continue
        # The page clock is already UTC; keep it as-is.
        start=datetime.combine(day,tm,tzinfo=TZ)
        rows.append((start,title))

    if not rows:
        # Small structural diagnostic for official markup changes. It contains
        # no cookies/tokens and lets CI explain a parser miss instead of silently
        # publishing an empty feed.
        print("ROTANA_MARKUP hour_divs=%d accordions=%d blocks=%d bg=%d" % (
            len(soup.select(".hour > div")),
            len(soup.select(".iq-accordion")),
            len(soup.select(".iq-accordion-block")),
            len(soup.select(".bg")),
        ))
        samples=soup.select(".hour, .iq-accordion, .tv-schedule, .schedule, [class*=accordion]")
        for sample in samples[:2]:
            snippet=clean(str(sample))[:900]
            print("ROTANA_MARKUP_SAMPLE",snippet)

    # De-duplicate exact schedule entries and infer stops from the next event.
    unique=[]
    seen=set()
    for start,title in sorted(rows,key=lambda x:(x[0],x[1])):
        k=(start,title)
        if k in seen:
            continue
        seen.add(k)
        unique.append((start,title))

    out=[]
    for i,(start,title) in enumerate(unique):
        nxt=unique[i+1][0] if i+1<len(unique) else None
        if nxt and nxt>start and nxt-start<=timedelta(hours=8):
            stop=nxt
        else:
            stop=start+timedelta(hours=1)
        out.append((cid,start,stop,title))
    return out

def fmt(dt):
    return dt.strftime("%Y%m%d%H%M%S +0000")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    ap.add_argument("--report",required=True)
    ap.add_argument("--window-hours",type=int,default=72)
    args=ap.parse_args()

    session=requests.Session()
    session.headers.update({
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en-US;q=0.8,en;q=0.7",
        "Referer":"https://www.rotana.net/",
        "Cache-Control":"no-cache",
    })

    now=datetime.now(timezone.utc)
    limit=now+timedelta(hours=max(24,args.window_hours))
    all_events=[]
    status={}
    for site_id,(cid,name) in CHANNELS.items():
        try:
            html,http_status=fetch_page(session,site_id)
            events=parse_events(html,cid)
            events=[e for e in events if e[2]>now-timedelta(hours=2) and e[1]<limit]
            all_events.extend(events)
            status[cid]={"site_id":site_id,"name":name,"http_status":http_status,"programmes":len(events),"state":"ok" if events else "no-guide"}
            print(f"ROTANA {cid} HTTP={http_status} programmes={len(events)}")
        except Exception as exc:
            status[cid]={"site_id":site_id,"name":name,"programmes":0,"state":"error","error":str(exc)}
            print(f"ROTANA {cid} ERROR {exc}")

    active={cid for cid,_,_,_ in all_events}
    root=ET.Element("tv",{
        "generator-info-name":"Rotana official resilient scraper",
        "generator-info-url":"https://www.rotana.net/tv/channels",
    })
    for site_id,(cid,name) in CHANNELS.items():
        ch=ET.SubElement(root,"channel",{"id":cid})
        ET.SubElement(ch,"display-name",{"lang":"ar"}).text=name
        ET.SubElement(ch,"url",{"system":"rotana-id"}).text=site_id
    for cid,start,stop,title in sorted(all_events,key=lambda x:(x[1],x[0],x[3])):
        p=ET.SubElement(root,"programme",{"start":fmt(start),"stop":fmt(stop),"channel":cid})
        ET.SubElement(p,"title",{"lang":"ar"}).text=title
    ET.indent(root,space="  ")
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(out,encoding="utf-8",xml_declaration=True)

    report={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "source":"https://www.rotana.net/ar/streams",
        "timezone":"UTC (Rotana streams page clock)",
        "channels_configured":len(CHANNELS),
        "channels_with_epg":len(active),
        "zero_epg_ids":sorted(set(cid for _,(cid,_) in CHANNELS.items())-active),
        "programmes":len(all_events),
        "timezone_output":"UTC +0000",
        "channels":status,
    }
    rp=Path(args.report); rp.parent.mkdir(parents=True,exist_ok=True)
    import json
    rp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"ROTANA_RESULT channels={len(active)} programmes={len(all_events)}")
    return 0 if len(active)>=5 and len(all_events)>=40 else 3

if __name__=="__main__":
    raise SystemExit(main())
