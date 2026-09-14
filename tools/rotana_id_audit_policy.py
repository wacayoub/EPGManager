#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical receiver audit policy for the Rotana provider shard.

Strategy frozen after the 2026-09-14 ID-by-ID comparison:
- keep one preferred MENA receiver identity per audited service;
- prefer official rotana.net IDs when an official adapter exists;
- keep unique MENA services that currently have no equivalent official adapter;
- keep music/Clip services as secondary/no-autolock when the schedule is generic
  or intentionally sparse;
- legacy Egypt/UAE/generic/US twins are removed earlier at the receiver boundary
  but remain available in internal merge/catalogue evidence.
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

# High-confidence, useful linear guides. 30h is the nominal target; 29.5h is the
# operational boundary tolerance used for a rolling 48h build launched mid-hour.
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

# Real services retained on the receiver, but guide shape is not suitable for
# automatic mapping confidence. They must never silently become auto-lock safe.
SECONDARY_IDS = {
    "RotanaClip.sa@SD",
    "Rotana M+ HD.sa",
    "Rotana Music HD.sa",
}

EXPECTED_RECEIVER_IDS = FROZEN_CORE_IDS | SECONDARY_IDS
MIN_COVERAGE_HOURS = 29.5
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


def core_issues(row):
    issues = []
    if row["events"] <= 0:
        issues.append("NO_PROGRAMMES")
    if row["coverage_hours"] < MIN_COVERAGE_HOURS:
        issues.append("LOW_COVERAGE=%.1fh" % row["coverage_hours"])
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
    missing = sorted(EXPECTED_RECEIVER_IDS - actual_ids, key=str.casefold)
    unexpected = sorted(actual_ids - EXPECTED_RECEIVER_IDS, key=str.casefold)
    rows = []
    errors = []

    for cid in sorted(actual_ids, key=str.casefold):
        row = profile(cid, channels[cid], events.get(cid, []))
        if cid in FROZEN_CORE_IDS:
            row["class"] = "FROZEN_CORE"
            row["issues"] = core_issues(row)
            row["verdict"] = "FROZEN" if not row["issues"] else "FAIL"
            row["auto_lock_safe"] = not row["issues"]
        elif cid in SECONDARY_IDS:
            row["class"] = "SECONDARY"
            row["issues"] = secondary_issues(row)
            row["verdict"] = "SECONDARY" if not row["issues"] else "FAIL"
            row["auto_lock_safe"] = False
        else:
            row["class"] = "UNCLASSIFIED"
            row["issues"] = ["UNEXPECTED_RECEIVER_ID"]
            row["verdict"] = "FAIL"
            row["auto_lock_safe"] = False
        rows.append(row)
        if row["issues"] and row["class"] != "SECONDARY":
            errors.append("%s:%s" % (cid, ",".join(row["issues"])))
        elif row["class"] == "SECONDARY" and row["issues"]:
            errors.append("%s:%s" % (cid, ",".join(row["issues"])))

    for cid in missing:
        errors.append("MISSING_ROTANA_ID=%s" % cid)
    for cid in unexpected:
        errors.append("NEW_UNAUDITED_ROTANA_ID=%s" % cid)

    core_ok = sum(1 for r in rows if r.get("class") == "FROZEN_CORE" and r.get("verdict") == "FROZEN")
    secondary_ok = sum(1 for r in rows if r.get("class") == "SECONDARY" and r.get("verdict") == "SECONDARY")
    status = "FAIL" if errors else "PASS"
    summary = {
        "status": status,
        "channels": len(rows),
        "core_expected": len(FROZEN_CORE_IDS),
        "core_ok": core_ok,
        "secondary_expected": len(SECONDARY_IDS),
        "secondary_ok": secondary_ok,
        "minimum_clean_coverage_h": MIN_COVERAGE_HOURS,
    }
    payload = {
        "schema": 1,
        "summary": summary,
        "channels": rows,
        "missing_ids": missing,
        "unexpected_ids": unexpected,
        "errors": errors,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "ROTANA CANONICAL PROVIDER AUDIT: %s" % status,
        "channels=%d core=%d/%d secondary=%d/%d" % (
            len(rows), core_ok, len(FROZEN_CORE_IDS), secondary_ok, len(SECONDARY_IDS)),
        "",
    ]
    for row in rows:
        lines.append("[%s] %s class=%s events=%d coverage=%.1fh gaps=%d title_AR=%.0f%% desc_AR=%.0f%% auto_lock=%s" % (
            row["verdict"], row["id"], row["class"], row["events"], row["coverage_hours"],
            row["gaps_gt_2h"], row["title_ar_ratio"] * 100.0, row["desc_ar_ratio"] * 100.0,
            "YES" if row["auto_lock_safe"] else "NO"))
        if row["issues"]:
            lines.append("  issues=%s" % ", ".join(row["issues"]))
    if errors:
        lines.extend(["", "Errors:"] + ["- " + e for e in errors])
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
