#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

URL="https://www.tabie.net/live"
TIME=re.compile(r"^\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*$")
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-TabieProbe/1.0"

r=requests.get(URL,headers={"User-Agent":UA,"Accept-Language":"ar,en;q=0.8"},timeout=30)
r.raise_for_status()
s=BeautifulSoup(r.text,"html.parser")
rows=[]
for node in s.find_all(string=TIME):
    p=node.parent
    ancestors=[]
    q=p
    for _ in range(5):
        if not q: break
        ancestors.append({
            "tag":q.name,
            "id":q.get("id"),
            "class":q.get("class"),
            "data":{k:v for k,v in q.attrs.items() if str(k).startswith("data-")},
            "text":" ".join(q.stripped_strings)[:500],
        })
        q=q.parent
    rows.append({"time":node.strip(),"ancestors":ancestors})
    if len(rows)>=30: break

# Record likely repeated EPG row/container classes and scripts mentioning EPG.
scripts=[]
for tag in s.find_all("script"):
    raw=tag.get_text("\n",strip=False) or ""
    src=tag.get("src")
    if src or re.search(r"epg|schedule|channel|program",raw,re.I):
        scripts.append({"src":src,"sample":raw[:3000]})
        if len(scripts)>=40: break

channel_nodes=[]
for node in s.select("[data-channel-id]"):
    channel_nodes.append({
        "tag":node.name,
        "id":node.get("id"),
        "class":node.get("class"),
        "data_channel_id":node.get("data-channel-id"),
        "text":" ".join(node.stripped_strings)[:350],
    })
    if len(channel_nodes)>=100: break

asset_probes=[]
for tag in s.find_all("script",src=True):
    src=tag.get("src") or ""
    if "live.js" not in src: continue
    from urllib.parse import urljoin
    u=urljoin(URL,src)
    try:
        rr=requests.get(u,headers={"User-Agent":UA,"Referer":URL},timeout=20)
        txt=rr.text
        urls=sorted(set(re.findall(r"""['"]([^'"]*(?:schedule|epg|program|channel|date)[^'"]*)['"]""",txt,re.I)))
        asset_probes.append({"url":u,"status":rr.status_code,"bytes":len(rr.content),"candidate_strings":urls[:150],"sample":txt[:20000]})
    except Exception as exc:
        asset_probes.append({"url":u,"status":"ERROR","error":str(exc)})

schedule_api_samples=[]
selected_date="2026-09-28"
for channel_id in (23,22,50,32,12,11,34,35):
    u=f"https://www.tabie.net/Live/GetSchedule?channelId={channel_id}&date={selected_date}"
    try:
        rr=requests.get(u,headers={"User-Agent":UA,"Referer":URL,"Accept":"application/json,text/plain,*/*"},timeout=20)
        sample=rr.text[:12000]
        parsed=None
        if "json" in (rr.headers.get("content-type") or "").lower() or sample.lstrip().startswith(("{","[")):
            try: parsed=rr.json()
            except Exception: parsed=None
        schedule_api_samples.append({
            "channel_id":channel_id,
            "status":rr.status_code,
            "content_type":rr.headers.get("content-type",""),
            "bytes":len(rr.content),
            "json":parsed,
            "text_sample":sample if parsed is None else None,
        })
    except Exception as exc:
        schedule_api_samples.append({"channel_id":channel_id,"status":"ERROR","error":str(exc)})

out={
    "status":r.status_code,
    "bytes":len(r.content),
    "time_nodes":len(s.find_all(string=TIME)),
    "rows":rows,
    "channel_nodes":channel_nodes,
    "scripts":scripts,
    "asset_probes":asset_probes,
    "schedule_api_samples":schedule_api_samples,
}
Path("reports").mkdir(exist_ok=True)
Path("reports/tabie-structure.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"status":out["status"],"bytes":out["bytes"],"time_nodes":out["time_nodes"],"first_rows":rows[:5]},ensure_ascii=False))
