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
try:
    import audit_published_xmltv as payload_audit  # python tools/audit_rc80_public_feed_provenance.py
except ModuleNotFoundError:
    from tools import audit_published_xmltv as payload_audit  # unittest from repository root

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



def _safe_partial_48h_ids(source, feed, row):
    """Split per-ID audited FAIL into empty and incomplete-but-real guides.

    OSN's official Legacy services can have zero programmes. ElCinema has
    authentic current events on 97/97 services, but some have short horizons
    and multi-hour internal gaps. Such IDs may be shown for manual review,
    NEVER marked REAL_CURRENT_EPG or auto-mapped as high-quality.

    Require an exhaustive, unique per-ID audit with clean source-wide times,
    known failure reasons only, and a matching failure count. Do not let a
    summary from another source bypass this gate.
    """
    if not isinstance(row, dict) or row.get("status") != "FAIL":
        return None
    count = feed.get("channels")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return None
    if row.get("channels") != count:
        return None
    if row.get("invalid_times") != 0 or row.get("orphan_programmes") != 0:
        return None
    rows = row.get("rows")
    if not isinstance(rows, list) or len(rows) != count:
        return None
    seen, zero, short = set(), set(), set()
    for item in rows:
        if not isinstance(item, dict):
            return None
        cid = item.get("id")
        status = item.get("status")
        if (not isinstance(cid, str) or not cid.strip() or cid in seen or
                status not in ("PASS", "WARN", "FAIL")):
            return None
        seen.add(cid)
        if status != "FAIL":
            continue
        reason = item.get("reason")
        if source in ("osn", "elcinema") and reason == "NO_FUTURE_EPG":
            # An actually empty ElCinema service is not a short-but-real
            # programme and must never grant an automatic green candidate.
            if source == "elcinema" and (item.get("future_hours") != 0 or
                                         item.get("max_gap_minutes") is not None):
                return None
            zero.add(cid)
        elif source == "elcinema" and reason in ("TOO_SHORT", "TOO_SHORT+INTERNAL_GAP"):
            # ElCinema's published minimum is 36h, NOT 48h. After the
            # corrected October 17 broadcast-date anchor its remaining
            # incomplete guides end at ~28h, gap=0. These are real but
            # not complete and must be yellow/manual rather than making
            # the other current IDs globally untrusted.
            future = item.get("future_hours")
            gap = item.get("max_gap_minutes")
            if (not isinstance(future, (int, float)) or isinstance(future, bool)
                    or not 0 < future < 36
                    or not isinstance(gap, (int, float)) or isinstance(gap, bool)
                    or gap < 0):
                return None
            if reason == "TOO_SHORT" and gap > 90:
                return None
            if reason == "TOO_SHORT+INTERNAL_GAP" and gap <= 90:
                return None
            short.add(cid)
        else:
            return None
    failed = row.get("failed_channels")
    if (not zero and not short) or not isinstance(failed, int) or isinstance(failed, bool):
        return None
    if failed != len(zero) + len(short):
        return None
    return {"zero": zero, "short": short}


