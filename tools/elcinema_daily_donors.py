#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeatable ElCinema extension AFTER today's Shahid + Dubai+ updates.

Only exact shared XMLTV IDs and genuine title-bearing nonoverlapping donor
programmes are accepted; original nodes, source times and source age stay
unchanged. Validates today's original provider generations and actual gzip
hashes before processing. Default is dry run. No scraping, fake EPG, SSH or
receiver access. Production output changes only with explicit --apply.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

try:
    import elcinema_exact_id_gap_shadow as donor
    import validate_48h
    import audit_source_quality
except ModuleNotFoundError:
    from tools import elcinema_exact_id_gap_shadow as donor
    from tools import validate_48h, audit_source_quality

TZ = ZoneInfo("Africa/Casablanca")
INPUTS = ("elcinema", "shahid", "dubaiplus")
MIN_IMPROVEMENT_EVENTS = 1


def identity(p):
    return ET.tostring(p, encoding="utf-8")


def gen_date(raw):
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("NAIVE_PROVIDER_GENERATION")
    return value.astimezone(TZ).date()


def inspect_inputs(root, now):
    """Verified same-local-day source, no reserialized feed as a fake refresh."""
    stamp = now.astimezone(TZ).date()
    meta = {}
    for name in INPUTS:
        _, info, _ = donor.payload(root, name, now)  # SHA, size, IDs, safe gzip verified
        if gen_date(info.get("generated_utc")) != stamp:
            raise ValueError("SOURCE_NOT_UPDATED_TODAY:" + name)
        meta[name] = info
    return meta


