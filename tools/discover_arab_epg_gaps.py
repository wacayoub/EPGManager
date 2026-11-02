#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover Arab-country XMLTV IDs available upstream but missing from our feeds."""
from __future__ import annotations
import argparse, gzip, json, re
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

ARAB_COUNTRIES = {
    "dz":"Algeria","bh":"Bahrain","km":"Comoros","dj":"Djibouti","eg":"Egypt",
    "iq":"Iraq","jo":"Jordan","kw":"Kuwait","lb":"Lebanon","ly":"Libya",
    "mr":"Mauritania","ma":"Morocco","om":"Oman","ps":"Palestine","qa":"Qatar",
    "sa":"Saudi Arabia","so":"Somalia","sd":"Sudan","sy":"Syria","tn":"Tunisia",
    "ae":"UAE","ye":"Yemen",
}
EXCLUDE_SITES={"epgshare01.online"}

def norm_xmltv_id(cid):
    return (cid or "").strip().split("@",1)[0].casefold()

def country_from_id(cid):
    base=(cid or "").split("@",1)[0]
    m=re.search(r"\.([a-z]{2})$",base,re.I)
    return m.group(1).lower() if m else None

def load_existing(feeds_dir):
    ids=set()
    for p in feeds_dir.glob("*.xml.gz"):
        try:
            root=ET.fromstring(gzip.decompress(p.read_bytes()))
        except Exception:
            continue
        for ch in root.findall("channel"):
            cid=(ch.get("id") or "").strip()
            if cid:
                ids.add(norm_xmltv_id(cid))
    return ids

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--vendor",default="vendor/iptv-org-epg")
    ap.add_argument("--feeds-dir",default="feeds")
    ap.add_argument("--json",default="reports/arab-epg-gaps.json")
    ap.add_argument("--md",default="reports/arab-epg-gaps.md")
    a=ap.parse_args()

    existing=load_existing(Path(a.feeds_dir))
    candidates=defaultdict(list)
    fallback=defaultdict(list)

    for path in Path(a.vendor).glob("sites/**/*.channels.xml"):
        site=path.parent.name
        try:
            root=ET.fromstring(path.read_bytes())
        except Exception:
            continue
        for ch in root.findall("channel"):
            xid=(ch.get("xmltv_id") or "").strip()
            if not xid:
                continue
            cc=country_from_id(xid)
            if cc not in ARAB_COUNTRIES:
                continue
            key=norm_xmltv_id(xid)
            row={
                "xmltv_id":xid,
                "name":(ch.text or "").strip(),
                "site":site,
                "site_id":ch.get("site_id") or "",
                "lang":ch.get("lang") or "",
                "country":ARAB_COUNTRIES[cc],
            }
            (fallback if site in EXCLUDE_SITES else candidates)[key].append(row)

    missing=[]
    for key,rows in candidates.items():
        if key in existing:
            continue
        langs=sorted(set(r["lang"] for r in rows if r["lang"]))
        sites=sorted(set(r["site"] for r in rows))
        best=sorted(rows,key=lambda r:(r["lang"]=="ar", bool(r["site_id"])),reverse=True)[0]
        missing.append({
            "canonical":key,
            "xmltv_id":best["xmltv_id"],
            "country":best["country"],
            "sample_name":best["name"] or best["xmltv_id"],
            "candidate_sites":sites,
            "languages":langs,
            "candidates":rows,
        })

    missing.sort(key=lambda x:(x["country"],x["sample_name"].casefold()))
    by_country=defaultdict(list)
    for x in missing:
        by_country[x["country"]].append(x)

    report={
        "existing_unique_ids":len(existing),
        "missing_unique_ids":len(missing),
        "countries":{c:len(v) for c,v in sorted(by_country.items())},
        "missing":missing,
        "fallback_only_missing":[
            {"canonical":k,"candidates":v}
            for k,v in fallback.items() if k not in existing and k not in candidates
        ],
    }
    Path(a.json).parent.mkdir(parents=True,exist_ok=True)
    Path(a.json).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    lines=[
        "# Arab EPG gap discovery","",
        f"Existing unique channel IDs: **{len(existing)}**  ",
        f"Missing IDs with non-EPGShare upstream candidates: **{len(missing)}**","",
        "| Country | Missing IDs |","|---|---:|",
    ]
    for country,vals in sorted(by_country.items()):
        lines.append(f"| {country} | {len(vals)} |")
    lines += ["","## Missing channels","",
              "| Country | Channel | XMLTV ID | Candidate sites | Langs |",
              "|---|---|---|---|---|"]
    for x in missing[:500]:
        lines.append(f'| {x["country"]} | {x["sample_name"].replace("|","/")} | {x["xmltv_id"]} | {", ".join(x["candidate_sites"])} | {", ".join(x["languages"])} |')
    Path(a.md).write_text("\n".join(lines)+"\n",encoding="utf-8")

    print(json.dumps({
        "existing_unique_ids":len(existing),
        "missing_unique_ids":len(missing),
        "countries":report["countries"],
        "top_missing":[{"country":x["country"],"name":x["sample_name"],"id":x["xmltv_id"],"sites":x["candidate_sites"]} for x in missing[:80]]
    },ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
