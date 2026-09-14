#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict second-pass validation for exact-ID rescue candidates.

This is diagnostic only. It intentionally rejects superficially healthy feeds
that are actually placeholders, generic repeated labels, or cloned timetables
shared across numbered sibling channels.
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
    r"^(?:tv guide is not available|the schedule is not available|schedule is not available|"
    r"schedule unavailable|programme schedule unavailable|program schedule unavailable|"
    r"no information|no info|tba|جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$",
    re.I,
)
GENERIC_MAX_RE = re.compile(r"^beIN\s+Sports\s+MAX$", re.I)
WS_RE = re.compile(r"\s+")


def norm(value: str) -> str:
    value = WS_RE.sub(" ", value or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value).strip()


def parse_dt(value: str):
    raw = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})", raw)
    if not m:
        return None
    s = m.group(1)
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S" if len(s) == 14 else "%Y%m%d%H%M")
    except Exception:
        return None


def first_text(p: ET.Element, tag: str) -> str:
    node = p.find(tag)
    return ((node.text or "").strip() if node is not None else "")


def profile(cid: str, rows: list[ET.Element]) -> dict:
    rows = sorted(rows, key=lambda p: p.get("start") or "")
    placeholders = invalid = long_gt_12h = 0
    coverage_h = 0.0
    titles = []
    fp = []
    for p in rows:
        title = first_text(p, "title")
        titles.append(title)
        if PLACEHOLDER_RE.match(title or ""):
            placeholders += 1
        start, stop = parse_dt(p.get("start")), parse_dt(p.get("stop"))
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        dur_h = (stop - start).total_seconds() / 3600.0
        coverage_h += dur_h
        if dur_h > 12:
            long_gt_12h += 1
        fp.append((p.get("start") or "", p.get("stop") or "", norm(title)))

    unique = {norm(x) for x in titles if norm(x)}
    generic_max = sum(1 for x in titles if GENERIC_MAX_RE.match((x or "").strip()))
    n = len(rows)
    return {
        "id": cid,
        "events": n,
        "coverage_h": round(coverage_h, 2),
        "placeholders": placeholders,
        "placeholder_pct": round(placeholders / float(max(1, n)) * 100.0, 1),
        "invalid": invalid,
        "long_gt_12h": long_gt_12h,
        "unique_titles": len(unique),
        "unique_title_pct": round(len(unique) / float(max(1, n)) * 100.0, 1),
        "generic_max_events": generic_max,
        "samples": titles[:5],
        "fingerprint": tuple(fp),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--grabbed", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    meta = {x["id"]: x for x in plan.get("targets", [])}
    tv = ET.parse(args.grabbed).getroot()
    events = defaultdict(list)
    for p in tv.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid in meta:
            events[cid].append(p)

    profiles = {cid: profile(cid, events.get(cid, [])) for cid in meta}
    fp_groups = defaultdict(list)
    for cid, p in profiles.items():
        if p["fingerprint"]:
            fp_groups[p["fingerprint"]].append(cid)

    results = []
    counts = Counter()
    for cid in sorted(meta, key=str.casefold):
        p = profiles[cid]
        reasons = []
        clone_group = fp_groups.get(p["fingerprint"], []) if p["fingerprint"] else []
        n = p["events"]
        if n == 0:
            reasons.append("EMPTY")
        if p["invalid"]:
            reasons.append("INVALID_EVENTS=%d" % p["invalid"])
        if p["long_gt_12h"]:
            reasons.append("LONG_GT_12H=%d" % p["long_gt_12h"])
        if p["placeholders"]:
            reasons.append("PLACEHOLDER=%d/%d" % (p["placeholders"], max(1, n)))
        if n >= 5 and p["unique_title_pct"] < 20.0:
            reasons.append("LOW_TITLE_DIVERSITY=%.1f%%" % p["unique_title_pct"])
        if p["generic_max_events"] >= max(3, int(max(1, n) * 0.5)):
            reasons.append("GENERIC_BEIN_MAX_LABEL=%d/%d" % (p["generic_max_events"], max(1, n)))
        if len(clone_group) >= 3:
            reasons.append("CLONED_TIMELINE_GROUP=%d" % len(clone_group))

        hard = any(x.startswith((
            "EMPTY", "INVALID_EVENTS", "LONG_GT_12H", "PLACEHOLDER",
            "GENERIC_BEIN_MAX_LABEL", "CLONED_TIMELINE_GROUP"
        )) for x in reasons)
        if hard:
            verdict = "REJECT"
        elif n >= 3 and p["coverage_h"] >= 6 and p["unique_title_pct"] >= 20.0:
            verdict = "SAFE"
        else:
            verdict = "REVIEW"
        counts[verdict] += 1

        row = dict(meta[cid])
        row.update({k: v for k, v in p.items() if k != "fingerprint"})
        row["clone_group"] = sorted(clone_group, key=str.casefold)
        row["reasons"] = reasons
        row["verdict"] = verdict
        results.append(row)

    out = {
        "schema": 2,
        "mode": "strict-exact-id-rescue-validation",
        "counts": dict(counts),
        "results": results,
        "safe": [x for x in results if x["verdict"] == "SAFE"],
        "rejected": [x for x in results if x["verdict"] == "REJECT"],
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "STRICT EXACT-ID RESCUE VALIDATION",
        "targets=%d SAFE=%d REVIEW=%d REJECT=%d" % (
            len(results), counts["SAFE"], counts["REVIEW"], counts["REJECT"]),
        "",
    ]
    for x in results:
        lines.append("- [%s] %s | %s -> %s | events=%d cov=%.1fh unique=%.1f%% ph=%d" % (
            x["verdict"], x["id"], x.get("current_site") or "<none>", x.get("candidate_site") or "<none>",
            x["events"], x["coverage_h"], x["unique_title_pct"], x["placeholders"]))
        if x["reasons"]:
            lines.append("    reasons=%s" % "; ".join(x["reasons"]))
        if x["clone_group"]:
            lines.append("    clone_group=%s" % ", ".join(x["clone_group"]))
        if x["samples"]:
            lines.append("    samples=%s" % " | ".join(x["samples"][:3]))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
