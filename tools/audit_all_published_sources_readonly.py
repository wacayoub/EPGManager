#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only verification of EVERY published direct source in EPG-Scrapers.

Assesses public XMLTV checksums, report cohorts, stale publications,
missing per-ID EPG, and flagged placeholder-looking content. No network, no
scraping, no EPG import, no feed changes, no auto mappings and no false green.
A report is informative even when a source has useful channels and fails 48h.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

SOURCES = (
    "morocco", "bein", "osn", "elcinema", "starzplay", "stctv", "shahid",
    "dubaiplus", "sport24", "rotana", "aljazeera", "alkass", "tabie",
    "tunisiatv",
)
FRESH_SECONDS = 72 * 3600
COHORT_SECONDS = 15 * 60
MAX_GZIP_BYTES = 12 * 1024 * 1024
SOURCE_LIMITS = {"stctv": 3}  # intended source scope: STARZPLAY Sports 1-3
HARD = frozenset(("MISSING_REPORT", "INVALID_PUBLIC_METADATA", "GZIP_SHA_MISMATCH",
                  "STALE_PUBLISHED_DATA", "OUT_OF_COHORT_REPORT", "BAD_REPORT_SOURCE"))
RISK = frozenset(("ZERO_EPG_IDS", "48H_FAILED_IDS", "SUSPECT_PLACEHOLDER_IDS",
                  "MISSING_DESCRIPTIONS", "SHORT_48H_COVERAGE", "UNEXPECTED_SOURCE_SCOPE"))


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return x.astimezone(timezone.utc)


