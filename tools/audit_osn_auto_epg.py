#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Vu+ EPGManager OSN AUTO vs MANUAL diagnosis.

Checks saved mappings, SRP channels.xml and the published 48h OSN XMLTV.
No Enigma2 imports, cache flush, bouquet changes or plugin restart.
"""
import argparse
import collections
import gzip
import io
import json
import os
import re
import sys
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

MAP_PATH = "/etc/enigma2/epgmanager_mappings.json"
SRP_PATH = "/etc/enigma2/epgmanager_srp_maps.json"
CORE_PATH = "/usr/lib/enigma2/python/Plugins/Extensions/EPGManager/core/direct_native_import.py"
FEED_URL = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/feeds/osn.xml.gz"

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print("UNREADABLE", path, str(exc)[:130])
        return {}

def ref_key(value):
    return str(value or "").strip().lower().rstrip(":")

def name_ok(value):
    return bool(re.search(r"\bosn\b|osntv|osnmovies|osnyahala|osnone|osnshowcase|osnkids|osncrime|osndocumentary|osncomedy", str(value or ""), re.I))

def osn_saved_rows(data):
    saved = data.get("mappings") or {}
    result = []
    for key, row in saved.items():
        if not isinstance(row, dict) or "::" not in str(key):
            continue
        sid, cid = str(key).split("::", 1)
        display = str(row.get("display_name") or "")
        if not (name_ok(sid) or name_ok(cid) or name_ok(display)):
            continue
        result.append({"sid":sid.lower(), "cid":cid, "name":display or cid,
                       "mode":str(row.get("mode") or "unknown").lower(),
                       "refs":[str(x).strip() for x in row.get("refs") or [] if str(x).strip()],
                       "confidence":row.get("confidence")})
    return result

def load_srp(index):
    maps = {}
    for sid,row in (index.get("sources") or {}).items():
        if not (name_ok(sid) or name_ok((row or {}).get("name"))):
            continue
        path = str((row or {}).get("map_path") or "")
        if not os.path.isfile(path):
            maps[sid.lower()] = {"path":path,"map":{}}
            continue
        try:
            entries = collections.defaultdict(set)
            for event, elem in ET.iterparse(path, events=("end",)):
                if elem.tag.rsplit("}",1)[-1] == "channel":
                    cid = str(elem.attrib.get("id") or "").strip().lower()
                    if cid:
                        for ref in re.split(r"[\s,]+", (elem.text or "").strip()):
                            if ref:
                                entries[cid].add(ref_key(ref))
                elem.clear()
            maps[sid.lower()] = {"path":path,"map":dict(entries)}
        except Exception as exc:
            print("MAP_PARSE_ERROR", path, str(exc)[:150])
            maps[sid.lower()] = {"path":path,"map":{}}
    return maps

def read_osn_feed(online):
    counts = collections.Counter()
    ids = set()
    if not online:
        return ids, counts, "skipped (--online not set)"
    try:
        req = Request(FEED_URL,headers={"User-Agent":"EPGManager-OSN-Audit/1.0"})
        with urlopen(req, timeout=20) as response:
            binary = response.read(12 * 1024 * 1024 + 1)
        if len(binary) > 12 * 1024 * 1024:
            return ids, counts, "feed too big"
        with gzip.GzipFile(fileobj=io.BytesIO(binary)) as data:
            for _,elem in ET.iterparse(data,events=("end",)):
                tag = elem.tag.rsplit("}",1)[-1]
                if tag == "channel":
                    cid = str(elem.get("id") or "").strip().lower()
                    if cid: ids.add(cid)
                elif tag == "programme":
                    cid = str(elem.get("channel") or "").strip().lower()
                    if cid:
                        counts[cid] += 1
                elem.clear()
        return ids, counts, "OK"
    except Exception as exc:
        return ids, counts, "ERROR " + str(exc)[:150]

def report(args):
    print("=== EPGMANAGER OSN AUTO DIAGNOSTIC — READ ONLY ===")
    try:
        src=open(CORE_PATH,encoding="utf-8").read()
        print("NATIVE_MODULE =",CORE_PATH)
        print("AUTO_OVERLAY_PATCH =", "YES" if "def _merge_persisted_auto_overrides(" in src else "NO")
        print("DIRECT_GITHUB_PATCH =", "YES" if "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed" in src else "NO")
    except Exception as exc:
        print("NATIVE_MODULE = UNKNOWN",str(exc)[:100])

    mapping=load_json(MAP_PATH)
    rows=osn_saved_rows(mapping)
    groups=collections.Counter((r["sid"],r["mode"]) for r in rows)
    print("SAVED_OSN_MAPPINGS =",len(rows))
    for (source,mode),number in sorted(groups.items()):
        print("  SOURCE %-34s MODE %-16s ROWS %d" %(source[:34],mode[:16],number))

    index=load_json(SRP_PATH)
    maps=load_srp(index)
    print("SRP_OSN_SOURCES =",len(maps))
    for sid,info in sorted(maps.items()):
        print("  SRP %-34s IDS %-4d PATH %s"%(sid,len(info["map"]),info["path"][:100]))

    feed_ids,counts,feed_status=read_osn_feed(args.online)
    print("OSN_FEED =",feed_status, "IDS =",len(feed_ids), "WITH_PROGRAMMES =",sum(bool(v) for v in counts.values()))

    # Avoid exposing full receiver service references or user-specific bouquet names.
    mode_totals=collections.Counter()
    candidates=[]
    for row in rows:
        source=row["sid"]; cid=row["cid"].lower(); refs=row["refs"]
        owners=[v for k,v in maps.items() if k==source]
        in_map=any(bool(set(ref_key(r) for r in refs)&(owner["map"].get(cid) or set())) for owner in owners)
        cid_in_map=any(cid in owner["map"] for owner in owners)
        in_feed=("YES" if cid in feed_ids else "NO") if feed_status=="OK" else "?"
        programmes=str(counts.get(cid,0)) if feed_status=="OK" else "?"
        mode_totals[(row["mode"],bool(refs),bool(owners),cid_in_map,in_map)] += 1
        item={**row,"map":"YES" if in_map else "NO", "map_id":"YES" if cid_in_map else "NO",
              "feed":in_feed,"programmes":programmes}
        candidates.append(item)

    print("=== OWNERSHIP vs GENERATION ===")
    for (mode,has_ref,has_source,has_id,has_ref_map),n in sorted(mode_totals.items()):
        print("  %-15s refs=%-5s source=%-5s cid_in_srp=%-5s ref_in_srp=%-5s : %d"%(
            mode,str(has_ref),str(has_source),str(has_id),str(has_ref_map),n))
    manual=sorted((x for x in candidates if x["mode"]=="manual"),key=lambda x:x["name"])
    auto=sorted((x for x in candidates if x["mode"]!="manual"),
                key=lambda x:(x["map"]=="YES", x["programmes"] not in ("0","?"),x["name"]))
    for label,chosen,limit in (("MANUAL",manual,8),("AUTO",auto,18)):
        print("=== %s SAMPLE ==="%label)
        for r in chosen[:limit]:
            print("  %-30.30s | source=%-20.20s id=%-37.37s | refs=%-2d SRP=%s/%s FEED=%s EPG=%s CONF=%s"%(
                r["name"],r["sid"],r["cid"],len(r["refs"]),r["map_id"],r["map"],r["feed"],
                r["programmes"],str(r["confidence"] or "-")))
    if not auto:
        print("NO SAVED AUTO OSN ROWS: displayed [A] may come only from SRP Master, not MappingStore.")
    if not maps:
        print("NO OSN SRP MAP: selected feed map may be under a non-OSN source ID.")
    print("NO CHANGES MADE. Share this output for a targeted fix.")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--online",action="store_true",help="also download current OSN XMLTV from GitHub")
    report(ap.parse_args())
