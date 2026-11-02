#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Truthful OSN per-ID EPG health for future Best Match consumption.

Reads ONLY the already published OSN XMLTV and its pinned JSON sidecar. A
channel with no genuine programme can NEVER become a verified Best Match
candidate just because it is present in the official channel catalogue.
OSN's Android and Legacy/Other catalogues are intentionally kept intact for
manual mapping. No programme rows or stored receiver settings are changed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

try:
    from audit_source_quality import parse_time, norm, BAD_TITLE, BAD_DESC
except ImportError:
    from tools.audit_source_quality import parse_time, norm, BAD_TITLE, BAD_DESC

MIN_GREEN_HOURS = 8.0
MAX_SOURCE_AGE_HOURS = 36.0

def publish_health(feed_path, metadata_path, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    now = now.astimezone(timezone.utc)
    contents = Path(feed_path).read_bytes()
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata.get("label") != "osn":
        raise ValueError("NOT_OSN_SOURCE")
    digest = hashlib.sha256(contents).hexdigest()
    if metadata.get("sha256") != digest or metadata.get("size_bytes") != len(contents):
        raise ValueError("OSN_XMLTV_METADATA_SHA_OR_SIZE_MISMATCH")
    if contents[:2] != bytes((31,139)):
        raise ValueError("OSN_EXPECTED_GZIP")
    generated = datetime.fromisoformat(metadata["generated_utc"].replace("Z","+00:00"))
    if generated.tzinfo is None:
        raise ValueError("OSN_TIMESTAMP_MISSING_TIMEZONE")
    generated = generated.astimezone(timezone.utc)
    age = (now-generated).total_seconds()/3600
    if age < -1:
        raise ValueError("OSN_FUTURE_GENERATION_TIMESTAMP")
    tv = ET.fromstring(gzip.decompress(contents))
    if tv.tag != "tv":
        raise ValueError("OSN_XMLTV_ROOT_NOT_TV")
    channels = {}
    for channel in tv.findall("channel"):
        cid = (channel.get("id") or "").strip()
        if not cid or cid in channels:
            raise ValueError("OSN_DUPLICATE_OR_EMPTY_XMLTV_ID")
        channels[cid] = tuple((n.text or "").strip() for n in channel.findall("display-name"))
    if len(channels) != metadata.get("channels"):
        raise ValueError("OSN_CATALOGUE_ID_COUNT_MISMATCH")
    future = defaultdict(list)
    stored = defaultdict(int)
    placeholder = defaultdict(int)
    invalid = 0
    orphan = 0
    for p in tv.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in channels:
            orphan += 1
            continue
        start = parse_time(p.get("start"))
        stop = parse_time(p.get("stop"))
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        stored[cid] += 1
        if stop <= now:
            continue
        future[cid].append((start,stop))
        title = next(((t.text or "").strip() for t in p.findall("title") if t.text),"")
        desc = next(((t.text or "").strip() for t in p.findall("desc") if t.text),"")
        names = {norm(n) for n in channels[cid] if len(norm(n)) >= 7}
        if (not title or any(x in title.casefold() for x in BAD_TITLE)
                or (len(norm(title)) >= 7 and norm(title) in names)
                or any(x in desc.casefold() for x in BAD_DESC)):
            placeholder[cid] += 1
    if invalid or orphan:
        raise ValueError("OSN_INVALID_OR_ORPHAN_PROGRAMMES")
    if sum(stored.values()) != metadata.get("programmes"):
        raise ValueError("OSN_PUBLISHED_PROGRAMME_COUNT_MISMATCH")
    rows = []
    for cid in sorted(channels,key=str.casefold):
        intervals = sorted(future[cid])
        # Valid programmes may have gaps; the farthest dated programme is
        # NOT proof of continuous guide coverage.
        usable = len(intervals) - placeholder[cid]
        current = sum(start <= now < stop for start,stop in intervals)
        if not intervals:
            status,why = "NO_EPG","NO_FUTURE_PROGRAMMES"
        elif placeholder[cid]:
            status,why = "REVIEW","PLACEHOLDER_OR_MISSING_TITLE"
        elif age > MAX_SOURCE_AGE_HOURS:
            status,why = "REVIEW","STALE_PUBLISHED_SOURCE"
        else:
            # Require 8 hours worth of real continuous coverage from now.
            # The intervals are merged to prevent duplicate rows inflating
            # quality; an internal gap breaks contiguous coverage.
            cursor = now
            for start,stop in intervals:
                if start > cursor:
                    break
                if stop > cursor:
                    cursor = stop
            covered = max(0.0,(cursor-now).total_seconds()/3600.0)
            if usable > 0 and covered >= MIN_GREEN_HOURS:
                status,why = "VERIFIED","REAL_CONTINUOUS_FUTURE_EPG"
            else:
                status,why = "REVIEW","REAL_BUT_SHORT_OR_GAPPED_GUIDE"
        if intervals:
            cursor = now
            for start,stop in intervals:
                if start > cursor:
                    break
                cursor = max(cursor,stop)
            covered = max(0.,(cursor-now).total_seconds()/3600.)
        else:
            covered=0.
        rows.append({
            "xmltv_id":cid,
            "status":status,
            "reason":why,
            "published_programmes_total":stored[cid],
            "future_programmes":len(intervals),
            "currently_on_air":bool(current),
            "continuous_future_hours":round(covered,2),
            "suspected_placeholder_future":placeholder[cid],
            "best_match_green_allowed":status=="VERIFIED",
            "manual_mapping_visible":True,
        })
    counts = {status:sum(row["status"]==status for row in rows)
              for status in ("VERIFIED","REVIEW","NO_EPG")}
    if sum(counts.values()) != len(channels):
        raise ValueError("OSN_MISSING_CLASSIFICATION")
    # Some publishers index non-future as active. Validate only structural
    # catalogue consistency, never assume their cached active count is live.
    absent = sorted(row["xmltv_id"] for row in rows if row["status"]=="NO_EPG")
    return {
        "source":"osn",
        "schema":1,
        "source_sha256":digest,
        "source_generated_utc":generated.isoformat(),
        "checked_utc":now.isoformat(),
        "source_age_hours":round(age,2),
        "catalogue_channels":len(channels),
        "programme_rows_unchanged":sum(stored.values()),
        "synthetic_programmes_added":0,
        "source_provenance":"published_osn_feed_not_per_id_provider_assertion",
        "policy":{
            "min_green_continuous_hours":MIN_GREEN_HOURS,
            "max_source_age_hours":MAX_SOURCE_AGE_HOURS,
            "zero_epg_never_green":True,
            "no_missing_id_pruning":True,
            "candidate_green_is_readiness_not_auto_mapping_change":True,
        },
        "counts":counts,
        "missing_ids":absent,
        "ids":rows,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--feed",default="feeds/osn.xml.gz")
    p.add_argument("--metadata",default="feeds/osn.json")
    p.add_argument("--output",default="reports/osn-per-id-health.json")
    a=p.parse_args()
    report=publish_health(a.feed,a.metadata)
    dest=Path(a.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print("OSN_REAL_ID_HEALTH",json.dumps({k:report[k] for k in
          ("catalogue_channels","counts","missing_ids","source_sha256",
           "synthetic_programmes_added")},ensure_ascii=False))
    return 0

if __name__=="__main__":
    sys.exit(main())
