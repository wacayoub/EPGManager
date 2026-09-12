#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager Morocco Cloud - SNRT + Arryadia + 2M + Chada + Medi1.
Runs on GitHub Actions. The Vu+ only downloads morocco.xml.gz.
"""
from __future__ import annotations
import argparse,gzip,hashlib,html,json,re,time,unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from datetime import datetime,timedelta,time as dtime
from pathlib import Path
from xml.etree import ElementTree as ET
import requests
from bs4 import BeautifulSoup
from lxml import html as LH
from zoneinfo import ZoneInfo
try: import cloudscraper
except Exception: cloudscraper=None

TZ=ZoneInfo("Africa/Casablanca"); PARIS=ZoneInfo("Europe/Paris")
UA="Mozilla/5.0 EPGManagerCloud/8.2.2-rc23"
CHANNELS={
 "AlAoula":"Al Aoula","Arrabiaa":"Attaqafia / Arrabiaa","AlMaghribiya":"Al Maghribia",
 "Assadisa":"Assadissa","Tamazight":"Tamazight","AFLAM.ma":"Aflam TV",
 "Arryadia_HD":"Arryadia HD","Arryadia_TNT":"Arryadia TNT","Arryadia_HD1":"Arryadia HD1",
 "Arryadia_HD2":"Arryadia HD2","Arryadia_HD3":"Arryadia HD3","2M":"2M","Chada TV":"Chada TV",
 "MEDI1TV_AR.ma":"Medi1 TV Arabic","MEDI1TV_MAGHREB.ma":"Medi1 TV Maghreb"}
GROUPS={"snrt":{"AlAoula","Arrabiaa","AlMaghribiya","Assadisa","Tamazight","AFLAM.ma"},
 "arryadia":{"Arryadia_HD","Arryadia_TNT","Arryadia_HD1","Arryadia_HD2","Arryadia_HD3"},
 "2m_chada":{"2M","Chada TV"},"medi1":{"MEDI1TV_AR.ma","MEDI1TV_MAGHREB.ma"}}
SNRT={"AlAoula":"https://www.snrt.ma/ar/node/1208","Arrabiaa":"https://www.snrt.ma/ar/node/4071",
 "AlMaghribiya":"https://www.snrt.ma/ar/node/4072","Assadisa":"https://www.snrt.ma/ar/node/4073",
 "Tamazight":"https://www.snrt.ma/ar/node/4075"}
MEDI1=(
 ("MEDI1TV_AR.ma",("https://www.medi1tv.com/ar/grille/arabic","https://www.medi1tv.ma/ar/grille/arabic")),
 ("MEDI1TV_MAGHREB.ma",("https://www.medi1tv.ma/ar/grille/maghreb","https://www.medi1tv.com/ar/grille/maghreb")))
ARR_IDS=["Arryadia_HD","Arryadia_TNT","Arryadia_HD1","Arryadia_HD2","Arryadia_HD3"]
ARR_TAGS={r"\btnt\b":"Arryadia_TNT",r"\bsat\b":"Arryadia_HD",r"\bhd1\b":"Arryadia_HD1",r"\bhd2\b":"Arryadia_HD2",r"\bhd3\b":"Arryadia_HD3"}
NEWS={"الظهيرة":"أخبار الظهيرة","الأمازيغية":"الأخبار الأمازيغية","الفرنسية":"الأخبار الفرنسية",
 "الإسبانية":"الأخبار الإسبانية","الرئيسية":"الأخبار الرئيسية","الأخيرة":"الأخبار الأخيرة","الرياضية":"أخبار الرياضة"}
T2M={"al akhbar":"الأخبار","sabahiyat 2m":"2M صباحيات","sabahiyat":"2M صباحيات",
 "charqi ou gharbi":"شرقي أو غربي","charqi ou lgharbi":"شرقي أو غربي","soiree chaabi":"سهرة شعبية",
 "soiree cha3bi":"سهرة شعبية","attahssina":"التحصينة","at tahssina":"التحصينة",
 "priere du vendredi":"صلاة الجمعة","priere vendredi":"صلاة الجمعة","ayne lkebrite":"عين الكبريت",
 "ayn lkebrite":"عين الكبريت","wlad 3li":"ولاد علي","oulad 3li":"ولاد علي","jt arabe":"الأخبار بالعربية",
 "journal amazigh":"الأخبار بالأمازيغية","al massaiya":"المسائية","al dahira":"الظهيرة","rachid show":"رشيد شو",
 "moudawala":"مداولة","lmktoub":"المكتوب","dar nsa":"دار النسا","najm chaabi":"النجم الشعبي"}
CHADA={
 "dandana":("دندنة","برنامج موسيقي على شدى تي في يستضيف فنانين ويتابع جديد أعمالهم، مع حوار وفقرات موسيقية."),
 "dandanah":("دندنة","برنامج موسيقي على شدى تي في يستضيف فنانين ويتابع جديد أعمالهم، مع حوار وفقرات موسيقية."),
 "l botola":("البطولة","برنامج رياضي يتابع أخبار كرة القدم المغربية وأبرز مباريات البطولة، مع التحليل والنقاش الرياضي."),
 "lbotola":("البطولة","برنامج رياضي يتابع أخبار كرة القدم المغربية وأبرز مباريات البطولة، مع التحليل والنقاش الرياضي."),
 "100 var":("100% VAR","مجلة رياضية لتحليل أخبار كرة القدم والمباريات، مع نقاشات فنية وتكتيكية وتركيز على الكرة المغربية."),
 "chada al ousra":("شدى الأسرة","برنامج اجتماعي وأسري يناقش قضايا الأسرة والمجتمع ويستضيف مختصين وضيوفاً."),
 "assahm":("السهم","برنامج حواري يتناول مواضيع الساعة وقضايا المجتمع مع ضيوف ونقاش."),
 "9hiwa ola atay":("قهيوة ولا أتاي","موعد خفيف يجمع الحوار والترفيه ومواضيع الحياة اليومية في أجواء مغربية."),
 "hbab rabab":("حباب رباب","برنامج ترفيهي وفني على شدى تي في مع فقرات متنوعة وضيوف."),
 "funny m3a nani":("فاني مع ناني","برنامج ترفيهي يقدم فقرات خفيفة ومواقف مرحة وضيوفاً متنوعين."),
 "din wa dounia":("دين ودنيا","برنامج ديني واجتماعي يتناول قضايا الحياة اليومية من منظور توعوي مبسط."),
 "mag info":("MAG INFO - الأخبار","موعد إخباري يقدم أبرز الأخبار والمستجدات الوطنية والدولية."),
 "chada cover":("شدى كوفر","برنامج موسيقي يسلط الضوء على المواهب والأصوات الجديدة."),}
HOSTS={"imad ntifi":"عماد النتيفي","fakherddine":"فخر الدين الرجحي","houssine chahb":"حسين شهب",
 "jamila ouyoub":"جميلة أيوب","sarah azmi":"سارة عزمي","imane bououlid":"إيمان بوعوليد الإدريسي"}

def log(x): print("[%s] %s"%(datetime.now(TZ).strftime("%F %T %Z"),x),flush=True)
def clean(x): return re.sub(r"\s+"," ",html.unescape(str(x or "")).replace("\xa0"," ")).strip()
def norm(x):
 x=clean(x).casefold().replace("’","'"); x="".join(c for c in unicodedata.normalize("NFKD",x) if not unicodedata.combining(c))
 return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9%]+"," ",x)).strip()
def ar(x): return bool(re.search(r"[\u0600-\u06ff]",str(x or "")))
def lang(x,default="ar"): return "ar" if ar(x) else ("fr" if re.search(r"[A-Za-zÀ-ÿ]",str(x or "")) else default)
def xdt(d): return d.strftime("%Y%m%d%H%M%S %z")

class Http:
 def __init__(self):
  self.s=(cloudscraper.create_scraper() if cloudscraper else requests.Session()); self.s.headers.update({"User-Agent":UA,"Accept-Language":"ar,fr;q=.9,en;q=.7"})
 def get(self,url,**kw):
  last=None
  for n in range(3):
   try:
    r=self.s.get(url,timeout=18,**kw); r.raise_for_status(); return r
   except Exception as e: last=e; time.sleep(.7*(n+1))
  raise last

@dataclass
class Event:
 channel:str; start:datetime; title:str; desc:str=""; stop:datetime|None=None; tl:str="ar"; dl:str="ar"; source:str=""

def infer(rows):
 by=defaultdict(list)
 for e in rows: by[e.channel].append(e)
 for rr in by.values():
  rr.sort(key=lambda e:e.start)
  for i,e in enumerate(rr):
   if not e.stop: e.stop=rr[i+1].start if i+1<len(rr) else e.start+timedelta(hours=1)

def previous(path):
 if not path.exists(): return []
 try:
  raw=gzip.decompress(path.read_bytes()) if path.suffix==".gz" else path.read_bytes(); root=ET.fromstring(raw); out=[]
  for p in root.findall("programme"):
   def dt(v): return datetime.strptime(v[:19],"%Y%m%d%H%M%S %z").astimezone(TZ)
   t=p.find("title"); d=p.find("desc"); out.append(Event(p.get("channel"),dt(p.get("start")),t.text or "",d.text if d is not None else "",dt(p.get("stop")) if p.get("stop") else None,t.get("lang") or "ar",d.get("lang") if d is not None else "ar","old"))
  return out
 except Exception as e: log("Previous feed unreadable: %s"%e); return []

def valid(group,rows):
 now=datetime.now(TZ); rr=[e for e in rows if e.channel in GROUPS[group] and e.start<now+timedelta(days=8) and (e.stop or e.start+timedelta(hours=1))>now-timedelta(hours=12)]
 c=defaultdict(int)
 for e in rr:c[e.channel]+=1
 if group=="snrt": ok=sum(c[x] for x in GROUPS[group] if x!="AFLAM.ma")>=10 and sum(bool(c[x]) for x in GROUPS[group] if x!="AFLAM.ma")>=3
 elif group=="arryadia": ok=sum(c.values())>=10
 elif group=="2m_chada": ok=c["2M"]>=6 and c["Chada TV"]>=3
 else: ok=sum(c.values())>=6 and max(c.values() or [0])>=3
 return ok,"events=%d"%sum(c.values())

def google_ar(http,text):
 if not clean(text) or ar(text): return clean(text)
 try:
  r=http.get("https://translate.googleapis.com/translate_a/single",params={"client":"gtx","sl":"auto","tl":"ar","dt":"t","q":text}); data=r.json(); y=clean("".join(x[0] for x in data[0] if x and x[0])); return y if ar(y) else clean(text)
 except Exception:return clean(text)

def tr2m_title(http,title):
 raw=clean(title); n=norm(raw)
 if n in T2M:return T2M[n]
 for pre,apre in (("serie marocaine","مسلسل مغربي"),("serie turque","مسلسل تركي"),("serie","مسلسل"),("film marocain","فيلم مغربي"),("film","فيلم"),("documentaire","وثائقي"),("rediffusion","إعادة")):
  if n.startswith(pre+" "):
   rest=raw[len(pre):].strip(" :-"); return apre+(" : "+(T2M.get(norm(rest)) or google_ar(http,rest)) if rest else "")
 return google_ar(http,raw)
def tr2m_desc(http,desc):
 y=google_ar(http,desc)
 if ar(y): return y
 n=norm(desc)
 if "meteo" in n:return "نشرة تقدم توقعات الطقس ودرجات الحرارة والرياح والتساقطات."
 if "journal" in n or "info" in n:return "برنامج إخباري من قناة 2M."
 return "برنامج يُعرض على قناة 2M."

def scrape_medi1(days):
 h=Http(); out=[]; today=datetime.now(TZ).date(); tre=re.compile(r"^([0-2]?\d)[:hH]([0-5]\d)\s*(.*)$"); ctas={x.casefold() for x in ("صفحة البرنامج","صفحة النشرة","شاهد النشرات","شاهد البرنامج","التفاصيل","المزيد","voir les jts","voir le programme","en direct","المباشر","MEDI1TV Maghreb","MEDI1TV Arabic")}
 for cid,bases in MEDI1:
  rows=[]
  for i in range(days):
   day=today+timedelta(days=i); ds=day.strftime("%d-%m-%Y"); r=None
   for base in bases:
    try:r=h.get(base+"/"+ds,headers={"Referer":"https://www.medi1tv.ma/ar/"});break
    except Exception:pass
   if r is None and i==0:
    for base in bases:
     try:r=h.get(base,headers={"Referer":"https://www.medi1tv.ma/ar/"});break
     except Exception:pass
   if r is None:continue
   prev=None;pday=day
   for a in BeautifulSoup(r.text,"lxml").find_all("a"):
    pieces=[clean(x) for x in a.stripped_strings if clean(x)]; payload=[]
    if not pieces:continue
    m=tre.match(pieces[0]); only=re.match(r"^([0-2]?\d)[:hH]([0-5]\d)$",pieces[0])
    if only:h1,m1=int(only.group(1)),int(only.group(2));payload=pieces[1:]
    elif m:h1,m1=int(m.group(1)),int(m.group(2));payload=([clean(m.group(3))] if clean(m.group(3)) else [])+pieces[1:]
    else:continue
    payload=[x for x in payload if x.casefold() not in ctas]
    if not payload:continue
    mins=h1*60+m1
    if prev is not None and mins+300<prev:pday+=timedelta(days=1)
    prev=mins; title=payload[0]; desc=clean(" ".join(x for x in payload[1:] if x!=title)) or title
    rows.append(Event(cid,datetime.combine(pday,dtime(h1,m1),TZ),title,desc,None,lang(title),lang(desc),"medi1"))
  seen=set()
  for e in sorted(rows,key=lambda e:e.start):
   k=(e.start,e.title.casefold())
   if k not in seen:seen.add(k);out.append(e)
 infer(out);return out

def parse_2m(h,text,day,period):
 tree=LH.fromstring(text); out=[];seen=set();rolled=False;prevh=None
 for b in tree.xpath("//div[contains(@class,'item-programme')] | //div[contains(@class,'news')]"):
  tx=clean(" ".join(b.xpath(".//*[contains(@class,'schedule-hour')]//text()"))); m=re.search(r"\b(\d{1,2}:\d{2})\b",tx or clean(" ".join(b.itertext()))[:120]); tt=b.xpath(".//*[contains(@class,'item-content')]//h3//strong | .//*[contains(@class,'item-content')]//h3 | .//h3//strong | .//h3")
  if not m or not tt:continue
  title=clean(" ".join(tt[0].itertext()));desc=clean(" ".join(b.xpath(".//*[contains(@class,'item-content')]//p//text() | .//p//text()"))) or title;k=(m.group(1),title.casefold())
  if not title or k in seen:continue
  seen.add(k);hr,mi=map(int,m.group(1).split(":"))
  if prevh is not None and prevh>=18 and hr<6:rolled=True
  prevh=hr; d=day+(timedelta(days=1) if rolled or (period in ("afternoon","evening") and hr<6) else timedelta()); start=datetime.combine(d,dtime(hr,mi),PARIS).astimezone(TZ)
  at=tr2m_title(h,title);out.append(Event("2M",start,at,tr2m_desc(h,desc),None,lang(at),"ar","2m"))
 return out

def scrape_2m_chada(days):
 h=Http();today=datetime.now(TZ).date();out=[];base="https://tv-programme.telecablesat.fr/chaine/340/2m-monde.html"
 for i in range(days):
  d=today+timedelta(days=i)
  for period in ("morning","noon","afternoon"):
   try:out+=parse_2m(h,h.get(base,params={"date":d.isoformat(),"period":period},headers={"Referer":"https://tv-programme.telecablesat.fr/"}).text,d,period)
   except Exception:pass
 seen=set();out=[e for e in sorted(out,key=lambda e:e.start) if not ((e.start,e.title.casefold()) in seen or seen.add((e.start,e.title.casefold())))]
 try:r=h.get("https://chada.ma/fr/chada-tv/grille-tv/")
 except Exception:r=None
 if r:
  tree=LH.fromstring(r.text)
  for bad in tree.xpath("//script|//style|//nav|//footer|//header"):
   if bad.getparent() is not None:bad.getparent().remove(bad)
  area=(tree.xpath("//div[contains(@class,'elementor-text-editor')]") or tree.xpath("//div[contains(@class,'posts-area')]") or tree.xpath("//body"));full="  ".join(clean(x) for x in area[0].xpath(".//text()") if clean(x)) if area else ""; raw=[]
  for m in re.finditer(r"(\d{2}:\d{2})(?:\s*(?:à|-)\s*\d{2}:\d{2})?\s*[.\-]?\s*(.*?)(?=\s*(?:\d{2}:\d{2})|$)",full):
   title=clean(m.group(2)).strip(" .-|:")
   if len(title)>2:raw.append((datetime.strptime(m.group(1),"%H:%M").time(),title))
  seq=[];last=None
  for t,title in raw:
   if last is None or t>last or (t<last and last.hour>=18 and t.hour<=5):seq.append((t,title));last=t
  proc=[]
  for t,orig in seq:
   low=orig.casefold(); presenter=next((v for k,v in HOSTS.items() if k in low),""); cleaned=orig
   for k in HOSTS:cleaned=re.sub(re.escape(k),"",cleaned,flags=re.I)
   cleaned=clean(cleaned).strip(" .-|:") or "برنامج شدى تي في"; n=norm(cleaned); meta=next((CHADA[k] for k in sorted(CHADA,key=len,reverse=True) if k in n),None); title,desc=meta or (cleaned,"برنامج ضمن شبكة شدى تي في.")
   if presenter and presenter not in desc:desc=desc.rstrip(" .")+". تقديم "+presenter+"."
   proc.append((t,title,desc))
  for i in range(days):
   cur=today+timedelta(days=i);prev=proc[0][0] if proc else dtime(0)
   for t,title,desc in proc:
    if t<prev:cur+=timedelta(days=1)
    prev=t;out.append(Event("Chada TV",datetime.combine(cur,t,TZ),title,desc,None,lang(title),"ar","chada"))
 infer(out);return out

def scrape_snrt(days):
 def one(cid,url):
  h=Http();r=h.get(url);soup=BeautifulSoup(r.text,"lxml");rows=[]
  for row in soup.find_all("div",class_=lambda x:x and "grille-line" in x.split()):
   dc=[x for x in row.get("class",[]) if x.isdigit() and len(x)==8];tt=row.find("div",class_="grille-time")
   if not dc or not tt:continue
   try:start=datetime.strptime(dc[0]+" "+tt.get_text().strip().replace("H",":"),"%Y%m%d %H:%M").replace(tzinfo=TZ)
   except Exception:continue
   title=clean(row.find("h2",class_="program-title-sm").get_text()) if row.find("h2",class_="program-title-sm") else "Programme";desc=clean(row.get_text(" ",strip=True));ctx=title+" "+desc
   if "الأخبار" in ctx:
    for k,v in NEWS.items():
     if k in ctx:title=v;break
   rows.append(Event(cid,start,title,desc,None,lang(title),lang(desc),"snrt"))
  return rows
 out=[]
 with ThreadPoolExecutor(max_workers=5) as ex:
  fs={ex.submit(one,c,u):c for c,u in SNRT.items()}
  for f in as_completed(fs):
   try:out+=f.result()
   except Exception as e:log("SNRT %s: %s"%(fs[f],e))
 start=datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0)
 for i in range(days*8):out.append(Event("AFLAM.ma",start+timedelta(hours=3*i),"برامج قناة السابعة AFLAM","أفضل الأفلام والبرامج السينمائية على القناة السابعة المغربية",start+timedelta(hours=3*(i+1)),"ar","ar","snrt"))
 infer(out);return out

def scrape_arryadia(days):
 h=Http();start=datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0);end=start+timedelta(days=min(days,3));parsed=[]
 try:
  soup=BeautifulSoup(h.get("https://www.snrt.ma/ar/node/4070").text,"lxml")
  for row in soup.find_all("div",class_=lambda x:x and "grille-line" in x.split()):
   dc=[x for x in row.get("class",[]) if x.isdigit() and len(x)==8];tt=row.find("div",class_="grille-time")
   if not dc or not tt:continue
   try:s=datetime.strptime(dc[0]+" "+tt.get_text().strip().replace("H",":"),"%Y%m%d %H:%M").replace(tzinfo=TZ)
   except Exception:continue
   title=clean(row.find("h2",class_="program-title-sm").get_text()) if row.find("h2",class_="program-title-sm") else "Programme";desc=clean(row.get_text(" ",strip=True));parsed.append((s,title,desc))
 except Exception as e:log("Arryadia: %s"%e)
 parsed.sort();out=[]
 for i,(s,title,desc) in enumerate(parsed):
  stop=parsed[i+1][0] if i+1<len(parsed) else s+timedelta(hours=2);full=(title+" "+desc).lower();ids=[cid for pat,cid in ARR_TAGS.items() if re.search(pat,full)] or ["Arryadia_HD","Arryadia_TNT"]
  live=bool(re.search(r"\b(?:live|direct)\b|مباشر",full,re.I));title=("Live: "+title if live and not title.lower().startswith("live:") else title)
  for cid in ids:out.append(Event(cid,s,title,desc,stop,lang(title,"fr"),lang(desc,"ar"),"arryadia"))
 by=defaultdict(list)
 for e in out:by[e.channel].append(e)
 result=[]
 for cid in ARR_IDS:
  cur=start;name=CHANNELS[cid]
  for e in sorted(by[cid],key=lambda e:e.start):
   while cur<e.start-timedelta(minutes=1):ge=min(e.start,cur+timedelta(hours=3));result.append(Event(cid,cur,"Programmes "+name,"Suivez le meilleur du sport marocain et international sur %s."%name,ge,"fr","fr","arryadia"));cur=ge
   result.append(e);cur=e.stop
  while cur<end:ge=min(end,cur+timedelta(hours=3));result.append(Event(cid,cur,"Programmes "+name,"Suivez le meilleur du sport marocain et international sur %s."%name,ge,"fr","fr","arryadia"));cur=ge
 return result

def to_xml(rows,path):
 root=ET.Element("tv",{"generator-info-name":"EPGManager Morocco Cloud rc23"});counts=defaultdict(int)
 for cid in sorted({e.channel for e in rows}):ch=ET.SubElement(root,"channel",{"id":cid});ET.SubElement(ch,"display-name").text=CHANNELS.get(cid,cid)
 for e in sorted(rows,key=lambda e:(e.start,e.channel,e.title)):
  p=ET.SubElement(root,"programme",{"start":xdt(e.start),"stop":xdt(e.stop),"channel":e.channel});ET.SubElement(p,"title",{"lang":e.tl or lang(e.title)}).text=e.title;ET.SubElement(p,"desc",{"lang":e.dl or lang(e.desc)}).text=e.desc or e.title;counts[e.channel]+=1
 ET.indent(root,space="  ");ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True);return dict(counts)

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--output-dir",default="output");ap.add_argument("--previous",default="");ap.add_argument("--days",type=int,default=7);ap.add_argument("--scheduled",action="store_true");a=ap.parse_args();now=datetime.now(TZ)
 if a.scheduled and now.hour not in (6,18):log("Schedule gate skip: local hour %02d"%now.hour);return 0
 outdir=Path(a.output_dir);outdir.mkdir(parents=True,exist_ok=True);old=previous(Path(a.previous)) if a.previous else [];results={};jobs={"snrt":lambda:scrape_snrt(a.days),"arryadia":lambda:scrape_arryadia(min(a.days,3)),"2m_chada":lambda:scrape_2m_chada(a.days),"medi1":lambda:scrape_medi1(a.days)}
 with ThreadPoolExecutor(max_workers=4) as ex:
  fs={ex.submit(fn):name for name,fn in jobs.items()}
  for f in as_completed(fs):
   try:results[fs[f]]=f.result()
   except Exception as e:log("%s crashed: %s"%(fs[f],e));results[fs[f]]=[]
 final=[];status={}
 for g in ("snrt","arryadia","2m_chada","medi1"):
  rr=results.get(g,[]);ok,detail=valid(g,rr);mode="fresh"
  if not ok:
   rr=[e for e in old if e.channel in GROUPS[g]];ok,od=valid(g,rr);mode="last-known-good";detail+="; fallback="+od
  if not ok:log("FATAL %s invalid and no Last Known Good (%s)"%(g,detail));return 2
  final+=rr;status[g]={"mode":mode,"events":len(rr),"detail":detail};log("%s: %s (%s)"%(g,mode,detail))
 seen=set();merged=[]
 for e in sorted(final,key=lambda e:(e.channel,e.start,e.title.casefold())):
  k=(e.channel,xdt(e.start),e.title.casefold())
  if k not in seen:seen.add(k);merged.append(e)
 infer(merged);xml=outdir/"morocco.xml";counts=to_xml(merged,xml);raw=xml.read_bytes();gz=outdir/"morocco.xml.gz"
 with gzip.GzipFile(filename="morocco.xml",mode="wb",fileobj=gz.open("wb"),compresslevel=9,mtime=0) as f:f.write(raw)
 (outdir/"morocco.txt").write_text("\n".join("%s | %s"%(c,CHANNELS[c]) for c in sorted(counts))+"\n",encoding="utf-8")
 manifest={"version":now.strftime("%Y%m%d-%H%M%S"),"generated":now.isoformat(),"timezone":"Africa/Casablanca","url":"morocco.xml.gz","sha256":hashlib.sha256(gz.read_bytes()).hexdigest(),"size":gz.stat().st_size,"channels":len(counts),"programmes":sum(counts.values()),"channel_counts":counts,"sources":status}
 (outdir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8");log("PASS: %d channels / %d programmes / %.1f KiB"%(manifest["channels"],manifest["programmes"],manifest["size"]/1024));return 0
if __name__=="__main__":raise SystemExit(main())
