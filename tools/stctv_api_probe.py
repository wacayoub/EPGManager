#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe only public STC TV metadata/config endpoints discovered in the web bundle."""
from __future__ import annotations
import json, re
from pathlib import Path
import requests

TARGETS = [
    ("config", "https://jawwy2-prod-cdn.intigral-ott.net/gateway/public/v2/config"),
    ("channels", "https://api.intigral-ott.net/popcorn-api-rs-7.9.4/v3/channels?apikey=stbGDMSTGExy0sVDlZMzNDdUyZ"),
    ("content_api_root", "https://prod-cdn-content-api.intigral-ott.net/content-api-2.7.2"),
    ("bff_root", "https://bff-prod.stctv.com"),
]
KEY_RE=re.compile(r"(epg|schedule|listing|channel|guide|feed|content_channels)",re.I)

def walk(obj,path="$",out=None):
    if out is None: out=[]
    if len(out)>=500: return out
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}"
            if KEY_RE.search(str(k)) or (isinstance(v,str) and KEY_RE.search(v)):
                out.append({"path":p,"value":v if isinstance(v,(str,int,float,bool,type(None))) else type(v).__name__})
            walk(v,p,out)
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:200]):
            walk(v,f"{path}[{i}]",out)
    return out

def main():
    s=requests.Session()
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept":"application/json,text/plain,*/*",
        "Accept-Language":"ar,en;q=0.8",
        "Origin":"https://web.stctv.com",
        "Referer":"https://web.stctv.com/livetv",
    })
    report=[]
    for name,url in TARGETS:
        row={"name":name,"url":url}
        try:
            r=s.get(url,timeout=25,allow_redirects=True)
            row.update({"status":r.status_code,"final_url":r.url,"content_type":r.headers.get("content-type",""),"bytes":len(r.content)})
            text=r.text[:3000000]
            row["sample"]=re.sub(r"\s+"," ",text[:1200]).strip()
            try:
                data=r.json()
                row["json_type"]=type(data).__name__
                row["interesting"]=walk(data)
                if name=="channels":
                    if isinstance(data,dict):
                        row["top_keys"]=list(data.keys())[:80]
                        channels=data.get("channels") or data.get("entries") or data.get("items") or data.get("data")
                        if isinstance(channels,list):
                            row["channel_count"]=len(channels)
                            row["channel_samples"]=channels[:3]
                    elif isinstance(data,list):
                        row["channel_count"]=len(data)
                        row["channel_samples"]=data[:3]
            except Exception as e:
                row["json_error"]=str(e)[:200]
        except Exception as e:
            row["error"]=str(e)[:300]
        report.append(row)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/stctv-api-probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in report:
        print("STC_API_RESULT",json.dumps(row,ensure_ascii=False)[:30000])
    return 0

if __name__=="__main__":
    raise SystemExit(main())
