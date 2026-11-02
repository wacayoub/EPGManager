#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

TARGETS={
 "alkass":"https://dirorigin.alkass.net/tvguide",
 "tunisiatv":"https://tunisiatv.tn/ar",}
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPGManager-DirectProbe/1.0"
TIME=re.compile(r"(?:^|\s)(?:[01]?\d|2[0-3])[:.]\d{2}(?:\s*(?:AM|PM|ص|م))?(?:\s|$)",re.I)
ENDPOINT=re.compile(r"""(?:https?:)?//[^"'\s<>]+|/[A-Za-z0-9_./?=&%-]*(?:api|schedule|guide|program|epg|ajax)[A-Za-z0-9_./?=&%-]*""",re.I)

sess=requests.Session(); sess.headers.update({"User-Agent":UA,"Accept-Language":"ar,en;q=0.8"})
out={}
for name,url in TARGETS.items():
 row={"url":url}
 try:
  r=sess.get(url,timeout=30,allow_redirects=True)
  row.update({"status":r.status_code,"final_url":r.url,"bytes":len(r.content),"content_type":r.headers.get("content-type","")})
  s=BeautifulSoup(r.text,"html.parser")
  row["title"]=s.title.get_text(" ",strip=True) if s.title else ""
  samples=[]
  for node in s.find_all(string=TIME):
   q=node.parent
   anc=[]
   for _ in range(4):
    if not q: break
    attrs={k:v for k,v in q.attrs.items() if k in {"id","class","data-id","data-channel","data-channel-id","data-date","data-day","data-time","href"} or str(k).startswith("data-")}
    anc.append({"tag":q.name,"attrs":attrs,"text":" ".join(q.stripped_strings)[:600]})
    q=q.parent
   samples.append({"time_text":str(node).strip()[:120],"ancestors":anc})
   if len(samples)>=30: break
  row["time_samples"]=samples
  scripts=[]
  endpoints=set()
  for tag in s.find_all("script"):
   src=tag.get("src")
   raw=tag.get_text("\n",strip=False) or ""
   if src:
    endpoints.add(urljoin(r.url,src))
   for m in ENDPOINT.findall(raw[:300000]):
    endpoints.add(urljoin(r.url,m))
   typ=(tag.get("type") or "").lower()
   if ("json" in typ or re.search(r"schedule|epg|program|channel|ajax|fetch\(",raw,re.I)) and len(scripts)<20:
    scripts.append({"src":urljoin(r.url,src) if src else None,"type":typ,"sample":raw[:12000]})
  row["scripts"]=scripts
  row["candidate_endpoints"]=sorted(endpoints)[:200]
  forms=[]
  for f in s.find_all("form"):
   forms.append({"action":urljoin(r.url,f.get("action") or ""),"method":f.get("method"),"text":" ".join(f.stripped_strings)[:500]})
  row["forms"]=forms[:20]
  links=[]
  for a in s.find_all("a",href=True):
   txt=" ".join(a.stripped_strings)
   href=urljoin(r.url,a.get("href"))
   if re.search(r"schedule|guide|programme|program|epg|جدول|برامج",txt+" "+href,re.I):
    links.append({"text":txt[:200],"url":href})
  row["guide_links"]=links[:100]

  details={}
  if name=="alkass":
   tables=[]
   for i,t in enumerate(s.select("table.team-result")):
    holder=t.parent
    imgs=[]
    q=holder
    for _ in range(4):
     if not q: break
     for im in q.find_all("img",src=True,limit=8):
      item={"src":urljoin(r.url,im.get("src")),"alt":im.get("alt"),"title":im.get("title")}
      if item not in imgs: imgs.append(item)
     if imgs: break
     q=q.parent
    tables.append({
      "index":i,
      "attrs":t.attrs,
      "images":imgs[:8],
      "previous_heading": (t.find_previous(["h1","h2","h3","h4","h5"]) or {}).get_text(" ",strip=True) if t.find_previous(["h1","h2","h3","h4","h5"]) else "",
      "rows":[" | ".join(tr.stripped_strings) for tr in t.select("tr")[:6]],
    })
   details["tables"]=tables
  elif name=="tunisiatv":
   try:
    rr=sess.get("https://tunisiatv.tn/ar/programme",timeout=30)
    ss=BeautifulSoup(rr.text,"html.parser")
    details["programme_status"]=rr.status_code
    details["programme_bytes"]=len(rr.content)
    details["programme_text_matches"]=[x[:500] for x in ss.stripped_strings if re.search(r"الوطنية|غدا|اليوم|دليل البرامج",x)][:80]
    details["programme_links"]=[{"text":" ".join(a.stripped_strings)[:200],"href":urljoin(rr.url,a.get("href"))} for a in ss.find_all("a",href=True) if re.search(r"programme|program|الوطنية|غد|اليوم",(" ".join(a.stripped_strings)+" "+a.get("href")),re.I)][:100]
    details["selects"]=[{"name":sel.get("name"),"id":sel.get("id"),"options":[{"value":o.get("value"),"text":" ".join(o.stripped_strings)} for o in sel.find_all("option")]} for sel in ss.find_all("select")][:20]
   except Exception as exc:
    details["programme_error"]=str(exc)
  row["source_details"]=details
 except Exception as exc:
  row["status"]="ERROR"; row["error"]=str(exc)
 out[name]=row

Path("reports").mkdir(exist_ok=True)
Path("reports/direct-mena-structure.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
for name,row in out.items():
 print(name,row.get("status"),row.get("bytes"),"times",len(row.get("time_samples",[])),"endpoints",len(row.get("candidate_endpoints",[])))
