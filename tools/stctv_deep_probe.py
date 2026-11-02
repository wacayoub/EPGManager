#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deep public JS inspection for STC TV metadata/EPG endpoints only."""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

PAGE = "https://web.stctv.com/livetv"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
URL_RE = re.compile(r"""https?://[^"'<>\\\s)]+""", re.I)
PATH_RE = re.compile(r"""["']([^"']{0,120}(?:epg|schedule|programmes?|channels?|listing|linear|livetv|live-tv|guide)[^"']{0,220})["']""", re.I)
KEY_RE = re.compile(r"(epg|schedule|programme|program|channel|listing|linear|guide|baseurl|apiurl|endpoint)", re.I)

def noise(u):
    x=u.lower().split("?")[0]
    return x.endswith((".png",".jpg",".jpeg",".svg",".webp",".gif",".css",".woff",".woff2",".ttf",".mp4",".mpd",".m3u8"))

def main():
    s=requests.Session()
    s.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8"
    })
    r=s.get(PAGE,timeout=25)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    scripts=[urljoin(r.url,t["src"]) for t in soup.find_all("script",src=True)]
    out={"page":PAGE,"scripts":[],"hosts":{},"absolute_urls":[],"path_strings":[],"keyword_snippets":[]}
    urls=set()
    paths=set()
    snippets=[]

    for src in scripts:
        row={"url":src}
        try:
            q=s.get(src,timeout=30)
            row.update({"status":q.status_code,"bytes":len(q.content),"content_type":q.headers.get("content-type","")})
            text=q.text[:12000000]
            for u in URL_RE.findall(text):
                u=u.replace("\\/","/")
                if not noise(u):
                    urls.add(u)
            for p in PATH_RE.findall(text):
                p=p.replace("\\/","/").strip()
                if len(p) < 450:
                    paths.add(p)
            for m in KEY_RE.finditer(text):
                a=max(0,m.start()-180); b=min(len(text),m.end()+260)
                sn=re.sub(r"\s+"," ",text[a:b]).strip()
                if sn and sn not in snippets:
                    snippets.append(sn)
                if len(snippets)>=240:
                    break
        except Exception as e:
            row["error"]=str(e)[:300]
        out["scripts"].append(row)

    relevant=[]
    for u in sorted(urls):
        low=u.lower()
        if any(k in low for k in ("intigral","stctv","theplatform","epg","schedule","program","channel","guide","linear","listing")):
            relevant.append(u)
        try:
            h=urlparse(u).netloc.lower()
            if h:
                out["hosts"][h]=out["hosts"].get(h,0)+1
        except Exception:
            pass

    out["absolute_urls"]=relevant[:400]
    out["path_strings"]=sorted(paths)[:500]
    out["keyword_snippets"]=snippets[:240]

    Path("reports").mkdir(exist_ok=True)
    Path("reports/stctv-deep.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    print("STC_DEEP_SCRIPTS",json.dumps(out["scripts"],ensure_ascii=False))
    print("STC_DEEP_HOSTS",json.dumps(dict(sorted(out["hosts"].items(),key=lambda kv:(-kv[1],kv[0]))[:80]),ensure_ascii=False))
    print("STC_DEEP_URLS",json.dumps(out["absolute_urls"][:180],ensure_ascii=False))
    print("STC_DEEP_PATHS",json.dumps(out["path_strings"][:220],ensure_ascii=False))
    print("STC_DEEP_SNIPPETS",json.dumps(out["keyword_snippets"][:100],ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
