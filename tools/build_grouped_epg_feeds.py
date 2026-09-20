#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build fresh XMLTV feeds grouped for Vu+ / EPGManager import.

Current programme data is read from EPG-Scrapers production feeds. The MENA
master preserves stable receiver IDs and preferred winners, while a healthy
fallback is allowed when the preferred feed has no current/future EPG.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import unicodedata
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
FEED_SOURCE_ALIASES = {
    "bein": {"bein.com", "beinsports.com"},
    "osn": {"osn.com"},
    "elcinema": {"elcinema.com"},
    "morocco": set(),
    "sport24": set(),
    "others": set(),
}
FEED_PRIORITY = {
    "morocco": 600,
    "bein": 500,
    "osn": 450,
    "sport24": 400,
    "elcinema": 300,
    "others": 100,
    "legacy": 50,
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
        dt = dt.replace(tzinfo=timezone(timedelta(minutes=mins)))
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)

def norm_name(value: str) -> str:
    s=unicodedata.normalize("NFKC", value or "").casefold()
    s=re.sub(r"@[a-z0-9_-]+$", "", s)
    s=re.sub(r"\.(?:ae|eg|ma|sa|qa|kw|lb|jo|iq|bh|om|tn|dz|ly|sy|ye|ps|sd|mr|mena)$", "", s)
    s=re.sub(r"\b(?:uhd|fhd|hd|sd|tv|channel)\b", " ", s)
    s=s.replace("&", " and ")
    s=re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", s)
    return " ".join(s.split())

def forced_identity_group(raw: str, canonical: str="", display: str=""):
    p=" ".join((raw or "", canonical or "", display or "")).casefold().strip()
    rawp=(raw or "").casefold()
    if rawp.startswith("official.rotana") or "rotana" in rawp:
        return "ROTANA"
    if rawp.startswith("mbc") or re.match(r"^mbc\b", norm_name(display)):
        return "MBC"
    if rawp.startswith("osn") or norm_name(display).startswith("osntv"):
        return "OSN"
    if rawp.startswith("bein"):
        return "BEIN_SPORTS" if "sport" in rawp or "sport" in norm_name(display) else "BEIN_MEDIA"
    if rawp.startswith("art") or norm_name(raw).startswith("alfa ") or norm_name(display).startswith("alfa "):
        return "ART"
    if "aljazeera" in rawp or "al.jazeera" in rawp or "al jazeera" in norm_name(display):
        return "ALJAZEERA"
    if rawp.startswith("sport24.adsports") or rawp.startswith("sport24.dubaisports"):
        return "EMIRATES"
    n=norm_name(raw + " " + display)
    if n.startswith("oman ") or n == "oman":
        return "OMAN"
    if n.startswith("bahrain "):
        return "BAHRAIN"
    if n.startswith("al mamlaka") or n.startswith("amman ") or n.startswith("jordan ") or n.startswith("roya "):
        return "JORDAN"
    return None

def fallback_group(cid: str) -> str:
    forced=forced_identity_group(cid)
    if forced:
        return forced
    m=re.search(r"\.([a-z]{2})(?:@[^.]*)?$", cid or "", re.I)
    return COUNTRY_GROUPS.get(m.group(1).lower(), "OTHER") if m else "OTHER"

def parse_feed_arg(value: str):
    if "=" not in value:
        raise argparse.ArgumentTypeError("--feed must be KEY=PATH")
    key, path = value.split("=", 1)
    key=key.strip().lower(); path=path.strip()
    if not key or not path:
        raise argparse.ArgumentTypeError("--feed must be KEY=PATH")
    return key, Path(path)

def feed_declared_source(feed_key: str, raw: str):
    if feed_key == "others":
        r=(raw or "").casefold()
        if r.startswith("official.rotana"):
            return "rotana.net"
    return None

def source_matches_feed(winner_source: str, feed_key: str, raw: str="") -> bool:
    winner=(winner_source or "").strip().lower()
    if not winner or winner == "no_xmltv_id":
        return False
    declared=feed_declared_source(feed_key, raw)
    if declared:
        return winner == declared
    aliases=FEED_SOURCE_ALIASES.get(feed_key, set())
    return winner in aliases if aliases else False

