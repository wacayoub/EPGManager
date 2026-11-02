#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ElCinema verified per-ID readiness. Never manufacture missing EPG hours.

Only the bytes of the published XMLTV file are examined. ElCinema's aggregate
48h FAIL must NOT turn 61 real long-horizon IDs red, while a few valid hours
must NOT turn short IDs green. Known placeholder-only channels are quarantined
even if they have 48h of apparent timeline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

try:
    import validate_48h
    import audit_source_quality
except ImportError:
    from tools import validate_48h, audit_source_quality

SCHEMA = 1
GREEN_MIN_HOURS = 36.
MAX_GAP_MINUTES = 90.
MAX_AGE_HOURS = 24.


def iso_utc(raw):
    value = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    if not value.tzinfo:
        raise ValueError("SOURCE_TIMESTAMP_WITHOUT_TIMEZONE")
    return value.astimezone(timezone.utc)


def verified_report(feed, metadata, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    feed = Path(feed)
    metadata = Path(metadata)
    compressed = feed.read_bytes()
    sidecar = json.loads(metadata.read_text(encoding="utf-8"))
    if sidecar.get("label") != "elcinema":
        raise ValueError("WRONG_FEED_LABEL")
    if len(compressed) != sidecar.get("size_bytes"):
        raise ValueError("SIZE_MISMATCH")
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != sidecar.get("sha256"):
        raise ValueError("SOURCE_SHA256_MISMATCH")
    if compressed[:2] != bytes((31,139)):
        raise ValueError("NOT_GZIP")
    stamp = iso_utc(sidecar.get("generated_utc"))
    age = (now - stamp).total_seconds()/3600
    if age < -1:
        raise ValueError("FUTURE_SOURCE_GENERATION")
    audit = validate_48h.audit(str(feed),48,90)
    quality = audit_source_quality.analyze(str(feed),"elcinema",now)
    if audit["invalid_times"] or audit["orphan_programmes"]:
        raise ValueError("BROKEN_XMLTV_TIMESTAMPS_OR_IDS")
    if audit["channels"] != sidecar.get("channels") or quality["channels"] != audit["channels"]:
        raise ValueError("MISMATCHED_CHANNEL_CATALOGUE")
    suspect = {
        row["id"]: row["count"]
        for row in quality["suspected_placeholder_channels"]
    }
    statuses = []
    for row in audit["rows"]:
        cid = row["id"]
        horizon = float(row.get("future_hours",0))
        gap = row.get("max_gap_minutes")
        bad_title_count = suspect.get(cid,0)
        # Even a 48h full channel-title loop is not a real EPG.
        if bad_title_count:
            verdict,reason="QUARANTINE","PLACEHOLDER_TITLES"
        elif horizon <= 0 or row["reason"].startswith("NO_FUTURE"):
            verdict,reason="QUARANTINE","NO_REAL_FUTURE_EPG"
        elif age > MAX_AGE_HOURS:
            verdict,reason="REVIEW","SOURCE_OLDER_THAN_24H"
        elif gap is None or float(gap) > MAX_GAP_MINUTES:
            verdict,reason="REVIEW","INTERNAL_GAP"
        elif horizon < GREEN_MIN_HOURS:
            verdict,reason="REVIEW","SHORT_GENUINE_GUIDE"
        else:
            verdict,reason="VERIFIED","REAL_CURRENT_PER_ID"
        statuses.append({
            "xmltv_id": cid, "verdict": verdict, "reason": reason,
            "future_hours": horizon, "internal_gap_minutes": gap,
            "actual_programme_rows": row.get("programmes",0),
            "suspected_placeholder_rows": bad_title_count,
            "auto_green_allowed": verdict=="VERIFIED",
            "manual_review_allowed": verdict=="REVIEW" and horizon>0,
        })
    counts = {k:sum(x["verdict"]==k for x in statuses)
              for k in ("VERIFIED","REVIEW","QUARANTINE")}
    assert sum(counts.values()) == audit["channels"]
    return {
        "schema": SCHEMA,
        "source": "elcinema",
        "mode": "verified-real-per-id-not-global-48h",
        "checked_at_utc": now.isoformat(),
        "source_generated_utc": stamp.isoformat(),
        "source_age_hours": round(age,2),
        "source_sha256": digest,
        "source_channels": audit["channels"],
        "original_programmes_preserved": sidecar.get("programmes"),
        "audit_invalid_times": audit["invalid_times"],
        "audit_orphan_programmes": audit["orphan_programmes"],
        "synthetic_programmes_added": 0,
        "counts": counts,
        "ids": statuses,
        "policy": {
            "green_requires_hours": GREEN_MIN_HOURS,
            "max_internal_gap_minutes": MAX_GAP_MINUTES,
            "placeholder_only_never_green": True,
            "short_real_ids_remain_visible_for_manual_mapping": True,
            "stale_source_never_green": True,
        }
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--feed",default="feeds/elcinema.xml.gz")
    p.add_argument("--metadata",default="feeds/elcinema.json")
    p.add_argument("--output",default="reports/elcinema-per-id-readiness.json")
    a=p.parse_args()
    report=verified_report(a.feed,a.metadata)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("ELCINEMA_PER_ID_QUALITY",json.dumps({
        "source_sha256":report["source_sha256"],
        "source_age_hours":report["source_age_hours"],
        "channels":report["source_channels"],
        "counts":report["counts"],
        "synthetic_programmes_added":0
    }))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
