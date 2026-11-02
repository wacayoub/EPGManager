#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe public MENA TV-guide platforms without publishing EPG feeds.

This is discovery-only: it records HTTP status, likely guide/channel data,
embedded JSON and candidate API/JSON endpoints. No authentication bypass,
no subscription/private APIs, and no feed publication.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

TARGETS = {
    "gobx": [
        "https://www.gobx.com/en/whats-on",
        "https://www.gobx.com/en/",
    ],
    "shahid": [
        "https://shahid.mbc.net/ar/mbcchannels",
        "https://shahid.mbc.net/en/mbcchannels",
    ],
    "stctv": [
        "https://www.stc.com.sa/ar/personal/lifestyle/stctv.html",
        "https://www.stctv.com/",
    ],
    "starzplay": [
        "https://starzplay.com/en/",
        "https://starzplay.com/en/aboutus",
        "https://starzplay.com/ar/",
    ],
    "jawwy": [
        "https://www.jawwy.tv/",
        "https://www.jawwy.tv/en/",
        "https://cast.jawwy.tv/index.html",
    ],
    "tabie": [
        "https://www.tabie.net/live",
        "https://www.tabie.net/",
    ],
    "roya": [
        "https://roya.tv/",
        "https://en.roya.tv/",
    ],
    "awaan": [
        "https://www.awaan.ae/live/6/Dubai",
        "https://www.awaan.ae/catchup/6/%D9%82%D9%86%D8%A7%D8%A9-%D8%AF%D8%A8%D9%8A",
    ],
    "sba": [
        "https://sbctv.sba.sa/",
    ],
    "sharjah_sba": [
        "https://www.sharjahtv.ae/",
    ],
    "kuwait_media": [
        "https://media.gov.kw/",
        "https://www.media.gov.kw/Frequency.aspx?FreqType=TVKSC",
    ],
    "bahrain_tv": [
        "https://www.bahrain.bh/",
    ],
    "weyyak": [
        "https://weyyak.com/",
    ],
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-Discovery/1.0"
GUIDE_WORDS = ("tv guide","guide","schedule","epg","what's on","whats on","جدول","دليل","البرامج","مواعيد")
CHANNEL_WORDS = ("channel","channels","قناة","قنوات")
API_RE = re.compile(r'''(?:"|')(https?://[^"'\s<>]+|/[^"'\s<>]*(?:api|graphql|schedule|epg|guide|channel)[^"'\s<>]*)(?:"|')''', re.I)
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def inspect_html(url, text):
    soup=BeautifulSoup(text,"html.parser")
    plain=clean(soup.get_text(" ",strip=True))
    lower=plain.casefold()
    scripts=soup.find_all("script")
    json_scripts=[]
    for s in scripts:
        typ=(s.get("type") or "").casefold()
        sid=(s.get("id") or "").casefold()
        raw=s.string or s.get_text("",strip=False) or ""
        if "json" in typ or sid in ("__next_data__","__nuxt__","__apollo_state__"):
            json_scripts.append({"id":sid,"type":typ,"bytes":len(raw)})
    endpoints=[]
    for m in API_RE.finditer(text):
        e=m.group(1).replace("\\/","/")
        if e not in endpoints:
            endpoints.append(e)
        if len(endpoints)>=30: break
    links=[]
    for a in soup.find_all("a",href=True):
        label=clean(a.get_text(" ",strip=True))
        href=urljoin(url,a["href"])
        hay=(label+" "+href).casefold()
        if any(w in hay for w in GUIDE_WORDS+CHANNEL_WORDS):
            links.append({"label":label[:120],"url":href})
        if len(links)>=30: break
    # Extract conservative visible text fragments likely to be channel names.
    channel_fragments=[]
    for tag in soup.find_all(["h1","h2","h3","h4","li","span","div"]):
        t=clean(tag.get_text(" ",strip=True))
        if not t or len(t)>90: continue
        if any(x in t.casefold() for x in ("mbc","sports","sport","dubai","abu dhabi","osn","starz","روتانا","دبي","السعود","قناة","shahid")):
            if t not in channel_fragments:
                channel_fragments.append(t)
        if len(channel_fragments)>=50: break
    return {
        "title": clean(soup.title.get_text(" ",strip=True)) if soup.title else "",
        "bytes": len(text.encode("utf-8","ignore")),
        "guide_keyword_hits": sum(lower.count(x) for x in GUIDE_WORDS),
        "channel_keyword_hits": sum(lower.count(x) for x in CHANNEL_WORDS),
        "visible_time_tokens": len(TIME_RE.findall(plain)),
        "json_scripts": json_scripts[:10],
        "candidate_endpoints": endpoints,
        "guide_links": links,
        "channel_fragments": channel_fragments,
        "text_sample": plain[:1000],
    }

def main():
    sess=requests.Session()
    sess.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8",
    })
    report={"targets":{}}
    for name,urls in TARGETS.items():
        rows=[]
        for url in urls:
            row={"url":url}
            try:
                r=sess.get(url,timeout=25,allow_redirects=True)
                row.update({"status":r.status_code,"final_url":r.url,"content_type":r.headers.get("content-type","")})
                if "html" in row["content_type"].lower() or "<html" in r.text[:1000].lower():
                    row.update(inspect_html(r.url,r.text))
                else:
                    row["bytes"]=len(r.content)
                    row["text_sample"]=r.text[:1000]
            except Exception as exc:
                row.update({"status":"ERROR","error":str(exc)[:300]})
            rows.append(row)
        report["targets"][name]=rows
    Path("reports").mkdir(exist_ok=True)
    Path("reports/platform-epg-discovery.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    # Concise markdown summary.
    lines=["# Platform EPG discovery","", "| Platform | HTTP | Guide hits | Times | JSON | Candidate endpoints |","|---|---:|---:|---:|---:|---:|"]
    for name,rows in report["targets"].items():
        best=max(rows,key=lambda x: (isinstance(x.get("status"),int) and x.get("status")==200, x.get("guide_keyword_hits",0), x.get("visible_time_tokens",0)))
        lines.append(f'| {name} | {best.get("status")} | {best.get("guide_keyword_hits",0)} | {best.get("visible_time_tokens",0)} | {len(best.get("json_scripts",[]))} | {len(best.get("candidate_endpoints",[]))} |')
    Path("reports/platform-epg-discovery.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
