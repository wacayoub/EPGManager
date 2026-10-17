"""Read-only diagnostics for Arryadia upstream freshness (no publishing)."""
import json,re,requests
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
for label,url in [
 ("snrt-ar","https://www.snrt.ma/ar/node/4070"),
 ("snrt-home","https://www.snrt.ma/ar/arryadia"),
 ("soccer-main","https://www.livesoccertv.com/fr/channels/arryadia/"),
 ("soccer-tnt","https://www.livesoccertv.com/fr/channels/arryadia-tnt/"),
 ("hd3","https://www.livefootballtv.info/channel/moroccan-sports")
]:
 try:
  r=requests.get(url,timeout=(7,17),headers={"User-Agent":"Mozilla/5.0","Accept-Language":"fr,ar;q=.9"})
  print("SOURCE",label,"status",r.status_code,"bytes",len(r.content),"url",r.url,flush=True)
  soup=BeautifulSoup(r.text,"lxml")
  if label in ("snrt-ar","snrt-home"):
   elems=soup.select(".grille-line")
   print("SNRT_ROWS",len(elems),"date_classes",[(e.get("class"),e.select_one(".grille-time").get_text(" ",strip=True) if e.select_one(".grille-time") else None) for e in elems[:10]],flush=True)
   print("SNRT_DATES",sorted(set(v for e in elems for v in e.get("class",[]) if re.fullmatch(r"\d{8}",v)))[:15],flush=True)
  if label.startswith("soccer"):
   for needle in ["CODM", "Difa", "Widad","Maghreb","ASEC"]:
    hit=soup.find(string=re.compile(needle,re.I))
    if hit:
     print("SOCCER_HIT",needle,"parent",str(hit.parent.parent.parent)[:1500].replace("\n"," "),flush=True)
   for selector in ["tr","script[type='application/ld+json']","time","[data-datetime]"]:
    nodes=soup.select(selector)
    print("SOCCER_ELEMENTS",selector,len(nodes),[str(z)[:400] for z in nodes[:3]],flush=True)
  if label=="hd3":
   for needle in ["CODM","Meknes","Maghreb Fez"]:
    hit=soup.find(string=re.compile(needle,re.I))
    if hit:print("HD3_HIT",needle,str(hit.parent.parent.parent)[:1400].replace("\n"," "),flush=True)
 except Exception as exc:
  print("UPSTREAM_ERROR",label,type(exc).__name__,str(exc)[:180],flush=True)
