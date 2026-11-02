#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time guarded publication of verified, non-overlapping exact-ID ElCinema rows.

No receiver access, no synthetic EPG. Default is DRY RUN with shadow files only.
--apply requires three preaudited SHA256 values and measured improvement,
retains all original programme entries, channel IDs and provider generation age.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

try:
    import elcinema_exact_id_gap_shadow as donor
    import validate_48h
    import audit_source_quality
except ModuleNotFoundError:
    from tools import elcinema_exact_id_gap_shadow as donor
    from tools import validate_48h, audit_source_quality

ORIGINAL_SHA = {
    "elcinema": "525ed58676eb5528271b856946658d83f8fd88ecd3a86eaeafe612d0db2d7e0a",
    "shahid": "690424d7aeb472009fde84ec43165f932571b9b3d04b35bcacefa695acead357",
    "dubaiplus": "7ca05adc3062e8748a3390896e1ea3e0bc75b0015ff5000b36174b92677fbaa8",
}
MIN_NEW_ROWS = 100
MIN_IMPROVED_CHANNELS = 10
MIN_FEWER_FAILED = 8


def identity_rows(root):
    # Canonicalized exact XML for each programme, duplicates counted.
    return Counter(ET.tostring(p, encoding="utf-8") for p in root.findall("programme"))


