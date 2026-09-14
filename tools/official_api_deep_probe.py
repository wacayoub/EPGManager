#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

UA = "EPGManager-Official-API-Deep-Probe/1.0"

ROUTES = {
    "sharjah": [
        "https://sbauae.faulio.com/api/",
        "https://sbauae.faulio.com/api/v1",
        "https://sbauae.faulio.com/api/v1/channels",
        "https://sbauae.faulio.com/api/v1/programs",
        "https://sbauae.faulio.com/api/v1/program-grid",
        "https://sbauae.faulio.com/api/v1/programgrid",
        "https://sbauae.faulio.com/api/v1/schedule",
        "https://sbauae.faulio.com/api/v1/schedules",
        "https://sbauae.faulio.com/api/v1/epg",
        "https://www.sba.net.ae/ar/program-grid",
        "https://www.sba.net.ae/en/program-grid",
    ],
    "admedia": [
        "https://api.admedia.ae/",
        "https://api.admedia.ae/api",
        "https://api.admedia.ae/api/v1",
        "https://api.admedia.ae/api/v1/channels",
        "https://api.admedia.ae/api/v1/programs",
        "https://api.admedia.ae/api/v1/schedule",
        "https://api.admedia.ae/api/v1/epg",
        "https://api.admedia.ae/channels",
        "https://api.admedia.ae/programs",
    ],
    "playco_epg": [
        "https://epg.aws.playco.com/api/v1.1/",
        "https://epg.aws.playco.com/api/v1.1/channels",
        "https://epg.aws.playco.com/api/v1.1/programs",
        "https://epg.aws.playco.com/api/v1.1/schedule",
        "https://epg.aws.playco.com/api/v1.1/schedules",
        "https://epg.aws.playco.com/api/v1.1/epg",
    ],
    "alfa": [
        "https://orbit.ae/api/profile",
        "https://alfatv.com/ar/tv-guide",
    ],
}

PAGES = {
    "sba": "https://www.sba.net.ae/",
    "majid": "https://www.admn.ae/en/brand/4197603/majid-tv",
    "starz": "https://starzplay.com/en/admn",
    "alfa": "https://alfatv.com/ar/tv-guide",
}

SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
SEARCH_TERMS = [
    "sbauae.faulio.com", "program-grid", "programgrid", "api.admedia.ae",
    "epg.aws.playco.com", "tv-guide", "orbit.ae/api/profile", "PROGRAMS", "guide",
]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return {
                "requested": url,
                "final_url": r.geturl(),
                "status": getattr(r, "status", 200),
                "content_type": r.headers.get("Content-Type", ""),
                "allow": r.headers.get("Allow", ""),
                "bytes": len(data),
                "body": data.decode("utf-8", "replace"),
            }
    except Exception as exc:
        code = getattr(exc, "code", None)
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return {"requested": url, "status": code or "ERROR", "error": str(exc)[:220], "body": body}


def brief(r):
    body = re.sub(r"\s+", " ", r.get("body", "")).strip()
    low = body.casefold()
    interesting = (
        isinstance(r.get("status"), int) and r.get("status") < 400
    ) or any(x in low for x in ("channel", "program", "schedule", "epg", "data", "route"))
    return {
        "requested": r.get("requested"),
        "final_url": r.get("final_url", ""),
        "status": r.get("status"),
        "content_type": r.get("content_type", ""),
        "allow": r.get("allow", ""),
        "bytes": r.get("bytes", 0),
        "interesting": interesting,
        "sample": body[:1200],
        "error": r.get("error", ""),
    }


def contexts(text, term, width=420):
    out = []
    low = text.casefold(); needle = term.casefold(); pos = 0
    while len(out) < 8:
        i = low.find(needle, pos)
        if i < 0:
            break
        a = max(0, i - width); b = min(len(text), i + len(term) + width)
        snippet = re.sub(r"\s+", " ", text[a:b]).strip()
        out.append(snippet)
        pos = i + len(needle)
    return out


def script_urls(base, html):
    out=[]
    for raw in SCRIPT_RE.findall(html):
        if raw.startswith("//"):
            raw="https:"+raw
        url=urllib.parse.urljoin(base, raw)
        if url not in out:
            out.append(url)
    return out[:25]


def scan_page(name, url):
    r=get(url)
    rec={"page":brief(r),"contexts":{},"script_contexts":[]}
    text=r.get("body","")
    for term in SEARCH_TERMS:
        hits=contexts(text,term)
        if hits: rec["contexts"][term]=hits
    for js in script_urls(r.get("final_url",url),text)[:15]:
        jr=get(js,timeout=15)
        jtext=jr.get("body","")
        terms={}
        for term in SEARCH_TERMS:
            hits=contexts(jtext,term,300)
            if hits: terms[term]=hits[:4]
        if terms:
            rec["script_contexts"].append({"url":js,"status":jr.get("status"),"terms":terms})
    return rec


def main():
    out={"schema":1,"routes":{},"pages":{}}
    for name,urls in ROUTES.items():
        out["routes"][name]=[brief(get(u)) for u in urls]
    for name,url in PAGES.items():
        out["pages"][name]=scan_page(name,url)
    Path("output/official-api-deep-probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["OFFICIAL API DEEP PROBE",""]
    for name,rows in out["routes"].items():
        lines.append("[%s ROUTES]"%name.upper())
        for x in rows:
            lines.append("- status=%s bytes=%s ctype=%s %s"%(x["status"],x["bytes"],x["content_type"],x["requested"]))
            if x["sample"]: lines.append("    %s"%x["sample"][:650])
            if x["error"]: lines.append("    ERROR %s"%x["error"])
        lines.append("")
    for name,rec in out["pages"].items():
        lines.append("[%s CONTEXT]"%name.upper())
        for term,hits in rec["contexts"].items():
            for h in hits[:3]: lines.append("- %s :: %s"%(term,h[:800]))
        for s in rec["script_contexts"][:8]:
            lines.append("- SCRIPT %s"%s["url"])
            for term,hits in s["terms"].items():
                for h in hits[:2]: lines.append("    %s :: %s"%(term,h[:700]))
        lines.append("")
    Path("output/official-api-deep-probe.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))

if __name__=="__main__": main()
