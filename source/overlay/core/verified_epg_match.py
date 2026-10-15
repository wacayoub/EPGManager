# -*- coding: utf-8 -*-
"""RC80 read-only Smart Mapping evidence bridge.

Only a freshly hash-verified public XMLTV audit can certify real ON-AIR EPG
for a direct feed ID.  UI call-sites must not initiate HTTP, XMLTV parsing,
import, or mapping writes.  The default state is UNKNOWN / review-only.
This module does not auto-assign or change any existing mapping.
"""
from __future__ import print_function
from datetime import datetime, timezone
import json
import os
import time

EVIDENCE_PATH = "/tmp/epgmanager_verified_evidence.json"
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_AGE_SECONDS = 20 * 60
CACHE_POLL_SECONDS = 30
VALID_DIRECT = frozenset((
    "morocco", "bein", "osn", "elcinema", "starzplay", "stctv",
    "shahid", "dubaiplus", "alkass", "sport24", "rotana",
    "aljazeera", "tabie", "tunisiatv",
))
_CACHE = {"next_check": 0.0, "mtime_ns": None, "report": None}


def _epoch(value):
    try:
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError, TypeError):
        return None


def _fresh(value, now, maximum=MAX_EVIDENCE_AGE_SECONDS):
    observed = _epoch(value)
    return observed is not None and 0 <= now - observed <= maximum


def source_name(source_id):
    """Only known *direct* sources map to this verified public namespace."""
    source_id = str(source_id or "")
    if not source_id.startswith("direct_"):
        return ""
    label = source_id[len("direct_"):]
    return label if label in VALID_DIRECT else ""


def load_local_report(path=EVIDENCE_PATH, now=None):
    """Bounded, no-network, read-only on-receiver evidence cache.

    A missing, symlinked, oversized or malformed report is NOT EPG proof.
    The timer stops even on a corrupt file; browsing never spins on retries.
    """
    if now is None:
        now = time.monotonic()
    if path == EVIDENCE_PATH and now < _CACHE["next_check"]:
        return _CACHE["report"]
    try:
        if os.path.islink(path):
            raise ValueError("symlinked audit evidence rejected")
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size > MAX_REPORT_BYTES:
            raise ValueError("invalid evidence file")
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))
        if path == EVIDENCE_PATH and mtime_ns == _CACHE["mtime_ns"]:
            _CACHE["next_check"] = now + CACHE_POLL_SECONDS
            return _CACHE["report"]
        with open(path, "rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("oversized evidence")
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict) or doc.get("audit") != "EPGManager RC80 public feed provenance":
            raise ValueError("unrecognized EPG audit")
        if doc.get("full_xmltv_integrity_checks_enabled") is not True:
            raise ValueError("metadata-only report")
        if not isinstance(doc.get("sources"), list):
            raise ValueError("missing feed evidence list")
    except (OSError, ValueError, UnicodeError, TypeError):
        doc = None
        mtime_ns = None
    if path == EVIDENCE_PATH:
        _CACHE.update({"next_check": now + CACHE_POLL_SECONDS,
                       "mtime_ns": mtime_ns, "report": doc})
    return doc


def quality_for_id(source_id, channel_id, report=None, now=None):
    """Evaluate a source *and* channel, never a provider-wide green status.

    This read-only classification intentionally cannot authorize auto mapping.
    The caller's existing country, identity, language and manual-lock checks
    remain authoritative. Unknown data must never look like real EPG.
    """
    result = {"status": "UNKNOWN", "verified_now": False, "rank": 3}
    source = source_name(source_id)
    cid = str(channel_id or "").strip()
    if not source or not cid:
        return result
    if report is None:
        report = load_local_report()
    if not isinstance(report, dict) or report.get("full_xmltv_integrity_checks_enabled") is not True:
        return result
    if report.get("audit") != "EPGManager RC80 public feed provenance":
        return result
    if now is None:
        now = time.time()
    if not _fresh(report.get("checked_utc"), now):
        return result
    feeds = report.get("sources")
    if not isinstance(feeds, list):
        return result
    hits = [row for row in feeds if isinstance(row, dict) and row.get("source") == source]
    if len(hits) != 1:
        return result
    row = hits[0]
    if (row.get("xmltv_sha256_verified") is not True or
            row.get("channel_specific_epg_evidence_verified") is not True or
            row.get("real_now_coverage_verified") is not True or
            not _fresh(row.get("per_id_evidence_observed_utc"), now)):
        return result
    if cid in (row.get("suspected_placeholder_epg_ids") or ()):
        return {"status": "SUSPECT_GENERATED_EPG", "verified_now": False, "rank": 5}
    if cid in (row.get("xmltv_observed_zero_ids") or ()):
        return {"status": "NO_EPG", "verified_now": False, "rank": 6}
    if (row.get("ready_for_epg_ok") is True and
            row.get("status") == "HASHED_XMLTV_AND_AUDITS_HEALTHY" and
            cid in (row.get("trusted_current_epg_ids") or ())):
        return {"status": "REAL_CURRENT_EPG", "verified_now": True, "rank": 0}
    if cid in (row.get("review_future_epg_ids") or ()):
        return {"status": "FUTURE_ONLY_REVIEW", "verified_now": False, "rank": 2}
    if cid in (row.get("xmltv_current_real_ids") or ()):
        return {"status": "CURRENT_UNVERIFIED", "verified_now": False, "rank": 4}
    return result


def annotate_suggestions(rows, report=None, now=None):
    """Return a preview-only copy: saved owners always stay at the top.

    A REAL_CURRENT_EPG with valid identity wins over equally trustworthy name
    matches. Existing manual/mapped ownership is not reclassified or written.
    Non-direct sources remain UNKNOWN rather than being marked NO EPG.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    if report is None:
        report = load_local_report()
    annotated = []
    for epg in rows:
        if not isinstance(epg, dict):
            continue
        item = dict(epg)
        if item.get("_suggestion_current"):
            item["_verified_epg_health"] = "SAVED_OWNER"
            item["_verified_epg_rank"] = -1
        else:
            evidence = quality_for_id(item.get("source_id"), item.get("channel_id"), report=report, now=now)
            item["_verified_epg_health"] = evidence["status"]
            item["_verified_epg_rank"] = evidence["rank"]
        annotated.append(item)
    annotated.sort(key=lambda epg: (
        0 if epg.get("_suggestion_current") else 1,
        int(epg.get("_verified_epg_rank", 3)),
        0 if epg.get("_precision_auto") else 1,
        -int(epg.get("_precision_confidence") or 0),
        0 if epg.get("_suggestion_active") else 1,
        str(epg.get("source_name") or epg.get("source_id") or "").casefold(),
        str(epg.get("display_name") or epg.get("channel_id") or "").casefold(),
    ))
    return annotated
