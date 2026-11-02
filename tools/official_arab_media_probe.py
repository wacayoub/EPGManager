#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover official/local/government Arab TV guide sources.

Discovery only. The probe checks broadcaster/government/public media websites,
looks for visible schedules, time tokens, programme links, embedded JSON and
public API-like endpoints. It never logs in, bypasses protection, or publishes EPG.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# Curated official/public/local media starting points.
# Sites are intentionally conservative: broadcaster, ministry, public corporation,
# or a clearly official broadcaster platform.
TARGETS = {
    "Algeria": [
        "https://www.entv.dz/ar/tv1",
        "https://www.entv.dz/",
    ],
    "Bahrain": [
        "https://www.bahrain.bh/",
    ],
    "Comoros": [
        "https://www.ortc.km/",
    ],
    "Djibouti": [
        "https://rtd.dj/",
    ],
    "Egypt": [
        "https://www.maspero.eg/",
    ],
    "Iraq": [
        "https://www.imn.iq/ar",
        "https://news.imn.iq/",
    ],
    "Jordan": [
        "https://roya.tv/",
        "https://www.jrtv.gov.jo/",
    ],
    "Kuwait": [
        "https://media.gov.kw/",
        "https://www.media.gov.kw/Frequency.aspx?FreqType=TVKSC",
    ],
    "Lebanon": [
        "https://www.teleliban.com.lb/",
    ],
    "Libya": [
        "https://www.ltv.ly/",
    ],
    "Mauritania": [
        "https://www.mauritania.mr/",
    ],
    "Morocco": [
        "https://snrt.ma/",
        "https://www.2m.ma/",
    ],
    "Palestine": [
        "https://www.pbc.ps/category/programs/",
        "https://www.pbc.ps/",
    ],
    "Qatar": [
        "https://www.alkass.net/tvguide",
        "https://dirorigin.alkass.net/tvguide",
        "https://www.tabie.net/live",
        "https://www.qatartv.com/",
    ],
    "Saudi Arabia": [
        "https://sbctv.sba.sa/",
        "https://sba.sa/",
    ],
    "Somalia": [
        "https://sntv.so/",
    ],
    "Sudan": [
        "https://sudantv.net/",
    ],
    "Syria": [
        "https://www.ortas.online/",
    ],
    "Tunisia": [
        "https://www.tunisiatv.tn/ar",
        "https://tunisiatv.tn/ar",
        "https://www.television.tn/",
    ],
    "UAE - Dubai": [
        "https://www.dubaiplus.net/epg",
        "https://www.dubaiplus.net/",
    ],
    "UAE - Sharjah": [
        "https://www.sharjahtv.ae/",
    ],
    "UAE - Abu Dhabi": [
        "https://www.adtv.ae/",
    ],
    "Yemen": [
        "https://yementv.tv/",
    ],
}

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-OfficialProbe/1.0"
TIME_RE=re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
AR_TIME_RE=re.compile(r"(?:الساعة|بتوقيت|يبدأ|يعرض|الآن|التالي)")
GUIDE_WORDS=("جدول البرامج","مواعيد البرامج","دليل البرامج","البرامج اليوم","برنامج اليوم","tv guide","schedule","epg","program schedule","programme schedule")
API_RE=re.compile(r'''(?:"|')(https?://[^"'<>\\\s]+|/[^"'<>\\\s]*(?:api|graphql|epg|schedule|guide|program|programme|channel)[^"'<>\\\s]*)(?:"|')''',re.I)

def clean(x):
    return re.sub(r"\s+"," ",x or "").strip()

def get(sess,url):
    try:
        r=sess.get(url,timeout=22,allow_redirects=True)
        return r,None
    except Exception as e:
        return None,str(e)[:300]

def inspect(url,r):
    text=r.text[:5_000_000]
    soup=BeautifulSoup(text,"html.parser")
    plain=clean(soup.get_text(" ",strip=True))
    low=plain.casefold()
    guide_hits=sum(low.count(x.casefold()) for x in GUIDE_WORDS)
    times=TIME_RE.findall(plain)
    ar_time_hits=len(AR_TIME_RE.findall(plain))
    json_scripts=[]
    js_assets=[]
    for s in soup.find_all("script"):
        src=s.get("src")
        if src:
            js_assets.append(urljoin(r.url,src))
        typ=(s.get("type") or "").casefold()
        sid=(s.get("id") or "").casefold()
        raw=s.string or s.get_text("",strip=False) or ""
        if "json" in typ or sid in ("__next_data__","__nuxt__","__apollo_state__"):
            json_scripts.append({"id":sid,"type":typ,"bytes":len(raw)})
    api=[]
    for m in API_RE.finditer(text):
        v=m.group(1).replace("\\/","/")
        u=urljoin(r.url,v)
        if u not in api:
            api.append(u)
        if len(api)>=50: break
    guide_links=[]
    for a in soup.find_all("a",href=True):
        label=clean(a.get_text(" ",strip=True))
        href=urljoin(r.url,a["href"])
        hay=(label+" "+href).casefold()
        if any(k.casefold() in hay for k in GUIDE_WORDS+("program","programme","برامج","جدول","مواعيد")):
            guide_links.append({"label":label[:100],"url":href})
        if len(guide_links)>=40: break
    score=0
    score += 4 if r.status_code==200 else 0
    score += min(guide_hits,5)*3
    score += min(len(times),20)*0.3
    score += min(ar_time_hits,10)*0.5
    score += min(len(json_scripts),3)*2
    score += min(len(api),10)*0.7
    score += min(len(guide_links),10)*0.8
    return {
        "status":r.status_code,
        "final_url":r.url,
        "content_type":r.headers.get("content-type",""),
        "title":clean(soup.title.get_text(" ",strip=True)) if soup.title else "",
        "bytes":len(r.content),
        "guide_hits":guide_hits,
        "time_tokens":len(times),
        "arabic_time_hits":ar_time_hits,
        "json_scripts":json_scripts[:10],
        "js_assets":js_assets[:30],
        "api_candidates":api,
        "guide_links":guide_links,
        "sample":plain[:1200],
        "discovery_score":round(score,1),
    }

def main():
    sess=requests.Session()
    sess.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8",
    })
    report={"countries":{}}
    for country,urls in TARGETS.items():
        rows=[]
        for url in urls:
            r,err=get(sess,url)
            if err:
                rows.append({"url":url,"status":"ERROR","error":err,"discovery_score":0})
                continue
            row={"url":url,**inspect(url,r)}
            rows.append(row)
        report["countries"][country]=rows

    Path("reports").mkdir(exist_ok=True)
    Path("reports/official-arab-media-discovery.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"
    )

    lines=[
        "# Official Arab media EPG discovery","",
        "| Country | Best HTTP | Score | Guide hits | Times | JSON | API candidates | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    summary=[]
    for country,rows in report["countries"].items():
        best=max(rows,key=lambda x:x.get("discovery_score",0))
        s=best.get("discovery_score",0)
        if best.get("status")==200 and s>=12:
            verdict="PROMISING"
        elif best.get("status")==200:
            verdict="REACHABLE_NEEDS_API"
        elif best.get("status") in (401,403):
            verdict="BLOCKED"
        else:
            verdict="NO_SIGNAL"
        summary.append({"country":country,"verdict":verdict,"best":best})
        lines.append(
            f'| {country} | {best.get("status")} | {s} | {best.get("guide_hits",0)} | '
            f'{best.get("time_tokens",0)} | {len(best.get("json_scripts",[]))} | '
            f'{len(best.get("api_candidates",[]))} | {verdict} |'
        )
    Path("reports/official-arab-media-discovery.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
