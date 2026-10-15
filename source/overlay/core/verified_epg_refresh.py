# -*- coding: utf-8 -*-
"""RC80 experimental non-blocking public-XMLTV evidence refresher.

Only EPG-Scrapers public feeds are read; no receiver config, EPGImport or
mapping file is accessed. No refresh is started unless explicitly enabled.
Background worker performs bounded public downloads and XMLTV parsing; the
Enigma2 UI thread only queues work. The published package remains NO-GO.
"""
from __future__ import print_function
from datetime import datetime, timezone
from io import BytesIO
from urllib.request import Request, urlopen
import gzip
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

from . import verified_epg_match as evidence

ROOT = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/"
JSON_LIMIT = 320 * 1024
GZIP_LIMIT = 4 * 1024 * 1024
XML_LIMIT = 24 * 1024 * 1024
REPORT_LIMIT = 2 * 1024 * 1024
SOURCE_MAX_AGE_HOURS = 72
COHORT_MAX_DELTA = 3600
REFRESH_COOLDOWN_SECONDS = 120
PER_SOURCE_COOLDOWN_SECONDS = 600
TIME_PATTERN = re.compile(r"^\d{14} [+-]\d{4}$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_BG_LOCK = threading.Lock()
_BG_STATE = {"busy": False, "last_started": 0, "source_at": {}, "last_error": ""}


def _utc(now):
    return datetime.fromtimestamp(now, tz=timezone.utc).isoformat()


def _epg_time(value):
    if not TIME_PATTERN.fullmatch(str(value or "")):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S %z").timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _download(path, limit):
    """GET public GitHub content with fixed time and response-size budgets."""
    if not path.startswith(("feeds/", "reports/")) or ".." in path or "?" in path:
        raise ValueError("unsafe public URL path")
    with urlopen(Request(ROOT + path, headers={
            "User-Agent": "EPGManager-RC80-Evidence/preview",
            "Cache-Control": "no-cache"}), timeout=10) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversized public feed")
    return data


def _json(path, reader):
    raw = reader(path, JSON_LIMIT)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid source metadata")
    return payload


class _CappedRead(object):
    """Stream decompression to XML parser; never buffer an entire large XML."""
    def __init__(self, stream, maximum=XML_LIMIT):
        self._stream = stream
        self._remaining = maximum
    def read(self, size=-1):
        if size is None or size < 0:
            size = 65536
        amount = min(size, self._remaining + 1)
        data = self._stream.read(amount)
        self._remaining -= len(data)
        if self._remaining < 0:
            raise ValueError("uncompressed XMLTV limit exceeded")
        return data


def _programmes(gz, observed):
    """Parse actual current programmes per XMLTV ID with constant XML memory."""
    channels = set()
    populated = set()
    on_air = set()
    future = set()
    missing_title = 0
    bad_time = 0
    events = 0
    with gzip.GzipFile(fileobj=BytesIO(gz)) as stream:
        bounded = _CappedRead(stream)
        root = None
        try:
            for event, node in ET.iterparse(bounded, events=("start", "end")):
                if root is None and event == "start":
                    if node.tag != "tv":
                        raise ValueError("XMLTV root invalid")
                    root = node
                if event != "end":
                    continue
                if node.tag == "channel":
                    cid = node.get("id", "")
                    if not cid or cid in channels:
                        raise ValueError("missing/duplicate channel")
                    channels.add(cid)
                elif node.tag == "programme":
                    events += 1
                    cid = node.get("channel", "")
                    populated.add(cid)
                    title = (node.findtext("title") or "").strip()
                    start = _epg_time(node.get("start"))
                    stop = _epg_time(node.get("stop"))
                    if not title:
                        missing_title += 1
                    if start is None or stop is None or stop <= start:
                        bad_time += 1
                    elif title and cid in channels:
                        if start <= observed < stop:
                            on_air.add(cid)
                        if stop > observed:
                            future.add(cid)
                if node.tag in ("programme", "channel"):
                    node.clear()
                    if root is not None:
                        root.clear()
        except ET.ParseError as err:
            raise ValueError("invalid XMLTV: %s" % err)
    return {
        "channels": channels, "populated": populated,
        "current": on_air, "future": future, "events": events,
        "missing_titles": missing_title, "bad_times": bad_time,
    }


def _verified_cohort(source, meta, report48, quality, now):
    generated = evidence._epoch(meta.get("generated_utc"))
    if generated is None or not -3600 <= now - generated <= SOURCE_MAX_AGE_HOURS * 3600:
        return False, "STALE_OR_INVALID_PUBLISHED_FEED"
    cohort = generated
    if "repacked_utc" in meta:
        repacked = evidence._epoch(meta.get("repacked_utc"))
        original = str(meta.get("original_published_sha256") or "").lower()
        current = str(meta.get("sha256") or "").lower()
        if (repacked is None or repacked < generated or repacked > now + 3600 or
                not SHA_PATTERN.fullmatch(original) or original == current):
            return False, "INVALID_REPACK_PROVENANCE"
        cohort = repacked
    audit_timestamp = evidence._epoch(report48.get("generated_utc"))
    quality_timestamp = evidence._epoch(quality.get("generated_utc"))
    matches = [v for v in (report48.get("feeds") or [])
               if isinstance(v, dict) and v.get("feed") == source + ".xml.gz"]
    if (audit_timestamp is None or abs(audit_timestamp - cohort) > COHORT_MAX_DELTA or
            len(matches) != 1 or matches[0].get("status") not in ("PASS", "WARN", "FAIL")):
        return False, "48H_AUDIT_MISSING_OR_FAILED"
    if (quality_timestamp is None or abs(quality_timestamp - cohort) > COHORT_MAX_DELTA or
            quality.get("source") != source or
            quality.get("source_file") != source + ".xml.gz" or
            quality.get("quality_status") not in ("PASS", "WARN")):
        return False, "QUALITY_FROM_DIFFERENT_PUBLICATION"
    return True, "COHORT_VERIFIED"


def _blocked_48h_ids(source, audit48, actual_channels):
    """Explicitly exclude failing IDs without discarding healthy siblings.

    Only a structurally complete, matching report with channel-specific rows,
    no orphan programmes and no invalid times can authorize a partial cohort.
    None means the report cannot safely establish per-ID provenance.
    """
    matched = [v for v in (audit48.get("feeds") or ())
               if isinstance(v, dict) and v.get("feed") == source + ".xml.gz"]
    if len(matched) != 1:
        return None
    data = matched[0]
    status = data.get("status")
    if status in ("PASS", "WARN"):
        return set()
    if status != "FAIL":
        return None
    if data.get("invalid_times") != 0 or data.get("orphan_programmes") != 0:
        return None
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != len(actual_channels):
        return None
    seen, blocked = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        cid = row.get("id")
        row_status = row.get("status")
        if (not isinstance(cid, str) or not cid or cid in seen or
                cid not in actual_channels or row_status not in ("PASS", "WARN", "FAIL")):
            return None
        seen.add(cid)
        if row_status == "FAIL":
            blocked.add(cid)
    if seen != actual_channels or not blocked or data.get("failed_channels") != len(blocked):
        return None
    return blocked


def inspect_source(source, now=None, reader=None):
    """Fetch, hash and inspect one source; return evidence or fail-closed state.

    Exposes programme IDs only from actual compressed public bytes. Incomplete
    quality reports cannot grant TRUE verification. Never fabricate titles.
    """
    if source not in evidence.VALID_DIRECT:
        raise ValueError("unknown direct EPG source")
    if now is None:
        now = time.time()
    reader = reader or _download
    result = {
        "source": source, "status": "UNKNOWN",
        "ready_for_epg_ok": False, "xmltv_sha256_verified": False,
        "channel_specific_epg_evidence_verified": False,
        "real_now_coverage_verified": False,
        "per_id_evidence_observed_utc": _utc(now),
        "trusted_current_epg_ids": [], "review_future_epg_ids": [],
        "xmltv_current_real_ids": [], "xmltv_future_real_ids": [],
        "xmltv_observed_zero_ids": [], "suspected_placeholder_epg_ids": [],
    }
    try:
        meta = _json("feeds/" + source + ".json", reader)
        if meta.get("label") != source:
            raise ValueError("wrong feed metadata label")
        audit48 = _json("reports/" + source + "-48h-audit.json", reader)
        quality = _json("reports/" + source + "-quality.json", reader)
        raw = reader("feeds/" + source + ".xml.gz", GZIP_LIMIT)
        digest = hashlib.sha256(raw).hexdigest()
        if (digest != meta.get("sha256") or len(raw) != meta.get("size_bytes") or
                not SHA_PATTERN.fullmatch(str(meta.get("sha256") or ""))):
            raise ValueError("XMLTV checksum or compressed size mismatch")
        result["xmltv_sha256_verified"] = True
        measured = _programmes(raw, now)
        # Structural counts and orphan references must reflect the emitted XML,
        # not a publisher's summary JSON.
        counts_ok = (len(measured["channels"]) == meta.get("channels") and
                     measured["events"] == meta.get("programmes") and
                     len(measured["populated"]) == meta.get("active_channels") and
                     not (measured["populated"] - measured["channels"]))
        known_quality, reason = _verified_cohort(source, meta, audit48, quality, now)
        failing_48h_ids = _blocked_48h_ids(source, audit48, measured["channels"])
        result["partial_48h_blocked_ids"] = sorted(failing_48h_ids or ())
        if failing_48h_ids is None:
            known_quality = False
            reason = "48H_AUDIT_INCONSISTENT_IDS"
        placeholder = quality.get("suspected_placeholder_channels")
        flags_valid = isinstance(placeholder, list)
        flagged = set()
        if flags_valid:
            for row in placeholder:
                value = row.get("id") if isinstance(row, dict) else row
                if not isinstance(value, str) or not value.strip():
                    flags_valid = False
                    break
                flagged.add(value.strip())
        result["suspected_placeholder_epg_ids"] = sorted(flagged)
        result["channel_specific_epg_evidence_verified"] = True
        result["real_now_coverage_verified"] = True
        result["xmltv_current_real_ids"] = sorted(measured["current"])
        result["xmltv_future_real_ids"] = sorted(measured["future"])
        result["xmltv_observed_zero_ids"] = sorted(measured["channels"] - measured["populated"])
        result["xmltv_live_now_channels"] = len(measured["current"])
        result["xmltv_active_channels"] = len(measured["populated"])
        result["xmltv_missing_titles"] = measured["missing_titles"]
        result["xmltv_bad_times"] = measured["bad_times"]
        # The all-source gate is intentionally conservative for a first
        # receiver rollout. Invalid global quality => no auto-eligible IDs.
        valid = (counts_ok and flags_valid and known_quality and
                 measured["missing_titles"] == 0 and measured["bad_times"] == 0 and
                 len(measured["populated"]) > 0 and
                 100 * len(measured["current"]) >= 60 * len(measured["populated"]) and
                 100 * len(measured["populated"]) >= 60 * len(measured["channels"]))
        if valid:
            blocked = failing_48h_ids or set()
            result["trusted_current_epg_ids"] = sorted(measured["current"] - flagged - blocked)
            result["review_future_epg_ids"] = sorted(measured["future"] - measured["current"] - flagged - blocked)
            result["ready_for_epg_ok"] = bool(result["trusted_current_epg_ids"])
            if result["ready_for_epg_ok"]:
                result["status"] = ("HASHED_XMLTV_PER_ID_PARTIAL_48H" if blocked else
                                    "HASHED_XMLTV_AND_AUDITS_HEALTHY")
            else:
                result["status"] = "NO_CURRENT_REAL_EPG"
        else:
            result["review_future_epg_ids"] = sorted(
                measured["future"] - measured["current"] - flagged -
                (failing_48h_ids or set())) if known_quality else []
            result["status"] = reason if not known_quality else "XMLTV_CONTENT_QUALITY_ISSUES"
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, EOFError) as err:
        result["status"] = "XMLTV_DOWNLOAD_OR_INTEGRITY_UNVERIFIED"
        result["error_type"] = type(err).__name__
        result["ready_for_epg_ok"] = False
        result["xmltv_sha256_verified"] = False
        result["channel_specific_epg_evidence_verified"] = False
        result["real_now_coverage_verified"] = False
        result["trusted_current_epg_ids"] = []
    return result


