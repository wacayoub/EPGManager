#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public STC TV endpoint discovery.

Discovery only: fetches public web/app pages and public JS assets, extracts
candidate API/EPG/channel/schedule endpoints, and records HTTP reachability.
No login, token harvesting, DRM access, or auth bypass.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

START = [
    "https://web.stctv.com/livetv",
    "https://web.stctv.com/",
    "https://app.stctv.com/",
    "https://www.stctv.com/",
    "https://stctv.com/",
    "https://www.stc.com.sa/en/personal/lifestyle/stctv.html",
    "https://www.stc.com.sa/ar/personal/lifestyle/stctv.html",
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
URL_RE = re.compile(r'''https?://[^"'<>\\\s]+''', re.I)
REL_RE = re.compile(r'''["']([^"']*(?:api|epg|schedule|guide|channel|channels|program|programme|live)[^"']*)["']''', re.I)
HOST_HINT = re.compile(r"(api|epg|schedule|guide|channel|live|content|cms|graphql)", re.I)

def fetch(s,url):
    try:
        r=s.get(url,timeout=20,allow_redirects=True)
        return {
            "ok": True,
            "status": r.status_code,
            "url": url,
            "final_url": r.url,
            "content_type": r.headers.get("content-type",""),
            "text": r.text if len(r.content) < 6_000_000 else r.text[:6_000_000],
            "bytes": len(r.content),
        }
    except Exception as e:
        return {"ok":False,"status":"ERROR","url":url,"error":str(e)[:300],"text":"","bytes":0}

def extract_from_text(base,text):
    urls=[]
    for m in URL_RE.finditer(text):
        u=m.group(0).replace("\\/","/")
        if HOST_HINT.search(u) and u not in urls:
            urls.append(u)
        if len(urls)>=200: break
    rel=[]
    for m in REL_RE.finditer(text):
        v=m.group(1).strip()
        if not v or len(v)>500: continue
        u=urljoin(base,v)
        if u not in rel:
            rel.append(u)
        if len(rel)>=200: break
    return urls, rel

def main():
    s=requests.Session()
    s.headers.update({
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language":"ar,en;q=0.8",
    })
    report={"pages":[],"js_assets":[],"candidate_endpoints":[]}
    js=set()
    candidates=set()

    for url in START:
        row=fetch(s,url)
        text=row.pop("text","")
        page=dict(row)
        if text:
            soup=BeautifulSoup(text,"html.parser")
            page["title"]=soup.title.get_text(" ",strip=True) if soup.title else ""
            page["scripts"]=[]
            for tag in soup.find_all("script",src=True):
                src=urljoin(row.get("final_url",url),tag["src"])
                if src not in js:
                    js.add(src)
                page["scripts"].append(src)
            absu,relu=extract_from_text(row.get("final_url",url),text)
            for x in absu+relu: candidates.add(x)
            page["candidate_count"]=len(absu)+len(relu)
        report["pages"].append(page)

    # Inspect a bounded number of public JS bundles.
    for src in list(js)[:80]:
        row=fetch(s,src)
        text=row.pop("text","")
        item=dict(row)
        if text and row.get("status")==200:
            absu,relu=extract_from_text(row.get("final_url",src),text)
            item["candidate_urls"]=(absu+relu)[:120]
            for x in absu+relu: candidates.add(x)
        report["js_assets"].append(item)

    # Probe only clearly public-looking GET endpoints without credentials/body.
    checked=[]
    for u in sorted(candidates):
        if len(checked)>=120: break
        pu=urlparse(u)
        if pu.scheme not in ("http","https"): continue
        if not HOST_HINT.search(u): continue
        # Avoid obvious auth/account/payment/DRM endpoints.
        low=u.lower()
        if any(x in low for x in ("login","signin","oauth","token","account","payment","drm","widevine","license")):
            continue
        r=fetch(s,u)
        text=r.pop("text","")
        checked.append({
            **r,
            "sample": re.sub(r"\s+"," ",text[:500]).strip() if text else ""
        })
    report["candidate_endpoints"]=checked

    Path("reports").mkdir(exist_ok=True)
    Path("reports/stctv-discovery.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    useful=[x for x in checked if isinstance(x.get("status"),int) and x["status"]==200]
    lines=["# STC TV public endpoint discovery","",
           f"Pages checked: **{len(report['pages'])}**  ",
           f"Public JS assets inspected: **{len(report['js_assets'])}**  ",
           f"Reachable candidate endpoints: **{len(useful)}**","",
           "| Endpoint | HTTP | Content-Type | Sample |",
           "|---|---:|---|---|"]
    for x in useful[:50]:
        sample=(x.get("sample") or "").replace("|","/")[:160]
        lines.append(f'| {x.get("final_url") or x.get("url")} | {x.get("status")} | {x.get("content_type","")} | {sample} |')
    Path("reports/stctv-discovery.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

    print(json.dumps({
        "pages":[{"url":x.get("url"),"status":x.get("status"),"final_url":x.get("final_url"),"scripts":len(x.get("scripts",[]))} for x in report["pages"]],
        "js_assets":len(report["js_assets"]),
        "reachable_candidates":len(useful),
        "reachable":[{"url":x.get("final_url") or x.get("url"),"content_type":x.get("content_type"),"sample":x.get("sample","")[:250]} for x in useful[:30]]
    },ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