def channel_display(ch, raw):
    for d in ch.findall("display-name"):
        if d.text and d.text.strip():
            return d.text.strip()
    return raw

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
    now=datetime.now(timezone.utc)
    keep_after=now-timedelta(hours=6)

    meta_by_raw={}
    name_candidates={}
    canonical_default_group={}

    with Path(args.master).open("r", encoding="utf-8-sig", newline="") as fh:
        rows=list(csv.DictReader(fh))

    for r in rows:
        raw=(r.get("xmltv_id") or "").strip()
        src=(r.get("source") or "").strip()
        winner=(r.get("winner_source") or "").strip()
        canonical=(r.get("receiver_canonical_id") or raw).strip()
        if not raw or not canonical:
            continue

        forced=forced_identity_group(raw, canonical, r.get("channel_name") or "")
        group=forced or (r.get("id_group") or fallback_group(canonical)).strip() or "OTHER"
        if group not in GROUP_ORDER:
            group="OTHER"
        canonical_default_group.setdefault(canonical, group)

        # Store winner metadata; for duplicate rows of the same raw ID prefer
        # the row whose source is the declared winner.
        if src and winner and src == winner:
            meta={
                "canonical":canonical,
                "group":group,
                "winner_source":winner,
            }
            meta_by_raw[raw]=meta
            for label in (r.get("channel_name") or "", canonical, raw):
                k=norm_name(label)
                if k:
                    name_candidates.setdefault(k, set()).add(canonical)

    # Only exact, unambiguous normalized names are safe aliases.
    name_alias={}
    for k, vals in name_candidates.items():
        if len(vals) == 1:
            name_alias[k]=next(iter(vals))

    canonical_meta={}
    for raw, meta in meta_by_raw.items():
        canonical_meta.setdefault(meta["canonical"], meta)

    feeds=list(args.feed)
    if not feeds and args.mena:
        feeds.append(("legacy", Path(args.mena)))
    if args.morocco and Path(args.morocco).exists():
        feeds.append(("morocco", Path(args.morocco)))
    if not feeds:
        ap.error("provide at least one --feed KEY=PATH or --mena")

    candidates={}
    invalid_programmes=0
    alias_matches=0

    for feed_key, path in feeds:
        root=read_xml(path)
        prog_by_raw={}
        for p in root.findall("programme"):
            raw=(p.get("channel") or "").strip()
            if raw:
                prog_by_raw.setdefault(raw, []).append(p)

        for ch in root.findall("channel"):
            raw=(ch.get("id") or "").strip()
            if not raw:
                continue
            display=channel_display(ch, raw)
            meta=meta_by_raw.get(raw)
            canonical=None
            group=None
            winner=""

            if meta:
                canonical=meta["canonical"]
                group=meta["group"]
                winner=meta["winner_source"]
            else:
                # Try exact normalized aliases against the winner master.
                for label in (display, raw):
                    c=name_alias.get(norm_name(label))
                    if c:
                        canonical=c
                        cm=canonical_meta.get(c, {})
                        group=cm.get("group")
                        winner=cm.get("winner_source", "")
                        alias_matches += 1
                        break

            canonical=canonical or raw
            forced=forced_identity_group(raw, canonical, display)
            group=forced or group or canonical_default_group.get(canonical) or fallback_group(canonical)
            if feed_key == "morocco":
                group="MOROCCO"
            if group not in GROUP_ORDER:
                group="OTHER"

            cleaned=[]
            future_count=0
            latest=None
            for p in prog_by_raw.get(raw, []):
                st=parse_xmltv_time(p.get("start") or "")
                sp=parse_xmltv_time(p.get("stop") or "")
                if not st or not sp or st >= sp:
                    invalid_programmes += 1
                    continue
                if sp < keep_after:
                    continue
                node=copy.deepcopy(p)
                node.set("channel", canonical)
                cleaned.append(node)
                if st >= now-timedelta(hours=2):
                    future_count += 1
                if latest is None or st > latest:
                    latest=st

            node=copy.deepcopy(ch)
            node.set("id", canonical)
            winner_match=source_matches_feed(winner, feed_key, raw)
            priority=FEED_PRIORITY.get(feed_key, 0)
            score=(
                1 if future_count > 0 else 0,
                1 if winner_match else 0,
                priority,
                future_count,
                latest.timestamp() if latest else 0,
            )
            candidates.setdefault(canonical, []).append({
                "canonical":canonical,
                "raw":raw,
                "display":display,
                "group":group,
                "winner_source":winner,
                "winner_match":winner_match,
                "feed":feed_key,
                "channel":node,
                "programmes":cleaned,
                "future":future_count,
                "latest":latest,
                "score":score,
            })

    selected={}
    fallback_selected=0
    no_future_selected=0
    for canonical, rows in candidates.items():
        rows.sort(key=lambda x: x["score"], reverse=True)
        best=rows[0]
        selected[canonical]=best
        if best["future"] <= 0:
            no_future_selected += 1
        if best["winner_source"] and not best["winner_match"] and best["future"] > 0:
            fallback_selected += 1

    ids_by_group={g:set() for g in GROUP_ORDER}
    for cid, item in selected.items():
        ids_by_group[item["group"]].add(cid)

    seen_programmes=set()
    registry={
        "schema":3,
        "generated_utc":now.isoformat(),
        "policy":"fresh EPG-Scrapers production feeds grouped for Vu+; preferred winners used when healthy, fallback only when needed",
        "groups":[],
        "diagnostics":{
            "input_feeds":[{"key":k,"path":str(p)} for k,p in feeds],
            "alias_matches":alias_matches,
            "fallback_selected":fallback_selected,
            "no_future_selected":no_future_selected,
            "invalid_programmes_dropped":invalid_programmes,
            "selected_by_feed":{},
        },
    }
    for item in selected.values():
        registry["diagnostics"]["selected_by_feed"][item["feed"]]=registry["diagnostics"]["selected_by_feed"].get(item["feed"],0)+1

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
            item=selected[cid]
            tv.append(copy.deepcopy(item["channel"]))
            for p in item["programmes"]:
                title=(p.findtext("title") or "").strip()
                key=(cid, p.get("start") or "", p.get("stop") or "", title)
                if key in seen_programmes:
                    continue
                seen_programmes.add(key)
                group_programmes.append(p)
                st=parse_xmltv_time(p.get("start") or "")
                if st:
                    latest=st if latest is None or st > latest else latest
                    global_latest=st if global_latest is None or st > global_latest else global_latest
                    if st >= now-timedelta(hours=2):
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
            "enabled":future > 0,
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
        "GROUP_FEEDS PASS groups=%d channels=%d programmes=%d future=%d latest=%s fallback=%d" %
        (len(GROUP_ORDER), total_channels, total_programmes, total_future,
         registry["totals"]["latest_start_utc"], fallback_selected)
    )

if __name__ == "__main__":
    main()