def _elcinema_extreme_gap_ids(feed, report, source="elcinema"):
    """A source-wide WARN is NOT proof of per-channel continuity.

    ElCinema: flag >=12h WARN gaps; Shahid: flag >180min WARN gaps.
    Require exhaustive, unique channel-level rows and fail closed if invalid.
    Other actual guides retain their verified current/green status.
    """
    if report.get("status") not in ("PASS", "WARN", "FAIL"):
        return None
    count = feed.get("channels")
    rows = report.get("rows")
    if (not isinstance(count, int) or isinstance(count, bool) or count < 1
            or not isinstance(rows, list) or len(rows) != count
            or report.get("invalid_times") != 0
            or report.get("orphan_programmes") != 0):
        return None
    seen, flagged = set(), set()
    failures = 0
    for item in rows:
        if not isinstance(item, dict):
            return None
        cid, status = item.get("id"), item.get("status")
        if (not isinstance(cid, str) or not cid.strip() or cid in seen or
                status not in ("PASS", "WARN", "FAIL")):
            return None
        seen.add(cid)
        if status == "FAIL":
            # Failed rows are quarantined separately as NO_EPG / TOO_SHORT.
            # They must not hide extreme gaps in *other*, genuine WARN IDs.
            failures += 1
            continue
        gap = item.get("max_gap_minutes")
        if status == "WARN":
            if (not isinstance(gap, (int, float)) or isinstance(gap, bool)
                    or gap < 0):
                return None
            threshold = 720 if source == "elcinema" else 180
            if (gap >= threshold if source == "elcinema" else gap > threshold):
                flagged.add(cid)
    declared = report.get("failed_channels")
    if (not isinstance(declared, int) or isinstance(declared, bool)
            or declared != failures or
            (report.get("status") == "FAIL") != bool(failures)):
        return None
    return flagged


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
              "cohort_match_proof": "TIMESTAMP_ONLY_NOT_HASH_BOUND",
              "trusted_current_epg_ids": [], "review_future_epg_ids": [],
              "suspected_placeholder_epg_ids": []}
    pg = epoch(feed.get("generated_utc"))  # Original provider age; NEVER replaced by repack time.
    repack = epoch(feed.get("repacked_utc")) if feed.get("repacked_utc") else None
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
    # A title-only cleanup can regenerate quality audits without a new
    # provider fetch. The SOURCE age still comes only from generated_utc;
    # compare reports against the repack instant only when its original SHA
    # is declared and the temporal order is sane. Never let repack refresh
    # a stale guide's age or declare EPG_OK without verifying XMLTV bytes.
    cohort_epoch = pg
    if feed.get("repacked_utc") is not None:
        source_sha = str(feed.get("original_published_sha256") or "").lower()
        if (not repack or repack < pg or repack > now + 3600
                or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
                or source_sha == digest):
            result["status"] = "INVALID_REPACK_PROVENANCE"
            return result
        cohort_epoch = repack
        result["provider_data_reused_after_title_cleanup"] = True
        result["cleanup_timestamp_utc"] = feed["repacked_utc"]
    rows = audit.get("feeds") or []
    matching = [x for x in rows if isinstance(x, dict) and x.get("feed") == source + ".xml.gz"]
    if not ag or abs(ag - cohort_epoch) > MAX_COHORT_DELTA or len(matching) != 1:
        result["status"] = "AUDIT_FROM_DIFFERENT_PUBLICATION"
        return result
    result["pubcohort_verified"] = True
    if (quality.get("source") != source or
            quality.get("source_file") != source + ".xml.gz" or
            not qg or abs(qg - cohort_epoch) > MAX_COHORT_DELTA):
        result["status"] = "QUALITY_FROM_DIFFERENT_PUBLICATION"
        return result
    result["quality_cohort_verified"] = True
    partial_zero_ids, partial_short_ids, long_gap_ids = set(), set(), set()
    if ((source == "elcinema" and matching[0].get("status") in ("PASS", "WARN", "FAIL"))
            or (source == "shahid" and matching[0].get("status") == "WARN")):
        observed = _elcinema_extreme_gap_ids(feed, matching[0], source=source)
        if observed is None:
            result["status"] = "48H_AUDIT_INCONSISTENT_IDS"
            return result
        long_gap_ids = observed
        result["partial_48h_large_gap_ids"] = sorted(long_gap_ids)
        result["partial_48h_blocked_ids"] = sorted(long_gap_ids)
    if matching[0].get("status") == "FAIL":
        verified_failures = _safe_partial_48h_ids(source, feed, matching[0])
        if verified_failures is None:
            result["status"] = "48H_AUDIT_FAIL"
            return result
        partial_zero_ids = verified_failures["zero"]
        partial_short_ids = verified_failures["short"]
        result["partial_48h_zero_ids"] = sorted(partial_zero_ids)
        result["partial_48h_short_ids"] = sorted(partial_short_ids)
        result["partial_48h_blocked_ids"] = sorted(partial_zero_ids | partial_short_ids | long_gap_ids)
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
        # Never call an audit with failed IDs globally healthy on metadata
        # alone. SHA-checked XMLTV must first prove the other programmes.
        result["status"] = ("48H_PARTIAL_IDS_NEED_XMLTV_PROOF"
                            if partial_short_ids or long_gap_ids else
                            "48H_PARTIAL_ZERO_IDS_NEED_XMLTV_PROOF"
                            if partial_zero_ids else "PUBLICATION_METADATA_COHERENT")
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
        # Exact online IDs are usable even if other catalogue IDs lack a
        # full 48h guide. They require a current, SHA-verified XMLTV parse;
        # they are NOT an entire-source stable/48h certification.
        "xmltv_current_real_ids": report["current_real_xmltv_ids"],
        "xmltv_future_real_ids": report["future_real_xmltv_ids"],
        "xmltv_observed_zero_ids": report["observed_zero_epg_ids"],
        "per_id_evidence_observed_utc": report["per_id_evidence_observed_utc"],
        "channel_specific_epg_evidence_verified": True,
        "xmltv_missing_titles": report["missing_titles"],
        "xmltv_bad_times": report["bad_times"],
        "xmltv_issues": report["issues"],
        "xmltv_content_pass": report["pass"],
        "real_now_coverage_verified": True,
    }


