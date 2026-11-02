#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract small contexts around STC TV public metadata route identifiers."""
import re, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

PAGE="https://web.stctv.com/livetv"
TARGETS=[
 "gateway/public/v2/config",
 "GATEWAY_CONFIGURATION",
 "CONTENT_CHANNELS_CDN_SCHEDULE",
 "CONTENT_CHANNELS_MINI_EPG",
 "all_listings_feed",
 "getChannelScheduleByDataSource",
 "popcorn-api-rs-7.9.29",
 "bff-prod.stctv.com",
 "NX_PUBLIC_CHANNEL_CONTENT_CDN_BASE_URL",
 "epgNextDays",
 "epgPreviousDays",
]
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36","Accept-Language":"ar,en;q=0.8"})
r=s.get(PAGE,timeout=25); r.raise_for_status()
soup=BeautifulSoup(r.text,"html.parser")
scripts=[urljoin(r.url,t["src"]) for t in soup.find_all("script",src=True)]
for src in scripts:
    q=s.get(src,timeout=30); text=q.text[:12000000]
    print("BUNDLE",src,q.status_code,len(q.content))
    for target in TARGETS:
        positions=[m.start() for m in re.finditer(re.escape(target),text,re.I)]
        for idx,pos in enumerate(positions[:4]):
            a=max(0,pos-1200); b=min(len(text),pos+len(target)+1800)
            ctx=re.sub(r"\s+"," ",text[a:b])
            print("TARGET_CONTEXT",target,idx,ctx)
