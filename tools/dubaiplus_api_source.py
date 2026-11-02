#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dubai+ public JSON EPG adapter.

Uses the same public /content/channels JSON feed loaded by the Dubai+ web EPG.
No login, DRM, playback URLs, or anti-bot bypass. Two metadata requests maximum:
English + Arabic, then merge locally using the EPGManager Hybrid policy.
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

API="https://d1vr1mlm6fadud.cloudfront.net/content/channels"
LOCATION_ID="309639718595"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
AR=re.compile(r"[\u0600-\u06ff]")

CHANNELS={
 "dubai":("DubaiTV.ae@SD","Dubai TV","arabic_native"),
 "sama dubai":("SamaDubai.ae@SD","Sama Dubai","arabic_native"),
 "dubai sports 1":("DubaiSports1.ae@SD","Dubai Sports 1","arabic_native"),
 "dubai sports 2":("DubaiSports2.ae@SD","Dubai Sports 2","arabic_native"),
 "dubai racing":("DubaiRacing1.ae@SD","Dubai Racing 1","hybrid"),
 "dubai racing 1":("DubaiRacing1.ae@SD","Dubai Racing 1","hybrid"),
 "dubai racing 2":("DubaiRacing2.ae@SD","Dubai Racing 2","hybrid"),
 "noor dubaitv":("NoorDubaiTV.ae@SD","Noor Dubai TV","arabic_native"),
 "noor dubai tv":("NoorDubaiTV.ae@SD","Noor Dubai TV","arabic_native"),
 "one tv":("DubaiOne.ae@SD","Dubai One","hybrid"),
 "dubai one":("DubaiOne.ae@SD","Dubai One","hybrid"),
 "dubai zaman":("DubaiZaman.ae@SD","Dubai Zaman","arabic_native"),
}

def norm(s):
    return " ".join(re.sub(r"[^a-z0-9\u0600-\u06ff]+"," ",(s or "").casefold()).split())

def spec(name):
    n=norm(name)
    if "radio" in n or "quran radio" in n: return None
    if n in CHANNELS: return CHANNELS[n]
    for k,v in CHANNELS.items():
        if n==k or n.startswith(k+" ") or k.startswith(n+" "): return v
    return None

def has_ar(s): return bool(AR.search(s or ""))

def fetch(lang,hours,timeout):
    now=datetime.now(timezone.utc)
    # Request a small metadata window around now. Dubai+ expects epoch ms.
    start=int((now-timedelta(hours=4)).timestamp()*1000)
    end=int((now+timedelta(hours=max(2,hours)+4)).timestamp()*1000)
    params={
      "locationId":LOCATION_ID,
      "language":lang,
      "byListingTime":f"{start}~{end}",
      "offsetTime":"0",
      "origin":"mpx",
      "size":"50",
      "region":"US",
      "maxParentalRatings":"18",
      "platform":"web",
    }
    r=requests.get(API,params=params,headers={
      "User-Agent":UA,"Accept":"application/json,text/plain,*/*",
      "Referer":"https://www.dubaiplus.net/epg",
      "Origin":"https://www.dubaiplus.net",
      "Accept-Language":"ar-AE,ar;q=0.9,en;q=0.7" if lang.startswith("ar") else "en-US,en;q=0.9,ar;q=0.6",
    },timeout=timeout)
    r.raise_for_status()
    data=r.json()
    return data, r.url

def entries(data):
    if isinstance(data,dict) and isinstance(data.get("entries"),list): return data["entries"]
    return []

def listing_map(entry):
    out={}
    for x in entry.get("listings") or []:
        key=(str(x.get("id") or ""),int(x.get("startTime") or 0))
        out[key]=x
        # Localized API responses may use a different listing id for the same
        # programme. Within one channel, start time is a safe secondary key.
        if key[1]:
            out.setdefault(("",key[1]),x)
    return out

def text(x):
    return str(x or "").strip()

def native_ar(value):
    # Provider objects/lists are not descriptions; never stringify them.
    return value.strip() if isinstance(value,str) and has_ar(value) else ""


def arabic_localized(mapping):
    """Return only authentic Arabic provider metadata, never translations.

    Dubai+ may use ar, ar-AE or ar_AE locale keys. Ignore non-Arabic
    localizations even when the API returns language=en-US.
    """
    if not isinstance(mapping,dict): return ""
    for lang,value in mapping.items():
        key=str(lang).lower().replace("_","-")
        if key == "ar" or key.startswith("ar-"):
            candidate=native_ar(value)
            if candidate: return candidate
    return ""


