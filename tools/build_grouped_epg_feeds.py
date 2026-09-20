#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build current XMLTV feeds grouped for Vu+ / EPGManager import.

The grouped feeds are an import/presentation layer. Current programme data is
read from EPG-Scrapers production feeds. The MENA source master is used only to
preserve stable receiver IDs, winner-source choices and group assignment.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

GROUP_ORDER = [
    "OSN","ROTANA","BEIN_SPORTS","BEIN_MEDIA","ART","MBC","ALJAZEERA",
    "EMIRATES","EGYPT","MOROCCO","SAUDI","QATAR","KUWAIT","LEBANON",
    "JORDAN","IRAQ","BAHRAIN","OMAN","TUNISIA","ALGERIA","LIBYA",
    "SYRIA","YEMEN","PALESTINE","SUDAN","MAURITANIA","OTHER",
]
COUNTRY_GROUPS = {
    "ae":"EMIRATES","eg":"EGYPT","ma":"MOROCCO","sa":"SAUDI","qa":"QATAR",
    "kw":"KUWAIT","lb":"LEBANON","jo":"JORDAN","iq":"IRAQ","bh":"BAHRAIN",
    "om":"OMAN","tn":"TUNISIA","dz":"ALGERIA","ly":"LIBYA","sy":"SYRIA",
    "ye":"YEMEN","ps":"PALESTINE","sd":"SUDAN","mr":"MAURITANIA",
}

# A feed may legitimately represent more than one upstream provider label in
# the master. "others" is already the deduplicated/fallback production pack.
FEED_SOURCE_ALIASES = {
    "bein": {"bein.com", "beinsports.com"},
    "osn": {"osn.com"},
    "elcinema": {"elcinema.com"},
    "morocco": set(),
    "sport24": set(),
    "others": set(),
}

def read_xml(path: Path):
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def parse_xmltv_time(value: str):
    m = re.match(r"^(\d{14})(?:\s+([+-]\d{4}))?", (value or "").strip())
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    off = m.group(2)
    if off:
        sign = 1 if off[0] == "+" else -1
        mins = sign * (int(off[1:3]) * 60 + int(off[3:5]))
        from datetime import timedelta
        dt = dt.replace(tzinfo=timezone(timedelta(minutes=mins)))
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)

def fallback_group(cid: str) -> str:
    probe=(cid or "").casefold()
    if "rotana" in probe:
        return "ROTANA"
    if probe.startswith("bein") or re.search(r"\bbein\b", probe):
        return "BEIN_SPORTS" if "sport" in probe else "BEIN_MEDIA"
    if probe.startswith("art.") or probe.startswith("art_") or probe.startswith("art"):
        return "ART"
    if probe.startswith("osn.") or probe.startswith("osn_") or probe.startswith("osn"):
        return "OSN"
    if probe.startswith("mbc.") or probe.startswith("mbc_") or probe.startswith("mbc"):
        return "MBC"
    if "aljazeera" in probe or "al.jazeera" in probe:
        return "ALJAZEERA"
    m=re.search(r"\.([a-z]{2})(?:@[^.]*)?$", cid or "", re.I)
    return COUNTRY_GROUPS.get(m.group(1).lower(), "OTHER") if m else "OTHER"

def parse_feed_arg(value: str):
    if "=" not in value:
        raise argparse.ArgumentTypeError("--feed must be KEY=PATH")
    key, path = value.split("=", 1)
    key = key.strip().lower()
    path = path.strip()
    if not key or not path:
        raise argparse.ArgumentTypeError("--feed must be KEY=PATH")
    return key, Path(path)