def prepare(root=".", now=None, apply=False):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    source = inspect_inputs(root, now)
    summary = donor.build(root, now=now, shadow=True)
    shadow_dir = root / "output" / "shadow"
    candidate = shadow_dir / "elcinema-verified-gap-shadow.xml.gz"
    old_file = root / "feeds" / "elcinema.xml.gz"
    old = ET.fromstring(gzip.decompress(old_file.read_bytes()))
    new = ET.fromstring(gzip.decompress(candidate.read_bytes()))
    old_ids = [x.get("id") for x in old.findall("channel")]
    new_ids = [x.get("id") for x in new.findall("channel")]
    old_events = old.findall("programme")
    new_events = new.findall("programme")
    if len(old_ids) < 1 or old_ids != new_ids:
        raise ValueError("CHANNEL_CATALOGUE_CHANGED")
    old_rows = Counter(identity(x) for x in old_events)
    new_rows = Counter(identity(x) for x in new_events)
    if any(new_rows[key] < qty for key, qty in old_rows.items()):
        raise ValueError("ORIGINAL_EVENT_CHANGED")
    added = sum(new_rows.values()) - sum(old_rows.values())
    if added != summary["donor_rows_appended"]:
        raise ValueError("INCORRECT_ADDED_EVENT_COUNT")
    for p in new_events:
        if not any((title.text or "").strip() for title in p.findall("title")):
            raise ValueError("EMPTY_PROGRAMME_TITLE_IN_CANDIDATE")
    if added < MIN_IMPROVEMENT_EVENTS:
        return {
            "status": "NO_NEW_REAL_EVENTS", "source": "elcinema",
            "receiver_modified": False, "source_modified": False,
            "sources_verified_for_local_day": True,
            "donor_rows_added": 0,
        }

    compare = shadow_dir / "elcinema.xml.gz"
    shutil.copyfile(candidate, compare)
    before = validate_48h.audit(str(old_file), 48, 90)
    after = validate_48h.audit(str(compare), 48, 90)
    old_results = {x["id"]: x for x in before["rows"]}
    new_results = {x["id"]: x for x in after["rows"]}
    if old_results.keys() != new_results.keys():
        raise ValueError("AUDIT_CATALOGUE_CHANGED")
    for channel in old_results:
        if new_results[channel]["future_hours"] + 0.02 < old_results[channel]["future_hours"]:
            raise ValueError("EPG_HORIZON_REGRESSION:" + channel)
    if after["failed_channels"] > before["failed_channels"]:
        raise ValueError("STRICT_48H_FAILURE_REGRESSION")

    sidecar = dict(source["elcinema"])
    compressed = candidate.read_bytes()
    active, on_air = set(), set()
    for p in new_events:
        cid = p.get("channel")
        active.add(cid)
        start, end = donor.dt(p.get("start")), donor.dt(p.get("stop"))
        if start and end and start <= now < end:
            on_air.add(cid)
    old_sha = sidecar["sha256"]
    sidecar.update({
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "size_bytes": len(compressed),
        "programmes": len(new_events),
        "active_channels": len(active),
        "zero_epg_channels": len(old_ids) - len(active),
        "zero_epg_ids": sorted(set(old_ids) - active),
        "coverage_pct": round(100 * len(active) / len(old_ids), 1),
        "currently_on_air_channels": len(on_air),
        "current_coverage_pct": round(100 * len(on_air) / len(old_ids), 1),
        "currently_uncovered_ids": sorted(set(old_ids) - on_air),
        # generated_utc MUST remain the original ElCinema scrape time.
        "repacked_utc": now.isoformat(),
        "original_published_sha256": old_sha,
        "verified_donor_extensions": {
            "mode": "daily_after_donor_refresh_exact_id_no_overlap",
            "donor_sha256": {name: source[name]["sha256"] for name in ("shahid", "dubaiplus")},
            "programmes_added_this_pass": added,
            "channels_extended": summary["channels_extended"],
            "fabricated_programmes": 0,
        },
    })
    quality = audit_source_quality.analyze(str(compare), "elcinema", now)
    publish_report = {
        "status": "READY_TO_PUBLISH" if apply else "DRY_RUN_VALIDATED",
        "read_only_receiver": True,
        "source_modified": bool(apply),
        "sources_verified_for_local_day": True,
        "provider_generated_utc": source["elcinema"]["generated_utc"],
        "verified_provider_sha256": {n: source[n]["sha256"] for n in INPUTS},
        "original_elcinema_programmes": len(old_events),
        "total_elcinema_programmes": len(new_events),
        "donor_rows_added": added,
        "channels_extended": summary["channels_extended_count"],
        "strict_failed_before": before["failed_channels"],
        "strict_failed_after": after["failed_channels"],
        "output_sha256": sidecar["sha256"],
        "preserved_original_programmes": True,
        "generated_or_guessed_programmes": 0,
    }
    payloads = {
        "feeds/elcinema.xml.gz": compressed,
        "feeds/elcinema.json": (json.dumps(sidecar, ensure_ascii=False, indent=2)+"\n").encode(),
        "reports/elcinema-48h-audit.json": (
            json.dumps({"generated_utc": now.isoformat(), "mode": "source-aware",
                        "feeds": [dict(after, feed="elcinema.xml.gz")]},
                       ensure_ascii=False, indent=2)+"\n").encode(),
        "reports/elcinema-quality.json": (
            json.dumps(quality, ensure_ascii=False, indent=2)+"\n").encode(),
        "reports/elcinema-daily-donor.json": (
            json.dumps(publish_report, ensure_ascii=False, indent=2)+"\n").encode(),
    }
    # All checks done before writing production paths.
    if apply:
        for rel, data in payloads.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    return publish_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true",
                        help="Update only ElCinema published gzip/sidecar/reports")
    ns = parser.parse_args()
    try:
        print(json.dumps(prepare(root=ns.root, apply=ns.apply),
                         indent=2, ensure_ascii=False, sort_keys=True))
    except (ValueError, TypeError, OSError, ET.ParseError) as err:
        print(json.dumps({
            "status": "REFUSED", "error_type": type(err).__name__,
            "reason": str(err)[:120], "receiver_modified": False,
        }, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