def ar_desc(en_li, ar_li):
    """Prefer provider's native Arabic description, including short synopsis.

    Existing descriptionLocalized(ar) takes priority. Other genuine fields
    are accepted only when they contain Arabic script; never translate an
    English text, invent a blurb, or duplicate the programme title as desc.
    """
    for li in (ar_li,en_li):
        if not isinstance(li,dict): continue
        p=li.get("program") or {}
        if not isinstance(p,dict): p={}
        for field in ("descriptionLocalized","shortDescriptionLocalized",
                      "longDescriptionLocalized","summaryLocalized",
                      "synopsisLocalized"):
            candidate=arabic_localized(p.get(field))
            if candidate: return candidate
        for field in ("description","shortDescription","longDescription",
                      "summary","synopsis","overview"):
            candidate=native_ar(p.get(field))
            if candidate: return candidate
        candidate=arabic_localized(li.get("descriptionLocalized"))
        if candidate: return candidate
        for field in ("description","shortDescription","summary"):
            candidate=native_ar(li.get(field))
            if candidate: return candidate
    return ""

def title_en(li):
    p=li.get("program") or {}
    return text(p.get("title") or li.get("originalTitle") or p.get("originalTitle"))

def title_ar(en_li, ar_li):
    for li in (ar_li,en_li):
        if not isinstance(li,dict): continue
        p=li.get("program") or {}
        for candidate in (p.get("title"),li.get("originalTitle"),p.get("originalTitle")):
            v=text(candidate)
            if has_ar(v): return v
        v=arabic_localized(p.get("titleLocalized") or p.get("localizedTitle"))
        if v: return v
    return title_en(en_li)

def fmt(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")

def build(en_data,ar_data,hours):
    now=datetime.now(timezone.utc)
    end=now+timedelta(hours=max(1,hours))
    ar_by_id={str(x.get("id")):x for x in entries(ar_data)}
    root=ET.Element("tv",{
      "generator-info-name":"EPGManager Dubai+ public API Hybrid",
      "generator-info-url":"https://www.dubaiplus.net/epg",
    })
    stats={"generated_utc":now.isoformat(),"channels":0,"programmes":0,"hybrid_programmes":0,
           "arabic_native_programmes":0,"arabic_desc_present":0,"english_title_applied":0,
           "source_channels":[]}
    for ch in entries(en_data):
        cid_src=str(ch.get("id") or "")
        name=text(ch.get("title") or ch.get("originalTitle"))
        sp=spec(name)
        if not sp: continue
        xmlid,display,profile=sp
        ar_ch=ar_by_id.get(cid_src,{})
        ar_map=listing_map(ar_ch)
        c=ET.SubElement(root,"channel",{"id":xmlid})
        ET.SubElement(c,"display-name",{"lang":"en"}).text=display
        ET.SubElement(c,"url",{"system":"dubaiplus-id"}).text=cid_src
        stats["channels"]+=1
        stats["source_channels"].append({"source_id":cid_src,"name":name,"xmltv_id":xmlid,"profile":profile})
        for li in ch.get("listings") or []:
            st=int(li.get("startTime") or 0); et=int(li.get("endTime") or 0)
            if not st or not et: continue
            sdt=datetime.fromtimestamp(st/1000,tz=timezone.utc)
            edt=datetime.fromtimestamp(et/1000,tz=timezone.utc)
            if sdt>=end or edt<=now-timedelta(hours=1): continue
            ali=ar_map.get((str(li.get("id") or ""),st)) or ar_map.get(("",st))
            desc=ar_desc(li,ali)
            if profile=="hybrid":
                title=title_en(li)
                tlang="en" if title else "ar"
                stats["hybrid_programmes"]+=1
                if title and not has_ar(title): stats["english_title_applied"]+=1
            else:
                title=title_ar(li,ali)
                tlang="ar" if has_ar(title) else "en"
                stats["arabic_native_programmes"]+=1
            if not title: continue
            p=ET.SubElement(root,"programme",{"channel":xmlid,"start":fmt(st),"stop":fmt(et)})
            ET.SubElement(p,"title",{"lang":tlang}).text=title
            if desc:
                ET.SubElement(p,"desc",{"lang":"ar"}).text=desc
                stats["arabic_desc_present"]+=1
            stats["programmes"]+=1
    return root,stats

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    ap.add_argument("--report",required=True)
    ap.add_argument("--hours",type=int,default=48)
    ap.add_argument("--timeout",type=int,default=25)
    args=ap.parse_args()
    err=""
    urls={}
    try:
        en,urls["en"]=fetch("en-US",args.hours,args.timeout)
        ar,urls["ar"]=fetch("ar-AE",args.hours,args.timeout)
        root,stats=build(en,ar,args.hours)
        status=200
    except Exception as e:
        root=ET.Element("tv")
        stats={"channels":0,"programmes":0}
        status=0; err=str(e)
    ET.indent(root,space="  ")
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    stats.update({"http_status":status,"error":err,"urls":urls,"policy":"public metadata only; two JSON requests; no playback/auth bypass"})
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(stats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in stats.items() if k not in ("urls","source_channels")},ensure_ascii=False))
    return 0 if stats.get("channels") and stats.get("programmes") else 11

if __name__=="__main__":
    raise SystemExit(main())
