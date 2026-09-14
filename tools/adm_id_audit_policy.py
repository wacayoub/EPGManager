#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Receiver-facing quality policy for the Abu Dhabi Media provider shard.

Hard failures are identity/timeline failures.  Language quality is deliberately
reported separately so an English-only upstream fallback can stay visible as a
REVIEW without being falsely declared frozen.  No synthetic programmes or fake
Arabic descriptions are created by this policy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")
XMLTV_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")

ARABIC_CORE_IDS = {
    "AbuDhabiTV.ae",
    "AbuDhabiEmirates.ae",
    "AbuDhabiSports1.ae",
    "AbuDhabiSports2.ae",
    "Majid.ae",
}
PREMIUM_CORE_IDS = {
    "ADSportsPremium1.ae",
    "ADSportsPremium2.ae",
}
# NatGeo content policy was frozen in the earlier Disney/NatGeo phase.  ADM only
# owns its receiver identity here; this gate therefore checks timeline integrity
# but does not silently change that already-reviewed language policy.
INHERITED_CORE_IDS = {"NationalGeographicAbuDhabi.ae"}
# Yas is a core ADM brand, but the current public upstream guide is English-only.
# Keep it visible, canonical and no-autolock until an Arabic-safe donor is found.
REVIEW_CORE_IDS = {"YasTV.ae"}
CORE_IDS = ARABIC_CORE_IDS | PREMIUM_CORE_IDS | INHERITED_CORE_IDS | REVIEW_CORE_IDS
SECONDARY_IDS = {"ADSportsExtra.ae", "YasTVExtra.ae"}
OPTIONAL_STANDBY_IDS = {"BaynounahTV.ae"}
REQUIRED_IDS = CORE_IDS | SECONDARY_IDS
ALLOWED_IDS = REQUIRED_IDS | OPTIONAL_STANDBY_IDS

MIN_CORE_COVERAGE_H = 29.0
MIN_AR_TITLE = 0.85
MIN_AR_DESC = 0.80
MIN_PREMIUM_AR_DESC = 0.80
MAX_EMPTY_DESC_ARABIC_CORE = 0.20

LEGACY_IDS = {
    "Abu Dhabi.sa",
    "AbuDhabiSports1.ae@SD",
    "AbuDhabiTV.ae@SD",
    "AD Sports 2.sa",
    "AD Sports Premium 1.sa",
    "AD Sports Premium 2.sa",
    "Al Emarat.sa",
    "en:.AD.Sports.Extra.ae",
    "en:.YAS.TV.Extra.ae",
    "Majid.sa",
    "Nat.Geo.Abu.Dhabi.HD.ae",
    "Yas.TV.HD.ae",
    "Emarat.HD.ae",
}


def load_xml(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def text(node, tag):
    el = node.find(tag)
    return ((el.text or "").strip() if el is not None else "")


def parse_dt(raw):
    m = XMLTV_RE.match((raw or "").strip())
    if not m:
        return None
    digits, offset = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits, fmt)
    except ValueError:
        return None
    if not offset or offset == "Z":
        tz = timezone.utc
    else:
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def ratio(values, rx):
    return (sum(bool(rx.search(v or "")) for v in values) / float(len(values))) if values else 0.0


def union_profile(intervals):
    rows = sorted((s, e) for s, e in intervals if s and e and e > s)
    if not rows:
        return 0.0, 0, 0.0, 0
    covered = 0.0
    gaps = 0
    gap_h = 0.0
    overlaps = 0
    cur_s, cur_e = rows[0]
    for start, stop in rows[1:]:
        if start < cur_e:
            overlaps += 1
            cur_e = max(cur_e, stop)
        elif start == cur_e:
            cur_e = max(cur_e, stop)
        else:
            covered += (cur_e - cur_s).total_seconds() / 3600.0
            gap = (start - cur_e).total_seconds() / 3600.0
            if gap > 2.0:
                gaps += 1
                gap_h += gap
            cur_s, cur_e = start, stop
    covered += (cur_e - cur_s).total_seconds() / 3600.0
    return covered, gaps, gap_h, overlaps


