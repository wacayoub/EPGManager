#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public XMLTV provenance gate: read-only, metadata-only, no receiver secrets.

Reports whether the actually published feed and corresponding 48h/quality
audits appear time-aligned with the same publication. A fresh quality audit of a rejected candidate
does NOT certify the older last-known-good public XMLTV.
Default mode is offline examples; --online makes bounded public HTTP GETs.
Live-now coverage is NOT verified without --verify-xmltv and direct hash-checked XMLTV parsing.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import re
from urllib.request import Request, urlopen
from tools import audit_published_xmltv as payload_audit

BASE = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/"
SOURCES = ("morocco", "bein", "elcinema", "osn", "sport24", "dubaiplus",
           "starzplay", "shahid", "rotana", "stctv", "aljazeera", "alkass",
           "tunisiatv", "tabie")
MAX_BYTES = 300000
MAX_AGE_HOURS = 72
MAX_COHORT_DELTA = 3600


def epoch(value):
    try:
        s = str(value or "")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        value = datetime.fromisoformat(s)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def assess(source, feed, audit, quality, now=None):
    """Pure, bounded, honest classification: quality unknown is not failure."""
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    result = {"source": source, "published_generated_utc": feed.get("generated_utc"),
              "audit_generated_utc": audit.get("generated_utc"),
              "quality_generated_utc": quality.get("generated_utc"),
              "pubcohort_verified": False, "quality_cohort_verified": False,
              "real_now_coverage_verified": False, "ready_for_epg_ok": False,
              "xmltv_sha256_verified": False,
              "cohort_match_proof": "TIMESTAMP_ONLY_NOT_HASH_BOUND"}
    pg = epoch(feed.get("generated_utc"))
    ag = epoch(audit.get("generated_utc"))
    qg = epoch(quality.get("generated_utc"))
    if (not pg or now < pg - 3600 or now - pg > MAX_AGE_HOURS * 3600):
        result["status"] = "STALE_OR_INVALID_PUBLISHED_FEED"
        return result
    digest = str(feed.get("sha256") or "").lower()
    try:
        size = int(feed.get("size_bytes") or 0)
    except (ValueError, TypeError, OverflowError):
        size = 0
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or size <= 0:
        result["status"] = "INVALID_PUBLISHED_METADATA"
        return result
    rows = audit.get("feeds") or []
    matching = [x for x in rows if isinstance(x, dict) and x.get("feed") == source + ".xml.gz"]
    if not ag or abs(ag - pg) > MAX_COHORT_DELTA or len(matching) != 1:
        result["status"] = "AUDIT_FROM_DIFFERENT_PUBLICATION"
        return result
    result["pubcohort_verified"] = True
    if (quality.get("source") != source or
            quality.get("source_file") != source + ".xml.gz" or
            not qg or abs(qg - pg) > MAX_COHORT_DELTA):
        result["status"] = "QUALITY_FROM_DIFFERENT_PUBLICATION"
        return result
    result["quality_cohort_verified"] = True
    if matching[0].get("status") == "FAIL":
        result["status"] = "48H_AUDIT_FAIL"
        return result
    if quality.get("quality_status") not in ("PASS", "WARN"):
        result["status"] = "QUALITY_AUDIT_FAIL_OR_UNKNOWN"
        return result
    current = feed.get("currently_on_air_channels")
    active = feed.get("active_channels")
    if current is None or active is None:
        result["status"] = "PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN"
        return result
    try:
        current, active = int(current), int(active)
    except (ValueError, TypeError, OverflowError):
        result["status"] = "INVALID_LIVE_COVERAGE"
        return result
    if active < 1 or current < 0 or current > active:
        result["status"] = "INVALID_LIVE_COVERAGE"
        return result
    result["reported_live_now_channels"] = current
    result["reported_active_channels"] = active
    result["coverage_reported_at_publication"] = True
    if current * 100 < active * 60:
        result["status"] = "PUBLISHED_NOW_COVERAGE_LOW"
    else:
        result["status"] = "PUBLICATION_METADATA_COHERENT"
        result["ready_for_epg_ok"] = False  # Only actual verified XMLTV qualifies.
    return result


