#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two official XMLTV grabs for the same xmltv_ids.

Used to detect cases where a statically higher-priority official site only emits
placeholder/all-day rows while another official provider has a real timetable.
Diagnostic only: it never changes catalogue priorities.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PLACEHOLDER_RE = re.compile(
    r"^(?:tv guide is not available|schedule unavailable|programme schedule unavailable|"
    r"program schedule unavailable|no information|no info|tba|جدول البرامج غير متاح|"
    r"لا توجد معلومات|لا يوجد برنامج)$", re.I)


def parse_dt(value):
    value = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})", value)
    if not m:
        return None
    digits = m.group(1)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        return datetime.strptime(digits, fmt)
    except Exception:
        return None


def first_text(node, tag):
    el = node.find(tag)
    return ((el.text or "").strip() if el is not None else "")


def load(path):
    root = ET.parse(path).getroot()
    names = {}
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            names[cid] = first_text(c, "display-name") or cid
    rows = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            rows[cid].append(p)
    return names, rows


def stats(rows):
    events = len(rows)
    starts = []
    total_h = 0.0
    placeholders = 0
    very_long = 0
    titles = []
    for p in rows:
        start, stop = parse_dt(p.get("start")), parse_dt(p.get("stop"))
        if start:
            starts.append(start)
        if start and stop and stop > start:
            dur = (stop - start).total_seconds() / 3600.0
            total_h += dur
            if dur >= 6:
                very_long += 1
        title = first_text(p, "title")
        if title:
            titles.append(title)
            if PLACEHOLDER_RE.match(title):
                placeholders += 1
    distinct = len({x.casefold() for x in titles if x})
    return {
        "events": events,
        "distinct_starts": len(set(starts)),
        "coverage_h": round(total_h, 2),
        "placeholders": placeholders,
        "very_long": very_long,
        "distinct_titles": distinct,
        "samples": titles[:3],
    }


def quality(s):
    # Structural score only. Placeholder/all-day rows are heavily penalized.
    score = min(s["events"], 96) * 1.0
    score += min(s["coverage_h"], 48) * 1.5
    score += min(s["distinct_titles"], 30) * 1.5
    score -= s["placeholders"] * 25
    score -= s["very_long"] * 8
    if s["events"] <= 2:
        score -= 35
    if s["coverage_h"] < 6:
        score -= 25
    return round(score, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--a-name", default="A")
    ap.add_argument("--b-name", default="B")
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    anames, arows = load(args.a)
    bnames, brows = load(args.b)
    ids = sorted(set(anames) | set(bnames), key=str.casefold)
    results = []
    counts = Counter()
    for cid in ids:
        sa, sb = stats(arows.get(cid, [])), stats(brows.get(cid, []))
        qa, qb = quality(sa), quality(sb)
        if sa["events"] == 0 and sb["events"] == 0:
            verdict = "BOTH_EMPTY"
        elif sb["events"] >= 3 and qb >= qa + 25 and sb["placeholders"] == 0:
            verdict = "SWITCH_TO_%s" % args.b_name.upper()
        elif sa["events"] >= 3 and qa >= qb - 10:
            verdict = "KEEP_%s" % args.a_name.upper()
        else:
            verdict = "REVIEW"
        counts[verdict] += 1
        results.append({
            "id": cid,
            "name_a": anames.get(cid, ""),
            "name_b": bnames.get(cid, ""),
            args.a_name: dict(sa, quality=qa),
            args.b_name: dict(sb, quality=qb),
            "verdict": verdict,
        })

    out = {
        "schema": 1,
        "mode": "official-duplicate-source-health-compare",
        "a": args.a_name,
        "b": args.b_name,
        "ids": len(ids),
        "counts": dict(counts),
        "results": results,
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "OFFICIAL DUPLICATE SOURCE HEALTH: %s vs %s" % (args.a_name, args.b_name),
        "ids=%d %s" % (len(ids), " ".join("%s=%d" % x for x in sorted(counts.items()))),
        "",
    ]
    priority = [x for x in results if x["verdict"].startswith("SWITCH_TO_")]
    other = [x for x in results if not x["verdict"].startswith("SWITCH_TO_")]
    lines.append("SAFE SWITCH CANDIDATES")
    for x in priority:
        a, b = x[args.a_name], x[args.b_name]
        lines.append("- %s | %s -> %s | %s events=%d cov=%.1fh ph=%d q=%.1f | %s events=%d cov=%.1fh ph=%d q=%.1f" % (
            x["id"], args.a_name, args.b_name,
            args.a_name, a["events"], a["coverage_h"], a["placeholders"], a["quality"],
            args.b_name, b["events"], b["coverage_h"], b["placeholders"], b["quality"]))
        if b["samples"]:
            lines.append("    %s samples=%s" % (args.b_name, " | ".join(b["samples"])))
    lines += ["", "OTHER/REVIEW"]
    for x in other:
        a, b = x[args.a_name], x[args.b_name]
        lines.append("- [%s] %s | %s=%d/%.1fh/%dph q=%.1f | %s=%d/%.1fh/%dph q=%.1f" % (
            x["verdict"], x["id"], args.a_name, a["events"], a["coverage_h"], a["placeholders"], a["quality"],
            args.b_name, b["events"], b["coverage_h"], b["placeholders"], b["quality"]))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