def profile(cid, name, programmes):
    intervals = []
    titles, descs = [], []
    invalid = empty_title = empty_desc = very_long = 0
    title_counter = Counter()
    for p in programmes:
        title = text(p, "title")
        desc = text(p, "desc")
        start, stop = parse_dt(p.get("start")), parse_dt(p.get("stop"))
        if title:
            titles.append(title)
            title_counter[re.sub(r"\s+", " ", title.casefold()).strip()] += 1
        else:
            empty_title += 1
        if desc:
            descs.append(desc)
        else:
            empty_desc += 1
        if not start or not stop or stop <= start:
            invalid += 1
            continue
        if (stop - start).total_seconds() > 12 * 3600:
            very_long += 1
        intervals.append((start, stop))
    coverage, gaps, gap_h, overlaps = union_profile(intervals)
    events = len(programmes)
    top_ratio = title_counter.most_common(1)[0][1] / float(max(1, events)) if title_counter else 0.0
    unique_ratio = len(title_counter) / float(max(1, events))
    return {
        "id": cid,
        "name": name,
        "events": events,
        "coverage_hours": round(coverage, 3),
        "invalid": invalid,
        "overlaps": overlaps,
        "very_long_gt_12h": very_long,
        "gaps_gt_2h": gaps,
        "gap_hours": round(gap_h, 3),
        "empty_title": empty_title,
        "empty_desc": empty_desc,
        "title_ar_ratio": round(ratio(titles, AR), 4),
        "title_latin_ratio": round(ratio(titles, LAT), 4),
        "desc_ar_ratio": round(ratio(descs, AR), 4),
        "top_title_ratio": round(top_ratio, 4),
        "unique_title_ratio": round(unique_ratio, 4),
    }


def structural_issues(row, require_coverage=True):
    issues = []
    if row["events"] <= 0:
        issues.append("NO_PROGRAMMES")
    if require_coverage and row["coverage_hours"] < MIN_CORE_COVERAGE_H:
        issues.append("LOW_COVERAGE=%.1fh<%.1fh" % (row["coverage_hours"], MIN_CORE_COVERAGE_H))
    if row["invalid"]:
        issues.append("INVALID=%d" % row["invalid"])
    if row["overlaps"]:
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row["very_long_gt_12h"]:
        issues.append("VERY_LONG_GT_12H=%d" % row["very_long_gt_12h"])
    if row["gaps_gt_2h"]:
        issues.append("GAPS_GT_2H=%d/TOTAL=%.1fh" % (row["gaps_gt_2h"], row["gap_hours"]))
    if row["empty_title"]:
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])
    return issues