def audit_and_prepare(root=".", apply=False):
    root = Path(root)
    now = datetime.now(timezone.utc)
    for source, expected in ORIGINAL_SHA.items():
        compressed = (root / "feeds" / (source + ".xml.gz")).read_bytes()
        sidecar = json.loads((root / "feeds" / (source + ".json")).read_text())
        if hashlib.sha256(compressed).hexdigest() != expected or sidecar.get("sha256") != expected:
            raise ValueError("SHA_LOCK_CHANGED:" + source)
    report = donor.build(root, now=now, shadow=True)
    if (report["donor_rows_appended"] < MIN_NEW_ROWS
            or report["channels_extended_count"] < MIN_IMPROVED_CHANNELS
            or report["synthetic_programmes"] or report["xmltv_id_rewrites"]):
        raise ValueError("NO_VERIFIED_COVERAGE_GAIN")
    shadow = root / "output/shadow"
    candidate = shadow / "elcinema-verified-gap-shadow.xml.gz"
    target = shadow / "elcinema.xml.gz"  # Same basename for 36h ElCinema validation profile.
    shutil.copyfile(candidate, target)
    before = root / "feeds/elcinema.xml.gz"
    initial = ET.fromstring(gzip.decompress(before.read_bytes()))
    modified = ET.fromstring(gzip.decompress(candidate.read_bytes()))
    old_channels = [c.get("id") for c in initial.findall("channel")]
    new_channels = [c.get("id") for c in modified.findall("channel")]
    if old_channels != new_channels or len(old_channels) != 97:
        raise ValueError("CHANNEL_IDS_OR_ORDER_CHANGED")
    old_rows, new_rows = identity_rows(initial), identity_rows(modified)
    if any(new_rows[row] < count for row, count in old_rows.items()):
        raise ValueError("ORIGINAL_PROGRAMME_MODIFIED_OR_LOST")
    if sum(new_rows.values()) - sum(old_rows.values()) != report["donor_rows_appended"]:
        raise ValueError("DONOR_COUNT_DOES_NOT_MATCH")
    if any(not any((t.text or "").strip() for t in p.findall("title"))
           for p in modified.findall("programme")):
        raise ValueError("PUBLISHED_EMPTY_TITLE")

    before_audit = validate_48h.audit(str(before), 48, 90)
    after_audit = validate_48h.audit(str(target), 48, 90)
    previous = {r["id"]: r for r in before_audit["rows"]}
    following = {r["id"]: r for r in after_audit["rows"]}
    if previous.keys() != following.keys():
        raise ValueError("AUDIT_ID_REGRESSION")
    for cid in previous:
        if following[cid]["future_hours"] + 0.02 < previous[cid]["future_hours"]:
            raise ValueError("HORIZON_REGRESSION:" + cid)
    recovered = before_audit["failed_channels"] - after_audit["failed_channels"]
    if recovered < MIN_FEWER_FAILED:
        raise ValueError("TOO_FEW_48H_FAILURES_RECOVERED")

    meta = json.loads((root / "feeds/elcinema.json").read_text(encoding="utf-8"))
    active, airing = set(), set()
    for p in modified.findall("programme"):
        cid = p.get("channel")
        active.add(cid)
        start, stop = donor.dt(p.get("start")), donor.dt(p.get("stop"))
        if start and stop and start <= now < stop:
            airing.add(cid)
    body = candidate.read_bytes()
    meta.update({
        "programmes": len(modified.findall("programme")),
        "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
        "active_channels": len(active),
        "zero_epg_channels": len(old_channels) - len(active),
        "zero_epg_ids": sorted(set(old_channels)-active),
        "coverage_pct": round(len(active)/len(old_channels)*100.0,1),
        "currently_on_air_channels": len(airing),
        "current_coverage_pct": round(len(airing)/len(old_channels)*100.0,1),
        "currently_uncovered_ids": sorted(set(old_channels)-airing),
        "repacked_utc": now.isoformat(),
        "original_published_sha256": ORIGINAL_SHA["elcinema"],
        "verified_donor_extensions": {
            "reason": "fill_nonoverlapping_real_xmltv_gaps_by_exact_id",
            "donors": ORIGINAL_SHA | {"elcinema": ORIGINAL_SHA["elcinema"]},
            "programmes_added": report["donor_rows_appended"],
            "channels_extended": report["channels_extended"],
            "no_fabricated_programmes": True,
        },
    })
    quality = audit_source_quality.analyze(str(target), "elcinema", now)
    comparison = {
        "audit": "ElCinema donor extension one-time publish candidate",
        "generated_utc": now.isoformat(), "production_changed": apply,
        "original_provider_generated_utc": meta["generated_utc"],
        "original_programmes_preserved": report["original_programmes_kept"],
        "added_real_programmes": report["donor_rows_appended"],
        "channels_extended": report["channels_extended_count"],
        "failure_count_before": before_audit["failed_channels"],
        "failure_count_after": after_audit["failed_channels"],
        "candidate_sha256": meta["sha256"],
        "all_ids_preserved": True, "synthetic_programmes": 0,
        "input_donor_sha_verified": True, "remaining_failures_are_not_suppressed": True,
    }
    # Reports and JSON are written to shadow for independent CI examination.
    (shadow / "elcinema.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2)+"\n")
    (shadow / "elcinema-quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2)+"\n")
    (shadow / "elcinema-48h-audit.json").write_text(
        json.dumps({"generated_utc": now.isoformat(), "mode": "source-aware",
                    "feeds": [dict(after_audit, feed="elcinema.xml.gz")]},
                   ensure_ascii=False, indent=2)+"\n")
    (shadow / "elcinema-exact-donor.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2)+"\n")
    if apply:
        # Only exact feed + metadata + reports. Source filenames and IDs unchanged.
        for dst, src in (
            ("feeds/elcinema.xml.gz", "elcinema.xml.gz"),
            ("feeds/elcinema.json", "elcinema.json"),
            ("reports/elcinema-quality.json", "elcinema-quality.json"),
            ("reports/elcinema-48h-audit.json", "elcinema-48h-audit.json"),
            ("reports/elcinema-exact-donor.json", "elcinema-exact-donor.json"),
        ):
            shutil.copyfile(shadow / src, root / dst)
    return comparison


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".")
    p.add_argument("--apply", action="store_true", help="Publish only after pinned SHA and improvement checks; source repo only")
    a = p.parse_args()
    try:
        print(json.dumps(audit_and_prepare(a.root, apply=a.apply), indent=2, ensure_ascii=False))
    except (OSError, ValueError, TypeError, ET.ParseError) as error:
        print(json.dumps({"status": "REFUSED", "error_type": type(error).__name__,
                          "reason": str(error)[:130], "production_changed": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
