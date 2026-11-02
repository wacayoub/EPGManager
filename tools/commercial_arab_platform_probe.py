#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe public Arab commercial TV/streaming platforms for real EPG/schedule data.

Discovery only. No login, DRM, token harvesting or auth bypass.
The goal is to find public guide/schedule/API surfaces that can later become
validated EPG adapters.
"""
from __future__ import annotations
import json, re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

PLATFORMS = {
    "shahid": [
        "https://shahid.mbc.net/ar/todaysepisode",
        "https://shahid.mbc.net/en/todaysepisode",
        "https://shahid.mbc.net/ar",
    ],
    "gobx": [
        "https://www.gobx.com/",
        "https://www.mbc.net/channels?title=GOBX",
    ],
    "mbcnow": [
        "https://www.gobx.com/",
    ],
    "starzplay": [
        "https://www.starzplay.com/en/arabic",
        "https://playarabia-prod.starzplay.com/en/arabic",
    ],
    "stctv": [
        "https://app.stctv.com/",
        "https://www.stctv.com/",
        "https://stctv.com/",
    ],
    "rotana": [
        "https://www.rotana.net/tv/channels",
        "https://www.rotana.net/en/channels",
        "https://www.rotana.net/en/streams",
    ],
    "artonline": [
        "https://www.artonline.tv/",
        "https://www.artonline.tv/guide",
    ],
}

KEYWORDS=(
    "epg","schedule","guide","tv guide","programme","program","programs",
    "today","live tv","livestream","channels","channel",
    "جدول","دليل","البرامج","برامج","مواعيد","اليوم","قنوات","قناة","مباشر"
)
TIME_RE=re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
DATE_RE=re.compile(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")
API_RE=re.compile(r'''(?:"|')(https?://[^"'<>\\\s]+|/[^"'<>\\\s]*(?:api|graphql|epg|schedule|guide|program|programme|channel|channels|live)[^"'<>\\\s]*)(?:"|')''',re.I)
JSON_HINT=re.compile(r'"(?:title|name|start|startTime|start_time|begin|end|stop|duration|channel|program|programme|schedule)"\s*:',re.I)
BAD=("login","signin","oauth","token","account","payment","checkout","drm","widevine","license","captcha")
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPG-Scrapers-CommercialProbe/1.0"

def same_hostish(base,u):
    a=(urlparse(base).hostname or "").lower().removeprefix("www.")
    b=(urlparse(u).hostname or "").lower().removeprefix("www.")
    return a==b or a.endswith("."+b) or b.endswith("."+a)

def clean(s):
    return re.sub(r"\s+"," ",s or "").strip()

def fetch(sess,url):
    try:
        r=sess.get(url,timeout=20,allow_redirects=True)
        return r,None
    except Exception as e:
        return None,str(e)[:240]

def inspect_html(base,r):
    text=r.text[:5_000_000]
    soup=BeautifulSoup(text,"html.parser")
    plain=clean(soup.get_text(" ",strip=True))
    low=plain.casefold()
    kws=sum(low.count(x.casefold()) for x in KEYWORDS)
    times=len(TIME_RE.findall(plain))
    dates=len(DATE_RE.findall(plain))
    json_hits=len(JSON_HINT.findall(text))
    links=[]; js=[]; apis=[]
    for a in soup.find_all("a",href=True):
        u=urldefrag(urljoin(r.url,a["href"]))[0]
        label=clean(a.get_text(" ",strip=True))
        hay=(label+" "+u).casefold()
        if any(k.casefold() in hay for k in KEYWORDS):
            links.append({"label":label[:120],"url":u})
        if len(links)>=80: break
    for s in soup.find_all("script",src=True):
        u=urljoin(r.url,s["src"])
        if same_hostish(base,u) or any(x in (urlparse(u).hostname or "").lower() for x in ("cdn","static","assets")):
            js.append(u)
        if len(js)>=50: break
    for m in API_RE.finditer(text):
        u=urljoin(r.url,m.group(1).replace("\\/","/"))
        if not any(x in u.lower() for x in BAD) and u not in apis:
            apis.append(u)
        if len(apis)>=100: break
    return {
        "title":clean(soup.title.get_text(" ",strip=True)) if soup.title else "",
        "keyword_hits":kws,
        "time_tokens":times,
        "date_tokens":dates,
        "json_field_hits":json_hits,
        "guide_links":links,
        "js_assets":js,
        "api_candidates":apis,
        "sample":plain[:1000],
    }

def main():
    sess=requests.Session()
    sess.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8",
    })
    report={"platforms":{}}
    for platform,starts in PLATFORMS.items():
        pages=[]; js_assets=set(); candidates=set(); q=deque(starts); seen=set()
        while q and len(seen)<12:
            url=q.popleft()
            if url in seen: continue
            seen.add(url)
            r,err=fetch(sess,url)
            if err:
                pages.append({"url":url,"status":"ERROR","error":err}); continue
            row={"url":url,"status":r.status_code,"final_url":r.url,"content_type":r.headers.get("content-type",""),"bytes":len(r.content)}
            if r.status_code==200 and ("html" in row["content_type"].lower() or "<html" in r.text[:800].lower()):
                info=inspect_html(starts[0],r); row.update(info)
                js_assets.update(info["js_assets"]); candidates.update(info["api_candidates"])
                for x in info["guide_links"]:
                    u=x["url"]
                    if same_hostish(starts[0],u) and u not in seen and len(q)<30:
                        q.append(u)
            pages.append(row)

        js_hits=[]
        for src in list(js_assets)[:35]:
            r,err=fetch(sess,src)
            if err or not r or r.status_code!=200: continue
            text=r.text[:5_000_000]
            found=[]
            for m in API_RE.finditer(text):
                u=urljoin(r.url,m.group(1).replace("\\/","/"))
                if any(x in u.lower() for x in BAD): continue
                if u not in found:
                    found.append(u); candidates.add(u)
                if len(found)>=100: break
            if found:
                js_hits.append({"url":src,"api_candidates":found[:100]})

        probes=[]
        for u in sorted(candidates):
            if len(probes)>=60: break
            r,err=fetch(sess,u)
            if err or not r: continue
            sample=clean(r.text[:1000]) if r.text else ""
            probes.append({
                "url":u,"status":r.status_code,
                "content_type":r.headers.get("content-type",""),
                "bytes":len(r.content),"sample":sample[:700]
            })

        api_ok=[]
        for p in probes:
            if p["status"]!=200: continue
            sl=p["sample"].casefold()
            ct=p["content_type"].casefold()
            if ("json" in ct or p["sample"].startswith(("{","["))) and any(k in sl for k in ("title","program","programme","channel","start","schedule","epg")):
                api_ok.append(p)

        visible_score=max([
            x.get("time_tokens",0)*2 + x.get("keyword_hits",0) + x.get("json_field_hits",0)
            for x in pages
        ] or [0])

        if api_ok:
            verdict="PUBLIC_EPG_API_FOUND"
        elif visible_score>=15:
            verdict="VISIBLE_GUIDE_PROMISING"
        elif any(x.get("status")==200 for x in pages):
            verdict="REACHABLE_NEEDS_DEEPER_ADAPTER"
        elif any(x.get("status") in (401,403) for x in pages):
            verdict="BLOCKED"
        else:
            verdict="UNREACHABLE"

        report["platforms"][platform]={
            "verdict":verdict,
            "visible_score":visible_score,
            "pages":pages,
            "js_hits":js_hits,
            "api_probes":probes,
            "api_ok":api_ok,
        }

    Path("reports").mkdir(exist_ok=True)
    Path("reports/commercial-arab-platforms.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# Commercial Arab platform EPG discovery","",
           "| Platform | Verdict | Pages 200 | Visible score | Public JSON EPG APIs |",
           "|---|---|---:|---:|---:|"]
    for name,d in report["platforms"].items():
        p200=sum(1 for x in d["pages"] if x.get("status")==200)
        lines.append(f'| {name} | {d["verdict"]} | {p200} | {d["visible_score"]} | {len(d["api_ok"])} |')
    Path("reports/commercial-arab-platforms.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({
        k:{
            "verdict":v["verdict"],
            "pages_200":sum(1 for x in v["pages"] if x.get("status")==200),
            "visible_score":v["visible_score"],
            "api_ok":[x["url"] for x in v["api_ok"][:10]],
        } for k,v in report["platforms"].items()
    },ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