def language_review(row, klass):
    review = []
    if klass == "ARABIC_CORE":
        if row["title_ar_ratio"] < MIN_AR_TITLE:
            review.append("TITLE_AR_LOW=%.0f%%" % (row["title_ar_ratio"] * 100.0))
        if row["events"] and row["empty_desc"] / float(row["events"]) > MAX_EMPTY_DESC_ARABIC_CORE:
            review.append("EMPTY_DESC=%d/%d" % (row["empty_desc"], row["events"]))
        populated = row["events"] - row["empty_desc"]
        if populated and row["desc_ar_ratio"] < MIN_AR_DESC:
            review.append("DESC_AR_LOW=%.0f%%" % (row["desc_ar_ratio"] * 100.0))
    elif klass == "PREMIUM_CORE":
        populated = row["events"] - row["empty_desc"]
        if not populated:
            review.append("NO_DESCRIPTIONS")
        elif row["desc_ar_ratio"] < MIN_PREMIUM_AR_DESC:
            review.append("PREMIUM_DESC_AR_LOW=%.0f%%" % (row["desc_ar_ratio"] * 100.0))
    elif klass == "REVIEW_CORE":
        if row["title_ar_ratio"] < 0.50:
            review.append("ARABIC_TITLE_FALLBACK_ONLY=%.0f%%" % (row["title_ar_ratio"] * 100.0))
        if row["desc_ar_ratio"] < 0.50:
            review.append("ARABIC_DESC_FALLBACK_ONLY=%.0f%%" % (row["desc_ar_ratio"] * 100.0))
    return review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    root = load_xml(args.xml)
    channels = {}
    for ch in root.findall("channel"):
        cid = (ch.get("id") or "").strip()
        dn = ch.find("display-name")
        if cid:
            channels[cid] = ((dn.text or "").strip() if dn is not None else cid) or cid
    events = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            events[cid].append(p)

    actual = set(channels)
    missing = sorted(REQUIRED_IDS - actual, key=str.casefold)
    optional_missing = sorted(OPTIONAL_STANDBY_IDS - actual, key=str.casefold)
    unexpected = sorted(actual - ALLOWED_IDS, key=str.casefold)
    legacy_present = sorted(actual & LEGACY_IDS, key=str.casefold)
    rows = []
    errors = []
    reviews = []

    for cid in sorted(actual, key=str.casefold):
        row = profile(cid, channels[cid], events.get(cid, []))
        if cid in ARABIC_CORE_IDS:
            klass = "ARABIC_CORE"
        elif cid in PREMIUM_CORE_IDS:
            klass = "PREMIUM_CORE"
        elif cid in INHERITED_CORE_IDS:
            klass = "INHERITED_CORE"
        elif cid in REVIEW_CORE_IDS:
            klass = "REVIEW_CORE"
        elif cid in SECONDARY_IDS:
            klass = "SECONDARY_EVENT"
        elif cid in OPTIONAL_STANDBY_IDS:
            klass = "OPTIONAL_STANDBY"
        else:
            klass = "UNEXPECTED"
        row["class"] = klass
        row["issues"] = structural_issues(row, require_coverage=(klass in {
            "ARABIC_CORE", "PREMIUM_CORE", "INHERITED_CORE", "REVIEW_CORE"
        })) if klass != "UNEXPECTED" else ["UNEXPECTED_RECEIVER_ID"]
        row["reviews"] = language_review(row, klass)
        if klass in {"SECONDARY_EVENT", "OPTIONAL_STANDBY", "REVIEW_CORE"}:
            row["auto_lock_safe"] = False
        else:
            row["auto_lock_safe"] = not row["issues"] and not row["reviews"]
        if row["issues"]:
            row["verdict"] = "FAIL"
            errors.append("%s:%s" % (cid, ",".join(row["issues"])))
        elif row["reviews"]:
            row["verdict"] = "REVIEW"
            reviews.append("%s:%s" % (cid, ",".join(row["reviews"])))
        elif klass in {"SECONDARY_EVENT", "OPTIONAL_STANDBY"}:
            row["verdict"] = "SECONDARY"
        else:
            row["verdict"] = "FROZEN"
        rows.append(row)

    for cid in missing:
        errors.append("MISSING_REQUIRED_ADM_ID=%s" % cid)
    for cid in unexpected:
        errors.append("NEW_UNAUDITED_ADM_ID=%s" % cid)
    for cid in legacy_present:
        errors.append("LEGACY_ADM_ID_STILL_PUBLISHED=%s" % cid)

    if errors:
        status = "FAIL"
    elif reviews:
        status = "REVIEW"
    else:
        status = "PASS"

    frozen_core = sum(1 for r in rows if r["id"] in CORE_IDS and r["verdict"] == "FROZEN")
    reviewed_core = sum(1 for r in rows if r["id"] in CORE_IDS and r["verdict"] == "REVIEW")
    secondary_ok = sum(1 for r in rows if r["id"] in SECONDARY_IDS and r["verdict"] == "SECONDARY")
    summary = {
        "status": status,
        "channels": len(rows),
        "core_expected": len(CORE_IDS),
        "core_frozen": frozen_core,
        "core_review": reviewed_core,
        "secondary_expected": len(SECONDARY_IDS),
        "secondary_ok": secondary_ok,
        "optional_standby_allowed": len(OPTIONAL_STANDBY_IDS),
        "optional_standby_present": sum(1 for r in rows if r["id"] in OPTIONAL_STANDBY_IDS),
        "minimum_core_coverage_h": MIN_CORE_COVERAGE_H,
    }
    payload = {
        "schema": 1,
        "summary": summary,
        "channels": rows,
        "missing_required_ids": missing,
        "optional_missing_ids": optional_missing,
        "unexpected_ids": unexpected,
        "legacy_ids_present": legacy_present,
        "reviews": reviews,
        "errors": errors,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "ABU DHABI MEDIA CANONICAL PROVIDER AUDIT: %s" % status,
        "channels=%d core_frozen=%d/%d core_review=%d secondary=%d/%d optional_present=%d/%d" % (
            len(rows), frozen_core, len(CORE_IDS), reviewed_core, secondary_ok, len(SECONDARY_IDS),
            summary["optional_standby_present"], len(OPTIONAL_STANDBY_IDS)),
        "",
    ]
    for row in rows:
        lines.append("- [%s] %s | %s | events=%d coverage=%.1fh auto_lock=%s" % (
            row["verdict"], row["id"], row["class"], row["events"], row["coverage_hours"],
            "YES" if row["auto_lock_safe"] else "NO"))
        if row["issues"]:
            lines.append("    issues=" + "; ".join(row["issues"]))
        if row["reviews"]:
            lines.append("    review=" + "; ".join(row["reviews"]))
    if optional_missing:
        lines.append("- optional standby absent (healthy): " + ", ".join(optional_missing))
    if errors:
        lines.extend(["", "Errors:"] + ["- " + x for x in errors])
    if reviews:
        lines.extend(["", "Reviews:"] + ["- " + x for x in reviews])
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
