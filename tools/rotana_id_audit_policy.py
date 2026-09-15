#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical receiver audit policy for the Rotana provider shard.

Strategy frozen after the 2026-09-14 ID-by-ID comparison:
- keep one preferred MENA receiver identity per audited service;
- prefer official rotana.net IDs when an official adapter exists;
- keep unique MENA services that currently have no equivalent official adapter;
- keep music/Clip services as secondary/no-autolock when the schedule is generic
  or intentionally sparse;
- zero-useful-EPG services may disappear from receiver XML while remaining known
  internally, so Rotana Clip is an optional standby secondary;
- legacy Egypt/UAE/generic/US twins are removed earlier at the receiver boundary
  but remain available in internal merge/catalogue evidence.

Coverage policy preserves the user's two-calendar-day grab. A Casablanca evening
run starts late enough that only ~27-30 future hours can remain inside
today+tomorrow. The narrow 27.0h floor applies only to official rotana.net core
IDs and only alongside the unchanged structural/language/gap checks. Other core
Rotana services retain the 29.5h operational interpretation of the nominal 30h
coverage target.
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

FROZEN_CORE_IDS = {
    "Rotana + HD.sa",
    "Rotana Aflam +.sa",
    "Rotana Kids.sa",
    "RotanaCinemaEgypt.eg@SD",
    "RotanaCinemaKSA.sa@SD",
    "RotanaClassic.sa@SD",
    "RotanaComedy.sa@SD",
    "RotanaDrama.sa@SD",
    "RotanaKhalijia.sa@SD",
}

# Official adapters have the known two-calendar-day evening boundary described
# above. No unrelated/legacy Rotana ID receives this narrower tolerance.
OFFICIAL_ROTANA_CORE_IDS = {
    "RotanaCinemaEgypt.eg@SD",
    "RotanaCinemaKSA.sa@SD",
    "RotanaClassic.sa@SD",
    "RotanaComedy.sa@SD",
    "RotanaDrama.sa@SD",
    "RotanaKhalijia.sa@SD",
}

# These two services have stable receiver-worthy schedules but remain no-autolock
# because music-guide identity is less discriminative than ordinary linear TV.
REQUIRED_SECONDARY_IDS = {
    "Rotana M+ HD.sa",
    "Rotana Music HD.sa",
}

# Rotana Clip can publish a very large generic holding guide on one day and only a
# couple of rows on another. The zero-EPG/quality finalizer may therefore remove
# it completely from receiver XML. Absence is healthy standby behaviour; if it is
# present, it must still pass the structural secondary checks and stay no-autolock.
OPTIONAL_SECONDARY_IDS = {
    "RotanaClip.sa@SD",
}

SECONDARY_IDS = REQUIRED_SECONDARY_IDS | OPTIONAL_SECONDARY_IDS
REQUIRED_RECEIVER_IDS = FROZEN_CORE_IDS | REQUIRED_SECONDARY_IDS
ALLOWED_RECEIVER_IDS = REQUIRED_RECEIVER_IDS | OPTIONAL_SECONDARY_IDS
MIN_COVERAGE_HOURS = 29.5
OFFICIAL_MIN_COVERAGE_HOURS = 27.0
MIN_AR_RATIO = 0.90
MAX_EMPTY_DESC_RATIO = 0.15


def load_xml(path: Path):
    data = path.read_bytes()
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
    if offset == "Z" or not offset:
        tz = timezone.utc
    else:
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def script_ratio(values, rx):
    if not values:
        return 0.0
    return sum(bool(rx.search(v or "")) for v in values) / float(len(values))


def union_profile(intervals):
    intervals = sorted((s, e) for s, e in intervals if s is not None and e is not None and e > s)
    if not intervals:
        return 0.0, 0, 0.0, 0
    covered = 0.0
    gaps = 0
    gap_hours = 0.0
    overlaps = 0
    cur_s, cur_e = intervals[0]
    for start, stop in intervals[1:]:
        if start < cur_e:
            overlaps += 1
            if stop > cur_e:
                cur_e = stop
        elif start == cur_e:
            cur_e = max(cur_e, stop)
        else:
            covered += (cur_e - cur_s).total_seconds() / 3600.0
            gap = (start - cur_e).total_seconds() / 3600.0
            if gap > 2.0:
                gaps += 1
                gap_hours += gap
            cur_s, cur_e = start, stop
    covered += (cur_e - cur_s).total_seconds() / 3600.0
    return covered, gaps, gap_hours, overlaps


