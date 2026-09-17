#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a safe standalone Sport24 XMLTV feed.

Sport24 programme cards expose source-provided UTC ISO timestamps in pairs of
``data-time`` spans. We parse the enclosing card so title, description, start
and stop stay attached to the same programme. No local timezone is guessed.
"""
from __future__ import annotations
import argparse, html as html_lib, json, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

BASE = "https://www.sport24.rest/channels"
TARGETS = [
    ("sport24.adsports.1", "Abu Dhabi Sports 1", f"{BASE}/adsports/1"),
    ("sport24.adsports.2", "Abu Dhabi Sports 2", f"{BASE}/adsports/2"),
    ("sport24.adsports.3", "Abu Dhabi Sports 3", f"{BASE}/adsports/3"),
    ("sport24.adsports.4", "Abu Dhabi Sports 4", f"{BASE}/adsports/4"),
    ("sport24.dubaisports.1", "Dubai Sports 1", f"{BASE}/dubaisports/1"),
    ("sport24.dubaisports.2", "Dubai Sports 2", f"{BASE}/dubaisports/2"),
    ("sport24.thmanyah.1", "Thmanyah 1", f"{BASE}/thmanyah/1"),
    ("sport24.thmanyah.2", "Thmanyah 2", f"{BASE}/thmanyah/2"),
    ("sport24.thmanyah.3", "Thmanyah 3", f"{BASE}/thmanyah/3"),
    ("sport24.bein.news", "beIN SPORTS News", f"{BASE}/bein/news"),
    ("sport24.bein.0", "beIN SPORTS Free", f"{BASE}/bein/0"),
] + [(f"sport24.bein.{n}", f"beIN SPORTS {n}", f"{BASE}/bein/{n}") for n in range(1, 10)]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager/1.3"
TITLE_KEYS = ("title","name","program","programme","program_title","programme_title","eventTitle","event_name","eventName")
DESC_KEYS = ("description","desc","summary","details","subtitle","synopsis")
START_HINTS = ("start","begin","from","airtime","air_time","airdate","air_date","broadcaststart","broadcast_start","datetime","date_time","timestamp")
STOP_HINTS = ("stop","end","finish","until","to","broadcastend","broadcast_end")
BAD_TIME_HINTS = ("publish","modified","created","updated","upload","release","expire","article")


def clean(text):
    return re.sub(r"\s+", " ", html_lib.unescape(str(text or ""))).strip()


def parse_dt(value):
    if value is None or isinstance(value, bool): return None
    if isinstance(value, (int,float)):
        x=float(value)
        if x>10_000_000_000: x/=1000.0
        if 500_000_000 <= x <= 5_000_000_000:
            try: return datetime.fromtimestamp(x,tz=timezone.utc)
            except Exception: return None
        return None
    raw=str(value).strip()
    if not raw: return None
    if re.fullmatch(r"\d{10,13}(?:\.0+)?",raw):
        try: return parse_dt(float(raw))
        except Exception: return None
    cand=raw[:-1]+"+00:00" if raw.endswith("Z") else raw
    try:
        dt=datetime.fromisoformat(cand)
        if dt.tzinfo is not None: return dt.astimezone(timezone.utc)
    except Exception: pass
    try:
        from email.utils import parsedate_to_datetime
        dt=parsedate_to_datetime(raw)
        if dt and dt.tzinfo is not None: return dt.astimezone(timezone.utc)
    except Exception: pass
    return None


def xmltv_dt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def normkey(k): return re.sub(r"[^a-z0-9]","",str(k).casefold())


def first_value(mapping, keys):
    if not isinstance(mapping,dict): return None
    normalized={normkey(k):v for k,v in mapping.items()}
    for k in keys:
        v=normalized.get(normkey(k))
        if v not in (None,""): return v
    return None


def hinted_time(mapping, start=True):
    if not isinstance(mapping,dict): return None, None
    hints=START_HINTS if start else STOP_HINTS
    for k,v in mapping.items():
        nk=normkey(k)
        if any(b in nk for b in BAD_TIME_HINTS): continue
        if not any(normkey(h) in nk for h in hints): continue
        dt=parse_dt(v)
        if dt: return dt,str(k)
    return None,None


def event_from_mapping(row):
    if not isinstance(row,dict): return None
    title=clean(first_value(row,TITLE_KEYS))
    if not title: return None
    start,start_key=hinted_time(row,True)
    if not start: return None
    stop,stop_key=hinted_time(row,False)
    desc=clean(first_value(row,DESC_KEYS))
    return {"start":start,"stop":stop,"title":title,"desc":desc,"via":"json","start_key":start_key,"stop_key":stop_key}


def walk_json(v):
    if isinstance(v,dict):
        yield v
        for c in v.values(): yield from walk_json(c)
    elif isinstance(v,list):
        for c in v: yield from walk_json(c)


def json_blobs(soup):
    for script in soup.find_all("script"):
        raw=script.string or script.get_text("",strip=False) or ""
        if not raw: continue
        stype=(script.get("type") or "").casefold()
        sid=(script.get("id") or "").casefold()
        if "json" in stype or sid in ("__next_data__","__nuxt__"):
            try: yield json.loads(raw)
            except Exception: pass
        for m in re.finditer(r"(?:programs?|programmes?|schedule|events?)\s*[:=]\s*(\[[\s\S]*?\])\s*[;,<]",raw,re.I):
            try: yield json.loads(m.group(1))
            except Exception: pass


def attr_absolute_time(node,start=True):
    hints=START_HINTS if start else STOP_HINTS
    for elem in [node,*node.find_all(True)]:
        for k,v in elem.attrs.items():
            nk=normkey(k)
            if any(b in nk for b in BAD_TIME_HINTS): continue
            if not any(normkey(h) in nk for h in hints): continue
            if isinstance(v,list): v=" ".join(map(str,v))
            dt=parse_dt(v)
            if dt: return dt,str(k)
    return None,None


def node_title_desc(node):
    title=""; desc=""
    for sel in (".card-title",".title",".program-title",".programme-title",".event-title","[class*='title']","h2","h3","h4","h5","h6","strong"):
        hit=node.select_one(sel)
        if hit and clean(hit.get_text(" ",strip=True)):
            title=clean(hit.get_text(" ",strip=True)); break
    for sel in (".card-text",".description",".program-description",".programme-description",".event-description","[class*='description']","p"):
        hit=node.select_one(sel)
        if hit and clean(hit.get_text(" ",strip=True)):
            txt=clean(hit.get_text(" ",strip=True))
            if txt != title:
                desc=txt; break
    if not title: title=clean(node.get("data-title") or node.get("aria-label") or "")
    return title,desc


def event_from_node(node):
    # Sport24 cards carry exact UTC start/stop as the first two data-time spans.
    pair=[]
    for span in node.select("[data-time]"):
        dt=parse_dt(span.get("data-time"))
        if dt: pair.append(dt)
    if pair:
        start=pair[0]; start_key="data-time[0]"
        stop=pair[1] if len(pair)>1 else None
        stop_key="data-time[1]" if len(pair)>1 else None
    else:
        start,start_key=attr_absolute_time(node,True)
        if not start: return None
        stop,stop_key=attr_absolute_time(node,False)
    if not start: return None
    title,desc=node_title_desc(node)
    if not title: return None
    return {"start":start,"stop":stop,"title":title,"desc":desc,"via":"dom","start_key":start_key,"stop_key":stop_key}


def scrape(session,url):
    r=session.get(url,timeout=25); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    events=[]; parsed_blobs=0; key_samples=set()

    # Critical: parse the enclosing programme card, not the isolated data-time span.
    selectors=("section.card","article.card",".schedule-item",".program",".programme",".event")
    seen=set()
    for sel in selectors:
        for node in soup.select(sel):
            ident=id(node)
            if ident in seen: continue
            seen.add(ident)
            ev=event_from_node(node)
            if ev:
                events.append(ev); key_samples.add(f"dom:{ev.get('start_key')}")

    for data in json_blobs(soup):
        parsed_blobs += 1
        for row in walk_json(data):
            ev=event_from_mapping(row)
            if ev:
                events.append(ev); key_samples.add(f"json:{ev.get('start_key')}")

    dedup={}
    for ev in events:
        key=(ev["start"].isoformat(),ev["title"].casefold())
        old=dedup.get(key)
        if old is None or (old.get("stop") is None and ev.get("stop") is not None): dedup[key]=ev
    rows=sorted(dedup.values(),key=lambda x:x["start"])
    for i,row in enumerate(rows[:-1]):
        if row.get("stop") is None and rows[i+1]["start"]>row["start"]: row["stop"]=rows[i+1]["start"]
    valid=[x for x in rows if x.get("stop") and x["stop"]>x["start"]]

    return valid,{"http_status":r.status_code,"html_bytes":len(r.content),"candidate_events":len(events),"parsed_json_blobs":parsed_blobs,"valid_timeline_events":len(valid),"used_time_keys":sorted(key_samples)[:20]}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",required=True); ap.add_argument("--report",required=True); ap.add_argument("--window-hours",type=int,default=48); ap.add_argument("--delay-ms",type=int,default=350); args=ap.parse_args()
    root=ET.Element("tv",{"generator-info-name":"EPGManager Sport24 standalone source","generator-info-url":"https://github.com/wacayoub/EPGManager"})
    session=requests.Session(); session.headers.update({"User-Agent":UA,"Accept-Language":"ar,en;q=0.8","Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"})
    now=datetime.now(timezone.utc); max_start=now+timedelta(hours=max(1,args.window_hours))
    report={"source":"https://www.sport24.rest","policy":"source-provided UTC data-time timestamps; no guessed timezone/local clocks","channels":[],"programmes":0}
    active=0
    for cid,name,url in TARGETS:
        row={"id":cid,"name":name,"url":url,"programmes":0,"status":"NO_TIMELINE"}
        try:
            events,diag=scrape(session,url); row.update(diag)
            events=[e for e in events if e["stop"]>now-timedelta(hours=2) and e["start"]<max_start]
            if events:
                c=ET.SubElement(root,"channel",{"id":cid}); ET.SubElement(c,"display-name",{"lang":"en"}).text=name; active+=1
                for ev in events:
                    p=ET.SubElement(root,"programme",{"channel":cid,"start":xmltv_dt(ev["start"]),"stop":xmltv_dt(ev["stop"])})
                    ET.SubElement(p,"title",{"lang":"ar"}).text=ev["title"]
                    if ev.get("desc"): ET.SubElement(p,"desc",{"lang":"ar"}).text=ev["desc"]
                row["programmes"]=len(events); row["status"]="OK"; report["programmes"]+=len(events)
        except Exception as exc:
            row["status"]="ERROR"; row["error"]=str(exc)[:240]
        report["channels"].append(row); time.sleep(max(0,args.delay_ms)/1000.0)
    ET.indent(root,space="  "); Path(args.output).write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    report["active_channels"]=active; report["generated_utc"]=datetime.now(timezone.utc).isoformat(); Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"active_channels":active,"programmes":report["programmes"]})); return 0

if __name__=="__main__": raise SystemExit(main())
