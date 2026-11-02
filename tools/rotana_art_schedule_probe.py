#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json,re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36 EPG-Scrapers-ScheduleProbe/1.0"
CASABLANCA=ZoneInfo("Africa/Casablanca")

def rotana_tz_minutes():
  # Rotana uses the browser Date.getTimezoneOffset convention:
  # UTC+1 => -60, UTC+0 => 0.
  off=datetime.now(CASABLANCA).utcoffset() or timedelta(0)
  return -int(off.total_seconds()//60)

ROTANA={
  "official.rotana.cinema.ksa":("Rotana Cinema KSA",431),
  "official.rotana.lbc":("LBC",434),
  "official.rotana.khalijia":("Rotana Khalijia",435),
  "official.rotana.drama":("Rotana Drama",436),
  "official.rotana.comedy":("Rotana Comedy",437),
  "official.rotana.classic":("Rotana Classic",438),
  "official.rotana.cinema.egypt":("Rotana Cinema Egypt",439),
  "official.rotana.clip":("Rotana Clip",443),
  "official.rotana.resalah":("Al Resalah",446),
}
ART_GUIDES={
  "official.art.aflam1":("ART Aflam 1",1),
  "official.art.aflam2":("ART Aflam 2",2),
  "official.art.cinema":("ART Cinema",3),
  "official.art.hekayat":("ART Hekayat",4),
  "official.art.hekayat2":("ART Hekayat 2",5),
}
TIME_TITLE_RE=re.compile(r"(?m)^\s*(\d{1,2}:\d{2})\s+(.+?)\s*$")
DATE_RE=re.compile(r"(?m)^\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(20\d{2}-\d{2}-\d{2})\s*$",re.I)
AR=re.compile(r"[\u0600-\u06ff]")

def get(sess,url):
  r=sess.get(url,timeout=25,allow_redirects=True)
  r.raise_for_status()
  return r

def clean(s):
  return re.sub(r"\s+"," ",s or "").strip()

def arabic_score(s):
  letters=[ch for ch in (s or "") if ch.isalpha()]
  if not letters:
    return 0.0
  ar=sum(1 for ch in letters if "\u0600" <= ch <= "\u06ff")
  return 100.0*ar/len(letters)

def prefer_arabic_text(candidates):
  vals=[clean(x) for x in candidates if clean(x)]
  if not vals:
    return ""
  vals.sort(key=lambda x:(arabic_score(x),len(x)),reverse=True)
  return vals[0]

def rotana_channel(sess,cid,name,chid):
  tz=rotana_tz_minutes()
  url=f"https://www.rotana.net/ar/streams?channel={chid}&tz={tz}"
  r=get(sess,url)
  soup=BeautifulSoup(r.text,"html.parser")
  text=soup.get_text("\n",strip=True)
  # Parse day blocks robustly even when HTML headings/list items add spacing.
  day_re=re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|الاثنين|الثلاثاء|الأربعاء|الاربعاء|الخميس|الجمعة|السبت|الأحد|الاحد)\s+(20\d{2}-\d{2}-\d{2})",re.I)
  marks=list(day_re.finditer(text))
  events=[]
  for i,m in enumerate(marks):
    date=re.search(r"(20\d{2}-\d{2}-\d{2})",m.group(0)).group(1)
    block=text[m.end():(marks[i+1].start() if i+1<len(marks) else len(text))]
    # Each schedule row is HH:MM followed by the title, possibly separated by newlines.
    for mt in re.finditer(r"(?m)(?:^|\n)\s*(\d{1,2}:\d{2})\s+([^\n]+)",block):
      hhmm,title=mt.group(1),clean(mt.group(2))
      if not title or title.lower() in {"search","live"}:
        continue
      try:
        dt=datetime.strptime(date+" "+hhmm,"%Y-%m-%d %H:%M").replace(tzinfo=CASABLANCA).astimezone(timezone.utc)
      except Exception:
        continue
      events.append({"start":dt.isoformat(),"title":title})
  events=sorted(events,key=lambda x:x["start"])
  for i,e in enumerate(events):
    if i+1<len(events):
      e["stop"]=events[i+1]["start"]
    else:
      e["stop"]=(datetime.fromisoformat(e["start"])+timedelta(hours=2)).isoformat()
  now=datetime.now(timezone.utc)
  future=[e for e in events if datetime.fromisoformat(e["stop"])>now]
  sample=future[0] if future else (events[0] if events else {})
  return {
    "id":cid,"name":name,"provider":"official","source":"rotana",
    "url":url,"programmes":len(future),
    "future_hours":round(max([(datetime.fromisoformat(e["stop"])-now).total_seconds()/3600 for e in future] or [0]),1),
    "sample_title":sample.get("title",""),"sample_desc":"",
    "events":future[:120],
    "status":"VALID_SCHEDULE" if len(future)>=4 else "INSUFFICIENT_SCHEDULE"
  }

