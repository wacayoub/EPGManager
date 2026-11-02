#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager Morocco Cloud - SNRT + Arryadia + 2M + Chada + Medi1.
Runs on GitHub Actions. The Vu+ only downloads morocco.xml.gz.
"""
from __future__ import annotations
import argparse,gzip,hashlib,html,json,re,time,unicodedata
import threading
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from datetime import datetime,timedelta,time as dtime,timezone
from pathlib import Path
from xml.etree import ElementTree as ET
import requests
from bs4 import BeautifulSoup
from lxml import html as LH
from zoneinfo import ZoneInfo
try:
 from tools.programme_authenticity import generated_placeholder
except ImportError:
 from programme_authenticity import generated_placeholder
try: import cloudscraper
except Exception: cloudscraper=None

TZ=ZoneInfo("Africa/Casablanca"); PARIS=ZoneInfo("Europe/Paris")
# Morocco returned permanently to legal GMT on 2026-09-20 at 02:00 local.
# Keep this explicit cutoff because GitHub runners and Enigma2 images may carry
# older tzdata that still reports the former permanent UTC+1 regime.
MOROCCO_GMT_EFFECTIVE_LOCAL=datetime(2026,9,20,2,0,0)

def morocco_localize(naive):
    if naive >= MOROCCO_GMT_EFFECTIVE_LOCAL:
        return naive.replace(tzinfo=timezone.utc)
    return naive.replace(tzinfo=TZ)

def morocco_wall_clock(day, tm):
    return morocco_localize(datetime.combine(day,tm))
UA="Mozilla/5.0 EPGManagerCloud/8.2.2-rc23"
_SNRT_HTTP_SLOTS=threading.BoundedSemaphore(2)

def _is_snrt_url(url):
 host=(urlsplit(url).hostname or "").lower()
 return host=="snrt.ma" or host.endswith(".snrt.ma")

def _snrt_retry_delay(response,attempt):
 """Respect Retry-After without letting one provider consume the whole job."""
 raw=(getattr(response,"headers",{}) or {}).get("Retry-After","")
 try:
  delay=float(raw)
 except (TypeError,ValueError):
  try:
   stamp=parsedate_to_datetime(raw)
   if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
   delay=(stamp-datetime.now(timezone.utc)).total_seconds()
  except (TypeError,ValueError,OverflowError):delay=3*(2**attempt)
 if not (0<=delay< float("inf")):delay=3*(2**attempt)
 return max(1.0,delay)
CHANNELS={
 "AlAoula":"Al Aoula","Arrabiaa":"Attaqafia / Arrabiaa","AlMaghribiya":"Al Maghribia",
 "Assadisa":"Assadissa","Tamazight":"Tamazight","AFLAM.ma":"Aflam TV",
 "Arryadia_HD":"Arryadia HD","Arryadia_TNT":"Arryadia TNT","Arryadia_HD1":"Arryadia HD1",
 "Arryadia_HD2":"Arryadia HD2","Arryadia_HD3":"Arryadia HD3","2M":"2M","Chada TV":"Chada TV",
 "MEDI1TV_AR.ma":"Medi1 TV Arabic","MEDI1TV_MAGHREB.ma":"Medi1 TV Maghreb"}
GROUPS={"snrt":{"AlAoula","Arrabiaa","AlMaghribiya","Assadisa","Tamazight","AFLAM.ma"},
 "arryadia":{"Arryadia_HD","Arryadia_TNT","Arryadia_HD1","Arryadia_HD2","Arryadia_HD3"},
 "2m":{"2M"},"chada":{"Chada TV"},"medi1":{"MEDI1TV_AR.ma","MEDI1TV_MAGHREB.ma"}}
SNRT={"AlAoula":"https://www.snrt.ma/ar/node/1208","Arrabiaa":"https://www.snrt.ma/ar/node/4071",
 "AlMaghribiya":"https://www.snrt.ma/ar/node/4072","Assadisa":"https://www.snrt.ma/ar/node/4073",
 "Tamazight":"https://www.snrt.ma/ar/node/4075"}
SNRT_AR_NAMES={
 "AlAoula":"قناة الأولى","Arrabiaa":"قناة الثقافية","AlMaghribiya":"قناة المغربية",
 "Assadisa":"قناة السادسة","Tamazight":"قناة الأمازيغية","AFLAM.ma":"قناة السابعة أفلام"
}
# SNRT programme tabs are broadcast days: 00:00-06:59 belongs to the next
# calendar date; the next tab/daytime schedule starts at 07:00.
SNRT_DAY_START_HOUR=7
MEDI1=(
 ("MEDI1TV_AR.ma",("https://www.medi1tv.com/ar/grille/arabic","https://www.medi1tv.ma/ar/grille/arabic")),
 ("MEDI1TV_MAGHREB.ma",("https://www.medi1tv.com/ar/grille/maghreb","https://www.medi1tv.ma/ar/grille/maghreb")))
ARR_IDS=["Arryadia_HD","Arryadia_TNT","Arryadia_HD1","Arryadia_HD2","Arryadia_HD3"]
ARR_TAGS={r"\btnt\b":"Arryadia_TNT",r"\bsat\b":"Arryadia_HD",r"\bhd1\b":"Arryadia_HD1",r"\bhd2\b":"Arryadia_HD2",r"\bhd3\b":"Arryadia_HD3"}
NEWS={"الظهيرة":"أخبار الظهيرة","الأمازيغية":"الأخبار الأمازيغية","الفرنسية":"الأخبار الفرنسية",
 "الإسبانية":"الأخبار الإسبانية","الرئيسية":"الأخبار الرئيسية","الأخيرة":"الأخبار الأخيرة","الرياضية":"أخبار الرياضة"}
T2M={
 "addam al machrouk":"الدم المشروك",
 "al akhbar":"الأخبار","al khobarae":"الخبراء","sabahiyat 2m":"صباحيات 2M","sabahiyat":"صباحيات 2M",
 "ahsane patissier":"أحسن حلواني","andaloussiyat":"أندلسيات","lharba":"الهربة",
 "sahatna jmi3":"صحتنا جميع","tajwid al qor an":"تجويد القرآن","ch hiwat bladi":"شهيوات بلادي",
 "kif al hal":"كيف الحال","al barlamane wa annass":"البرلمان والناس","alhane 3chaqnaha":"ألحان عشقناها",
 "salon shehrazade":"صالون شهرزاد","ch hiwa ma3a choumicha":"شهيوة مع شميشة","al amana":"الأمانة",
 "asrar al mondial":"أسرار المونديال","bulletin meteo":"النشرة الجوية","meteo":"النشرة الجوية","journal amazigh":"الأخبار بالأمازيغية",
 "3ailti":"عائلتي","sir al morjane":"سر المرجان","bahr addalam":"بحر الظلام","qalb aswad":"قلب أسود",
 "nass al khir":"ناس الخير","nass lkhir":"ناس الخير",
 "mondial stories":"حكايات المونديال","info soir":"أخبار المساء","eco news":"أخبار الاقتصاد",
 "al massaiya":"المسائية","hikayat chama":"حكايات شامة",
 "jabha f rassou":"جبهة فراسو",

 "charqi ou gharbi":"شرقي و غربي","charqi ou lgharbi":"شرقي و غربي","soiree chaabi":"سهرة شعبية",
 "soiree cha3bi":"سهرة شعبية","soirees chaabi":"سهرة شعبية","soirees cha3bi":"سهرة شعبية","attahssina":"التحصينة","at tahssina":"التحصينة",
 "priere du vendredi":"صلاة الجمعة","priere vendredi":"صلاة الجمعة","ayne lkebrite":"عين الكبريت",
 "ayn lkebrite":"عين الكبريت","wlad 3li":"ولاد علي","oulad 3li":"ولاد علي","jt arabe":"الأخبار بالعربية",
 "journal amazigh":"الأخبار بالأمازيغية","al massaiya":"المسائية","al dahira":"الظهيرة","rachid show":"رشيد شو",
 "talk show rachid show":"رشيد شو","coran avec laureat":"القرآن مع الفائزين",
 "coran avec laureats":"القرآن مع الفائزين","coran avec lauréat":"القرآن مع الفائزين",
 "coran avec lauréats":"القرآن مع الفائزين","moudawala":"مداولة","lmktoub":"المكتوب",
 "dar nsa":"دار النسا","najm chaabi":"النجم الشعبي",
 "3ayne lkebrite":"عين الكبريت","auto moto":"السيارات والدراجات النارية","auto-moto":"السيارات والدراجات النارية",
 "ba lahbib":"با الحبيب","chef f daro":"الطاهي في داره",
 "dna al hayawanat":"دي إن إيه الحيوانات","des histoires et des hommes":"قصص ورجال",
 "everybody loves touda":"الجميع يحب تودا","ghidae wa siha":"الغذاء والصحة",
 "grand angle":"زاوية واسعة","kan ya ma kan":"كان يا ما كان",
 "koulna mgharba":"كلنا مغاربة","les filles de lalla mennana":"بنات لالة منانة",
 "mabrouk 3lina":"مبروك علينا","nidae":"نداء",
 "qayd al tahqiq":"قيد التحقيق","telefilm addar al machrouka":"فيلم تلفزيوني: الدار المشروكة",
 "yid al fellah":"يد الفلاح",
 "capsules festival international du film de femmes de sale":"كبسولات المهرجان الدولي لفيلم المرأة بسلا"}

T2M_FORCE_AR={
 "les interventions des partis politiques":"مداخلات الأحزاب السياسية",
 "interventions des partis politiques":"مداخلات الأحزاب السياسية"
}
T2M_MIXED={
 "telefilm chrif moul lbaraka":"فيلم تلفزيوني شريف مول البركة",
 "chrif moul lbaraka":"شريف مول البركة",
 "clips soirees chaabi":"سهرات شعبية",
 "clips soiree chaabi":"سهرة شعبية",
 "talk show twahachnak":"توحشناك",
 "talk show rachid show":"رشيد شو","twahachnak":"توحشناك",
 "akhir tamanna":"آخر تمني","akhir tamane":"آخر ثمن","akhir tamaneh":"آخر ثمن",
 "al khobare":"الأخبار",
 "coran avec laureats tajwid al qor an":"القرآن مع الفائزين",
 "coran avec laureat tajwid al qor an":"القرآن مع الفائزين",
 "tajwid al qor an":"تجويد القرآن"
}

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
 # Keep Arabic letters in normalization. Dropping them made every Arabic title
 # normalize to an empty string, so legitimate SNRT Arabic synopses were
 # incorrectly rejected as "same as title" and cross-channel matching failed.
 return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9\u0600-\u06ff%]+"," ",x)).strip()
def ar(x): return bool(re.search(r"[\u0600-\u06ff]",str(x or "")))
def lang(x,default="ar"): return "ar" if ar(x) else ("fr" if re.search(r"[A-Za-zÀ-ÿ]",str(x or "")) else default)
def xdt(d): return d.strftime("%Y%m%d%H%M%S %z")

class Http:
 def __init__(self):
  self.s=(cloudscraper.create_scraper() if cloudscraper else requests.Session()); self.s.headers.update({"User-Agent":UA,"Accept-Language":"ar,fr;q=.9,en;q=.7"})
  self.deadline=time.monotonic()+90
 def get(self,url,**kw):
  last=None;snrt=_is_snrt_url(url)
  for n in range(3):
   remaining=self.deadline-time.monotonic()
   if remaining<=0:raise TimeoutError("Provider HTTP budget exhausted; keep genuine partial/LKG rows")
   r=None
   try:
    if snrt:
     if not _SNRT_HTTP_SLOTS.acquire(timeout=remaining):raise TimeoutError("SNRT request capacity budget exhausted")
     try:
      remaining=self.deadline-time.monotonic()
      if remaining<=0:raise TimeoutError("SNRT provider HTTP budget exhausted")
      r=self.s.get(url,timeout=min(18,remaining),**kw)
     finally:_SNRT_HTTP_SLOTS.release()
    else:r=self.s.get(url,timeout=min(18,remaining),**kw)
    r.raise_for_status(); return r
   except Exception as e:
    last=e
    response=getattr(e,"response",None)
    if response is None:response=r
    status=getattr(response,"status_code",None)
    delay=_snrt_retry_delay(response,n) if snrt else .7*(n+1)
    if response is not None:
     try:response.close()
     except Exception:pass
    if snrt and status is not None and status not in (408,429,500,502,503,504):raise
    # A long Retry-After is a reason to defer this provider, not to retry
    # earlier than requested or hold the workflow for an unbounded interval.
    if snrt and delay>20:raise
    if n<2:
     remaining=self.deadline-time.monotonic()
     if remaining<=0:raise TimeoutError("Provider HTTP budget exhausted; keep genuine partial/LKG rows") from e
     if snrt:log("SNRT temporary HTTP failure status=%s attempt=%d/3 retry_in=%.1fs"%(status,n+1,min(delay,remaining)))
     time.sleep(min(delay,remaining))
  raise last

@dataclass
class Event:
 channel:str; start:datetime; title:str; desc:str=""; stop:datetime|None=None; tl:str="ar"; dl:str="ar"; source:str=""

def _arryadia_history_load(path,days=7):
 p=Path(path)
 if not p.exists():return []
 try:
  data=json.loads(p.read_text(encoding="utf-8"))
  rows=[]
  cutoff=datetime.now(TZ)-timedelta(days=days)
  for x in data.get("events",[]):
   try:
    s=datetime.fromisoformat(x["start"]).astimezone(TZ)
    e=datetime.fromisoformat(x["stop"]).astimezone(TZ)
    if e<cutoff:continue
    rows.append(Event(x["channel"],s,x["title"],x.get("desc",""),e,
                      x.get("tl","ar"),x.get("dl","ar"),"arryadia-history"))
   except Exception:
    continue
  return rows
 except Exception as e:
  log("Arryadia history load: %s"%e);return []

def _arryadia_history_update(path,official_rows,days=7):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 old=_arryadia_history_load(p,days=days)
 cutoff=datetime.now(TZ)-timedelta(days=days)
 # Persist only real SNRT rows; external fixtures and inferred fillers must
 # never become training data for the recurring-programme model.
 fresh=[
  e for e in official_rows
  if e.channel in ARR_IDS and e.stop and e.stop>e.start and
     e.source.startswith("arryadia-snrt") and e.stop>=cutoff
 ]
 merged={}
 for e in old+fresh:
  if e.stop<cutoff:continue
  k=(e.channel,e.start.isoformat(),norm(e.title))
  prev=merged.get(k)
  if prev is None or len(e.desc or "")>len(prev.desc or ""):merged[k]=e
 rows=sorted(merged.values(),key=lambda e:(e.start,e.channel,e.title.casefold()))
 payload={
  "generated":datetime.now(TZ).isoformat(),
  "retention_days":days,
  "events":[{
   "channel":e.channel,"start":e.start.isoformat(),"stop":e.stop.isoformat(),
   "title":e.title,"desc":e.desc,"tl":e.tl,"dl":e.dl
  } for e in rows]
 }
 p.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 log("Arryadia official history=%d events / %d days"%(len(rows),days))
 return rows

def _snrt_fallback_desc(cid,title):
 channel=SNRT_AR_NAMES.get(cid,CHANNELS.get(cid,cid))
 if "أخبار" in title or "الأخبار" in title:
  return "نشرة إخبارية على %s تقدم أبرز الأخبار والمستجدات."%channel
 if "طقس" in title or "النشرة الجوية" in title:
  return "نشرة الطقس على %s مع توقعات الأحوال الجوية."%channel
 return "برنامج «%s» يُعرض على %s."%(title,channel)

def _snrt_desc_from_row(cid,row,title,time_text,original_title=""):
 # Primary official SNRT field (same selector used by the upstream SNRT adapter).
 candidates=[]
 direct=row.select_one(".program-description-sm")
 if direct:
  txt=clean(direct.get_text(" ",strip=True))
  if txt:candidates.append(txt)
 # Compatibility selectors for SNRT template variants.
 for el in row.find_all(["p","div","span"]):
  classes=" ".join(el.get("class",[]) or []).casefold()
  if any(k in classes for k in ("description","desc","synopsis","resume","résumé","program-text","programme-text","grille-desc")):
   txt=clean(el.get_text(" ",strip=True))
   if txt:candidates.append(txt)
 full=clean(row.get_text(" ",strip=True))
 if full:candidates.append(full)
 # Some SNRT channel pages render the synopsis as a sibling block immediately
 # after grille-line instead of nesting it inside the programme row.
 sibling_count=0
 for sib in row.next_siblings:
  if getattr(sib,"name",None) is None:
   continue
  classes=" ".join(sib.get("class",[]) or []).casefold()
  if "grille-line" in classes.split():
   break
  txt=clean(sib.get_text(" ",strip=True))
  if not txt:
   continue
  if re.match(r"^[0-2]?\d\s*[Hh:]\s*[0-5]\d\b",txt):
   break
  if len(txt)<=600:
   candidates.insert(0,txt)
   sibling_count+=1
  if sibling_count>=3:
   break
 for raw in candidates:
  desc=clean(raw)
  if time_text:
   desc=clean(desc.replace(clean(time_text)," ",1))
  for ttl in (original_title,title):
   if ttl:
    desc=clean(desc.replace(clean(ttl)," ",1))
  desc=re.sub(r"\b(?:SAT|TNT|HD|الآن|مباشر)\b"," ",desc,flags=re.I)
  desc=clean(desc).strip(" -–—|:")
  if len(desc)>=8 and norm(desc)!=norm(title) and norm(desc)!=norm(original_title):
   return desc
 return _snrt_fallback_desc(cid,title)

def _snrt_detail_desc(http,href,title=""):
 href=clean(href)
 if not href or href.startswith("#") or "javascript:" in href.casefold() or "jascript:" in href.casefold():
  return ""
 url=href if href.startswith("http") else "https://www.snrt.ma/"+href.lstrip("/")
 try:
  soup=BeautifulSoup(http.get(url,headers={"Referer":"https://www.snrt.ma/ar/"}).text,"lxml")
 except Exception:
  return ""
 candidates=[]
 # Prefer the programme body itself; site-wide meta text is only a fallback.
 for el in soup.select(
  ".carousel-program-info-content p, .carousel-program-info p, "
  ".field--name-body p, .field--name-field-description p, "
  ".programme-description p, .program-description p, article p"
 ):
  txt=clean(el.get_text(" ",strip=True))
  if txt:candidates.append(txt)
 # Also support a title immediately followed by the official synopsis.
 heading=soup.find(["h1","h2","h3"],string=lambda x:x and norm(clean(x))==norm(title))
 if heading:
  p=heading.find_next("p")
  if p:
   txt=clean(p.get_text(" ",strip=True))
   if txt:candidates.insert(0,txt)
 for attrs in ({"property":"og:description"},{"name":"description"},{"name":"twitter:description"}):
  el=soup.find("meta",attrs=attrs)
  if el:
   txt=clean(el.get("content"))
   if txt:candidates.append(txt)
 nt=norm(title)
 for desc in candidates:
  nd=norm(desc)
  if not desc or len(desc)<20 or nd==nt:
   continue
  # Reject generic site metadata/navigation if SNRT ever changes templates.
  if "الشركة الوطنية للإذاعة والتلفزة" in desc and len(desc)<80:
   continue
  return desc
 return ""

def _snrt_generic_desc(desc,title=""):
 d=clean(desc);n=norm(d);t=norm(title)
 return (
  not d or n==t or len(d)<35
  or d.startswith("برنامج «")
  or d.startswith("نشرة إخبارية على ")
  or d.startswith("نشرة الطقس على ")
 )

def _snrt_visible_descs(soup):
 # Build a date-agnostic lookup from the actual visible SNRT sequence.
 # Some channel pages emit "07H00 Title" in one text node, while others emit
 # separate time/title nodes. Support both forms.
 strings=[clean(x) for x in soup.stripped_strings if clean(x)]
 time_rx=re.compile(r"^([0-2]?\d)\s*[Hh:]\s*([0-5]\d)(?:\s+(.+))?$")
 day_rx=re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
 footer={"الرئيسية","الشركة","القنوات","الوسيط","طلبات العروض","Régie publicitaire","Mentions légales"}
 noise={"الآن","SAT","TNT","Image"}
 out=defaultdict(list);i=0
 while i<len(strings):
  m=time_rx.match(strings[i])
  if not m:
   i+=1;continue
  hm="%02d:%02d"%(int(m.group(1)),int(m.group(2)))
  parts=[]
  tail=clean(m.group(3) or "")
  if tail and tail not in noise:
   parts.append(tail)
  j=i+1
  while j<len(strings) and not time_rx.match(strings[j]):
   txt=strings[j]
   if txt in footer:
    break
   if day_rx.search(txt):
    if parts:break
    j+=1;continue
   if txt not in noise:
    parts.append(txt)
   j+=1
  if parts:
   title=parts[0]
   desc=clean(" ".join(parts[1:]))
   if desc and norm(desc)!=norm(title):
    out[(hm,norm(title))].append(desc)
  i=max(j,i+1)
 return out

def _snrt_flat_events(cid,soup):
 # SNRT increasingly renders schedule metadata as a flat visible sequence:
 # date tabs, then repeated "time / title / synopsis" blocks. Parse that
 # representation as a second authoritative path so synopses are not lost
 # when the old grille-line markup changes.
 now=datetime.now(TZ)
 strings=[clean(x) for x in soup.stripped_strings if clean(x)]
 time_rx=re.compile(r"^([0-2]?\d)\s*[Hh:]\s*([0-5]\d)$")
 day_rx=re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
 day_labels=[];day_positions=[]
 for pos,txt in enumerate(strings):
  m=day_rx.search(txt)
  if not m:continue
  try:
   d=datetime(now.year,int(m.group(2)),int(m.group(1))).date()
   if d<now.date()-timedelta(days=180):d=datetime(now.year+1,d.month,d.day).date()
   if d>now.date()+timedelta(days=180):d=datetime(now.year-1,d.month,d.day).date()
  except Exception:
   continue
  if abs((d-now.date()).days)<=14:
   day_positions.append(pos)
   if d not in day_labels:day_labels.append(d)
 # Ignore unrelated clock strings in the site header. The programme grid begins
 # after the cluster of visible date tabs.
 if not day_labels or not day_positions:
  return []
 first_time=next((pos for pos in range(max(day_positions)+1,len(strings)) if time_rx.match(strings[pos])),None)
 if first_time is None:
  return []

 footer={"الرئيسية","الشركة","القنوات","الوسيط","طلبات العروض","Régie publicitaire","Mentions légales"}
 noise={"الآن","SAT","TNT","Image"}
 raw=[];i=first_time
 while i<len(strings):
  m=time_rx.match(strings[i])
  if not m:
   i+=1;continue
  hr,mi=int(m.group(1)),int(m.group(2))
  if hr>23:
   i+=1;continue
  j=i+1;parts=[]
  while j<len(strings) and not time_rx.match(strings[j]):
   txt=strings[j]
   if txt in footer:
    break
   if not day_rx.search(txt) and txt not in noise:
    parts.append(txt)
   j+=1
  if parts:
   title=parts[0]
   desc=clean(" ".join(parts[1:]))
   if norm(desc)==norm(title):
    desc=""
   raw.append({"minutes":hr*60+mi,"hr":hr,"mi":mi,"title":title,"desc":desc})
  i=max(j,i+1)
 if not raw:
  return []

 # Split the visible sequence into day groups. Post-midnight rows stay attached
 # to the preceding broadcast day; a morning restart starts the next tab/day.
 groups=[[]];prev=None
 for item in raw:
  cur=item["minutes"];new_day=False
  if prev is not None:
   if prev<7*60 and cur>=7*60:
    new_day=True
   elif cur+4*60<prev and cur>=7*60:
    new_day=True
  if new_day:groups.append([])
  groups[-1].append(item);prev=cur
 groups=[g for g in groups if g]
 if not groups:return []
 dates=sorted(day_labels)[-len(groups):]
 if len(dates)!=len(groups):
  return []

 out=[]
 for day,group in zip(dates,groups):
  carry=False;prev=None
  for item in group:
   cur=item["minutes"]
   if prev is not None and cur+4*60<prev and cur<7*60:carry=True
   if prev is not None and prev<7*60 and cur>=7*60:carry=False
   evday=day+(timedelta(days=1) if carry and item["hr"]<7 else timedelta(0))
   start=morocco_wall_clock(evday,dtime(item["hr"],item["mi"]))
   original_title=item["title"]
   desc=item["desc"]
   title=original_title
   ctx=original_title+" "+desc
   if "الأخبار" in ctx or "الاخبار" in ctx:
    for k,v in NEWS.items():
     if k in ctx:
      title=v
      break
   if not desc:
    desc=_snrt_fallback_desc(cid,title)
   out.append(Event(cid,start,title,desc,None,"ar","ar","snrt-ar-flat"))
   prev=cur
 infer(out)
 return out

def _arryadia_is_generic(e):
 return norm(e.title) in {"arryadia programme","programme arryadia"} or e.source in ("arryadia-auto","arryadia-smart","arryadia-repeat")

def _arryadia_repeat_safe(e):
 # Reuse only stable magazine/studio programmes. Never project a match/live
 # event into another day because sports rights and kick-off times change.
 s=norm((e.title or "")+" "+(e.desc or ""))
 blocked=(
  "live","direct","match","matches","fifa","uefa","caf","champions","league",
  "ligue","coupe","cup","world cup","mondial","botola"
 )
 if any(k in s for k in blocked):return False
 if any(k in (e.title or "")+(e.desc or "") for k in ("مباشر","مباراة","كأس","دوري","البطولة")):return False
 return not _arryadia_is_generic(e)

BOTOLA_AR={
 "as far":"الجيش الملكي","far rabat":"الجيش الملكي",
 "codm meknes":"النادي المكناسي","cod meknes":"النادي المكناسي",
 "difaa el jadida":"الدفاع الحسني الجديدي","difa el jadida":"الدفاع الحسني الجديدي",
 "fus rabat":"الفتح الرياضي","fath union sport":"الفتح الرياضي",
 "hassania agadir":"حسنية أكادير","husa":"حسنية أكادير",
 "ittihad tanger":"اتحاد طنجة","ir tanger":"اتحاد طنجة",
 "kawkab marrakech":"الكوكب المراكشي","kacm":"الكوكب المراكشي",
 "mas fes":"المغرب الفاسي","maghreb fes":"المغرب الفاسي",
 "moghreb tetouan":"المغرب التطواني","mat tetouan":"المغرب التطواني",
 "raja casablanca":"الرجاء الرياضي","raja ca":"الرجاء الرياضي",
 "renaissance zemamra":"نهضة الزمامرة","renaissance club zemamra":"نهضة الزمامرة",
 "renaissance zemamra club":"نهضة الزمامرة","rc zemamra":"نهضة الزمامرة",
 "rs berkane":"نهضة بركان","renaissance berkane":"نهضة بركان",
 "amal tiznit":"أمل تيزنيت",
 "union touarga":"اتحاد تواركة","ut salé":"اتحاد تواركة","ut sale":"اتحاد تواركة",
 "wydad casablanca":"الوداد الرياضي","wac":"الوداد الرياضي",
 "wydad temara":"وداد تمارة","ws temara":"وداد تمارة"
}

def _botola_team_ar(name):
 raw=clean(name);n=norm(raw)
 if n in BOTOLA_AR:return BOTOLA_AR[n]
 for k,v in BOTOLA_AR.items():
  if len(k)>=4 and (k in n or n in k):return v
 return raw

BOTOLA_FR={
 "as far":"FAR Rabat","far rabat":"FAR Rabat",
 "codm meknes":"CODM Meknès","cod meknes":"CODM Meknès",
 "difaa el jadida":"Difaâ El Jadida","difa el jadida":"Difaâ El Jadida",
 "fus rabat":"FUS Rabat","fath union sport":"FUS Rabat",
 "hassania agadir":"Hassania Agadir","husa":"Hassania Agadir",
 "ittihad tanger":"Ittihad Tanger","ir tanger":"Ittihad Tanger",
 "kawkab marrakech":"KACM","kacm":"KACM",
 "mas fes":"MAS Fès","maghreb fes":"MAS Fès",
 "moghreb tetouan":"MAT Tétouan","mat tetouan":"MAT Tétouan",
 "raja casablanca":"Raja","raja ca":"Raja","raja club athletic":"Raja",
 "renaissance zemamra":"Renaissance Zemamra","renaissance club zemamra":"Renaissance Zemamra",
 "renaissance zemamra club":"Renaissance Zemamra","rc zemamra":"Renaissance Zemamra",
 "rs berkane":"RS Berkane","renaissance berkane":"RS Berkane",
 "amal tiznit":"Amal Tiznit",
 "union touarga":"Union Touarga","ut salé":"Union Touarga","ut sale":"Union Touarga",
 "wydad casablanca":"Wydad","wydad ac":"Wydad","wac":"Wydad",
 "wydad temara":"Wydad Témara","ws temara":"Wydad Témara"
}

def _botola_team_fr(name):
 raw=clean(name);n=norm(raw)
 if n in BOTOLA_FR:return BOTOLA_FR[n]
 for k,v in BOTOLA_FR.items():
  if len(k)>=4 and (k in n or n in k):return v
 return raw


def _morocco_from_unix(ts):
 u=datetime.fromtimestamp(int(ts),timezone.utc)
 # Since 2026-09-20 Morocco legal/broadcast clock is UTC+0.
 if u.replace(tzinfo=None)>=MOROCCO_GMT_EFFECTIVE_LOCAL:return u
 return u.astimezone(TZ)

def _sofascore_botola_fixtures(http,hours=48):
 # SofaScore is only a fixture/calendar fallback. It never overrides an
 # official SNRT row. Filtering by uniqueTournament=937 prevents other leagues
 # from entering the Arryadia EPG.
 now=datetime.now(timezone.utc)
 end=now+timedelta(hours=max(1,hours))
 days=[]
 d=now.date()
 while d<=end.date():
  days.append(d);d+=timedelta(days=1)
 out=[];seen=set()
 for day in days:
  try:
   url="https://www.sofascore.com/api/v1/sport/football/scheduled-events/%s"%day.isoformat()
   data=http.get(url,headers={"Referer":"https://www.sofascore.com/","Accept":"application/json"}).json()
   events=data.get("events",[]) if isinstance(data,dict) else []
  except Exception as e:
   log("Botola SofaScore %s: %s"%(day,e));continue
  for ev in events:
   try:
    tournament=ev.get("tournament") or {}
    ut=tournament.get("uniqueTournament") or {}
    if int(ut.get("id") or 0)!=937:continue
    status=((ev.get("status") or {}).get("type") or "").casefold()
    if status in ("canceled","cancelled","postponed"):continue
    ts=ev.get("startTimestamp")
    if not ts:continue
    start=_morocco_from_unix(ts)
    stop=start+timedelta(hours=2)
    # Keep matches that are already in progress. The former 15-minute grace
    # dropped a match as soon as a refresh happened >15 minutes after kickoff.
    if stop<=now-timedelta(minutes=10) or start>=end:continue
    home=clean((ev.get("homeTeam") or {}).get("name"))
    away=clean((ev.get("awayTeam") or {}).get("name"))
    if not home or not away:continue
    eid=str(ev.get("id") or "%s-%s-%s"%(int(ts),home,away))
    if eid in seen:continue
    seen.add(eid)
    rnd=(ev.get("roundInfo") or {}).get("round")
    # Club names stay in French/Latin in the public EPG.
    # Example: "Wydad Casablanca vs Raja Casablanca - Botola Pro".
    ha=_botola_team_fr(home);aa=_botola_team_fr(away)
    title="%s vs %s - Botola Pro"%(ha,aa)
    rd=(" - Journée %s"%rnd) if rnd else ""
    desc="%s vs %s - Botola Pro%s."%(ha,aa,rd)
    out.append({"id":eid,"start":start,"stop":stop,
                "home":home,"away":away,"title":title,"desc":desc})
   except Exception as e:
    log("Botola fixture parse: %s"%e)
 out.sort(key=lambda x:(x["start"],norm(x["home"]),norm(x["away"])))
 log("Botola SofaScore fixtures=%d window=%dh"%(len(out),hours))
 return out


def _livescore_parse_start(raw):
 s=str(raw or "").strip()
 m=re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$",s)
 if not m:return None
 try:
  dt=datetime(*(int(x) for x in m.groups()),tzinfo=timezone.utc)
  return _morocco_from_unix(int(dt.timestamp()))
 except Exception:
  return None

def _livescore_stage_is_botola(stage):
 label=" ".join(clean(stage.get(k)) for k in ("Cnm","Snm","CompN","CompUrlName","Scd"))
 n=norm(label)
 return "botola" in n or ("morocco" in n and any(k in n for k in ("pro","league","premier")))

def _livescore_botola_fixtures(http,hours=48):
 # LiveScore CDN is an unauthenticated fallback when SofaScore blocks GitHub
 # runners. It also exposes in-progress matches, which is essential when the
 # refresh happens after kickoff.
 now=datetime.now(timezone.utc);end=now+timedelta(hours=max(1,hours))
 urls=["https://prod-cdn-mev-api.livescore.com/v1/api/app/live/soccer/0?countryCode=MA&locale=en"]
 d=now.date()
 while d<=end.date():
  urls.append("https://prod-cdn-mev-api.livescore.com/v1/api/app/date/soccer/%s/0?countryCode=MA&locale=en"%d.strftime("%Y%m%d"))
  d+=timedelta(days=1)
 out=[];seen=set()
 headers={"Referer":"https://www.livescore.com/","Origin":"https://www.livescore.com","Accept":"application/json"}
 for url in urls:
  try:
   data=http.get(url,headers=headers).json()
  except Exception as e:
   log("Botola LiveScore %s: %s"%(url.split("/app/")[-1].split("?")[0],e));continue
  for stage in data.get("Stages",[]) if isinstance(data,dict) else []:
   if not _livescore_stage_is_botola(stage):continue
   for ev in stage.get("Events",[]) or []:
    try:
     start=_livescore_parse_start(ev.get("Esd"))
     if not start:continue
     stop=start+timedelta(hours=2)
     if stop<=now-timedelta(minutes=10) or start>=end:continue
     t1=(ev.get("T1") or [{}])[0] or {};t2=(ev.get("T2") or [{}])[0] or {}
     home=clean(t1.get("Nm"));away=clean(t2.get("Nm"))
     if not home or not away:continue
     eid="ls:"+str(ev.get("Eid") or "%s-%s-%s"%(int(start.timestamp()),home,away))
     if eid in seen:continue
     seen.add(eid)
     # Keep football club names in French/Latin in the EPG.
     ha=_botola_team_fr(home);aa=_botola_team_fr(away)
     title="%s vs %s - Botola Pro"%(ha,aa)
     phase=clean(ev.get("Eps"))
     # Keep the public EPG clean and stable: data-source/debug information and
     # live scores become stale quickly and must stay in logs, not in the TV synopsis.
     phase_txt=(" - "+phase) if phase and phase.casefold() not in ("not started","scheduled") else ""
     desc="%s vs %s - Botola Pro%s."%(ha,aa,phase_txt)
     out.append({"id":eid,"start":start,"stop":stop,"home":home,"away":away,
                 "title":title,"desc":desc})
    except Exception as e:
     log("Botola LiveScore event parse: %s"%e)
 out.sort(key=lambda x:(x["start"],norm(x["home"]),norm(x["away"])))
 log("Botola LiveScore fixtures=%d window=%dh"%(len(out),hours))
 return out

MOROCCO_SENIOR_SOFASCORE_ID=4778
NATIONAL_TEAM_AR={
 "gabon":"الغابون","congo":"الكونغو","congo republic":"الكونغو",
 "senegal":"السنغال","tunisia":"تونس","algeria":"الجزائر","egypt":"مصر",
 "south africa":"جنوب إفريقيا","cameroon":"الكاميرون","nigeria":"نيجيريا",
 "ivory coast":"ساحل العاج","cote d ivoire":"ساحل العاج","mali":"مالي",
 "ghana":"غانا","france":"فرنسا","spain":"إسبانيا","portugal":"البرتغال",
 "netherlands":"هولندا","belgium":"بلجيكا","brazil":"البرازيل","argentina":"الأرجنتين"
}

def _national_team_ar(name):
 raw=clean(name);n=norm(raw)
 return NATIONAL_TEAM_AR.get(n,raw)

def _sofascore_morocco_home_fixtures(http,hours=72):
 # Morocco senior men's national team only (SofaScore team 4778).
 # Home fixtures are inserted on Arryadia TNT as an EPG fallback when SNRT has
 # not yet exposed the detailed TV grid.
 now=datetime.now(timezone.utc);end=now+timedelta(hours=max(1,hours))
 days=[];d=now.date()
 while d<=end.date():
  days.append(d);d+=timedelta(days=1)
 out=[];seen=set()
 for day in days:
  try:
   url="https://www.sofascore.com/api/v1/sport/football/scheduled-events/%s"%day.isoformat()
   data=http.get(url,headers={"Referer":"https://www.sofascore.com/","Accept":"application/json"}).json()
   events=data.get("events",[]) if isinstance(data,dict) else []
  except Exception as e:
   log("Morocco NT SofaScore %s: %s"%(day,e));continue
  for ev in events:
   try:
    home=ev.get("homeTeam") or {};away=ev.get("awayTeam") or {}
    if int(home.get("id") or 0)!=MOROCCO_SENIOR_SOFASCORE_ID:continue
    status=((ev.get("status") or {}).get("type") or "").casefold()
    if status in ("canceled","cancelled","postponed"):continue
    ts=ev.get("startTimestamp")
    if not ts:continue
    start=_morocco_from_unix(ts)
    if start<datetime.now(timezone.utc)-timedelta(minutes=15) or start>=end:continue
    away_name=clean(away.get("name"))
    if not away_name:continue
    eid=str(ev.get("id") or "%s-%s"%(int(ts),away_name))
    if eid in seen:continue
    seen.add(eid)
    opp=_national_team_ar(away_name)
    tournament=clean(((ev.get("tournament") or {}).get("name")) or
                     (((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("name")))
    title="مباشر: المغرب × %s"%opp
    desc="مباراة المنتخب المغربي أمام %s على أرض المغرب"%opp
    if tournament:desc+=" ضمن %s"%tournament
    desc+=". مضافة تلقائياً إلى Arryadia TNT من جدول المباريات إلى أن تنشر SNRT شبكة البث الرسمية."
    out.append({"id":eid,"start":start,"stop":start+timedelta(hours=2,minutes=15),
                "opponent":away_name,"title":title,"desc":desc})
   except Exception as e:
    log("Morocco NT fixture parse: %s"%e)
 out.sort(key=lambda x:x["start"])
 log("Morocco NT home fixtures=%d window=%dh"%(len(out),hours))
 return out

def _arryadia_add_morocco_tnt(rows,fixtures):
 out=list(rows);added=0;official=0
 def overlap(s,t):
  return any(e.channel=="Arryadia_TNT" and e.stop and e.stop>s and e.start<t for e in out)
 for fx in fixtures:
  s,t=fx["start"],fx["stop"]
  # If SNRT already has a TNT programme at the same kickoff window, keep SNRT.
  hit=any(
   e.channel=="Arryadia_TNT" and e.stop and
   abs((e.start-s).total_seconds())<=35*60 and
   ("المغرب" in (e.title or "") or "maroc" in norm(e.title) or "morocco" in norm(e.title))
   for e in rows
  )
  if hit:
   official+=1;continue
  if overlap(s,t):continue
  out.append(Event("Arryadia_TNT",s,fx["title"],fx["desc"],t,"ar","ar","morocco-nt-sofascore"))
  added+=1
 log("Arryadia Morocco NT TNT added=%d official=%d"%(added,official))
 return out

def _botola_event_matches_text(fx,e):
 text=norm((e.title or "")+" "+(e.desc or ""))
 if not text:return False
 # A direct team-name match is enough to identify an already official fixture.
 for team in (fx["home"],fx["away"],_botola_team_fr(fx["home"]),_botola_team_fr(fx["away"]),
              _botola_team_ar(fx["home"]),_botola_team_ar(fx["away"])):
  n=norm(team)
  if n and len(n)>=4 and n in text:return True
 return False

def _arryadia_add_botola(rows,fixtures):
 # Botola fallback policy:
 # - every non-simultaneous match goes to Arryadia_HD (main channel);
 # - simultaneous kick-offs fan out to TNT/HD1/HD2/HD3;
 # - a generic "Arryadia Programme" must never hide a known Botola fixture.
 out=list(rows);occupied=defaultdict(list);added=0;confirmed=0;unplaced=0
 for e in rows:
  if e.channel in ARR_IDS and e.stop:occupied[e.channel].append(e)

 def blockers(cid,s,t):
  return [e for e in occupied[cid] if e.stop and e.stop>s and e.start<t and not _arryadia_is_generic(e)]

 def drop_generic_overlap(cid,s,t):
  nonlocal out
  doomed=[e for e in occupied[cid] if e.stop and e.stop>s and e.start<t and _arryadia_is_generic(e)]
  if not doomed:return
  occupied[cid]=[e for e in occupied[cid] if e not in doomed]
  out=[e for e in out if e not in doomed]

 groups=defaultdict(list)
 for fx in fixtures:groups[fx["start"]].append(fx)
 for start in sorted(groups):
  same_start=groups[start]
  for pos,fx in enumerate(same_start):
   stop=fx["stop"]
   official_hit=False
   for e in rows:
    if e.channel not in ARR_IDS or not e.stop:continue
    if abs((e.start-start).total_seconds())>35*60:continue
    if _botola_event_matches_text(fx,e):
     official_hit=True;confirmed+=1;break
   if official_hit:continue

   # First match at a kickoff always targets main Arryadia HD.
   preferred=ARR_IDS if pos==0 else ARR_IDS[1:]+["Arryadia_HD"]
   cid=None
   for candidate in preferred:
    drop_generic_overlap(candidate,start,stop)
    b=blockers(candidate,start,stop)
    if not b:
     cid=candidate;break
    # Consecutive Botola fixtures often start exactly two hours apart. If our
    # previous fallback slightly overlaps the next kickoff, clip it instead of
    # moving the next match off the main channel.
    if candidate=="Arryadia_HD" and all(x.source=="botola-sofascore" for x in b):
     if all(x.start<start and x.stop-start<=timedelta(minutes=20) for x in b):
      for x in b:x.stop=start
      cid=candidate;break
   if cid is None:
    unplaced+=1;continue
   ev=Event(cid,start,fx["title"],fx["desc"],stop,"fr","fr","botola-sofascore")
   out.append(ev);occupied[cid].append(ev);added+=1
   log("Arryadia Botola place %s | %s | %s"%(start.strftime("%F %H:%M"),cid,fx["title"]))
 log("Arryadia Botola external added=%d official_matches=%d unplaced=%d"%(added,confirmed,unplaced))
 return out

def _arryadia_fill(rows,hours=48,slot_hours=2,history=None):
 # Retain exact dated events only. Past recurrence and a football calendar
 # cannot establish a future broadcast, and gaps must remain honest gaps.
 now=datetime.now(TZ);end=now+timedelta(hours=max(1,hours))
 out=[e for e in rows if not _arryadia_is_generic(e) and not generated_placeholder(e.title)]
 for e in sorted(history or [],key=lambda e:e.start):
  if (e.channel not in ARR_IDS or not e.stop or e.stop<=now or
      e.stop<=e.start or e.start>=end or _arryadia_is_generic(e) or
      generated_placeholder(e.title)):
   continue
  if any(x.channel==e.channel and x.stop and x.stop>e.start and x.start<e.stop for x in out):
   continue
  out.append(Event(e.channel,e.start,e.title,e.desc,e.stop,e.tl,e.dl,"arryadia-lkg"))
 return out


def infer(rows):
 by=defaultdict(list)
 for e in rows: by[e.channel].append(e)
 for rr in by.values():
  rr.sort(key=lambda e:e.start)
  for i,e in enumerate(rr):
   # Repair missing or invalid stop times. Old LKG rows can contain stop<=start;
   # never let one malformed event poison the whole Morocco publication.
   if not e.stop or e.stop<=e.start:
    nxt=rr[i+1].start if i+1<len(rr) and rr[i+1].start>e.start else None
    e.stop=nxt if nxt else e.start+timedelta(hours=1)

def previous(path):
 if not path.exists(): return []
 try:
  raw=gzip.decompress(path.read_bytes()) if path.suffix==".gz" else path.read_bytes(); root=ET.fromstring(raw); out=[]
  for p in root.findall("programme"):
   def dt(v):
    raw=clean(v)
    parts=raw.split()
    if not parts or len(parts[0]) < 14:
     raise ValueError("invalid XMLTV datetime: %r"%raw)
    stamp=parts[0][:14]
    off=parts[1] if len(parts)>1 else "+0000"
    if len(off)==4 and off[0] in "+-":
     off=off+"0"
    if len(off)!=5 or off[0] not in "+-":
     raise ValueError("invalid XMLTV timezone: %r"%raw)
    return datetime.strptime(stamp+" "+off,"%Y%m%d%H%M%S %z").astimezone(TZ)
   t=p.find("title"); d=p.find("desc"); out.append(Event(p.get("channel"),dt(p.get("start")),t.text or "",d.text if d is not None else "",dt(p.get("stop")) if p.get("stop") else None,t.get("lang") or "ar",d.get("lang") if d is not None else "ar","old"))
  return out
 except Exception as e: log("Previous feed unreadable: %s"%e); return []

def valid(group,rows):
 now=datetime.now(TZ); rr=[e for e in rows if e.channel in GROUPS[group] and e.start<now+timedelta(days=8) and (e.stop or e.start+timedelta(hours=1))>now-timedelta(hours=12)]
 c=defaultdict(int)
 for e in rr:c[e.channel]+=1
 if group=="snrt": ok=sum(c[x] for x in GROUPS[group] if x!="AFLAM.ma")>=10 and sum(bool(c[x]) for x in GROUPS[group] if x!="AFLAM.ma")>=3
 elif group=="arryadia":
  # Do not accept a stale current-day page: require real future coverage.
  future=[e for e in rr if (e.stop or e.start+timedelta(hours=1))>now]
  fc=defaultdict(int)
  for e in future:fc[e.channel]+=1
  ok=len(future)>=6 and (fc["Arryadia_HD"]>=3 or fc["Arryadia_TNT"]>=3)
 elif group=="2m":
  two_m=[e for e in rr if e.channel=="2M"]
  ordered=sorted(two_m,key=lambda e:e.start)
  french=set()
  for i,e in enumerate(ordered):
   if norm(e.title)=="info soir":french.add(id(e))
   elif (norm(e.title) in ("meteo","bulletin meteo") and i and
         norm(ordered[i-1].title)=="info soir" and
         timedelta(0)<=e.start-ordered[i-1].start<=timedelta(hours=2)):
    french.add(id(e))
  latin=[e for e in two_m if id(e) not in french and re.search(r"[A-Za-zÀ-ÿ]",re.sub(r"(?i)\b2M\b","",e.title or ""))]
  ok=c["2M"]>=6 and len(latin)<=max(1,int(len(two_m)*0.05))
 elif group=="chada":
  future=sum(1 for e in rr if (e.stop or e.start+timedelta(hours=1))>now and e.start<now+timedelta(days=3))
  ok=future>=3
 else: ok=sum(c.values())>=6 and max(c.values() or [0])>=3
 detail="events=%d"%sum(c.values())
 if group=="2m": detail+="; untranslated_titles=%d/%d"%(len(latin),len(two_m))
 return ok,detail

def google_ar(http,text,force=False):
 # `force` is used for mixed-script programme names. The old early return on
 # any Arabic character left titles such as "S1 ..." partly untranslated.
 if not clean(text) or (ar(text) and not force): return clean(text)
 cache=getattr(http,"_ar_translation_cache",None)
 if cache is None:
  cache={}
  try:setattr(http,"_ar_translation_cache",cache)
  except Exception:pass
 key=(bool(force),clean(text))
 if key in cache:return cache[key]
 try:
  r=http.get("https://translate.googleapis.com/translate_a/single",params={"client":"gtx","sl":"auto","tl":"ar","dt":"t","q":text}); data=r.json(); y=clean("".join(x[0] for x in data[0] if x and x[0])); out=y if ar(y) else clean(text)
 except Exception:out=clean(text)
 cache[key]=out
 return out

def tr2m_title(http,title):
 raw=clean(title);n=norm(raw)
 # Genuine French-language news must keep its on-air French title.
 if n=="info soir":return raw
 if n in T2M_FORCE_AR:return T2M_FORCE_AR[n]
 if n in T2M_MIXED:return T2M_MIXED[n]
 if n in T2M:return T2M[n]
 # Translate the programme type and its name, instead of leaving unfamiliar
 # French/Latin titles in the receiver's guide. Keep known deterministic
 # mappings above; the provider translation is the fallback for every other
 # title, including names with an Arabic season/episode prefix.
 for pre,apre in (("serie marocaine","مسلسل مغربي"),("serie turque","مسلسل تركي"),("serie","مسلسل"),("film marocain","فيلم مغربي"),("film","فيلم"),("documentaire","وثائقي"),("rediffusion","إعادة")):
  if n.startswith(pre+" "):
   rest=raw[len(pre):].strip(" :-")
   rn=norm(rest)
   mapped=T2M_FORCE_AR.get(rn) or T2M.get(rn) or ""
   if not mapped:
    mapped=google_ar(http,rest,force=ar(rest) and bool(re.search(r"[A-Za-z]",rest)))
   if not ar(mapped):
    mapped=google_ar(http,rest,force=True)
   return apre+(" : "+mapped if mapped else "")
 translated=google_ar(http,raw,force=ar(raw) and bool(re.search(r"[A-Za-z]",raw)))
 return translated if ar(translated) else raw

def tr2m_desc(http,desc,category="",title=""):
 # Restore the stable 2M policy: category-aware Arabic descriptions, and only
 # translate a real synopsis when Telerama actually provides one.
 raw=clean(desc);cat=norm(category);ttl=norm(title)
 detailed=raw and len(raw)>=35 and norm(raw) not in {cat,ttl} and norm(raw)!=norm(category+" "+title)
 if detailed:
  y=google_ar(http,raw)
  if ar(y):return y
 if "meteo" in cat:return "نشرة جوية على قناة 2M تقدم توقعات الطقس ودرجات الحرارة."
 if "journal" in cat or "information" in cat:return "موعد إخباري على قناة 2M لمتابعة أبرز الأخبار والمستجدات."
 if "sport" in cat:return "برنامج رياضي على قناة 2M يتابع الأخبار والمنافسات الرياضية."
 if "serie" in cat or "feuilleton" in cat:return "مسلسل يُعرض على قناة 2M."
 if "film" in cat:return "فيلم يُعرض على قناة 2M."
 if "documentaire" in cat:return "برنامج وثائقي يُعرض على قناة 2M."
 if "debat" in cat:return "برنامج حواري على قناة 2M."
 if "theatre" in cat:return "عرض مسرحي يُعرض على قناة 2M."
 if "divertissement" in cat:return "برنامج ترفيهي يُعرض على قناة 2M."
 if "magazine" in cat:return "مجلة تلفزيونية تُعرض على قناة 2M."
 n=norm(title or desc)
 if "akhbar" in n or "info" in n or "journal" in n:return "موعد إخباري على قناة 2M."
 return "برنامج يُعرض على قناة 2M."

def _medi1_fallback_desc(cid,title):
 n=norm(title)
 if any(k in n for k in ("news","journal","akhbar","nashrat","flash")) or any(k in title for k in ("الأخبار","نشرة","المنتصف","حصاد اليوم")):
  return "موعد إخباري على قناة ميدي1 تيفي لمتابعة أبرز الأخبار والمستجدات."
 return "برنامج «%s» يُعرض على قناة ميدي1 تيفي."%title

def _medi1_detail_desc(http,href,title="",cache=None):
 href=clean(href)
 if not http or not href or href.startswith("#") or "javascript:" in href.casefold():return ""
 url=href if href.startswith("http") else requests.compat.urljoin("https://www.medi1tv.com/",href)
 if cache is not None and url in cache:return cache[url]
 out=""
 try:
  soup=BeautifulSoup(http.get(url,headers={"Referer":"https://www.medi1tv.com/ar/grille/"}).text,"lxml")
  nt=norm(title)
  candidates=[]
  heading=soup.find(["h1","h2","h3"],string=lambda x:x and nt and norm(clean(x))==nt)
  if heading:
   p=heading.find_next("p")
   if p:candidates.append(clean(p.get_text(" ",strip=True)))
  for el in soup.select(".description, .emission-description, .program-description, article p, .content p"):
   txt=clean(el.get_text(" ",strip=True))
   if txt:candidates.append(txt)
  for attrs in ({"property":"og:description"},{"name":"description"}):
   el=soup.find("meta",attrs=attrs)
   if el:
    txt=clean(el.get("content"))
    if txt:candidates.append(txt)
  for d in candidates:
   if len(d)>=25 and norm(d)!=nt and "copyright medi1" not in d.casefold():
    out=d;break
 except Exception:
  out=""
 if cache is not None:cache[url]=out
 return out

def _parse_medi1_page(cid,text,day,http=None,detail_cache=None):
 soup=BeautifulSoup(text,"lxml")
 tre=re.compile(r"^([0-2]?\d)[:hH]([0-5]\d)\s*(.*)$")
 exact=re.compile(r"^([0-2]?\d)[:hH]([0-5]\d)$")
 ctas={x.casefold() for x in (
  "صفحة البرنامج","صفحة النشرة","شاهد النشرات","شاهد البرنامج","التفاصيل","المزيد",
  "voir les jts","voir le programme","page de l'émission","en direct","المباشر",
  "البث المباشر","MEDI1TV Maghreb","MEDI1TV Arabic","MEDI1TV Afrique","Image")}
 stop_words={x.casefold() for x in (
  "الرئيسية","الأخبار","البرامج الإخبارية","برامج القناة","الحلقات الكاملة","أقوى اللحظات",
  "شبكة البث","ترددات البث","اتصل بنا","للإعلان","عن القناة","إشارات قانونية",
  "Actualités","Programmes","Replay","Moments forts","Grille","Fréquences","Contact","A propos",
  "Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche",
  "الإثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد")}
 rows=[]

 # Primary path: current Medi1 programme cards. A schedule card may put
 # "HH:MM title" in one text node and the synopsis in the next one.
 prev=None;pday=day
 if detail_cache is None:detail_cache={}
 for a in soup.find_all("a"):
  pieces=[clean(x) for x in a.stripped_strings if clean(x)]
  if not pieces:continue
  idx=None;m=None
  for pos,piece in enumerate(pieces):
   mm=tre.match(piece)
   if mm:
    idx=pos;m=mm;break
  if m is None:continue
  h1,m1=int(m.group(1)),int(m.group(2))
  tail=clean(m.group(3) or "")
  payload=([tail] if tail else [])+pieces[idx+1:]
  payload=[x for x in payload if x.casefold() not in ctas and x.casefold() not in stop_words and not re.fullmatch(r"image",x,re.I)]
  if not payload:continue
  mins=h1*60+m1
  if prev is not None and mins+300<prev:pday+=timedelta(days=1)
  prev=mins
  title=payload[0]
  desc=clean(" ".join(x for x in payload[1:] if x!=title))
  if len(desc)>1000:desc=""
  href=clean(a.get("href"))
  if not desc and href and any(k in href for k in ("/emission/","/programme/","/program/")):
   desc=_medi1_detail_desc(http,href,title,detail_cache)
  if not desc:desc=_medi1_fallback_desc(cid,title)
  rows.append(Event(cid,morocco_wall_clock(pday,dtime(h1,m1)),title,desc,None,lang(title),lang(desc),"medi1-card"))

 # Secondary authoritative path: Medi1 sometimes renders the schedule as plain
 # text (time and title are not wrapped in an <a>). The old parser silently
 # dropped these rows. Parse the visible sequence and merge it with card rows.
 strings=[clean(x) for x in soup.stripped_strings if clean(x)]
 raw=[];i=0
 while i<len(strings):
  m=tre.match(strings[i])
  if not m:
   i+=1;continue
  h1,m1=int(m.group(1)),int(m.group(2))
  tail=clean(m.group(3) or "")
  j=i+1;parts=([tail] if tail else [])
  while j<len(strings) and not tre.match(strings[j]):
   s=strings[j];sf=s.casefold()
   if sf in stop_words and parts:break
   if sf not in ctas and sf not in stop_words and not re.fullmatch(r"image",s,re.I):
    parts.append(s)
   j+=1
   if len(parts)>=8:break
  if parts:
   title=parts[0]
   desc=clean(" ".join(x for x in parts[1:] if x!=title))
   if len(desc)>1000:desc=""
   raw.append((h1,m1,title,desc))
  i=max(j,i+1)

 prev=None;pday=day
 for h1,m1,title,desc in raw:
  mins=h1*60+m1
  if prev is not None and mins+300<prev:pday+=timedelta(days=1)
  prev=mins
  if not desc:desc=_medi1_fallback_desc(cid,title)
  rows.append(Event(cid,morocco_wall_clock(pday,dtime(h1,m1)),title,desc,None,lang(title),lang(desc),"medi1-flat"))

 # One programme per start/title. Prefer the richest official description.
 merged={}
 for e in rows:
  k=(e.start,e.title.casefold())
  prev=merged.get(k)
  if prev is None:
   merged[k]=e;continue
  pg=prev.desc.startswith("برنامج «") or prev.desc.startswith("موعد إخباري على قناة ميدي1")
  eg=e.desc.startswith("برنامج «") or e.desc.startswith("موعد إخباري على قناة ميدي1")
  if (pg and not eg) or (pg==eg and len(e.desc)>len(prev.desc)):
   merged[k]=e
 out=sorted(merged.values(),key=lambda e:e.start)
 infer(out)
 official=sum(1 for e in out if not (e.desc.startswith("برنامج «") or e.desc.startswith("موعد إخباري على قناة ميدي1")))
 log("Medi1 %s page events=%d official_desc=%d flat=%d"%(cid,len(out),official,sum(1 for e in out if e.source=="medi1-flat")))
 return out

def scrape_medi1(days):
 h=Http();out=[];today=datetime.now(TZ).date();detail_cache={}
 for cid,bases in MEDI1:
  rows=[]
  for i in range(days):
   day=today+timedelta(days=i);ds=day.strftime("%d-%m-%Y")
   best=[]
   # Probe both official hosts and keep the richest parse. Some .ma pages are
   # cached/stale while .com exposes the complete current card list.
   for base in bases:
    for url in (base+"/"+ds,base) if i==0 else (base+"/"+ds,):
     try:
      r=h.get(url,headers={"Referer":"https://www.medi1tv.com/ar/"})
      cand=_parse_medi1_page(cid,r.text,day,h,detail_cache)
      if len(cand)>len(best):best=cand
     except Exception:pass
   if best:
    log("Medi1 %s %s selected=%d events"%(cid,day.isoformat(),len(best)))
    rows+=best
  seen={}
  for e in sorted(rows,key=lambda e:e.start):
   k=(e.start,e.title.casefold())
   prev=seen.get(k)
   if prev is None or len(e.desc or "")>len(prev.desc or ""):seen[k]=e

  # A linear TV channel cannot air two different programmes at the exact same
  # start time. Medi1 occasionally publishes a stale duplicate card (e.g.
  # Sunday 21:15 "ريمونطادا" beside the real Sunday show "مثير للجدل").
  # Enigma2 may then drop both overlapping rows. Resolve same-start collisions
  # by preferring the entry with the richest official synopsis.
  by_start=defaultdict(list)
  for e in seen.values():by_start[e.start].append(e)
  resolved=[];collisions=0
  for start,items in by_start.items():
   if len(items)==1:
    resolved.append(items[0]);continue
   collisions+=1
   def medi_score(e):
    d=clean(e.desc)
    generic=(not d or d.startswith("برنامج «") or d.startswith("موعد إخباري على قناة ميدي1"))
    return (0 if generic else 1,len(d),1 if e.source=="medi1-card" else 0)
   winner=max(items,key=medi_score)
   dropped=", ".join(x.title for x in items if x is not winner)
   log("Medi1 %s collision %s keep=%s drop=%s"%(cid,start.strftime("%F %H:%M"),winner.title,dropped))
   resolved.append(winner)
  out+=sorted(resolved,key=lambda e:e.start)
  if collisions:log("Medi1 %s same-start collisions resolved=%d"%(cid,collisions))
 infer(out)
 # Never publish a blank/title-only Medi1 description.
 for e in out:
  if not clean(e.desc) or norm(e.desc)==norm(e.title):
   e.desc=_medi1_fallback_desc(e.channel,e.title);e.dl="ar"
 return out

def _telerama_detail_desc(h,href,title=""):
 href=clean(href)
 if not href or href.startswith("#") or "javascript:" in href.casefold():
  return ""
 # Programme links are normally relative to television.telerama.fr; cinema
 # pages may redirect to www.telerama.fr and requests follows that safely.
 url=requests.compat.urljoin("https://television.telerama.fr/",href)
 try:
  soup=BeautifulSoup(h.get(url,headers={"Referer":"https://television.telerama.fr/"}).text,"lxml")
 except Exception:
  return ""
 candidates=[]
 # Prefer the explicit Synopsis block exposed by current Telerama programme pages.
 h2=soup.find(["h2","h3"],string=lambda x:x and clean(x).casefold()=="synopsis")
 if h2:
  p=h2.find_next("p")
  if p:
   txt=clean(p.get_text(" ",strip=True))
   if txt:candidates.append(txt)
 # Metadata fallback for template changes.
 for attrs in ({"property":"og:description"},{"name":"description"},{"name":"twitter:description"}):
  el=soup.find("meta",attrs=attrs)
  if el:
   txt=clean(el.get("content"))
   if txt:candidates.append(txt)
 nt=norm(title)
 for desc in candidates:
  nd=norm(desc)
  if not desc or len(desc)<20 or nd==nt:
   continue
  if desc.casefold().startswith(("programme tv","retrouvez le programme","telerama")):
   continue
  return desc
 return ""

def parse_2m(h,text,day,detail_cache=None):
 soup=BeautifulSoup(text,"lxml");out=[];seen=set()
 if detail_cache is None:detail_cache={}
 cats=("Magazine sportif","Documentaire de société","Magazine d'information",
       "Magazine de services","Magazine de société","Magazine culturel",
       "Série dramatique","Série sentimentale","Divertissement","Documentaire",
       "Feuilleton","Magazine","Journal","Météo","Débat","Théâtre","Film","Série")
 for a in soup.find_all("a"):
  parts=[clean(x) for x in a.stripped_strings if clean(x)]
  raw=clean(" ".join(parts))
  m=re.search(r"(?<!\d)([0-2]?\d)h([0-5]\d)(?!\d)",raw)
  if not m:continue
  prefix=clean(raw[:m.start()])
  category=""
  title=prefix
  for cat in sorted(cats,key=len,reverse=True):
   if prefix.casefold().startswith(cat.casefold()+" "):
    category=cat
    title=clean(prefix[len(cat):])
    break
  if not title or len(title)>120 or re.fullmatch(r"[0-2]?\d[h:]?[0-5]\d",title):continue
  k=("%02d:%02d"%(int(m.group(1)),int(m.group(2))),title.casefold())
  if k in seen:continue
  seen.add(k)
  hr,mi=int(m.group(1)),int(m.group(2))
  # Telerama's 2M grid is published one hour ahead of the actual Morocco
  # broadcast clock. Keep a fixed -1h correction here; do NOT convert through
  # Europe/Paris, otherwise summer time would incorrectly shift 13:45 to 11:45.
  source_dt=datetime.combine(day,dtime(hr,mi))-timedelta(hours=1)
  start=morocco_wall_clock(source_dt.date(),source_dt.time())

  # Same Morocco Cloud 2M logic: normalized Arabic title + translated/normalized description.
  # Prefer the real synopsis from the linked Telerama programme page, exactly as
  # SNRT now prefers its programme-detail synopsis over a generic fallback.
  desc=""
  href=clean(a.get("href")) if a else ""
  if href and (".php" in href or "/tele/" in href or "/cinema/" in href):
   if href not in detail_cache:
    detail_cache[href]=_telerama_detail_desc(h,href,title)
   desc=clean(detail_cache.get(href))
  if not desc and len(parts)>1:
   tail=[]
   passed_time=False
   for p in parts:
    if re.search(r"(?<!\d)[0-2]?\d[h:][0-5]\d(?!\d)",p):
     passed_time=True
     continue
    if passed_time and p!=title and p!=category:
     tail.append(p)
   desc=clean(" ".join(tail))
  if not desc:
   desc=category or title

  at=tr2m_title(h,title)
  # 2M language exception: the weather bulletin immediately following
  # Info Soir keeps its French on-air title. Other weather bulletins are Arabic.
  if norm(title) in ("bulletin meteo","meteo") and out and norm(out[-1].title)=="info soir":
   at=title
  ad=tr2m_desc(h,desc,category,title)
  out.append(Event("2M",start,at,ad,None,lang(at),"ar","2m-telerama"))
 out.sort(key=lambda e:e.start)
 return out

def scrape_2m(days):
 # Strict receiver policy: publish only the next 48 hours.
 # We may need up to 3 calendar pages when the run starts late in the day,
 # but events beyond now+48h are never kept.
 h=Http();now=datetime.now(TZ);today=now.date();target=now+timedelta(hours=48);out=[];detail_cache={}
 weekdays=["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
 page_days=(target.date()-today).days+1
 for i in range(page_days):
  d=today+timedelta(days=i)
  url="https://television.telerama.fr/chaine/2m-maroc" if i==0 else f"https://television.telerama.fr/programme-tv-{weekdays[d.weekday()]}/2m-maroc"
  try:
   rows=parse_2m(h,h.get(url,headers={"Referer":"https://television.telerama.fr/"}).text,d,detail_cache)
   log("2M %s telerama: %d events"%(d.isoformat(),len(rows)))
   out+=rows
  except Exception as ex:
   log("2M %s telerama failed: %s"%(d.isoformat(),ex))
 seen=set()
 out=[ev for ev in sorted(out,key=lambda ev:ev.start)
      if ev.start < target and not ((ev.start,ev.title.casefold()) in seen or seen.add((ev.start,ev.title.casefold())))]
 infer(out)
 out=[ev for ev in out if ev.stop and ev.stop>now-timedelta(hours=2) and ev.start<target and ev.stop>ev.start and ev.stop-ev.start<=timedelta(hours=4)]
 log("2M strict 48h total: %d events; detail_pages=%d; cutoff=%s"%(len(out),len(detail_cache),target.isoformat()))
 untranslated=[ev for ev in out if re.search(r"[A-Za-zÀ-ÿ]",re.sub(r"(?i)\b2M\b","",ev.title or ""))]
 log("2M Arabic title coverage: %d/%d; remaining_latin=%d"%(len(out)-len(untranslated),len(out),len(untranslated)))
 for ev in untranslated[:10]:log("2M untranslated title: %s"%ev.title)
 for ev in out[:20]:
  log("2M MAP %s | %s"%(ev.start.strftime("%m-%d %H:%M"),ev.title))
 return out

def _chada_title_desc(raw):
 title=clean(raw).strip(" .-|:")
 n=norm(title)
 meta=next((CHADA[k] for k in sorted(CHADA,key=len,reverse=True) if k in n),None)
 if meta:return meta
 # Chada is Arabic-native: do not publish French generic descriptions.
 if "capsule sport" in n:return ("كبسولة رياضية","كبسولة رياضية على قناة شدى تي في.")
 if "capsule culinaire" in n:return ("كبسولة طبخ","كبسولة طبخ على قناة شدى تي في.")
 if "capsule beaute" in n:return ("كبسولة جمال","كبسولة جمال على قناة شدى تي في.")
 if "capsule mode" in n:return ("كبسولة موضة","كبسولة موضة على قناة شدى تي في.")
 if "capsule bien etre" in n:return ("كبسولة رفاهية","كبسولة رفاهية على قناة شدى تي في.")
 return (title,"برنامج يُعرض على قناة شدى تي في.")

def _parse_chada_piisas(text,day):
 soup=BeautifulSoup(text,"lxml");raw=[]
 # Piisas renders each schedule item as a list row. Parse DOM rows first,
 # then fall back to every text node / raw HTML so minor markup changes
 # do not zero the feed.
 candidates=[]
 for el in soup.find_all(["li","p","div","span","a"]):
  t=clean(" ".join(el.stripped_strings))
  if t and re.search(r"\b[0-2]?\d:[0-5]\d\b",t):
   candidates.append(t)
 candidates.extend(clean(x) for x in soup.stripped_strings if x)
 candidates.append(clean(soup.get_text(" ",strip=True)))
 candidates.append(clean(re.sub(r"<[^>]+>"," ",text)))
 rx=re.compile(r"(?<!\d)([0-2]?\d:[0-5]\d)\s*(?:-|–|—|:|\u00a0)*\s*([^|\n]+?)(?=(?:\s+[0-2]?\d:[0-5]\d\b)|$)")
 for cand in candidates:
  for m in rx.finditer(cand):
   hm=clean(m.group(1));title=clean(m.group(2)).strip(" .-|–—:")
   # avoid swallowing navigation/footer text when a whole-page candidate is used
   title=re.split(r"\s+(?:HIER|CE MOMENT|AUJOURD.HUI|CE SOIR|DEMAIN)\b",title,1,flags=re.I)[0].strip()
   if len(title)<2 or len(title)>140:continue
   hr,mi=map(int,hm.split(":"))
   if hr>23:continue
   raw.append((morocco_wall_clock(day,dtime(hr,mi)),title))
 out=[];seen=set()
 for start,title in sorted(raw,key=lambda x:x[0]):
  k=(start,title.casefold())
  if k in seen:continue
  seen.add(k)
  t,d=_chada_title_desc(title)
  out.append(Event("Chada TV",start,t,d,None,lang(t,"fr"),lang(d,"fr"),"piisas"))
 infer(out)
 return out

def _scrape_chada_official(days):
 h=Http();today=datetime.now(TZ).date();out=[]
 try:r=h.get("https://chada.ma/fr/chada-tv/grille-tv/")
 except Exception:r=None
 if not r:return out
 tree=LH.fromstring(r.text)
 for bad in tree.xpath("//script|//style|//nav|//footer|//header"):
  if bad.getparent() is not None:bad.getparent().remove(bad)
 area=(tree.xpath("//div[contains(@class,'elementor-text-editor')]") or tree.xpath("//div[contains(@class,'posts-area')]") or tree.xpath("//body"))
 full="  ".join(clean(x) for x in area[0].xpath(".//text()") if clean(x)) if area else ""
 raw=[]
 for m in re.finditer(r"(\d{2}:\d{2})(?:\s*(?:à|-)\s*\d{2}:\d{2})?\s*[.\-]?\s*(.*?)(?=\s*(?:\d{2}:\d{2})|$)",full):
  title=clean(m.group(2)).strip(" .-|:")
  if len(title)>2:raw.append((datetime.strptime(m.group(1),"%H:%M").time(),title))
 seq=[];last=None
 for t,title in raw:
  if last is None or t>last or (t<last and last.hour>=18 and t.hour<=5):seq.append((t,title));last=t
 for i in range(min(days,1)):
  cur=today+timedelta(days=i);prev=seq[0][0] if seq else dtime(0)
  for t,orig in seq:
   if t<prev:cur+=timedelta(days=1)
   prev=t
   title,desc=_chada_title_desc(orig)
   out.append(Event("Chada TV",morocco_wall_clock(cur,t),title,desc,None,lang(title,"fr"),lang(desc,"fr"),"chada-official"))
 infer(out);return out

def scrape_chada(days):
 h=Http();today=datetime.now(TZ).date();out=[]
 pages=[(today,"https://piisas.com/chada-tv/aujourdhui")]
 if days>1:pages.append((today+timedelta(days=1),"https://piisas.com/chada-tv/demain"))
 for day,url in pages:
  try:
   rows=_parse_chada_piisas(h.get(url,headers={"Referer":"https://piisas.com/chada-tv/"}).text,day)
   log("Chada %s piisas: %d events"%(day.isoformat(),len(rows)))
   out+=rows
  except Exception as ex:
   log("Chada %s piisas failed: %s"%(day.isoformat(),ex))
 if not out:
  out=_scrape_chada_official(days)
  log("Chada official fallback: %d events"%len(out))
 seen=set();cleanrows=[]
 for ev in sorted(out,key=lambda ev:ev.start):
  k=(ev.start,ev.title.casefold())
  if k not in seen:
   seen.add(k);cleanrows.append(ev)
 infer(cleanrows)
 return cleanrows

def scrape_snrt(days):
 def one(cid,url):
  h=Http();r=h.get(url);soup=BeautifulSoup(r.text,"lxml");structured=[]
  visible=_snrt_visible_descs(soup);visible_used=defaultdict(int);visible_matches=0
  detail_cache={};detail_matches=0
  now=datetime.now(TZ);detail_from=now-timedelta(hours=12);detail_until=now+timedelta(days=min(max(days,1),3))
  for row in soup.find_all("div",class_=lambda x:x and "grille-line" in x.split()):
   dc=[x for x in row.get("class",[]) if x.isdigit() and len(x)==8]
   tt=row.find("div",class_="grille-time")
   if not dc or not tt:continue
   time_text=clean(tt.get_text())
   try:
    naive=datetime.strptime(dc[0]+" "+time_text.replace("H",":"),"%Y%m%d %H:%M")
    # SNRT uses a broadcast-day tab: its after-midnight rows still carry the
    # previous tab date. The next broadcast day starts at 07:00.
    if naive.hour<SNRT_DAY_START_HOUR:
     naive+=timedelta(days=1)
    start=morocco_localize(naive)
   except Exception:continue
   h2=row.find("h2",class_="program-title-sm")
   original_title=clean(h2.get_text(" ",strip=True)) if h2 else "برنامج"
   desc=_snrt_desc_from_row(cid,row,original_title,time_text,original_title)
   # Most current SNRT grids expose only time/title (or a genre badge). The
   # full official synopsis is on the linked programme page. Fetch it only for
   # the 48-72h working window and only when the row itself has no rich synopsis.
   if detail_from<=start<detail_until and _snrt_generic_desc(desc,original_title):
    a=None
    if h2:
     a=h2.find("a",href=True) or h2.find_parent("a")
    if a is None:
     a=row.find("a",href=True)
    href=clean(a.get("href")) if a else ""
    if href and "javascript:" not in href.casefold() and "jascript:" not in href.casefold():
     if href not in detail_cache:
      detail_cache[href]=_snrt_detail_desc(h,href,original_title)
     rich=clean(detail_cache.get(href))
     # _snrt_detail_desc already rejects empty/title-only/site-generic metadata.
     # Keep short official SNRT synopses too; some legitimate descriptions are
     # concise and should not be replaced by our generic fallback.
     if rich:
      desc=rich;detail_matches+=1
   tm=re.search(r"([0-2]?\d)\s*[Hh:]\s*([0-5]\d)",time_text)
   if tm:
    key=("%02d:%02d"%(int(tm.group(1)),int(tm.group(2))),norm(original_title))
    choices=visible.get(key,[])
    pos=visible_used[key]
    if pos<len(choices):
     vdesc=clean(choices[pos]);visible_used[key]+=1
     generic=(not desc or norm(desc)==norm(original_title) or desc.startswith("برنامج «") or desc.startswith("نشرة إخبارية على ") or desc.startswith("نشرة الطقس على "))
     if vdesc and (generic or len(vdesc)>len(desc)):
      desc=vdesc;visible_matches+=1
   title=original_title
   row_text=clean(row.get_text(" ",strip=True));ctx=original_title+" "+row_text+" "+desc
   if "الأخبار" in ctx or "الاخبار" in ctx:
    for k,v in NEWS.items():
     if k in ctx:
      title=v
      break
   structured.append(Event(cid,start,title,desc,None,"ar",lang(desc,"ar"),"snrt-ar"))

  flat=_snrt_flat_events(cid,soup)
  # One programme per channel/start. Prefer an official non-generic synopsis;
  # when both parsers found it, keep the richer description.
  merged={}
  def score(e):
   d=clean(e.desc)
   generic=(not d or norm(d)==norm(e.title) or d.startswith("برنامج «") or d.startswith("نشرة إخبارية على ") or d.startswith("نشرة الطقس على "))
   return (0 if generic else 1,len(d),1 if e.source=="snrt-ar-flat" else 0)
  for e in structured+flat:
   prev=merged.get(e.start)
   if prev is None or score(e)>score(prev):
    merged[e.start]=e
  rows=sorted(merged.values(),key=lambda e:e.start)
  infer(rows)
  official=sum(1 for e in rows if score(e)[0])
  log("SNRT %s structured=%d flat=%d merged=%d official_desc=%d detail_matches=%d detail_pages=%d visible_matches=%d visible_keys=%d"%(cid,len(structured),len(flat),len(rows),official,detail_matches,len(detail_cache),visible_matches,len(visible)))
  return rows

 out=[]
 with ThreadPoolExecutor(max_workers=2) as ex:
  fs={ex.submit(one,c,u):c for c,u in SNRT.items()}
  for f in as_completed(fs):
   try:out+=f.result()
   except Exception as e:log("SNRT %s: %s"%(fs[f],e))

 # Reuse only real SNRT synopses for the exact same programme title across
 # SNRT channels. Al Maghribiya rebroadcasts many Al Aoula programmes and often
 # exposes a richer synopsis for the same title.
 desc_bank={}
 for e in out:
  d=clean(e.desc);k=norm(e.title)
  generic=(not d or norm(d)==k or d.startswith("برنامج «") or
           d.startswith("نشرة إخبارية على ") or d.startswith("نشرة الطقس على "))
  if not k or generic:continue
  prev=desc_bank.get(k,"")
  if not prev or (ar(d) and not ar(prev)) or len(d)>len(prev):
   desc_bank[k]=d
 cross_desc=0
 for e in out:
  if not _snrt_generic_desc(e.desc,e.title):continue
  rich=desc_bank.get(norm(e.title),"")
  if rich:
   e.desc=rich;e.dl=lang(rich,"ar");cross_desc+=1
 log("SNRT cross-channel official descriptions applied=%d bank=%d"%(cross_desc,len(desc_bank)))

 # An unavailable AFLAM schedule remains empty. Never generate a timetable.
 infer(out)
 # Never publish a blank SNRT description.
 for e in out:
  if not clean(e.desc):e.desc=_snrt_fallback_desc(e.channel,e.title)
 return out

def _arryadia_date_token(raw,now):
 raw=clean(raw)
 candidates=[]
 for fmt in ("%Y%m%d","%d%m%Y"):
  try:candidates.append(datetime.strptime(raw,fmt).date())
  except Exception:pass
 if not candidates:return None
 # Accept only dates close to the current guide window. This prevents stale
 # hidden/archive markup from being published as current EPG.
 candidates.sort(key=lambda d:abs((d-now.date()).days))
 return candidates[0] if abs((candidates[0]-now.date()).days)<=10 else None

def _arryadia_date_from_node(node,now):
 cur=node
 for _ in range(8):
  if cur is None:break
  vals=[]
  try:
   vals.extend(cur.get("class",[]) or [])
   for key in ("id","data-date","data-day","data-dt"):
    v=cur.get(key)
    if v:vals.append(v)
  except Exception:pass
  for value in vals:
   for tok in re.findall(r"(?<!\d)\d{8}(?!\d)",str(value)):
    d=_arryadia_date_token(tok,now)
    if d:return d
  cur=getattr(cur,"parent",None)
 return None

def _arryadia_event_container(node):
 # Find the smallest useful parent containing time + title/description.
 cur=node
 best=getattr(node,"parent",None)
 for _ in range(6):
  cur=getattr(cur,"parent",None)
  if cur is None:break
  txt=clean(cur.get_text(" ",strip=True))
  if 8<=len(txt)<=700:best=cur
  if cur.name in ("li","article","tr"):break
  classes=" ".join(cur.get("class",[]) or []).lower()
  if any(k in classes for k in ("grille-line","program","programme","schedule","item")):break
 return best or getattr(node,"parent",node)

def _parse_arryadia_snrt(soup):
 now=datetime.now(TZ);raw=[];seen=set()
 # Scan every visible time token rather than only the old div.grille-line
 # markup. SNRT now mixes old rows and a newer card-style layout.
 time_rx=re.compile(r"^\s*([0-2]?\d)\s*[Hh:]\s*([0-5]\d)\s*$")
 for txtnode in soup.find_all(string=True):
  t=clean(txtnode)
  m=time_rx.match(t)
  if not m:continue
  day=_arryadia_date_from_node(txtnode,now)
  if not day:continue
  hr,mi=int(m.group(1)),int(m.group(2))
  if hr>23:continue
  box=_arryadia_event_container(txtnode)
  # Prefer an explicit heading/link for the programme title.
  title=""
  if box is not None:
   cand=box.find(["h1","h2","h3","h4","a"],string=lambda x:x and clean(x) and not time_rx.match(clean(x)))
   if cand:title=clean(cand.get_text(" ",strip=True))
  parts=[clean(x) for x in box.stripped_strings if clean(x)] if box is not None else []
  parts=[x for x in parts if not time_rx.match(x) and x not in ("الآن","SAT","TNT")]
  if not title and parts:title=parts[0]
  if not title or len(title)>220:continue
  desc=clean(" ".join(x for x in parts if x!=title))
  s=morocco_wall_clock(day,dtime(hr,mi))
  key=(s,title.casefold())
  if key in seen:continue
  seen.add(key);raw.append((s,title,desc or title))
 # Keep the legacy row parser as a compatibility supplement.
 for row in soup.find_all("div",class_=lambda x:x and "grille-line" in x.split()):
  vals=[]
  vals.extend(row.get("class",[]) or [])
  for key in ("id","data-date","data-day","data-dt"):
   v=row.get(key)
   if v:vals.append(v)
  day=None
  for value in vals:
   for tok in re.findall(r"(?<!\d)\d{8}(?!\d)",str(value)):
    day=_arryadia_date_token(tok,now)
    if day:break
   if day:break
  tt=row.find(class_=lambda x:x and "grille-time" in " ".join(x if isinstance(x,list) else [x]))
  if not day or not tt:continue
  m=re.search(r"([0-2]?\d)\s*[Hh:]\s*([0-5]\d)",clean(tt.get_text()))
  if not m:continue
  s=morocco_wall_clock(day,dtime(int(m.group(1)),int(m.group(2))))
  h2=row.find(["h2","h3"],class_=lambda x:x and "program" in " ".join(x if isinstance(x,list) else [x]).lower())
  title=clean(h2.get_text(" ",strip=True)) if h2 else ""
  if not title:continue
  desc=clean(row.get_text(" ",strip=True)) or title
  key=(s,title.casefold())
  if key not in seen:seen.add(key);raw.append((s,title,desc))
 raw.sort(key=lambda x:x[0])
 return raw

def _parse_arryadia_flat(soup):
 # Current SNRT page keeps the day tabs together, then emits programme blocks
 # in DOM order. Parse that official text layout when date attributes are not
 # attached to each programme row.
 now=datetime.now(TZ)
 strings=[clean(x) for x in soup.stripped_strings if clean(x)]
 time_rx=re.compile(r"^([0-2]?\d)\s*[Hh:]\s*([0-5]\d)$")
 day_rx=re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
 day_labels=[]
 first_time=None
 for pos,s in enumerate(strings):
  if first_time is None and time_rx.match(s):first_time=pos
  if first_time is not None and pos>=first_time:break
  m=day_rx.search(s)
  if not m:continue
  try:
   d=datetime(now.year,int(m.group(2)),int(m.group(1))).date()
   if d<now.date()-timedelta(days=180):d=datetime(now.year+1,d.month,d.day).date()
   if d>now.date()+timedelta(days=180):d=datetime(now.year-1,d.month,d.day).date()
  except Exception:continue
  if d not in day_labels:day_labels.append(d)
 if first_time is None or not day_labels:return []

 # Build programme blocks: one time token followed by title/description text.
 raw=[]
 i=first_time
 while i<len(strings):
  m=time_rx.match(strings[i])
  if not m:
   i+=1;continue
  hr,mi=int(m.group(1)),int(m.group(2))
  if hr>23:i+=1;continue
  j=i+1;parts=[]
  while j<len(strings) and not time_rx.match(strings[j]):
   s=strings[j]
   if s in ("الرئيسية","الشركة","القنوات","الوسيط","طلبات العروض","Régie publicitaire","Mentions légales"):
    break
   if not day_rx.search(s):parts.append(s)
   j+=1
  # Keep channel markers for feed selection but not as the visible title.
  visible=[x for x in parts if x not in ("الآن","SAT","TNT") and not re.fullmatch(r"Image",x,re.I)]
  if visible:
   title=visible[0]
   desc=clean(" ".join(visible[1:])) or title
   markers=" ".join(parts)
   raw.append({"minutes":hr*60+mi,"hr":hr,"mi":mi,"title":title,"desc":desc,"markers":markers})
  i=max(j,i+1)
 if not raw:return []

 # Split the flat list into daily blocks. A morning restart after a daytime
 # schedule, or after post-midnight carry-over, starts the next visible day.
 groups=[[]]
 prev=None
 for item in raw:
  cur=item["minutes"]
  new_day=False
  if prev is not None:
   if prev<7*60 and cur>=7*60:
    new_day=True
   elif cur+4*60<prev and cur>=7*60:
    new_day=True
  if new_day:groups.append([])
  groups[-1].append(item);prev=cur
 groups=[g for g in groups if g]
 if not groups:return []

 # SNRT can omit an expired first day from the programme DOM while leaving its
 # tab visible. Align the visible programme groups to the most recent day tabs.
 dates=sorted(day_labels)[-len(groups):]
 if len(dates)!=len(groups):return []
 out=[]
 for day,group in zip(dates,groups):
  carry=False;prev=None
  for item in group:
   cur=item["minutes"]
   if prev is not None and cur+4*60<prev and cur<7*60:carry=True
   if prev is not None and prev<7*60 and cur>=7*60:carry=False
   evday=day+(timedelta(days=1) if carry and item["hr"]<7 else timedelta(0))
   s=morocco_wall_clock(evday,dtime(item["hr"],item["mi"]))
   full=(item["title"]+" "+item["desc"]+" "+item["markers"]).lower()
   ids=[]
   if re.search(r"\btnt\b",full):ids.append("Arryadia_TNT")
   if re.search(r"\bsat\b",full):ids.append("Arryadia_HD")
   if not ids:ids=["Arryadia_HD","Arryadia_TNT"]
   title=item["title"]
   if re.search(r"\b(?:direct|live)\b|مباشر",full,re.I) and not title.startswith("مباشر"):
    title="مباشر: "+title
   for cid in ids:out.append(Event(cid,s,title,item["desc"],None,"ar","ar","arryadia-snrt-flat"))
   prev=cur
 infer(out)
 log("Arryadia flat parser groups=%d dates=%s events=%d"%(len(groups),",".join(d.isoformat() for d in dates),len(out)))
 return out

def scrape_arryadia(days,history=None,history_path=None):
 h=Http();parsed=[];flat=[];soup=None
 try:
  r=h.get("https://www.snrt.ma/ar/node/4070",
          params={"_":int(time.time())},
          headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
                   "Accept-Language":"ar-MA,ar;q=.9,fr;q=.8,en;q=.6",
                   "Cache-Control":"no-cache","Pragma":"no-cache"})
  soup=BeautifulSoup(r.text,"lxml")
  structured=_parse_arryadia_snrt(soup)
  flat=_parse_arryadia_flat(soup)
  # Keep legacy tuples and visible-layout Event objects separate; they are
  # normalized to Event below before de-duplication.
  parsed=structured
 except Exception as e:log("Arryadia SNRT parse: %s"%e)
 if parsed:
  log("Arryadia SNRT parsed=%d range=%s..%s"%(len(parsed),parsed[0][0].isoformat(),parsed[-1][0].isoformat()))
 else:
  log("Arryadia SNRT parsed=0")
  # Diagnostic for SNRT markup changes: log only structural attributes around
  # a few time tokens, never the full response.
  try:
   shown=0
   trx=re.compile(r"^\s*[0-2]?\d\s*[Hh:]\s*[0-5]\d\s*$")
   for tn in (soup.find_all(string=True) if soup is not None else []):
    if not trx.match(clean(tn)):continue
    chain=[];cur=getattr(tn,"parent",None)
    for _ in range(6):
     if cur is None:break
     attrs=[]
     for k in ("class","id","data-date","data-day","data-dt"):
      v=cur.get(k)
      if v:attrs.append("%s=%s"%(k,v))
     chain.append("%s[%s]"%(getattr(cur,"name","?"),",".join(attrs)))
     cur=getattr(cur,"parent",None)
    log("Arryadia DOM %s :: %s"%(clean(tn)," > ".join(chain)))
    shown+=1
    if shown>=12:break
  except Exception as de:log("Arryadia DOM diagnostic failed: %s"%de)
 out=[]
 for i,(s,title,desc) in enumerate(parsed):
  next_start=parsed[i+1][0] if i+1<len(parsed) else None
  stop=next_start if next_start and next_start>s and next_start-s<=timedelta(hours=6) else s+timedelta(hours=2)
  full=(title+" "+desc).lower()
  ids=[cid for pat,cid in ARR_TAGS.items() if re.search(pat,full)] or ["Arryadia_HD","Arryadia_TNT"]
  live=bool(re.search(r"\b(?:live|direct)\b|مباشر",full,re.I))
  if live and not title.startswith("مباشر"):title="مباشر: "+title
  for cid in ids:out.append(Event(cid,s,title,desc or title,stop,"ar","ar","arryadia-snrt"))
 out+=flat
 # De-duplicate rows exposed by both official SNRT layouts.
 dedup={}
 for e in out:
  k=(e.channel,e.start,e.title.casefold())
  prev=dedup.get(k)
  if prev is None or len(e.desc or "")>len(prev.desc or ""):dedup[k]=e
 out=sorted(dedup.values(),key=lambda e:(e.channel,e.start,e.title.casefold()))
 infer(out)
 # Capture every official SNRT row still visible, including past days, before
 # trimming the live feed. This becomes a rolling seven-day recurrence corpus.
 archive=[]
 if history_path:
  archive=_arryadia_history_update(history_path,out,days=7)
 combined_history=list(history or [])+archive
 now=datetime.now(TZ);limit=now+timedelta(days=min(days,3))
 current=[e for e in out if e.stop and e.stop>now-timedelta(hours=2) and e.start<limit and e.stop>e.start]
 future=[e for e in current if e.stop>now]
 log("Arryadia SNRT future=%d current_window=%d"%(len(future),len(current)))
 # A football fixture is not evidence that this exact TV service broadcasts it.
 # Only the broadcaster grid and exact still-future LKG events may fill gaps.
 filled=_arryadia_fill(current,hours=48,slot_hours=2,history=combined_history)
 auto_count=sum(1 for e in filled if e.source=="arryadia-auto")
 log("Arryadia generated filler=%d genuine dated events=%d"%(auto_count,len(filled)))
 return filled

def to_xml(rows,path):
 # Final XMLTV safety gate: malformed timestamps must never poison the whole
 # Morocco feed. infer() repairs most cases; any remaining invalid row is
 # discarded here and reported in logs.
 valid_rows=[];dropped=0
 for e in rows:
  if generated_placeholder(e.title) or e.source in ("arryadia-auto","arryadia-smart","arryadia-repeat"):
   dropped+=1
   continue
  if not e.start or not e.stop or e.stop<=e.start:
   dropped+=1
   continue
  valid_rows.append(e)
 if dropped: log("Morocco XML safety gate dropped %d invalid rows"%dropped)
 root=ET.Element("tv",{"generator-info-name":"EPGManager Morocco Cloud rc23"});counts=defaultdict(int)
 for cid in sorted(set(CHANNELS)|{e.channel for e in valid_rows}):ch=ET.SubElement(root,"channel",{"id":cid});ET.SubElement(ch,"display-name").text=CHANNELS.get(cid,cid)
 for e in sorted(valid_rows,key=lambda e:(e.start,e.channel,e.title)):
  p=ET.SubElement(root,"programme",{"start":xdt(e.start),"stop":xdt(e.stop),"channel":e.channel});ET.SubElement(p,"title",{"lang":e.tl or lang(e.title)}).text=e.title;ET.SubElement(p,"desc",{"lang":e.dl or lang(e.desc)}).text=e.desc or e.title;counts[e.channel]+=1
 ET.indent(root,space="  ");ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True);return dict(counts)

def select_groups(results,old,now=None):
 """Isolate provider failures; retain exact future LKG per channel, never shift dates."""
 now=now or datetime.now(TZ)
 def usable(e):
  return (e.start and e.stop and e.stop>e.start and e.stop>now and
          e.start<now+timedelta(days=3) and not generated_placeholder(e.title) and
          e.source not in ("arryadia-auto","arryadia-smart","arryadia-repeat"))
 final=[];status={}
 for g in ("snrt","arryadia","2m","chada","medi1"):
  fresh=[e for e in results.get(g,[]) if e.channel in GROUPS[g] and usable(e)]
  ok,detail=valid(g,fresh)
  rows=list(fresh);kept=0
  for e in sorted(old,key=lambda e:e.start):
   if e.channel not in GROUPS[g] or not usable(e):continue
   if any(x.channel==e.channel and x.start<e.stop and x.stop>e.start for x in rows):continue
   rows.append(e);kept+=1
  mode=("fresh" if ok else "degraded-fresh") if fresh else ("last-known-good" if rows else "missing")
  status[g]={"mode":mode,"events":len(rows),"retained_exact_lkg":kept,"detail":detail,"qualified":ok}
  log("%s: %s (%s); exact LKG=%d"%(g,mode,detail,kept))
  final+=rows
 return final,status


def main():
 ap=argparse.ArgumentParser();ap.add_argument("--output-dir",default="output");ap.add_argument("--previous",default="");ap.add_argument("--days",type=int,default=7);ap.add_argument("--arryadia-history",default="data/arryadia-history.json");ap.add_argument("--scheduled",action="store_true");a=ap.parse_args();now=datetime.now(TZ)
 if a.scheduled and now.hour not in (6,18):log("Schedule gate skip: local hour %02d"%now.hour);return 0
 outdir=Path(a.output_dir);outdir.mkdir(parents=True,exist_ok=True);old=previous(Path(a.previous)) if a.previous else [];results={};jobs={"snrt":lambda:scrape_snrt(a.days),"arryadia":lambda:scrape_arryadia(min(a.days,3),old,a.arryadia_history),"2m":lambda:scrape_2m(a.days),"chada":lambda:scrape_chada(a.days),"medi1":lambda:scrape_medi1(a.days)}
 with ThreadPoolExecutor(max_workers=4) as ex:
  fs={ex.submit(fn):name for name,fn in jobs.items()}
  for f in as_completed(fs):
   try:results[fs[f]]=f.result()
   except Exception as e:log("%s crashed: %s"%(fs[f],e));results[fs[f]]=[]
 final,status=select_groups(results,old)
 if not final:
  log("No genuine future programmes; retain the published feed");return 2
 seen=set();merged=[]
 for e in sorted(final,key=lambda e:(e.channel,e.start,e.title.casefold())):
  k=(e.channel,xdt(e.start),e.title.casefold())
  if k not in seen:seen.add(k);merged.append(e)
 infer(merged);xml=outdir/"morocco.xml";counts=to_xml(merged,xml);raw=xml.read_bytes();gz=outdir/"morocco.xml.gz"
 with gzip.GzipFile(filename="morocco.xml",mode="wb",fileobj=gz.open("wb"),compresslevel=9,mtime=0) as f:f.write(raw)
 (outdir/"morocco.txt").write_text("\n".join("%s | %s | %d programmes"%(cid,CHANNELS.get(cid,cid),counts.get(cid,0)) for cid in sorted(CHANNELS))+"\n",encoding="utf-8")
 manifest={"version":now.strftime("%Y%m%d-%H%M%S"),"generated":now.isoformat(),"timezone":"Africa/Casablanca","url":"morocco.xml.gz","sha256":hashlib.sha256(gz.read_bytes()).hexdigest(),"size":gz.stat().st_size,"channels":len(CHANNELS),"programmes":sum(counts.values()),"channel_counts":counts,"sources":status}
 (outdir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8");log("PASS: %d channels / %d programmes / %.1f KiB"%(manifest["channels"],manifest["programmes"],manifest["size"]/1024));return 0
if __name__=="__main__":raise SystemExit(main())
