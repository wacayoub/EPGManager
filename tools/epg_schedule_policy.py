#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Select degraded Shahid-only EPG for the 15:00 Casablanca retry.

Pure local metadata logic. A strict 48-hour failure by itself is NOT a reason
to re-scrape every day, since some official guides naturally have shorter
horizons. No network, no receiver, no source writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

CASABLANCA = ZoneInfo("Africa/Casablanca")
# ElCinema 01:20, OSN 01:40, beIN 02:22 Casablanca daily.
# Their 15:00 automatic retries are permanently disabled to avoid duplicates.
TARGETS = ("shahid",)
MIN_ON_AIR_AT_PUBLICATION = 0.60
MAX_RETRIES = 1


def local_date(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(CASABLANCA).date()
    except (ValueError, TypeError, OverflowError):
        return None


def inspect_one(name, root, today):
    base = Path(root)
    meta = base / "feeds" / (name + ".json")
    if not meta.is_file():
        return {"source": name, "retry": True, "reason": "PUBLISHED_FEED_METADATA_MISSING"}
    try:
        feed = json.loads(meta.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        return {"source": name, "retry": True, "reason": "PUBLISHED_METADATA_UNREADABLE"}
    # The real original provider generation is authoritative, never repacked_utc.
    stamp = feed.get("generated_utc") or feed.get("generated")
    if local_date(stamp) != today:
        return {"source": name, "retry": True, "reason": "PROVIDER_SOURCE_NOT_GENERATED_TODAY"}
    lkg = base / "reports" / (name + "-publish-lkg.json")
    if lkg.is_file():
        try:
            report = json.loads(lkg.read_text(encoding="utf-8"))
            item = (report.get("sources") or {}).get(name) or {}
            if local_date(report.get("generated_utc")) == today and item.get("decision") == "KEEP_LKG":
                return {"source": name, "retry": True, "reason": "TODAY_SOURCE_CANDIDATE_REJECTED"}
        except (ValueError, OSError, UnicodeError):
            pass
    try:
        airing = feed.get("currently_on_air_channels")
        active = feed.get("active_channels")
        if airing is not None and active is not None:
            airing, active = int(airing), int(active)
            if active > 0 and 0 <= airing <= active and (
                    airing / active < MIN_ON_AIR_AT_PUBLICATION):
                return {"source": name, "retry": True, "reason": "LOW_REAL_NOW_COVERAGE_AT_PUBLICATION"}
    except (ValueError, TypeError, OverflowError):
        pass
    return {"source": name, "retry": False, "reason": "PUBLISHED_SOURCE_ACCEPTABLE"}


def plan(root, now=None, main_chain_active=False):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have timezone")
    today = now.astimezone(CASABLANCA).date()
    outcomes = [inspect_one(name, root, today) for name in TARGETS]
    if main_chain_active:
        return {
            "evaluated_date_casablanca": str(today),
            "main_chain_active": True,
            "targets": [],
            "details": outcomes,
            "skip_reason": "MAIN_CHAIN_STILL_ACTIVE_NO_CONCURRENT_RETRY",
        }
    chosen = [r["source"] for r in outcomes if r["retry"]][:MAX_RETRIES]
    return {
        "evaluated_date_casablanca": str(today),
        "main_chain_active": False,
        "targets": chosen,
        "details": outcomes,
        "skip_reason": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--active-chain", action="store_true")
    parser.add_argument("--github-output", default="", help="Append safe matrix to GitHub GITHUB_OUTPUT")
    a = parser.parse_args()
    report = plan(a.root, main_chain_active=a.active_chain)
    if a.github_output:
        p = Path(a.github_output)
        with p.open("a", encoding="utf-8") as f:
            f.write("retry_count=%d\n" % len(report["targets"]))
            f.write("retry_sources=%s\n" % json.dumps(report["targets"], separators=(",", ":")))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