def art_programme_pages(sess):
  found=[]
  for base in ("https://www.artonline.tv/","https://www.artonline.tv/guide"):
    try:
      r=get(sess,base)
    except Exception:
      continue
    soup=BeautifulSoup(r.text,"html.parser")
    for a in soup.find_all("a",href=True):
      href=urljoin(r.url,a["href"])
      if re.search(r"/(?:Series|Movie)\?id=\d+",href,re.I) and href not in found:
        found.append(href)
  # Also known programme pages surfaced by public discovery/search.
  for pid in (221,265,4401,4405,5531,5796,5800,5806,5809,5797,5798,5799,5801,5802,5803,5804,5805,5807,5808):
    u=f"https://www.artonline.tv/Series?id={pid}"
    if u not in found: found.append(u)
  return found[:120]

def parse_art_page(sess,url):
  try:
    r=get(sess,url)
  except Exception:
    return None
  soup=BeautifulSoup(r.text,"html.parser")
  h1=soup.find("h1")
  title=clean(h1.get_text(" ",strip=True) if h1 else (soup.title.get_text(" ",strip=True) if soup.title else ""))
  raw=soup.get_text("\n",strip=True)
  text=clean(raw)

  # Description: first substantial paragraph/content block before scheduling metadata.
  desc_candidates=[]
  for p in soup.find_all(["p","div"]):
    t=clean(p.get_text(" ",strip=True))
    if len(t)>=80 and "تشاهدونه على قناة" not in t and "جميع الحقوق محفوظة" not in t:
      if not any(x in t for x in ("تواصل معنا","أعلن معنا","اشترك معنا","قنواتنا الرئيسية")):
        desc_candidates.append(t)
  desc=prefer_arabic_text(desc_candidates)

  # ART programme pages explicitly state: "تشاهدونه على قناة <channel>".
  mch=re.search(r"تشاهدونه\s+على\s+قناة\s+([^\n\r]+)",raw,re.I)
  channel=clean(mch.group(1)) if mch else ""
  amap=[
    ("حكايات 2","ART Hekayat 2"),
    ("حكايات2","ART Hekayat 2"),
    ("حكايات","ART Hekayat"),
    ("أفلام 1","ART Aflam 1"),
    ("أفلام1","ART Aflam 1"),
    ("افلام 1","ART Aflam 1"),
    ("افلام1","ART Aflam 1"),
    ("أفلام 2","ART Aflam 2"),
    ("أفلام2","ART Aflam 2"),
    ("افلام 2","ART Aflam 2"),
    ("افلام2","ART Aflam 2"),
    ("سينما","ART Cinema"),
  ]
  mapped=""
  for k,v in amap:
    if k in channel:
      mapped=v
      break
  channel=mapped or channel

  times=re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b",raw)
  return {"url":url,"title":title.split(" : شبكة",1)[0],"desc":desc,"channel":channel,"times":times[:12]}

def art_channels(sess):
  by_name={name:[] for name,_ in ART_GUIDES.values()}
  for u in art_programme_pages(sess):
    x=parse_art_page(sess,u)
    if not x or not x["channel"] or not x["title"]: continue
    if x["channel"] in by_name:
      by_name[x["channel"]].append(x)
  out=[]
  for cid,(name,gid) in ART_GUIDES.items():
    items=by_name.get(name,[])
    sample=items[0] if items else {}
    out.append({
      "id":cid,"name":name,"provider":"official","source":"artonline",
      "url":f"https://www.artonline.tv/guide/{gid}",
      "programmes":len(items),"future_hours":0.0,
      "sample_title":sample.get("title",""),"sample_desc":sample.get("desc",""),"text_language":"ar",
      "events":[{"title":x["title"],"times":x["times"],"url":x["url"]} for x in items[:30]],
      "status":"PROGRAMME_SAMPLES_FOUND" if items else "NO_PROGRAMME_SAMPLE"
    })
  return out

def main():
  s=requests.Session()
  s.headers.update({"User-Agent":UA,"Accept-Language":"ar,en;q=0.8"})
  rows=[]
  for cid,(name,chid) in ROTANA.items():
    try: rows.append(rotana_channel(s,cid,name,chid))
    except Exception as e:
      tz=rotana_tz_minutes()
      rows.append({"id":cid,"name":name,"provider":"official","source":"rotana","url":f"https://www.rotana.net/ar/streams?channel={chid}&tz={tz}","programmes":0,"future_hours":0.0,"sample_title":"","sample_desc":"","events":[],"status":"ERROR","error":str(e)[:200]})
  rows.extend(art_channels(s))
  report={"generated_at":datetime.now(timezone.utc).isoformat(),"channels":rows}
  Path("reports").mkdir(exist_ok=True)
  Path("reports/rotana-art-programmes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  import csv
  fields=["id","name","provider","source","status","programmes","future_hours","sample_title","sample_desc","url"]
  with Path("reports/rotana-art-programmes.csv").open("w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for r in rows:w.writerow({k:r.get(k,"") for k in fields})
  print(json.dumps({"channels":[{k:r.get(k) for k in ("id","name","source","status","programmes","future_hours","sample_title")} for r in rows]},ensure_ascii=False))
  return 0
if __name__=="__main__":
  raise SystemExit(main())
