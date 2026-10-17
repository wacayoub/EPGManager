"""Read-only survey of alternative Arryadia EPG streams.

Demand actual current-dated programmes and non-placeholder titles. Do not
publish, merge, shift dates or imply HD/TNT/HD1-3 simulcasts.
"""
import collections, datetime, gzip, io, json, re, requests, xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

NOW=datetime.datetime.now(ZoneInfo("Africa/Casablanca"))
URLS=[
("epgshare-MA1","https://epgshare01.online/epgshare01/epg_ripper_MA1.xml.gz"),
("epgshare-MA2","https://epgshare01.online/epgshare01/epg_ripper_MA2.xml.gz"),
("epgshare-AF1","https://epgshare01.online/epgshare01/epg_ripper_AF1.xml.gz"),
("iptvorg-ma-osn","https://iptv-org.github.io/epg/guides/ma/osn.com.epg.xml"),
("iptvorg-snrt-unpublished","https://iptv-org.github.io/epg/guides/ma/snrt.ma.epg.xml"),
("arab-global","https://iptv-org.github.io/epg/guides/eg-ar/elcinema.com.epg.xml"),
("epgpw-Arryadia","https://epg.pw/api/epg.xml?channel_id=55898"),
("sport-main","https://www.livefootballtv.info/channel/arryadia"),
("sport-hd1","https://www.livefootballtv.info/channel/arryadia-hd1"),
("sport-hd2","https://www.livefootballtv.info/channel/arryadia-hd2"),
("sport-hd3","https://www.livefootballtv.info/channel/moroccan-sports"),
]
def placeholder(title):
    title=" ".join(title.casefold().split())
    return (
        title.startswith("programmes arryadia") or
        title.startswith("la chaîne du sport marocain") or
        title.startswith("la chaine du sport marocain") or
        title.startswith("برنامج قناة الرياضية") or
        title in ("programme","programmes","arryadia")
    )
def read(url):
    r=requests.get(url,timeout=(8,20),headers={"User-Agent":"Mozilla/5.0","Accept":"*/*"},stream=True)
    status=r.status_code
    if not r.ok:
        return dict(status=status,error="HTTP %d"%status)
    # avoid decompressing whole huge Europe/World XMLTV feeds on GitHub Actions.
    if int(r.headers.get("content-length","0") or 0)>20000000:
        return dict(status=status,error="over-20MB; skipped safely")
    cap=20_000_000
    data=bytearray()
    for part in r.iter_content(65536):
        data.extend(part)
        if len(data)>cap:
            return dict(status=status,error="download exceeded 20MB cap")
    body=bytes(data)
    if body[:2]==bytes([31,139]):
        body=gzip.decompress(body)
    return dict(status=status,body=body,bytes=len(body),type=r.headers.get("content-type",""))
def parse_dt(text):
    try:
        return datetime.datetime.strptime(text.strip()[:20],"%Y%m%d%H%M%S %z").astimezone(ZoneInfo("Africa/Casablanca"))
    except Exception:
        return None
def survey(name,url):
    try:
        r=read(url)
        if "body" not in r:return {"name":name,"url":url,**r}
        if name.startswith("sport-"):
            soup=BeautifulSoup(r["body"],"lxml")
            header=soup.select_one("h1")
            text=soup.get_text(" ",strip=True)
            return dict(name=name,status=r["status"],bytes=r["bytes"],header=header.get_text(" ",strip=True) if header else None,
                upcoming_none=bool(re.search(r"no football match being televised",text,re.I)),
                recent_dates=re.findall(r"(?:\d{2}/\d{2}/2026)",text)[:12],
                before_stats=text[:420])
        root=ET.fromstring(r["body"])
        channels={}
        programmes=collections.defaultdict(list)
        for c in root.findall("channel"):
            names=[n.text or "" for n in c.findall("display-name")]
            cid=c.get("id")
            if "arryad" in " ".join(names+[cid or ""]).lower() or "الرياضية" in " ".join(names):
                channels[cid]=names
        # Sometimes channel IDs are obvious but there is no display-name.
        for p in root.findall("programme"):
            channel=p.get("channel")
            if channel not in channels and not ("arryad" in (channel or "").lower() or "الرياضية" in (channel or "")):
                continue
            title=p.findtext("title") or ""
            start=parse_dt(p.get("start") or "")
            if start and NOW-datetime.timedelta(hours=12)<start<NOW+datetime.timedelta(days=7):
                programmes[channel].append((start.isoformat(),title))
        summaries={}
        for cid,rows in programmes.items():
            real=[x for x in rows if not placeholder(x[1])]
            summaries[cid]=dict(total=len(rows),genuine=len(real),examples=real[:7],filler_count=len(rows)-len(real))
        return dict(name=name,status=r["status"],bytes=r["bytes"],channels=channels,within_horizon=summaries)
    except Exception as e:
        return dict(name=name,error=type(e).__name__+": "+str(e)[:130])
for name,url in URLS:
    print("SOURCE_CHECK",json.dumps(survey(name,url),ensure_ascii=False),flush=True)
