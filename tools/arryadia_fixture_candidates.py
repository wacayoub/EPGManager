#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Arryadia sports-fixture source diagnostic. NEVER emits XMLTV.

The only trusted information here is the *observed* channel assignment,
programme title, site-clock start time and date. WOSTI's local time zone is
unverified, so such a row cannot become an EPG programme without independent
time-zone and single-channel verification. Duplicate clocks on the same
linear TV service are actively flagged and quarantined.

Historical EPG/filler programmes have no standing as real future events.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Africa/Casablanca")
SOURCES = {
    "Arryadia_HD1": "https://www.livefootballtv.info/channel/arryadia-hd1",
    "Arryadia_HD2": "https://www.livefootballtv.info/channel/arryadia-hd2",
    "Arryadia_HD3": "https://www.livefootballtv.info/channel/moroccan-sports",
}
HEADING_DATE = re.compile(r"(\d{2})/(\d{2})/(20\d{2})")
CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def parse_fixture_candidates(html, cid, today, source_url):
    if cid not in SOURCES:
        raise ValueError("Unknown Arryadia HD variant: %r" % cid)
    soup = BeautifulSoup(html, "lxml")
    h = soup.select_one("h1")
    if not h or cid.upper().replace("_", " ") not in h.get_text(" ", strip=True).upper():
        # The alias URL currently presents an HD3 heading; avoid cross-channel.
        return []
    results = []
    day = None
    for tr in soup.select("tr"):
        heading = tr.get_text(" ", strip=True) if "cabeceraTabla" in tr.get("class", []) else ""
        match = HEADING_DATE.search(heading)
        if match:
            try:
                day = dt.date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            except ValueError:
                day = None
            continue
        if day is None or day < today or day > today + dt.timedelta(days=3):
            continue
        raw = tr.select_one("td.hora")
        home = tr.select_one("td.local")
        away = tr.select_one("td.visitante")
        competition = tr.select_one("td.detalles label[title]")
        if not all((raw, home, away, competition)):
            continue
        site_clock = raw.get_text(" ",strip=True)
        if not CLOCK.fullmatch(site_clock):
            continue
        a = (home.select_one("[title]") or home).get("title") or home.get_text(" ", strip=True)
        b = (away.select_one("[title]") or away).get("title") or away.get_text(" ", strip=True)
        league = competition.get("title") or competition.get_text(" ",strip=True)
        if not a or not b or a.casefold()==b.casefold():
            continue
        results.append({
            "channel": cid,
            "broadcast_date": day.isoformat(),
            "site_clock": site_clock,
            "site_timezone": "UNVERIFIED",
            "home": a.strip(),
            "away": b.strip(),
            "competition": league.strip(),
            "source": source_url,
            "status": "NOT_EPG__NEEDS_TIME_AND_CHANNEL_VERIFICATION",
            "may_publish": False,
        })
    return results


def flag_collisions(items):
    counts=collections.Counter((e["channel"],e["broadcast_date"],e["site_clock"]) for e in items)
    for e in items:
        overlap = counts[(e["channel"],e["broadcast_date"],e["site_clock"])] > 1
        if overlap:
            e["status"]="CONFLICT__TWO_FIXTURES_SAME_CHANNEL_SAME_CLOCK"
            e["may_publish"]=False
    return items


def collect(today=None, session=None):
    today=today or dt.datetime.now(TZ).date()
    session=session or requests.Session()
    collected=[]
    failures={}
    for cid,url in SOURCES.items():
        try:
            r=session.get(url,timeout=(7,18),headers={"User-Agent":"Mozilla/5.0","Accept-Language":"fr,ar;q=.8"})
            r.raise_for_status()
            found=parse_fixture_candidates(r.text,cid,today,url)
            collected.extend(found)
        except Exception as exc:
            failures[cid]=type(exc).__name__+": "+str(exc)[:130]
    return flag_collisions(collected),failures


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="output/arryadia_fixture_candidates.json")
    args=ap.parse_args()
    today=dt.datetime.now(TZ).date()
    rows,errors=collect(today)
    collisions=sum(x["status"].startswith("CONFLICT") for x in rows)
    out={
        "audit": "Arryadia date-specific sports matches advisory, not XMLTV",
        "observed_at": dt.datetime.now(TZ).isoformat(),
        "source_clock_timezone": "UNVERIFIED",
        "never_publish_directly": True,
        "channel_ids_probed": list(SOURCES),
        "candidate_count": len(rows),
        "collision_count": collisions,
        "source_errors": errors,
        "candidates": rows,
    }
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("ARRYADIA_ADVISORY",json.dumps({
        "candidate_count":len(rows),"collision_count":collisions,
        "source_errors":errors,"publication":"BLOCKED_UNVERIFIED_TIMEZONE",
    },ensure_ascii=False),flush=True)
    for e in rows[:12]:
        print("ARRYADIA_MATCH_CANDIDATE",json.dumps(e,ensure_ascii=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
