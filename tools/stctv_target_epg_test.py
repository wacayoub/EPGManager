#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-rate public STC TV EPG test for STARZPLAY/MBC channels.

Uses only public/guest metadata endpoints loaded by web.stctv.com. No playback,
DRM, account, login, or entitlement bypass. One channel-list request, then one
schedule request per matched channel, sequentially with a delay.
"""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
import requests

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
# Public web-app identifiers embedded in STC TV's guest frontend.
APP_KEY="webB2CGDMPrdExy0sVDlZMzNDdUyZ"
SCHEDULE_KEY="GDMPrdExy0sVDlZMzNDdUyZ"
CHANNELS_API=f"https://jawwy2-prod-cdn.intigral-ott.net/bolt/v2/{APP_KEY}/channels"
SCHEDULE_BASE="https://prod-cdn-content-api.intigral-ott.net/content-api-3.0.1/channels/schedules"
TARGET=re.compile(r"(starz\s*play|mbc|shahid)",re.I)
AR=re.compile(r"[\u0600-\u06ff]")

def get_json(session,url,params=None,timeout=25):
    r=session.get(url,params=params,timeout=timeout)
    r.raise_for_status()
    return r.json(),r.url

def channels(data):
    if isinstance(data,dict):
        d=data.get("data")
        if isinstance(d,dict) and isinstance(d.get("channels"),list):
            return d["channels"]
    return []

def schedule_rows(data):
    if isinstance(data,list):
        out=[]
        for block in data:
            if isinstance(block,dict) and isinstance(block.get("listings"),list):
                out.extend(block["listings"])
        return out
    return []

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--hours",type=int,default=2)
    ap.add_argument("--delay",type=float,default=1.5)
    ap.add_argument("--max-channels",type=int,default=25)
    ap.add_argument("--report",required=True)
    args=ap.parse_args()

    s=requests.Session()
    s.headers.update({
        "User-Agent":UA,
        "Accept":"application/json,text/plain,*/*",
        "Accept-Language":"ar-SA,ar;q=0.9,en;q=0.7",
        "Origin":"https://web.stctv.com",
        "Referer":"https://web.stctv.com/",
    })
    result={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "hours":args.hours,"matched_channels":[],"summary":{},
        "policy":"public guest metadata only; no playback/auth/DRM bypass",
    }
    try:
        data,_=get_json(s,CHANNELS_API,{
            "country":"SA","deviceType":"web","filter":"","device":"PC","appKey":APP_KEY,
        })
        allch=channels(data)
    except Exception as e:
        result["error"]="channels: "+str(e)
        Path(args.report).parent.mkdir(parents=True,exist_ok=True)
        Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False))
        return 10

    matched=[]
    for ch in allch:
        en=str(ch.get("channelTitle") or "")
        ar=str(ch.get("channelTitleAr") or "")
        if TARGET.search(en) or TARGET.search(ar):
            matched.append(ch)
    matched=matched[:args.max_channels]

    now=datetime.now(timezone.utc)
    end=now+timedelta(hours=max(1,args.hours))
    date=now.strftime("%Y-%m-%d")
    total_events=0
    active_ids=0
    for idx,ch in enumerate(matched):
        cid=str(ch.get("channelID") or "")
        row={
            "channelID":cid,
            "title":str(ch.get("channelTitle") or ""),
            "title_ar":str(ch.get("channelTitleAr") or ""),
            "events_2h":[],
        }
        if cid:
            try:
                url=f"{SCHEDULE_BASE}/{date}/3"
                data2,_=get_json(s,url,{
                    "apikey":SCHEDULE_KEY,
                    "productKey":"stc-tv",
                    "byId":cid,
                })
                for li in schedule_rows(data2):
                    st=int(li.get("startTime") or 0)
                    et=int(li.get("endTime") or 0)
                    if not st or not et:
                        continue
                    sdt=datetime.fromtimestamp(st/1000,tz=timezone.utc)
                    edt=datetime.fromtimestamp(et/1000,tz=timezone.utc)
                    if sdt < end and edt > now:
                        loc_title=li.get("localizedTitle") or {}
                        loc_desc=li.get("localizedDescription") or {}
                        ev={
                            "start_utc":sdt.isoformat(),
                            "end_utc":edt.isoformat(),
                            "title_en":str(li.get("title") or ""),
                            "title_ar":str(loc_title.get("ar") or "") if isinstance(loc_title,dict) else "",
                            "desc_en":str(li.get("description") or "")[:500],
                            "desc_ar":str(loc_desc.get("ar") or "")[:500] if isinstance(loc_desc,dict) else "",
                        }
                        row["events_2h"].append(ev)
                if row["events_2h"]:
                    active_ids+=1
                    total_events+=len(row["events_2h"])
            except Exception as e:
                row["error"]=str(e)
        result["matched_channels"].append(row)
        if idx+1 < len(matched):
            time.sleep(max(0,args.delay))

    result["summary"]={
        "all_channels":len(allch),
        "matched_target_channels":len(matched),
        "matched_with_epg_2h":active_ids,
        "events_2h":total_events,
        "starzplay_channels":sum(1 for x in matched if re.search(r"starz\s*play",str(x.get("channelTitle") or ""),re.I)),
        "mbc_shahid_channels":sum(1 for x in matched if re.search(r"(mbc|shahid)",str(x.get("channelTitle") or ""),re.I)),
    }
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result["summary"],ensure_ascii=False))
    for x in result["matched_channels"]:
        print("STC_TARGET",json.dumps({
            "title":x["title"],"title_ar":x["title_ar"],
            "events_2h":len(x["events_2h"]),
            "sample":x["events_2h"][0] if x["events_2h"] else None,
            "error":x.get("error"),
        },ensure_ascii=False))
    return 0 if matched else 11

if __name__=="__main__":
    raise SystemExit(main())