def source_matches_feed(winner_source: str, feed_key: str) -> bool:
    winner=(winner_source or "").strip().lower()
    aliases=FEED_SOURCE_ALIASES.get(feed_key, set())
    if not winner or winner == "no_xmltv_id" or not aliases:
        return True
    return winner in aliases

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--feed", action="append", type=parse_feed_arg, default=[],
                    help="Current production feed as KEY=PATH. Repeatable.")
    ap.add_argument("--mena", help="Legacy single merged MENA feed")
    ap.add_argument("--morocco", help="Legacy Morocco feed")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--base-url", required=True)
    args=ap.parse_args()

    out=Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    # raw xmltv id -> stable receiver metadata for the selected winner row.
    meta_by_raw={}
    canonical_group={}
    with Path(args.master).open("r", encoding="utf-8-sig", newline="") as fh:
        rows=list(csv.DictReader(fh))
    for r in rows:
        raw=(r.get("xmltv_id") or "").strip()
        src=(r.get("source") or "").strip()
        winner=(r.get("winner_source") or "").strip()
        if not raw:
            continue
        canonical=(r.get("receiver_canonical_id") or raw).strip()
        group=(r.get("id_group") or fallback_group(canonical or raw)).strip() or "OTHER"
        if group not in GROUP_ORDER:
            group="OTHER"
        # Keep canonical/group metadata even if this row is not the winner.
        canonical_group.setdefault(canonical, group)
        if src and winner and src == winner:
            meta_by_raw[raw] = {
                "canonical": canonical,
                "group": group,
                "winner_source": winner,
            }

    feeds=list(args.feed)
    if not feeds and args.mena:
        feeds.append(("legacy", Path(args.mena)))
    if args.morocco and Path(args.morocco).exists():
        feeds.append(("morocco", Path(args.morocco)))
    if not feeds:
        ap.error("provide at least one --feed KEY=PATH or --mena")

    # First feed wins for a canonical channel. Workflow passes feeds in strict
    # production priority order: Morocco, beIN, OSN, Sport24, ElCinema, others.
    selected_channels={}
    selected_programmes={}
    source_for_canonical={}
    seen_programmes=set()
    skipped_nonwinner=0
    skipped_duplicate_channel=0

    for feed_key, path in feeds:
        root=read_xml(path)
        raw_channels={}
        for ch in root.findall("channel"):
            raw=(ch.get("id") or "").strip()
            if raw:
                raw_channels[raw]=ch

        selected_raw={}
        for raw, ch in raw_channels.items():
            meta=meta_by_raw.get(raw)
            canonical=(meta or {}).get("canonical") or raw
            winner=(meta or {}).get("winner_source") or ""
            group=(meta or {}).get("group") or fallback_group(canonical)

            if feed_key == "morocco":
                group="MOROCCO"
            elif meta and not source_matches_feed(winner, feed_key):
                skipped_nonwinner += 1
                continue

            if canonical in selected_channels:
                skipped_duplicate_channel += 1
                continue

            node=copy.deepcopy(ch)
            node.set("id", canonical)
            selected_channels[canonical]=node
            canonical_group[canonical]=group if group in GROUP_ORDER else "OTHER"
            source_for_canonical[canonical]=feed_key
            selected_raw[raw]=canonical

        for p in root.findall("programme"):
            raw=(p.get("channel") or "").strip()
            canonical=selected_raw.get(raw)
            if not canonical:
                continue
            node=copy.deepcopy(p)
            node.set("channel", canonical)
            title=(node.findtext("title") or "").strip()
            key=(canonical, node.get("start") or "", node.get("stop") or "", title)
            if key in seen_programmes:
                continue
            seen_programmes.add(key)
            selected_programmes.setdefault(canonical, []).append(node)

    ids_by_group={g:set() for g in GROUP_ORDER}
    for cid in selected_channels:
        g=canonical_group.get(cid) or fallback_group(cid)
        if g not in ids_by_group:
            g="OTHER"
        ids_by_group[g].add(cid)

    now=datetime.now(timezone.utc)
    registry={
        "schema":2,
        "generated_utc":now.isoformat(),
        "policy":"current EPG-Scrapers production feeds grouped for Vu+; stable receiver IDs and winner-source policy preserved",
        "groups":[],
        "diagnostics":{
            "input_feeds":[{"key":k,"path":str(p)} for k,p in feeds],
            "skipped_nonwinner":skipped_nonwinner,
            "skipped_duplicate_channel":skipped_duplicate_channel,
        },
    }
    total_channels=total_programmes=total_future=0
    global_latest=None

    for group in GROUP_ORDER:
        tv=ET.Element("tv", {
            "generator-info-name":"EPGManager Group Builder",
            "source-info-name":"EPG-Scrapers current production feeds",
        })
        group_programmes=[]
        latest=None
        future=0

        for cid in sorted(ids_by_group[group], key=str.casefold):
            tv.append(copy.deepcopy(selected_channels[cid]))
            for p in selected_programmes.get(cid, []):
                group_programmes.append(p)
                st=parse_xmltv_time(p.get("start") or "")
                if st:
                    latest = st if latest is None or st > latest else latest
                    global_latest = st if global_latest is None or st > global_latest else global_latest
                    if st >= now:
                        future += 1

        group_programmes.sort(key=lambda p: ((p.get("start") or ""), (p.get("channel") or "")))
        for p in group_programmes:
            tv.append(copy.deepcopy(p))

        xml=ET.tostring(tv, encoding="utf-8", xml_declaration=True)
        fn="group-%s.xml.gz" % group.lower().replace("_","-")
        with gzip.open(out/fn, "wb", compresslevel=9) as gz:
            gz.write(xml)

        chn=len(ids_by_group[group]); prg=len(group_programmes)
        total_channels += chn; total_programmes += prg; total_future += future
        registry["groups"].append({
            "key":group,
            "label":group.replace("_"," "),
            "xml":fn,
            "url":args.base_url.rstrip("/")+"/"+fn,
            "channels":chn,
            "programmes":prg,
            "future_programmes":future,
            "latest_start_utc":latest.isoformat() if latest else None,
            "enabled":True,
        })

    registry["totals"]={
        "channels":total_channels,
        "programmes":total_programmes,
        "future_programmes":total_future,
        "latest_start_utc":global_latest.isoformat() if global_latest else None,
    }
    (out/"group-sources.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"
    )

    if total_programmes <= 0 or total_future <= 0:
        raise SystemExit("GROUP_FEEDS FAIL: no current/future programme data")
    print(
        "GROUP_FEEDS PASS groups=%d channels=%d programmes=%d future=%d latest=%s" %
        (len(GROUP_ORDER), total_channels, total_programmes, total_future,
         registry["totals"]["latest_start_utc"])
    )

if __name__ == "__main__":
    main()
