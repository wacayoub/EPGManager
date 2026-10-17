"""Read-only: inspect HD3 sport fixture page for unambiguous source datetimes.

No XMLTV output. Prints layout, time zone signals and same-start conflicts.
"""
import re,requests
from bs4 import BeautifulSoup
url="https://www.livefootballtv.info/channel/moroccan-sports"
r=requests.get(url,timeout=(8,20),headers={"User-Agent":"Mozilla/5.0","Accept-Language":"fr,ar;q=.8"})
r.raise_for_status()
s=BeautifulSoup(r.text,"lxml")
print("HD3_STATUS",r.status_code,"bytes",len(r.content))
for row in s.select("tr")[:16]:
    content=" ".join(row.get_text(" ",strip=True).split())
    if content:
        print("HD3_TABLE_ROW",str(row.get("class")),"HTML",str(row)[:1050].replace("\n"," "),"TEXT",content[:260])
for el in s.find_all(string=re.compile(r"(17/10/2026|18/10/2026)"))[:8]:
    p=el.parent
    print("HD3_DATE",str(el).strip()[:80],"HTML",str(p.parent)[:1100].replace("\n"," "))
for el in s.find_all(string=re.compile(r"^(21:00|23:00)$"))[:8]:
    p=el.parent
    print("HD3_TIME",str(el).strip()[:80],"HTML",str(p.parent.parent)[:1200].replace("\n"," "))
for x in s.select("script"):
    if any(k in str(x).lower() for k in ("timezone","utc","gmt","offset")):
        print("HD3_TZ_HINT",str(x)[:500].replace("\n"," "))
