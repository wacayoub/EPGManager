"""Six-month Arryadia archive coverage census. Read-only; no EPG publication.
Only count schedule rows whose DATE is truly within the 2026-04-17..10-17 window.
Never reinterpret 3h generic fillers as real broadcast entries.
"""
import collections,concurrent.futures,datetime,json,re,statistics,requests,time
from bs4 import BeautifulSoup

START=datetime.date(2026,4,17); END=datetime.date(2026,10,17)
OFFSETS=[0,5,10,20,35,50,70,90,110,130,150,170,190,220,260,300]
MONTH_DAYS=["2026-04-21","2026-05-21","2026-06-21","2026-07-21","2026-08-21","2026-09-21"]
session=requests.Session()
session.headers.update({"User-Agent":"Mozilla/5.0 (compatible; EPGManagerArchiveAudit/1.0; read-only)","Accept-Language":"fr-MA,fr;q=.9,ar;q=.8"})

def fetch_snrt(n):
    u=f"https://www.snrt.ma/fr/node/4070?limit=5&start={n}"
    try:
        r=requests.get(u,headers=session.headers,timeout=(6,17))
        r.raise_for_status()
        s=BeautifulSoup(r.text,"lxml")
        rows=[]
        for item in s.select("div.grille-line"):
            v=next((x for x in item.get("class",[]) if re.fullmatch(r"20\d{6}",x)),None)
            if not v:continue
            try:date=datetime.datetime.strptime(v,"%Y%m%d").date()
            except ValueError: continue
            t=item.select_one(".grille-time")
            title=item.select_one("h2.program-title-sm")
            content=item.select_one(".grille-content")
            if not t or not title:continue
            clock=t.get_text(" ",strip=True).replace("H",":")
            text=title.get_text(" ",strip=True)
            desc=content.get_text(" ",strip=True)[:200] if content else ""
            rows.append(dict(date=str(date),time=clock,title=text,description=desc,week_day=date.strftime("%A")))
        return dict(offset=n,http=r.status_code,rows=rows,days=sorted({x["date"] for x in rows}),bytes=len(r.content))
    except Exception as e:return dict(offset=n,error=str(e)[:140],rows=[],days=[])

def fetch_external(day):
    u=f"https://rabat.communesmaroc.com/services-tv-channels?channel=arryadia&dt={day}"
    try:
        r=requests.get(u,headers=session.headers,timeout=(6,17));r.raise_for_status()
        soup=BeautifulSoup(r.text,"lxml")
        text=soup.get_text(" ",strip=True)
        # Don't count if site's header doesn't correspond to the date requested.
        ds=[int(x) for x in re.findall(r"(?i)\b(?:19|20)\d{2}\b",text[:5000])[:6]]
        return dict(requested=day,status=r.status_code,date_visible=day in text[:2000],date_years=ds,
                    arr_mention="Arryadia" in text,preview=text[text.find("Programme TV Arryadia"):text.find("Programme TV Arryadia")+220])
    except Exception as e:return dict(requested=day,error=str(e)[:120])
def is_filler(title):
    v=title.strip().casefold()
    return v.startswith(("programmes arr","la chaîne du sport","برامج قناة الرياضية")) or v in {"programme","programmes"}
def family(title):
    n=title.lower().strip()
    if "arryadia chabab" in n:
        if "reportage" in n:return "Arryadia Chabab — Reportage"
        if "foot" in n:return "Arryadia Chabab — Football / Football féminin"
        if "basket" in n:return "Arryadia Chabab — Basketball"
        return "Arryadia Chabab — autre"
    if "flash info" in n:return "Flash info"
    if "journal" in n:return "Journal sportif / Journal télévisé"
    if "studio" in n or "plateau direct" in n:return "Studio / Plateau d'analyse"
    if "documentaire" in n:return "Documentaire"
    if "basket" in n:return "Basketball"
    if "football" in n or "botola" in n:return "Football / match"
    if "tennis" in n:return "Tennis"
    if "futsal" in n:return "Futsal"
    if "athlétisme" in n:return "Athlétisme"
    return title[:65]

print("WINDOW",START,END,"official SNRT queries",len(OFFSETS),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    pages=sorted(ex.map(fetch_snrt,OFFSETS),key=lambda x:x["offset"])
    outsiders=list(ex.map(fetch_external,MONTH_DAYS))
for p in pages:
    print("SNRT_PAGE",p["offset"],"rows",len(p["rows"]),"days",p["days"],"error",p.get("error",""),flush=True)
for p in outsiders:print("OTHER_ARCHIVE",json.dumps(p,ensure_ascii=False),flush=True)
seen={}
for p in pages:
    for e in p["rows"]:
        day=datetime.date.fromisoformat(e["date"])
        if START<=day<=END and not is_filler(e["title"]):
            seen[(e["date"],e["time"],e["title"])]=e
rows=sorted(seen.values(),key=lambda x:(x["date"],x["time"]))
per_month=collections.Counter(x["date"][:7] for x in rows)
per_day=collections.Counter(x["date"] for x in rows)
stat=collections.defaultdict(list)
for x in rows:stat[family(x["title"])].append(x)
summaries=[]
for k,rr in sorted(stat.items(),key=lambda t:-len(t[1])):
    dates=sorted(set(x["date"] for x in rr))
    times=collections.Counter(x["time"][:2] for x in rr if re.fullmatch(r"\d{1,2}:\d{2}",x["time"]))
    day_names=collections.Counter(x["week_day"] for x in rr)
    summaries.append(dict(family=k,events=len(rr),observed_days=len(dates),observed_months=sorted(set(d[:7] for d in dates)),
                          most_common_hours=times.most_common(5),weekdays=day_names.most_common(7),
                          examples=[dict(date=x["date"],time=x["time"],title=x["title"],description=x["description"][:90]) for x in rr[:4]]))
print("SNRT_SUMMARY",json.dumps(dict(from_date=str(START),to_date=str(END),unique_real_events=len(rows),
                                    observed_days=len(per_day),observed_months=dict(per_month),
                                    by_family=summaries[:30]),ensure_ascii=False),flush=True)
print("COVERAGE_LIMIT",json.dumps(dict(six_months_requested=6,months_with_real_schedules=len(per_month),days_with_real_schedules=len(per_day),all_archives_complete=False,
                                         note="No forward EPG events may be emitted solely from these historical statistics")),flush=True)
