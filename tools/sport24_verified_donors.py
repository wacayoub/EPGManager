#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Complete only Sport24 broadcast gaps with exact, audited, genuine TV guides.

Sport24 stays the primary source. Three manually reviewed SAME-BROADCAST
channel aliases may use real programmes from already-published OSN/Dubai+
when provider source pages expose only ~4h of listings. This is NOT a
similar-name match, and does not create Thmanyah/sports programmes.
Unverifiable donors fail closed to the original Sport24 XMLTV.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.source_feed_pack import parse_xmltv_dt
except ModuleNotFoundError:
    from source_feed_pack import parse_xmltv_dt

# Explicit, separately observed channel identity matches. Do not broaden.
DONORS = {
    "sport24.adsports.1": ("osn", "AbuDhabiSports1.ae@SD"),
    "sport24.dubaisports.1": ("dubaiplus", "DubaiSports1.ae@SD"),
    "sport24.dubaisports.2": ("dubaiplus", "DubaiSports2.ae@SD"),
}
MAX_DONOR_AGE = timedelta(hours=36)
MAX_COHORT_DELTA = timedelta(minutes=10)


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def valid_window(p, now):
    start = parse_xmltv_dt(p.get("start"))
    end = parse_xmltv_dt(p.get("stop"))
    if not start or not end or end <= start or end - start > timedelta(hours=24):
        return None
    if not any((x.text or "").strip() for x in p.findall("title")):
        return None
    if end <= now - timedelta(hours=2) or start >= now + timedelta(hours=48):
        return None
    return start, end


def audited_donor(feed_dir, source, channel, now):
    """Read only a completely consistent publication cohort for one ID."""
    report_dir = feed_dir.parent / "reports"
    try:
        meta = json.loads((feed_dir / (source + ".json")).read_text())
        audit = json.loads((report_dir / (source + "-48h-audit.json")).read_text())
        quality = json.loads((report_dir / (source + "-quality.json")).read_text())
        blob = (feed_dir / (source + ".xml.gz")).read_bytes()
        generated = timestamp(meta.get("generated_utc"))
        if (meta.get("label") != source or generated is None or
                not -timedelta(hours=1) <= now - generated <= MAX_DONOR_AGE or
                len(blob) != meta.get("size_bytes") or
                hashlib.sha256(blob).hexdigest() != meta.get("sha256")):
            return [], "DONOR_HASH_OR_FRESHNESS"
        at = timestamp(audit.get("generated_utc"))
        qt = timestamp(quality.get("generated_utc"))
        if (at is None or qt is None or
                abs(at - generated) > MAX_COHORT_DELTA or
                abs(qt - generated) > MAX_COHORT_DELTA or
                quality.get("source") != source or
                quality.get("source_file") != source + ".xml.gz" or
                quality.get("quality_status") not in ("PASS", "WARN")):
            return [], "DONOR_AUDIT_COHORT_MISMATCH"
        matches = [x for x in audit.get("feeds", ()) if
                   isinstance(x, dict) and x.get("feed") == source + ".xml.gz"]
        if len(matches) != 1:
            return [], "DONOR_48H_REPORT_MISSING"
        a = matches[0]
        if (a.get("invalid_times") != 0 or a.get("orphan_programmes") != 0 or
                a.get("channels") != meta.get("channels") or
                not isinstance(a.get("rows"), list) or
                len(a["rows"]) != a["channels"]):
            return [], "DONOR_48H_REPORT_INVALID"
        hits = [x for x in a["rows"] if isinstance(x, dict) and x.get("id") == channel]
        if len(hits) != 1 or hits[0].get("status") != "PASS":
            return [], "DONOR_EXACT_ID_NOT_48H_PASS"
        if (not isinstance(hits[0].get("future_hours"), (int, float)) or
                hits[0]["future_hours"] < 36 or
                not isinstance(hits[0].get("max_gap_minutes"), (int, float)) or
                hits[0]["max_gap_minutes"] > 90):
            return [], "DONOR_EXACT_ID_BAD_COVERAGE"
        flagged = quality.get("suspected_placeholder_channels")
        if not isinstance(flagged, list):
            return [], "DONOR_PLACEHOLDER_AUDIT_MISSING"
        if any((v.get("id") if isinstance(v, dict) else v) == channel for v in flagged):
            return [], "DONOR_EXACT_ID_PLACEHOLDER"
        if not blob.startswith(b"\x1f\x8b"):
            return [], "DONOR_GZIP_INVALID"
        unzipped = gzip.decompress(blob)
        if len(unzipped) > 12_000_000:
            return [], "DONOR_XML_TOO_LARGE"
        root = ET.fromstring(unzipped)
        if root.tag != "tv":
            return [], "DONOR_XML_ROOT_INVALID"
        cats = {(c.get("id") or "") for c in root.findall("channel")}
        ps = root.findall("programme")
        if (len(cats) != meta.get("channels") or
                len(ps) != meta.get("programmes") or channel not in cats):
            return [], "DONOR_XML_COUNTS_INVALID"
        rows = []
        for p in ps:
            if p.get("channel") != channel:
                continue
            iv = valid_window(p, now)
            if iv:
                rows.append((iv[0], iv[1], p))
        rows.sort(key=lambda x: (x[0], x[1]))
        # Require the *actual hashed XML*, not merely a reported PASS, to
        # provide at least 12h continuous future EPG (<=90min gaps).
        cursor = now
        for start, end, _ in rows:
            if end <= cursor:
                continue
            if start - cursor > timedelta(minutes=90):
                return [], "DONOR_REAL_XML_LARGE_GAP"
            cursor = max(cursor, end)
            if cursor >= now + timedelta(hours=12):
                break
        if cursor < now + timedelta(hours=12):
            return [], "DONOR_REAL_XML_SHORT_HORIZON"
        return rows, "DONOR_VERIFIED"
    except (OSError, ValueError, TypeError, KeyError, EOFError, ET.ParseError, gzip.BadGzipFile):
        return [], "DONOR_UNAVAILABLE_OR_INVALID"