def _get_json(path):
    request = Request(BASE + path, headers={"User-Agent": "EPGManager-RC80-ReadOnlyAudit/1.0",
                                            "Cache-Control": "no-cache"})
    with urlopen(request, timeout=8) as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("public JSON exceeds allowed size")
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def verify_payload(source, feed, measured_at, reader=payload_audit.fetch):
    """Check the actual published gzip bytes, not last-run metadata.

    audit_xml also checks every programme's timestamps/titles and the live
    current-window coverage; neither a GitHub workflow nor a timestamp-aligned
    JSON report alone can establish an EPG_OK state.
    """
    raw = reader(payload_audit.BASE + source + ".xml.gz", payload_audit.MAX_GZIP)
    report = payload_audit.audit_xml(
        raw, feed, datetime.fromtimestamp(measured_at, tz=timezone.utc))
    return {
        "xmltv_sha256_verified": True,  # audit_xml raised on SHA/size mismatch.
        "xmltv_checked_at_utc": datetime.fromtimestamp(
            measured_at, tz=timezone.utc).isoformat(),
        "xmltv_active_channels": report["active_channels"],
        "xmltv_live_now_channels": report["channels_with_current_programme"],
        "xmltv_missing_titles": report["missing_titles"],
        "xmltv_bad_times": report["bad_times"],
        "xmltv_issues": report["issues"],
        "xmltv_content_pass": report["pass"],
        "real_now_coverage_verified": True,
    }


def fetch_one(source, now, verify_xmltv=False):
    try:
        feed = _get_json("feeds/" + source + ".json")
        audit = _get_json("reports/" + source + "-48h-audit.json")
        quality = _get_json("reports/" + source + "-quality.json")
        result = assess(source, feed, audit, quality, now=now)
        if verify_xmltv:
            try:
                verified = verify_payload(source, feed, now)
                result.update(verified)
                # No successful "now" verdict without a downloaded, hashed,
                # fully parsed public feed AND matching, healthy audit metadata.
                result["ready_for_epg_ok"] = bool(
                    result["status"] in (
                        "PUBLICATION_METADATA_COHERENT",
                        "PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN",
                        "PUBLISHED_NOW_COVERAGE_LOW",
                    )
                    and verified["xmltv_content_pass"]
                    and verified["xmltv_sha256_verified"]
                )
                if not verified["xmltv_content_pass"]:
                    result["status"] = "XMLTV_CONTENT_QUALITY_ISSUES"
                elif result["ready_for_epg_ok"]:
                    result["status"] = "HASHED_XMLTV_AND_AUDITS_HEALTHY"
            except Exception as exc:
                result.update({
                    "status": "XMLTV_DOWNLOAD_OR_INTEGRITY_UNVERIFIED",
                    "xmltv_error_type": type(exc).__name__,
                    "xmltv_sha256_verified": False,
                    "real_now_coverage_verified": False,
                    "ready_for_epg_ok": False,
                })
        return result
    except Exception as err:
        return {"source": source, "status": "NETWORK_OR_FORMAT_UNVERIFIED",
                "error_type": type(err).__name__, "pubcohort_verified": False,
                "quality_cohort_verified": False, "real_now_coverage_verified": False,
                "ready_for_epg_ok": False}


def run_online(now=None, verify_xmltv=False):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(fetch_one, name, now, verify_xmltv): name for name in SOURCES}
        result = [future.result() for future in as_completed(tasks)]
    result.sort(key=lambda row: SOURCES.index(row["source"]))
    return {"audit": "EPGManager RC80 public feed provenance",
            "read_only": True, "receiver_modified": False,
            "install_authorized": False, "sources": result,
            "publication_cohorts_verified": sum(bool(x["pubcohort_verified"]) for x in result),
            "live_now_evidence_present": sum(bool(x["real_now_coverage_verified"]) for x in result),
            "live_now_not_claimed_when_missing": True,
            "full_xmltv_integrity_checks_enabled": bool(verify_xmltv),
            "xmltv_sha256_verified_count": sum(bool(x.get("xmltv_sha256_verified")) for x in result),
            "epg_ready_count": sum(bool(x.get("ready_for_epg_ok")) for x in result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Read 3 public GitHub JSON files per source; no changes")
    parser.add_argument("--verify-xmltv", action="store_true", help="Download hash-checked gzip and audit actual programme coverage; requires --online")
    args = parser.parse_args()
    if args.verify_xmltv and not args.online:
        parser.error("--verify-xmltv requires --online")
    if not args.online:
        parser.print_help()
        return 0
    print(json.dumps(run_online(verify_xmltv=args.verify_xmltv), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
