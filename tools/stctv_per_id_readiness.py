#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STC per-ID actual programme-content readiness, not mere 48h time coverage.

Use only the accepted STC XMLTV publication and its SHA-pinned metadata.
Never change names, IDs, mapping choices, or fabricate programme records.
A perfectly continuous loop of channel-name titles is NOT a verified guide.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools import audit_source_quality, validate_48h
except ImportError:
    import audit_source_quality
    import validate_48h

MAX_AGE_HOURS = 36.
MIN_GREEN_FUTURE_HOURS = 36.
MAX_GAP_MINUTES = 90.


def ready_report(feed_path, metadata_path, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    now = now.astimezone(timezone.utc)
    data = Path(feed_path).read_bytes()
    meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if meta.get("label") != "stctv":
        raise ValueError("WRONG_STCTV_LABEL")
    digest = hashlib.sha256(data).hexdigest()
    if meta.get("sha256") != digest or meta.get("size_bytes") != len(data):
        raise ValueError("STCTV_PUBLISHED_BYTES_HASH_OR_SIZE_MISMATCH")
    if not data.startswith(b"\x1f\x8b"):
        raise ValueError("STCTV_NOT_GZIP")
    generated = datetime.fromisoformat(meta["generated_utc"].replace("Z","+00:00"))
    if generated.tzinfo is None:
        raise ValueError("STCTV_GENERATED_TIME_NEEDS_TIMEZONE")
    generated = generated.astimezone(timezone.utc)
    age = (now-generated).total_seconds()/3600.
    if age < -1:
        raise ValueError("STCTV_SOURCE_TIME_IN_FUTURE")

    root=ET.fromstring(gzip.decompress(data))
    if root.tag != "tv":
        raise ValueError("STCTV_INVALID_XML_ROOT")
    channels=[(x.get("id") or "").strip() for x in root.findall("channel")]
    if (not channels or not all(channels) or len(set(channels)) != len(channels) or
            len(channels) != meta.get("channels")):
        raise ValueError("STCTV_CHANNEL_CATALOGUE_ID_INVALID")
    original_count = len(root.findall("programme"))
    if original_count != meta.get("programmes"):
        raise ValueError("STCTV_ORIGINAL_PROGRAMMES_COUNT_INVALID")

    # Quality stats recomputed directly from the SAME immutable feed. The
    # audit's suspected placeholder counter includes channel-name titles
    # and generic/unavailable descriptions, not simply absent descriptions.
    coverage=validate_48h.audit(str(feed_path),48,90)
    quality=audit_source_quality.analyze(str(feed_path),"stctv",now)
    if (coverage["channels"] != len(channels) or quality["channels"] != len(channels) or
            coverage["invalid_times"] or coverage["orphan_programmes"] or
            quality["invalid_dates"] or quality["programmes"] != original_count):
        raise ValueError("STCTV_INVALID_TIMES_OR_COHORT")
    indexed={x["id"]:x for x in coverage["rows"]}
    if len(indexed) != len(channels):
        raise ValueError("STCTV_COVERAGE_ROWS_COUNT_INVALID")
    counts_bad={x["id"]:x["count"] for x in quality["suspected_placeholder_channels"]}
    repeated=set(quality["suspected_repeated_title_channels"])
    rows=[]
    for cid in sorted(channels,key=str.casefold):
        audit=indexed[cid]
        horizon=float(audit.get("future_hours") or 0)
        gap=audit.get("max_gap_minutes")
        suspicious=counts_bad.get(cid,0)
        repeated_title=cid in repeated
        if not audit.get("programmes") or horizon<=0:
            status,reason="NO_EPG","NO_FUTURE_PROGRAMMES"
        elif suspicious:
            status,reason="REVIEW","SUSPECTED_CHANNEL_TITLE_OR_GENERIC_DESCRIPTION"
        elif repeated_title:
            # A repeating live-news title can be genuine. Flag for a human
            # rather than claiming fake content or deleting its schedule.
            status,reason="REVIEW","LOW_PROGRAMME_TITLE_DIVERSITY"
        elif age>MAX_AGE_HOURS:
            status,reason="REVIEW","PUBLISHED_FEED_STALE"
        elif (gap is None or float(gap)>MAX_GAP_MINUTES):
            status,reason="REVIEW","LONG_INTERNAL_GAP"
        elif horizon<MIN_GREEN_FUTURE_HOURS:
            status,reason="REVIEW","SHORT_REAL_GUIDE"
        else:
            status,reason="VERIFIED","TIME_AND_CONTENT_QUALITY_OK"
        rows.append({
            "xmltv_id":cid,
            "status":status,"reason":reason,
            "future_hours":round(horizon,2),
            "max_gap_minutes":gap,
            "future_programmes":audit.get("programmes",0),
            "suspected_channel_title_or_generic_description_rows":suspicious,
            "suspected_repeated_title":repeated_title,
            "best_match_green_allowed":status=="VERIFIED",
            "manual_mapping_visible":True,
        })
    totals={kind:sum(x["status"]==kind for x in rows)
            for kind in ("VERIFIED","REVIEW","NO_EPG")}
    if sum(totals.values())!=len(channels):
        raise ValueError("STCTV_INCOMPLETE_CLASSIFICATION")
    return {
        "schema":1,"source":"stctv",
        "mode":"real-content-and-time-per-id-not-automatic-mapping",
        "checked_utc":now.isoformat(),
        "source_sha256":digest,
        "source_generated_utc":generated.isoformat(),
        "source_age_hours":round(age,2),
        "channels":len(channels),
        "original_programmes_preserved":original_count,
        "synthetic_programmes_added":0,
        "quality_source_status":quality["quality_status"],
        "quality_future_programmes":quality["future_programmes"],
        "quality_suspected_placeholder_future":quality["future_programmes_with_placeholder_or_channel_title"],
        "quality_unavailable_descriptions":quality["future_programmes_with_unavailable_description"],
        "quality_missing_descriptions":quality["future_programmes_without_description"],
        "counts":totals,"ids":rows,
        "policy":{
            "verified_min_future_hours":MIN_GREEN_FUTURE_HOURS,
            "verified_max_internal_gap_minutes":MAX_GAP_MINUTES,
            "max_source_age_hours":MAX_AGE_HOURS,
            "channel_title_or_generic_description_never_green":True,
            "repeated_title_requires_review_not_automatic_fake_label":True,
            "xmltv_ids_preserved":True,
            "no_receiver_mapping_changes":True,
            "ready_index_not_active_epgmanager_integration":True,
        },
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--feed",default="feeds/stctv.xml.gz")
    parser.add_argument("--metadata",default="feeds/stctv.json")
    parser.add_argument("--output",default="reports/stctv-per-id-readiness.json")
    a=parser.parse_args()
    report=ready_report(a.feed,a.metadata)
    path=Path(a.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",
                    encoding="utf-8")
    print("STCTV_PER_ID_CONTENT_READINESS",json.dumps({
        "source_sha256":report["source_sha256"],
        "channels":report["channels"],"counts":report["counts"],
        "suspected_placeholder_rows":report["quality_suspected_placeholder_future"],
        "synthetic_programmes_added":0
    }))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
