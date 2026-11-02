#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-aware direct-feed coverage audit.

The audit distinguishes a broken feed (FAIL) from a healthy feed whose upstream
provider publishes less than 48 continuous hours (WARN). It never invents EPG.
Publication remains controlled independently by publish_lkg.py.
"""
from __future__ import annotations
import argparse,gzip,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
try:
    from bein_occasional_policy import is_occasional_bein
except ModuleNotFoundError:
    from tools.bein_occasional_policy import is_occasional_bein

DT_RE=re.compile(r"^(\d{14}|\d{12})(?:\s*([+-]\d{4}|Z))?$")

PROFILES={
    "morocco.xml.gz":{"target_hours":48.0,"min_hours":8.0,"gap_warn":90},
    "bein.xml.gz":{"target_hours":48.0,"min_hours":4.0,"gap_warn":90},
    "elcinema.xml.gz":{"target_hours":48.0,"min_hours":36.0,"gap_warn":90},
    "osn.xml.gz":{"target_hours":48.0,"min_hours":36.0,"gap_warn":90},
    "shahid.xml.gz":{"target_hours":48.0,"min_hours":8.0,"gap_warn":180},
    "rotana.xml.gz":{"target_hours":48.0,"min_hours":8.0,"gap_warn":180},
    "tabie.xml.gz":{"target_hours":48.0,"min_hours":24.0,"gap_warn":180,"allow_zero_epg":True},
    "alkass.xml.gz":{"target_hours":48.0,"min_hours":24.0,"gap_warn":240},
    "tunisiatv.xml.gz":{"target_hours":48.0,"min_hours":24.0,"gap_warn":240},
    # Request/audit 48h for Sport24 too; short event-driven coverage is WARN, never fabricated.
    "sport24.xml.gz":{"target_hours":48.0,"min_hours":4.0,"gap_warn":360},
}

def dt(v):
    m=DT_RE.match((v or "").strip())
    if not m:return None
    s=m.group(1); fmt="%Y%m%d%H%M%S" if len(s)==14 else "%Y%m%d%H%M"
    x=datetime.strptime(s,fmt)
    off=m.group(2)
    if not off or off=="Z": return x.replace(tzinfo=timezone.utc)
    sign=1 if off[0]=="+" else -1
    mins=sign*(int(off[1:3])*60+int(off[3:5]))
    return x.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)

def root(path):
    raw=Path(path).read_bytes()
    if str(path).endswith(".gz") or raw[:2]==b"\x1f\x8b": raw=gzip.decompress(raw)
    return ET.fromstring(raw)

def profile_for(path,hours,max_gap_minutes):
    name=Path(path).name
    p=dict(PROFILES.get(name,{}))
    p.setdefault("target_hours",float(hours))
    p.setdefault("min_hours",min(8.0,float(hours)))
    p.setdefault("gap_warn",int(max_gap_minutes))
    return p

def wataniya2_verified_night_offair(merged, now, limit):
    """Explain, but do not fill, Tunisia Watania 2's official 00-06 blackout.

    Tunisia TV's published Watania 2 day schedule shows actual events from
    about 06:00 through 23:59 Africa/Tunis. Only when ALL >limit holes match
    that midnight boundary may the 6-hour interval be informational rather
    than an unexplained programme-loss warning. Any unrelated hole is WARN.
    """
    if not merged:
        return False
    zone=ZoneInfo("Africa/Tunis")
    expected=[]
    if merged[0][0] - now > limit:
        local_now=now.astimezone(zone)
        local_first=merged[0][0].astimezone(zone)
        expected.append(
            0 <= local_now.hour < 6 and local_first.hour == 6 and
            local_first.minute <= 10 and local_first.date()==local_now.date()
        )
    for (_,end),(start,_) in zip(merged,merged[1:]):
        if start-end <= limit:
            continue
        e=end.astimezone(zone)
        a=start.astimezone(zone)
        span=start-end
        expected.append(
            e.hour==23 and e.minute>=50 and
            a.hour==6 and a.minute<=10 and
            a.date()==(e+timedelta(days=1)).date() and
            timedelta(hours=5,minutes=50)<=span<=timedelta(hours=6,minutes=30)
        )
    return bool(expected) and all(expected)


def audit(path,hours,max_gap_minutes):
    r=root(path); now=datetime.now(timezone.utc)
    prof=profile_for(path,hours,max_gap_minutes)
    target=now+timedelta(hours=prof["target_hours"])
    channels={}
    for c in r.findall("channel"):
        cid=(c.get("id") or "").strip()
        if cid: channels[cid]=c
    by={cid:[] for cid in channels}; invalid=0; orphan=0
    for p in r.findall("programme"):
        cid=(p.get("channel") or "").strip()
        if cid not in by:
            orphan+=1; continue
        a=dt(p.get("start")); b=dt(p.get("stop"))
        if not a or not b or b<=a:
            invalid+=1; continue
        if b>now-timedelta(hours=2) and a<target+timedelta(hours=12):
            by[cid].append((a,b))

    rows=[]; failures=[]; warnings=[]; informational=[]
    is_bein_feed = Path(path).name == 'bein.xml.gz'
    gap_limit=timedelta(minutes=prof["gap_warn"])
    for cid in sorted(channels,key=str.casefold):
        occasional = is_bein_feed and is_occasional_bein(
            cid, [n.text or '' for n in channels[cid].findall('display-name')]
        )
        iv=sorted(by[cid])
        if not iv:
            if occasional:
                row={"id":cid,"status":"INFO","reason":"OCCASIONAL_NO_EVENT",
                     "future_hours":0.0,"max_gap_minutes":None,"programmes":0,
                     "occasional_event_channel":True}
                rows.append(row); informational.append(row); continue
            if prof.get("allow_zero_epg"):
                row={"id":cid,"status":"WARN","reason":"ZERO_EPG","future_hours":0.0,"max_gap_minutes":None}
                rows.append(row); warnings.append(row); continue
            row={"id":cid,"status":"FAIL","reason":"NO_FUTURE_EPG","future_hours":0.0,"max_gap_minutes":None}
            rows.append(row); failures.append(row); continue

        merged=[]
        for a,b in iv:
            if not merged or a>merged[-1][1]: merged.append([a,b])
            elif b>merged[-1][1]: merged[-1][1]=b

        cursor=now
        max_gap=max(timedelta(0),merged[0][0]-now)
        for a,b in merged:
            if b<=cursor: continue
            if a>cursor: max_gap=max(max_gap,a-cursor)
            cursor=max(cursor,b)

        future_hours=max(0.0,(cursor-now).total_seconds()/3600.0)
        hard=[]; soft=[]
        if future_hours < prof["min_hours"]:
            hard.append("TOO_SHORT")
        elif cursor < target:
            soft.append("SHORT_HORIZON")
        if max_gap > gap_limit:
            soft.append("INTERNAL_GAP")

        confirmed_watania2_night_offair = (
            Path(path).name=="tunisiatv.xml.gz"
            and cid=="ElWatania2.tn@SD"
            and not hard and soft==["INTERNAL_GAP"]
            and wataniya2_verified_night_offair(merged,now,gap_limit)
        )
        status=("INFO" if occasional or confirmed_watania2_night_offair
                else ("FAIL" if hard else ("WARN" if soft else "PASS")))
        reason=("OCCASIONAL_EVENT_SCHEDULE" if occasional
                else "OFF_AIR_OVERNIGHT_0000_0600_TUNIS" if confirmed_watania2_night_offair
                else "+".join(hard+soft))
        row={
            "id":cid,"status":status,"reason":reason,
            "future_hours":round(future_hours,2),
            "latest_stop_utc":cursor.isoformat(),
            "max_gap_minutes":round(max_gap.total_seconds()/60.0,1),
            "programmes":len(iv),
            "occasional_event_channel":bool(occasional),
            "verified_regular_off_air":bool(confirmed_watania2_night_offair),
        }
        rows.append(row)
        if status=="INFO": informational.append(row)
        if status=="FAIL": failures.append(row)
        elif status=="WARN": warnings.append(row)

    if invalid or orphan or not channels or failures:
        status="FAIL"
    elif warnings:
        status="WARN"
    else:
        status="PASS"

    return {
        "feed":Path(path).name,
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "target_hours":prof["target_hours"],
        "minimum_acceptable_hours":prof["min_hours"],
        "max_gap_minutes_warning":prof["gap_warn"],
        "channels":len(channels),
        "invalid_times":invalid,
        "orphan_programmes":orphan,
        "failed_channels":len(failures),
        "warning_channels":len(warnings),
        "informational_channels":len(informational),
        "occasional_channel_ids":sorted(r['id'] for r in informational),
        "status":status,
        "rows":rows,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("feeds",nargs="+")
    ap.add_argument("--hours",type=float,default=48.0)
    ap.add_argument("--max-gap-minutes",type=int,default=90)
    ap.add_argument("--report",required=True)
    a=ap.parse_args()
    reports=[audit(p,a.hours,a.max_gap_minutes) for p in a.feeds]
    out={"generated_utc":datetime.now(timezone.utc).isoformat(),"mode":"source-aware","feeds":reports}
    Path(a.report).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    failed=[]
    for r in reports:
        print("%s %s channels=%d failed=%d warn=%d invalid=%d orphan=%d"%(
            r["status"],r["feed"],r["channels"],r["failed_channels"],r["warning_channels"],
            r["invalid_times"],r["orphan_programmes"]))
        for row in r["rows"]:
            if row["status"]!="PASS":
                print("  %-4s %-45s horizon=%6.2fh gap=%s reason=%s"%(
                    row["status"],row["id"],row["future_hours"],row["max_gap_minutes"],row["reason"]))
        if r["status"]=="FAIL": failed.append(r["feed"])

    if failed:
        print("DIRECT_COVERAGE_AUDIT FAIL:",", ".join(failed))
        return 2
    print("DIRECT_COVERAGE_AUDIT OK: no broken direct feed")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