def refresh_file(sources, destination=None, now=None, reader=None):
    """Atomically update ONLY /tmp report; preserve other still-fresh entries.

    Caller must be a dedicated worker; NEVER call on the GUI thread.
    """
    if destination is None:
        destination = evidence.EVIDENCE_PATH
    absolute = os.path.abspath(destination)
    root = os.path.realpath("/tmp")
    # The parent may itself be a symlink outside /tmp. Never allow the
    # write target to escape the receiver's ephemeral workspace.
    actual_parent = os.path.realpath(os.path.dirname(absolute))
    if (not absolute.startswith("/tmp/") or
            not (actual_parent == root or actual_parent.startswith(root + os.sep))):
        raise ValueError("evidence cache must be written only under /tmp")
    if os.path.islink(destination):
        raise ValueError("symlinked evidence target")
    if now is None:
        now = time.time()
    sources = list(dict.fromkeys(sources or ()))
    if not sources or len(sources) > 2:
        raise ValueError("refresh must select one or two public sources")
    if any(s not in evidence.VALID_DIRECT for s in sources):
        raise ValueError("invalid source")
    previous = evidence.load_local_report(destination)
    rows = {}
    if isinstance(previous, dict):
        for row in previous.get("sources") or []:
            if not isinstance(row, dict):
                continue
            source = row.get("source")
            observed = evidence._epoch(row.get("per_id_evidence_observed_utc"))
            if (source in evidence.VALID_DIRECT and observed is not None and
                    0 <= now - observed <= evidence.MAX_EVIDENCE_AGE_SECONDS):
                rows[source] = row
    for source in sources:
        rows[source] = inspect_source(source, now=now, reader=reader)
    report = {
        "audit": "EPGManager RC80 public feed provenance",
        "evidence_schema": 1, "checked_utc": _utc(now),
        "read_only": True, "receiver_modified": False,
        "full_xmltv_integrity_checks_enabled": True,
        "sources": [rows[k] for k in sorted(rows)],
    }
    raw = (json.dumps(report, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > REPORT_LIMIT:
        raise ValueError("evidence report too large")
    folder = os.path.dirname(os.path.abspath(destination))
    fd, tmp = tempfile.mkstemp(prefix=".rc80-evidence-", dir=folder)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.islink(destination):
            raise ValueError("target replaced with symlink")
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return report


def queue_background(source_ids, enabled=False):
    """Enigma2 callback: zero HTTP/JSON/XML and no disk I/O on UI thread.

    Explicit flag stays disabled by default until supervised receiver testing.
    Only ONE active worker; globally throttled, and limited to one source/job.
    """
    if not enabled:
        return False
    allowed = sorted(set(evidence.source_name(s) for s in (source_ids or ())))
    allowed = [s for s in allowed if s]
    if not allowed:
        return False
    when = time.monotonic()
    with _BG_LOCK:
        if (_BG_STATE["busy"] or
                (_BG_STATE["last_started"] and
                 when - _BG_STATE["last_started"] < REFRESH_COOLDOWN_SECONDS)):
            return False
        candidates = [s for s in allowed if when - _BG_STATE["source_at"].get(s, -1e9) >= PER_SOURCE_COOLDOWN_SECONDS]
        if not candidates:
            return False
        source = min(candidates, key=lambda s: _BG_STATE["source_at"].get(s, -1e9))
        _BG_STATE["busy"] = True
        _BG_STATE["last_started"] = when
        _BG_STATE["source_at"][source] = when

    def work():
        try:
            refresh_file([source])
            with _BG_LOCK:
                _BG_STATE["last_error"] = ""
        except Exception as err:
            with _BG_LOCK:
                _BG_STATE["last_error"] = type(err).__name__
        finally:
            with _BG_LOCK:
                _BG_STATE["busy"] = False

    try:
        threading.Thread(target=work, name="rc80-epg-evidence", daemon=True).start()
        return True
    except Exception:
        with _BG_LOCK:
            _BG_STATE["busy"] = False
        return False
