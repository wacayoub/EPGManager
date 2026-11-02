#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deep official Arab EPG discovery, excluding Morocco.

Second-stage discovery against official/local/government media only.
Crawls a small bounded set of same-host programme/schedule pages and public JS,
extracts public API candidates, and scores whether a real EPG path exists.
No authentication, anti-bot bypass, DRM access, or production publication.
"""
from __future__ import annotations
import json, re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup

TARGETS = {
    "Egypt": ["https://www.maspero.eg/"],
    "Libya": ["https://www.ltv.ly/"],
    "Palestine": ["https://www.pbc.ps/"],
    "Yemen": ["https://yementv.tv/"],
    "Kuwait": ["https://media.gov.kw/", "https://www.media.gov.kw/Frequency.aspx?FreqType=TVKSC"],
    "UAE-Dubai": ["https://www.dubaiplus.net/"],
    "Jordan": ["https://roya.tv/", "https://www.jrtv.gov.jo/"],
    "Lebanon": ["https://www.teleliban.com.lb/"],
    "Bahrain": ["https://www.bahrain.bh/"],
    "Somalia": ["https://sntv.so/"],
    "Syria": ["https://www.ortas.online/"],
    "Comoros": ["https://www.ortc.km/"],
    "Djibouti": ["https://rtd.dj/"],
    "Algeria": ["https://www.entv.dz/"],
    "Iraq": ["https://www.imn.iq/ar"],
    "Qatar": [
        "https://www.alkass.net/tvguide",
        "https://dirorigin.alkass.net/tvguide",
        "https://www.tabie.net/live",
        "https://www.qatartv.com/",
    ],
    "Saudi Arabia": ["https://sbctv.sba.sa/","https://sba.sa/"],
    "Tunisia": ["https://www.tunisiatv.tn/ar", "https://tunisiatv.tn/ar", "https://www.television.tn/"],
    "Mauritania": ["https://www.mauritania.mr/"],
    "Sudan": ["https://sudantv.net/"],
    "UAE-Sharjah": ["https://www.sharjahtv.ae/"],
    "UAE-AbuDhabi": ["https://www.adtv.ae/"],
}

KEYWORDS = (
    "epg","schedule","guide","program","programme","programs","programmes",
    "جدول","برامج","البرامج","مواعيد","دليل","البث","القنوات","قناة"
)
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
DATE_RE = re.compile(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")
API_PAT = re.compile(r'''(?:"|')(https?://[^"'<>\\\s]+|/[^"'<>\\\s]*(?:api|graphql|epg|schedule|guide|program|programme|channel|channels)[^"'<>\\\s]*)(?:"|')''', re.I)
JSON_HINT = re.compile(r'"(?:title|name|start|startTime|begin|end|stop|duration|channel|program|programme)"\s*:', re.I)
BAD = ("login","signin","oauth","token","payment","account","drm","widevine","license","captcha")

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-OfficialDeep/1.0"

def same_site(a,b):
    ha=urlparse(a).hostname or ""
    hb=urlparse(b).hostname or ""
    ha=ha.lower().removeprefix("www.")
    hb=hb.lower().removeprefix("www.")
    return ha==hb or ha.endswith("."+hb) or hb.endswith("."+ha)

def clean(s):
    return re.sub(r"\s+"," ",s or "").strip()

def fetch(sess,url):
    try:
        r=sess.get(url,timeout=18,allow_redirects=True)
        return r,None
    except Exception as e:
        return None,str(e)[:240]

def inspect_page(base,r):
    text=r.text[:4_000_000]
    soup=BeautifulSoup(text,"html.parser")
    plain=clean(soup.get_text(" ",strip=True))
    low=plain.casefold()
    kw=sum(low.count(k.casefold()) for k in KEYWORDS)
    times=len(TIME_RE.findall(plain))
    dates=len(DATE_RE.findall(plain))
    json_hits=len(JSON_HINT.findall(text))
    links=[]
    js=[]
    apis=[]
    for a in soup.find_all("a",href=True):
        u=urldefrag(urljoin(r.url,a["href"]))[0]
        label=clean(a.get_text(" ",strip=True))
        hay=(label+" "+u).casefold()
        if any(k.casefold() in hay for k in KEYWORDS):
            links.append({"label":label[:120],"url":u})
        if len(links)>=60: break
    for s in soup.find_all("script",src=True):
        u=urljoin(r.url,s["src"])
        if same_site(base,u):
            js.append(u)
        if len(js)>=30: break
    for m in API_PAT.finditer(text):
        u=urljoin(r.url,m.group(1).replace("\\/","/"))
        if not any(b in u.lower() for b in BAD) and u not in apis:
            apis.append(u)
        if len(apis)>=80: break
    return {
        "title": clean(soup.title.get_text(" ",strip=True)) if soup.title else "",
        "keyword_hits":kw,
        "time_tokens":times,
        "date_tokens":dates,
        "json_field_hits":json_hits,
        "links":links,
        "js_assets":js,
        "api_candidates":apis,
        "sample":plain[:900],
    }

def main():
    sess=requests.Session()
    sess.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8",
    })
    report={"countries":{}}
    for country,starts in TARGETS.items():
        rows=[]
        q=deque(starts)
        seen=set()
        js_to_scan=set()
        api_candidates=set()
        while q and len(seen)<10:
            url=q.popleft()
            if url in seen: continue
            seen.add(url)
            r,err=fetch(sess,url)
            if err:
                rows.append({"url":url,"status":"ERROR","error":err})
                continue
            row={"url":url,"status":r.status_code,"final_url":r.url,"content_type":r.headers.get("content-type","")}
            if r.status_code==200 and ("html" in row["content_type"].lower() or "<html" in r.text[:800].lower()):
                info=inspect_page(starts[0],r)
                row.update(info)
                for x in info["api_candidates"]: api_candidates.add(x)
                for x in info["js_assets"]: js_to_scan.add(x)
                for l in info["links"]:
                    u=l["url"]
                    if same_site(starts[0],u) and u not in seen and len(q)<30:
                        q.append(u)
            rows.append(row)

        js_hits=[]
        for src in list(js_to_scan)[:20]:
            r,err=fetch(sess,src)
            if err or not r or r.status_code!=200: continue
            text=r.text[:4_000_000]
            found=[]
            for m in API_PAT.finditer(text):
                u=urljoin(r.url,m.group(1).replace("\\/","/"))
                if not any(b in u.lower() for b in BAD) and u not in found:
                    found.append(u); api_candidates.add(u)
                if len(found)>=80: break
            if found:
                js_hits.append({"url":src,"api_candidates":found[:80]})

        probes=[]
        for u in sorted(api_candidates):
            if len(probes)>=30: break
            if not same_site(starts[0],u) and not any(h in (urlparse(u).hostname or "").lower() for h in ("api","cdn","cloudfront")):
                continue
            r,err=fetch(sess,u)
            if err:
                continue
            sample=clean(r.text[:700]) if r.text else ""
            probes.append({
                "url":u,"status":r.status_code,"content_type":r.headers.get("content-type",""),
                "bytes":len(r.content),"sample":sample[:500]
            })

        # Conservative verdict: we need either visible schedule signals or an accessible API-like endpoint
        # returning JSON with schedule-ish fields.
        visible_score=max([
            (x.get("time_tokens",0)*2 + x.get("keyword_hits",0) + x.get("json_field_hits",0))
            for x in rows
        ] or [0])
        api_ok=[]
        for p in probes:
            if p["status"]==200 and ("json" in p["content_type"].lower() or p["sample"].startswith(("{","["))):
                sl=p["sample"].casefold()
                if any(k in sl for k in ("title","program","programme","channel","start","schedule","epg")):
                    api_ok.append(p)
        if api_ok:
            verdict="API_FOUND"
        elif visible_score>=12:
            verdict="VISIBLE_SCHEDULE"
        elif any(x.get("status")==200 for x in rows):
            verdict="REACHABLE_NO_EPG_YET"
        elif any(x.get("status") in (401,403) for x in rows):
            verdict="BLOCKED"
        else:
            verdict="UNREACHABLE"

        report["countries"][country]={
            "verdict":verdict,
            "visible_score":visible_score,
            "pages":rows,
            "js_hits":js_hits,
            "api_probes":probes,
            "api_ok":api_ok,
        }

    Path("reports").mkdir(exist_ok=True)
    Path("reports/official-arab-media-deep.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# Deep official Arab EPG discovery","",
           "| Country | Verdict | Pages 200 | Best schedule score | Confirmed JSON APIs |",
           "|---|---|---:|---:|---:|"]
    for country,data in report["countries"].items():
        p200=sum(1 for x in data["pages"] if x.get("status")==200)
        lines.append(f'| {country} | {data["verdict"]} | {p200} | {data["visible_score"]} | {len(data["api_ok"])} |')
    Path("reports/official-arab-media-deep.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({
        c:{
            "verdict":d["verdict"],
            "pages_200":sum(1 for x in d["pages"] if x.get("status")==200),
            "visible_score":d["visible_score"],
            "api_ok":[{"url":x["url"],"sample":x["sample"][:220]} for x in d["api_ok"][:5]]
        }
        for c,d in report["countries"].items()
    },ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