def overlap(interval, occupied):
    return any(interval[0] < end and start < interval[1] for start, end in occupied)


def extend(root, feed_dir, now):
    if root.tag != "tv":
        raise ValueError("Sport24 input must be a tv XMLTV")
    channel_ids = {c.get("id") for c in root.findall("channel")}
    report = {
        "source": "sport24", "generated_utc": now.isoformat(),
        "mode": "primary_sport24_then_exact_verified_donor_nonoverlap",
        "donor_guards": "matching source gzip SHA256, complete cohort, 48h PASS exact ID, genuine titles, native dates",
        "added_programmes": 0, "donor_channels": 0, "synthetic_programmes_added": 0,
        "channels": [],
    }
    for own, (source, donor) in DONORS.items():
        item = {"sport24_id": own, "donor_source": source, "donor_xmltv_id": donor,
                "added": 0, "overlap_skipped": 0, "status": ""}
        if own not in channel_ids:
            item["status"] = "SPORT24_ID_NOT_IN_CATALOGUE"
            report["channels"].append(item)
            continue
        primary = []
        for p in root.findall("programme"):
            if p.get("channel") == own:
                iv = valid_window(p, now)
                if iv:
                    primary.append(iv)
        authentic, reason = audited_donor(feed_dir, source, donor, now)
        if not authentic:
            item["status"] = reason
            report["channels"].append(item)
            continue
        occupied = list(primary)
        for start, end, p in authentic:
            # Only fill holes: never replace/overlap a Sport24 programme or
            # another donor row. No guessed clipping or fabricated coverage.
            if overlap((start, end), occupied):
                item["overlap_skipped"] += 1
                continue
            copy = ET.fromstring(ET.tostring(p, encoding="utf-8"))
            copy.set("channel", own)
            root.append(copy)
            occupied.append((start, end))
            item["added"] += 1
        item["status"] = "VERIFIED_DONOR_APPLIED" if item["added"] else "VERIFIED_NO_OPEN_GAPS"
        report["added_programmes"] += item["added"]
        report["donor_channels"] += (1 if item["added"] else 0)
        report["channels"].append(item)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--feed-dir", default="feeds")
    ap.add_argument("--report", required=True)
    ap.add_argument("--now", default="")
    args = ap.parse_args()
    now = timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("Invalid timezone-aware --now")
    root = ET.parse(args.input).getroot()
    report = extend(root, Path(args.feed_dir), now)
    ET.indent(root, space="  ")
    Path(args.output).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"donor_channels":report["donor_channels"],
                      "added_programmes":report["added_programmes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