def profile(cid, name, programmes):
    intervals = []
    invalid = 0
    empty_title = 0
    empty_desc = 0
    titles = []
    descs = []
    title_counter = Counter()
    long12 = 0
    for p in programmes:
        title = text(p, "title")
        desc = text(p, "desc")
        start = parse_dt(p.get("start"))
        stop = parse_dt(p.get("stop"))
        if not title:
            empty_title += 1
        else:
            titles.append(title)
            title_counter[re.sub(r"\s+", " ", title.casefold()).strip()] += 1
        if not desc:
            empty_desc += 1
        else:
            descs.append(desc)
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        if (stop - start).total_seconds() > 12 * 3600:
            long12 += 1
        intervals.append((start, stop))

    coverage, gaps, gap_hours, overlaps = union_profile(intervals)
    events = len(programmes)
    unique_ratio = len(title_counter) / float(max(1, events))
    top_ratio = title_counter.most_common(1)[0][1] / float(max(1, events)) if title_counter else 0.0
    return {
        "id": cid,
        "name": name,
        "events": events,
        "coverage_hours": round(coverage, 3),
        "gaps_gt_2h": gaps,
        "gap_hours": round(gap_hours, 3),
        "overlaps": overlaps,
        "invalid": invalid,
        "long_gt_12h": long12,
        "empty_title": empty_title,
        "empty_desc": empty_desc,
        "title_ar_ratio": round(script_ratio(titles, AR), 4),
        "title_latin_ratio": round(script_ratio(titles, LAT), 4),
        "desc_ar_ratio": round(script_ratio(descs, AR), 4),
        "unique_title_ratio": round(unique_ratio, 4),
        "top_title_ratio": round(top_ratio, 4),
    }


def required_coverage(cid):
    return OFFICIAL_MIN_COVERAGE_HOURS if cid in OFFICIAL_ROTANA_CORE_IDS else MIN_COVERAGE_HOURS


def core_issues(row):
    issues = []
    minimum = required_coverage(row["id"])
    row["minimum_coverage_hours"] = minimum
    if row["events"] <= 0:
        issues.append("NO_PROGRAMMES")
    if row["coverage_hours"] < minimum:
        issues.append("LOW_COVERAGE=%.1fh<%.1fh" % (row["coverage_hours"], minimum))
    if row["invalid"]:
        issues.append("INVALID=%d" % row["invalid"])
    if row["overlaps"]:
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row["long_gt_12h"]:
        issues.append("VERY_LONG=%d" % row["long_gt_12h"])
    if row["gaps_gt_2h"]:
        issues.append("GAPS_GT_2H=%d/TOTAL=%.1fh" % (row["gaps_gt_2h"], row["gap_hours"]))
    if row["empty_title"]:
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])
    if row["title_ar_ratio"] < MIN_AR_RATIO:
        issues.append("TITLE_AR_LOW=%.0f%%" % (row["title_ar_ratio"] * 100.0))
    populated_desc = row["events"] - row["empty_desc"]
    if row["events"] and row["empty_desc"] / float(row["events"]) > MAX_EMPTY_DESC_RATIO:
        issues.append("EMPTY_DESC=%d/%d" % (row["empty_desc"], row["events"]))
    if populated_desc > 0 and row["desc_ar_ratio"] < MIN_AR_RATIO:
        issues.append("DESC_AR_LOW=%.0f%%" % (row["desc_ar_ratio"] * 100.0))
    return issues