def _read_json(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("non-object JSON")
    return data


def assess(source, root, now):
    """Single feed check. Never treat report pass as a valid channel-level EPG."""
    if source not in SOURCES:
        raise ValueError("unsupported source")
    root = Path(root)
    checks = []
    def flag(code):
        if code not in checks:
            checks.append(code)

    result = {
        "source": source,
        "channels": None, "channels_with_programmes": None,
        "programme_rows": None, "48h_failed_ids": None,
        "48h_warning_ids": None, "no_future_epg_ids": None,
        "suspect_placeholder_ids": None, "future_rows_missing_description": None,
        "published_age_hours": None, "feed_sha256_matches": None,
        "audit_status": "UNVERIFIED", "quality_status": "UNVERIFIED",
        "programme_provenance_certified": False,
        "receiver_cache_validated": False,
    }
    base = root / "feeds" / source
    try:
        meta = _read_json(base.with_suffix(".json"))
        xml = base.with_suffix(".xml.gz")
        if not xml.is_file() or xml.is_symlink():
            raise ValueError("not a regular published feed")
        raw = xml.read_bytes()
        if not 0 < len(raw) <= MAX_GZIP_BYTES:
            raise ValueError("missing or oversized published gzip")
        h = meta.get("sha256")
        sz = meta.get("size_bytes")
        if not isinstance(h, str) or len(h) != 64 or not isinstance(sz, int):
            flag("INVALID_PUBLIC_METADATA")
        elif h != hashlib.sha256(raw).hexdigest() or len(raw) != sz:
            flag("GZIP_SHA_MISMATCH")
            result["feed_sha256_matches"] = False
        else:
            result["feed_sha256_matches"] = True
        start = _utc(meta.get("generated_utc"))
        age = (now - start).total_seconds()
        result["published_age_hours"] = round(age / 3600, 1)
        if age < -3600 or age > FRESH_SECONDS:
            flag("STALE_PUBLISHED_DATA")
        ch = meta.get("channels")
        active = meta.get("active_channels")
        count = meta.get("programmes")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (ch, active, count)) or active > ch:
            flag("INVALID_PUBLIC_METADATA")
        else:
            result["channels"] = ch
            result["channels_with_programmes"] = active
            result["programme_rows"] = count
        if ch is not None and source in SOURCE_LIMITS and ch > SOURCE_LIMITS[source]:
            flag("UNEXPECTED_SOURCE_SCOPE")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        flag("INVALID_PUBLIC_METADATA")
        start = None
    try:
        audit = _read_json(root / "reports" / (source + "-48h-audit.json"))
        quality = _read_json(root / "reports" / (source + "-quality.json"))
        at = _utc(audit.get("generated_utc"))
        qt = _utc(quality.get("generated_utc"))
        cohort_start = start
        if source == "elcinema" and meta.get("repacked_utc"):
            # Donor extension preserves provider age. Its new byte cohort has
            # a separate timestamp, accepted only with matching provenance.
            donor = _read_json(root / "reports/elcinema-daily-donor.json")
            repacked = _utc(meta["repacked_utc"])
            verified = donor.get("verified_provider_sha256") or {}
            if (result["feed_sha256_matches"] is True
                    and donor.get("output_sha256") == meta.get("sha256")
                    and verified.get("elcinema") == meta.get("original_published_sha256")
                    and isinstance(verified.get("elcinema"), str)
                    and donor.get("provider_generated_utc") == meta.get("generated_utc")
                    and donor.get("preserved_original_programmes") is True
                    and donor.get("generated_or_guessed_programmes") == 0
                    and start is not None and start <= repacked <= now):
                cohort_start = repacked
        if cohort_start is None or abs((at - cohort_start).total_seconds()) > COHORT_SECONDS or abs((qt - cohort_start).total_seconds()) > COHORT_SECONDS:
            flag("OUT_OF_COHORT_REPORT")
        match = [x for x in audit.get("feeds", []) if isinstance(x, dict) and x.get("feed") == source + ".xml.gz"]
        if len(match) != 1 or quality.get("source") != source or quality.get("source_file") != source+".xml.gz":
            flag("BAD_REPORT_SOURCE")
        else:
            row = match[0]
            result["audit_status"] = row.get("status", "UNVERIFIED")
            result["quality_status"] = quality.get("quality_status", "UNVERIFIED")
            bad = int(row.get("failed_channels") or 0)
            warn = int(row.get("warning_channels") or 0)
            zeros = quality.get("channels_without_future_epg")
            suspicious = quality.get("suspected_placeholder_channels")
            if not isinstance(zeros, list) or not isinstance(suspicious, list):
                flag("BAD_REPORT_SOURCE")
            else:
                result["no_future_epg_ids"] = len(zeros)
                result["suspect_placeholder_ids"] = len(suspicious)
                if zeros:
                    flag("ZERO_EPG_IDS")
                if suspicious:
                    flag("SUSPECT_PLACEHOLDER_IDS")
            result["48h_failed_ids"] = bad
            result["48h_warning_ids"] = warn
            if bad:
                flag("48H_FAILED_IDS")
            if warn:
                flag("SHORT_48H_COVERAGE")
            result["future_rows_missing_description"] = int(quality.get("future_programmes_without_description") or 0)
            if result["future_rows_missing_description"]:
                flag("MISSING_DESCRIPTIONS")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        flag("MISSING_REPORT")
    result["issues"] = sorted(checks)
    result["release_health"] = (
        "BLOCKED" if any(t in HARD for t in checks)
        else "REVIEW" if any(t in RISK for t in checks)
        else "NO_RED_FLAGS_IN_SUMMARIES"
    )
    return result


def run(root, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC clock required")
    details = [assess(x, root, now.astimezone(timezone.utc)) for x in SOURCES]
    return {
        "audit": "EPG-Scrapers published sources health — read only",
        "checked_utc": now.astimezone(timezone.utc).isoformat(),
        "source_count": len(details),
        "source_release_blocked_count": sum(x["release_health"] == "BLOCKED" for x in details),
        "sources_needing_review_count": sum(x["release_health"] == "REVIEW" for x in details),
        "provider_metadata_not_equivalent_to_native_epg": True,
        "receiver_modified": False, "published_feeds_modified": False,
        "auto_mappings_changed": False, "release_authorized": False,
        "sources": details
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".", help="Path to a local repository checkout")
    args = ap.parse_args()
    print(json.dumps(run(Path(args.repo)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