def mapping_quality_for_id(xmltv_id, evidence):
    """Fail-closed, per-channel recommendation from *published* feed evidence.

    REAL_CURRENT_EPG is the only status eligible for automatic assignment.
    Future-only programmes must be reviewed; a current event in an unverified
    or stale publication cannot silently become a working EPG mapping.
    Manual locks are outside this read-only classifier and remain untouched.
    """
    channel_id = str(xmltv_id or "").strip()
    if not channel_id:
        return {"status": "UNKNOWN", "auto_eligible": False}
    if channel_id in (evidence.get("suspected_placeholder_epg_ids") or []):
        return {"status": "SUSPECT_GENERATED_EPG", "auto_eligible": False}
    if channel_id in (evidence.get("trusted_current_epg_ids") or []):
        return {"status": "REAL_CURRENT_EPG", "auto_eligible": True}
    if channel_id in (evidence.get("review_partial_epg_ids") or []):
        return {"status": "PARTIAL_GUIDE_REVIEW", "auto_eligible": False}
    if channel_id in (evidence.get("review_future_epg_ids") or []):
        return {"status": "FUTURE_ONLY_REVIEW", "auto_eligible": False}
    if channel_id in (evidence.get("xmltv_observed_zero_ids") or []):
        return {"status": "NO_EPG", "auto_eligible": False}
    if channel_id in (evidence.get("xmltv_current_real_ids") or []):
        return {"status": "CURRENT_UNVERIFIED", "auto_eligible": False}
    return {"status": "UNKNOWN", "auto_eligible": False}


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
                allowed_cohort = (
                    result["status"] in (
                        "PUBLICATION_METADATA_COHERENT",
                        "PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN",
                        "PUBLISHED_NOW_COVERAGE_LOW",
                        "48H_PARTIAL_ZERO_IDS_NEED_XMLTV_PROOF",
                        "48H_PARTIAL_IDS_NEED_XMLTV_PROOF",
                    )
                    and result.get("pubcohort_verified") is True
                    and result.get("quality_cohort_verified") is True
                )
                hash_and_ids_verified = (
                    verified.get("xmltv_sha256_verified") is True
                    and verified.get("channel_specific_epg_evidence_verified") is True
                    and verified.get("real_now_coverage_verified") is True
                )
                current_ids = verified.get("xmltv_current_real_ids") or []
                future_ids = verified.get("xmltv_future_real_ids") or []
                # A source with 19 missing OSN IDs need not block 82 real
                # siblings. Its reported zero-ID set must match the actual
                # downloaded, SHA-verified XMLTV, and no reported failed ID
                # may have future/current events. Otherwise fail closed.
                zero_ids = set(result.get("partial_48h_zero_ids") or [])
                short_ids = set(result.get("partial_48h_short_ids") or [])
                long_gap_ids = set(result.get("partial_48h_large_gap_ids") or [])
                blocked_ids = zero_ids | short_ids | long_gap_ids
                if blocked_ids:
                    observed_zero = verified.get("xmltv_observed_zero_ids")
                    # Distinguish real-but-incomplete ElCinema schedules from
                    # empty OSN IDs. A short guide MUST contain future events.
                    if (not isinstance(observed_zero, list) or
                            set(observed_zero) != zero_ids or
                            zero_ids.intersection(current_ids) or
                            zero_ids.intersection(future_ids) or
                            not (short_ids | long_gap_ids).issubset(future_ids) or
                            (short_ids | long_gap_ids).intersection(observed_zero)):
                        allowed_cohort = False
                        result["status"] = ("48H_PARTIAL_IDS_XMLTV_MISMATCH"
                                            if short_ids or long_gap_ids else
                                            "48H_PARTIAL_ZERO_IDS_XMLTV_MISMATCH")
                    elif (allowed_cohort and hash_and_ids_verified and
                          verified.get("xmltv_content_pass") is True):
                        # Yellow/review status, not green auto-owner evidence.
                        result["review_partial_epg_ids"] = sorted(
                            (short_ids | long_gap_ids).intersection(current_ids))
                
                # Quality reports explicitly flag programme placeholders such
                # as channel-name-only schedule rows. A valid programme time
                # and matching XML checksum do NOT make such content genuine.
                # A missing/malformed quality signal is not certification.
                placeholder_rows = quality.get("suspected_placeholder_channels")
                quality_flags_complete = isinstance(placeholder_rows, list)
                placeholder_ids = set()
                if quality_flags_complete:
                    for row in placeholder_rows:
                        ident = row.get("id") if isinstance(row, dict) else row
                        if isinstance(ident, str) and ident.strip():
                            placeholder_ids.add(ident.strip())
                        else:
                            quality_flags_complete = False
                            break
                result["suspected_placeholder_epg_ids"] = sorted(placeholder_ids)

                # Do not expose untrusted/stale programme matches as automatic
                # recommendations, even when their XML is syntactically valid.
                if (allowed_cohort and hash_and_ids_verified
                        and quality_flags_complete
                        and verified.get("xmltv_content_pass") is True):
                    result["trusted_current_epg_ids"] = sorted(
                        {x for x in current_ids if isinstance(x, str) and x.strip()}
                        - placeholder_ids - blocked_ids)
                else:
                    result["trusted_current_epg_ids"] = []
                # Real future events can be suggested for manual review, never
                # auto-mapped when the channel has no programme on air NOW.
                if allowed_cohort and hash_and_ids_verified and quality_flags_complete:
                    result["review_future_epg_ids"] = sorted(
                        {x for x in future_ids if isinstance(x, str) and x.strip()}
                        - {x for x in current_ids if isinstance(x, str) and x.strip()}
                        - placeholder_ids - blocked_ids)
                else:
                    result["review_future_epg_ids"] = []
                result["ready_for_epg_ok"] = bool(result["trusted_current_epg_ids"])
                result["xmltv_content_status"] = (
                    "PASS" if verified["xmltv_content_pass"]
                    else "XMLTV_CONTENT_QUALITY_ISSUES"
                )
                # Keep more serious reasons (stale published feed, failed
                # 48h audit, mismatched candidate quality) as PRIMARY status.
                if not verified["xmltv_content_pass"] and result["status"] in (
                    "PUBLICATION_METADATA_COHERENT",
                    "PUBLICATION_COHORT_OK_LIVE_NOW_UNKNOWN",
                    "PUBLISHED_NOW_COVERAGE_LOW",
                ):
                    result["status"] = "XMLTV_CONTENT_QUALITY_ISSUES"
                elif result["ready_for_epg_ok"]:
                    result["status"] = ("HASHED_XMLTV_PER_ID_PARTIAL_48H"
                                        if blocked_ids else "HASHED_XMLTV_AND_AUDITS_HEALTHY")
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
            "evidence_schema": 1,
            "checked_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
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