def secondary_issues(row):
    issues = []
    if row["events"] <= 0:
        issues.append("NO_PROGRAMMES")
    if row["invalid"]:
        issues.append("INVALID=%d" % row["invalid"])
    if row["overlaps"]:
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row["long_gt_12h"]:
        issues.append("VERY_LONG=%d" % row["long_gt_12h"])
    if row["empty_title"]:
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    root = load_xml(Path(args.xml))
    channels = {}
    for ch in root.findall("channel"):
        cid = (ch.get("id") or "").strip()
        if not cid:
            continue
        dn = ch.find("display-name")
        channels[cid] = ((dn.text or "").strip() if dn is not None else cid) or cid
    events = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            events[cid].append(p)

    actual_ids = set(channels)
    missing_required = sorted(REQUIRED_RECEIVER_IDS - actual_ids, key=str.casefold)
    optional_missing = sorted(OPTIONAL_SECONDARY_IDS - actual_ids, key=str.casefold)
    unexpected = sorted(actual_ids - ALLOWED_RECEIVER_IDS, key=str.casefold)
    rows = []
    errors = []

    for cid in sorted(actual_ids, key=str.casefold):
        row = profile(cid, channels[cid], events.get(cid, []))
        if cid in FROZEN_CORE_IDS:
            row["class"] = "FROZEN_CORE"
            row["issues"] = core_issues(row)
            row["verdict"] = "FROZEN" if not row["issues"] else "FAIL"
            row["auto_lock_safe"] = not row["issues"]
        elif cid in REQUIRED_SECONDARY_IDS:
            row["class"] = "SECONDARY"
            row["secondary_mode"] = "required"
            row["minimum_coverage_hours"] = None
            row["issues"] = secondary_issues(row)
            row["verdict"] = "SECONDARY" if not row["issues"] else "FAIL"
            row["auto_lock_safe"] = False
        elif cid in OPTIONAL_SECONDARY_IDS:
            row["class"] = "SECONDARY"
            row["secondary_mode"] = "optional_standby"
            row["minimum_coverage_hours"] = None
            row["issues"] = secondary_issues(row)
            row["verdict"] = "SECONDARY" if not row["issues"] else "FAIL"
            row["auto_lock_safe"] = False
        else:
            row["class"] = "UNCLASSIFIED"
            row["minimum_coverage_hours"] = None
            row["issues"] = ["UNEXPECTED_RECEIVER_ID"]
            row["verdict"] = "FAIL"
            row["auto_lock_safe"] = False
        rows.append(row)
        if row["issues"]:
            errors.append("%s:%s" % (cid, ",".join(row["issues"])))

    for cid in missing_required:
        errors.append("MISSING_REQUIRED_ROTANA_ID=%s" % cid)
    for cid in unexpected:
        errors.append("NEW_UNAUDITED_ROTANA_ID=%s" % cid)

    core_ok = sum(1 for r in rows if r.get("class") == "FROZEN_CORE" and r.get("verdict") == "FROZEN")
    required_secondary_ok = sum(
        1 for r in rows
        if r.get("class") == "SECONDARY" and r.get("secondary_mode") == "required" and r.get("verdict") == "SECONDARY"
    )
    optional_secondary_present = sum(
        1 for r in rows
        if r.get("class") == "SECONDARY" and r.get("secondary_mode") == "optional_standby"
    )
    optional_secondary_ok = sum(
        1 for r in rows
        if r.get("class") == "SECONDARY" and r.get("secondary_mode") == "optional_standby" and r.get("verdict") == "SECONDARY"
    )
    status = "FAIL" if errors else "PASS"
    summary = {
        "status": status,
        "channels": len(rows),
        "core_expected": len(FROZEN_CORE_IDS),
        "core_ok": core_ok,
        "required_secondary_expected": len(REQUIRED_SECONDARY_IDS),
        "required_secondary_ok": required_secondary_ok,
        "optional_secondary_allowed": len(OPTIONAL_SECONDARY_IDS),
        "optional_secondary_present": optional_secondary_present,
        "optional_secondary_ok": optional_secondary_ok,
        "minimum_clean_coverage_h": MIN_COVERAGE_HOURS,
        "official_evening_minimum_h": OFFICIAL_MIN_COVERAGE_HOURS,
    }
    payload = {
        "schema": 3,
        "summary": summary,
        "channels": rows,
        "missing_required_ids": missing_required,
        "optional_missing_ids": optional_missing,
        "unexpected_ids": unexpected,
        "errors": errors,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "ROTANA CANONICAL PROVIDER AUDIT: %s" % status,
        "channels=%d core=%d/%d required_secondary=%d/%d optional_present=%d/%d nominal_min=%.1fh official_evening_min=%.1fh" % (
            len(rows), core_ok, len(FROZEN_CORE_IDS), required_secondary_ok, len(REQUIRED_SECONDARY_IDS),
            optional_secondary_present, len(OPTIONAL_SECONDARY_IDS), MIN_COVERAGE_HOURS, OFFICIAL_MIN_COVERAGE_HOURS),
        "",
    ]
    for row in rows:
        min_cov = row.get("minimum_coverage_hours")
        min_text = "n/a" if min_cov is None else "%.1fh" % min_cov
        mode = row.get("secondary_mode") or "core"
        lines.append("[%s] %s class=%s mode=%s events=%d coverage=%.1fh min=%s gaps=%d title_AR=%.0f%% desc_AR=%.0f%% auto_lock=%s" % (
            row["verdict"], row["id"], row["class"], mode, row["events"], row["coverage_hours"], min_text,
            row["gaps_gt_2h"], row["title_ar_ratio"] * 100.0, row["desc_ar_ratio"] * 100.0,
            "YES" if row["auto_lock_safe"] else "NO"))
        if row["issues"]:
            lines.append("  issues=%s" % ", ".join(row["issues"]))
    if optional_missing:
        lines.append("OPTIONAL_STANDBY_MISSING=%s (allowed; zero-useful-EPG prune)" % ",".join(optional_missing))
    if errors:
        lines.extend(["", "Errors:"] + ["- " + e for e in errors])
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())