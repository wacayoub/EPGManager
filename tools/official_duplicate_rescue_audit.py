#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan and evaluate exact-ID alternative official/iptv-org sources for NO_EPG.

This is diagnostic only. It never changes production. For every current NO_EPG
xmltv_id it scans iptv-org channel catalogues for the exact same xmltv_id on a
different site, chooses the best alternate row deterministically, grabs 48h,
and reports whether that alternate source contains a usable timetable.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

EXCLUDED_SITES = {"sat.tv"}
SITE_PRIORITY = {
    "shahid.mbc.net": 10,
    "rotana.net": 11,
    "roya-tv.com": 12,
    "aljazeera.com": 13,
    "artonline.tv": 14,
    "ayn.om": 15,
    "bein.com": 16,
    "beinsports.com": 17,
    "saudiatv.sa": 18,
    "sba.net.ae": 19,
    "dmi.gov.ae": 20,
    "osn.com": 25,
    "elcinema.com": 100,
    "epgshare01.online": 900,
}
PLACEHOLDER_RE = re.compile(
    r"^(?:tv guide is not available|schedule unavailable|programme schedule unavailable|"
    r"program schedule unavailable|no information|no info|tba|جدول البرامج غير متاح|"
    r"لا توجد معلومات|لا يوجد برنامج)$", re.I)


def load_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def copy_channel(c):
    return ET.fromstring(ET.tostring(c, encoding="utf-8"))


def row_rank(c, current_site):
    site = (c.get("site") or "").strip()
    lang = (c.get("lang") or "").strip().lower()
    # Different current site is mandatory. Arabic preferred, then English,
    # then other languages. Official sites outrank broad aggregators.
    lang_rank = 0 if lang.startswith("ar") else (1 if lang.startswith("en") else 2)
    official_rank = SITE_PRIORITY.get(site, 500)
    return (lang_rank, official_rank, site, (c.get("site_id") or ""))


def plan(args):
    rows = load_csv(args.all_id_csv)
    no_epg = {r.get("id", ""): r for r in rows if (r.get("verdict") or "") == "NO_EPG" and r.get("id")}
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    current_site = {x.get("xmltv_id", ""): x.get("site", "") for x in catalog.get("channels", []) or []}

    alternatives = defaultdict(list)
    parse_errors = []
    root = Path(args.epg_root)
    for path in sorted(root.glob("sites/**/*.channels.xml")):
        try:
            tv = ET.parse(path).getroot()
        except Exception as exc:
            parse_errors.append({"path": str(path), "error": str(exc)[:160]})
            continue
        for c in tv.findall("channel"):
            cid = (c.get("xmltv_id") or "").strip()
            if cid not in no_epg:
                continue
            site = (c.get("site") or path.parent.name or "").strip()
            if not site or site in EXCLUDED_SITES or not (c.get("site_id") or "").strip():
                continue
            if site == current_site.get(cid, ""):
                continue
            alternatives[cid].append((copy_channel(c), str(path.relative_to(root))))

    out_channels = ET.Element("channels")
    selected = []
    for cid in sorted(no_epg, key=str.casefold):
        candidates = alternatives.get(cid, [])
        if not candidates:
            continue
        candidates.sort(key=lambda row: row_rank(row[0], current_site.get(cid, "")))
        c, source_file = candidates[0]
        out_channels.append(c)
        selected.append({
            "id": cid,
            "name": no_epg[cid].get("name", ""),
            "shard": no_epg[cid].get("shard", ""),
            "current_site": current_site.get(cid, ""),
            "candidate_site": (c.get("site") or "").strip(),
            "candidate_site_id": (c.get("site_id") or "").strip(),
            "candidate_lang": (c.get("lang") or "").strip(),
            "candidate_name": (c.text or "").strip(),
            "candidate_source_file": source_file,
            "alternate_count": len(candidates),
        })

    ET.indent(out_channels, space="  ")
    Path(args.channels_out).write_bytes(ET.tostring(out_channels, encoding="utf-8", xml_declaration=True))
    report = {
        "schema": 1,
        "mode": "exact-id-alternate-source-plan",
        "no_epg_input": len(no_epg),
        "targets_with_alternative": len(selected),
        "excluded_sites": sorted(EXCLUDED_SITES),
        "parse_errors": parse_errors[:20],
        "targets": selected,
    }
    Path(args.plan_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("DUPLICATE RESCUE PLAN: no_epg=%d alternatives=%d" % (len(no_epg), len(selected)))


def parse_dt(value):
    raw = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})", raw)
    if not m:
        return None
    s = m.group(1)
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S" if len(s) == 14 else "%Y%m%d%H%M")
    except Exception:
        return None


def first_text(p, tag):
    n = p.find(tag)
    return ((n.text or "").strip() if n is not None else "")


def evaluate(args):
    plan_data = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in plan_data.get("targets", [])}
    root = ET.parse(args.grabbed).getroot()
    events = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid in by_id:
            events[cid].append(p)

    results = []
    counts = Counter()
    for cid in sorted(by_id, key=str.casefold):
        meta = by_id[cid]
        rows = sorted(events.get(cid, []), key=lambda p: p.get("start") or "")
        placeholders = 0
        invalid = 0
        long_gt_12h = 0
        coverage_h = 0.0
        titles = []
        for p in rows:
            title = first_text(p, "title")
            if title:
                titles.append(title)
                if PLACEHOLDER_RE.match(title):
                    placeholders += 1
            start, stop = parse_dt(p.get("start")), parse_dt(p.get("stop"))
            if start is None or stop is None or stop <= start:
                invalid += 1
                continue
            dur = (stop - start).total_seconds() / 3600.0
            coverage_h += dur
            if dur > 12:
                long_gt_12h += 1
        n = len(rows)
        generic_ratio = placeholders / float(max(1, n))
        if n >= 3 and invalid == 0 and long_gt_12h == 0 and generic_ratio <= 0.10 and coverage_h >= 6:
            verdict = "SAFE_EXACT_ID_RESCUE"
        elif n > 0:
            verdict = "REVIEW_EXACT_ID_RESCUE"
        else:
            verdict = "EMPTY_ALTERNATE"
        counts[verdict] += 1
        results.append(dict(meta,
            events=n,
            coverage_h=round(coverage_h, 2),
            placeholders=placeholders,
            invalid=invalid,
            long_gt_12h=long_gt_12h,
            verdict=verdict,
            samples=titles[:3],
        ))

    out = {
        "schema": 1,
        "mode": "exact-id-alternate-source-evaluation",
        "counts": dict(counts),
        "results": results,
        "safe": [x for x in results if x["verdict"] == "SAFE_EXACT_ID_RESCUE"],
    }
    Path(args.report_json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "OFFICIAL EXACT-ID DUPLICATE RESCUE AUDIT",
        "targets=%d safe=%d review=%d empty=%d" % (
            len(results), counts["SAFE_EXACT_ID_RESCUE"], counts["REVIEW_EXACT_ID_RESCUE"], counts["EMPTY_ALTERNATE"]),
        "",
        "SAFE RESCUES",
    ]
    for x in out["safe"]:
        lines.append("- %s | %s | %s -> %s | lang=%s events=%d cov=%.1fh ph=%d" % (
            x["shard"], x["id"], x["current_site"] or "<none>", x["candidate_site"],
            x["candidate_lang"] or "?", x["events"], x["coverage_h"], x["placeholders"]))
        if x["samples"]:
            lines.append("    samples=%s" % " | ".join(x["samples"]))
    lines += ["", "REVIEW/EMPTY"]
    for x in results:
        if x["verdict"] == "SAFE_EXACT_ID_RESCUE":
            continue
        lines.append("- [%s] %s | %s | %s -> %s | events=%d cov=%.1fh ph=%d" % (
            x["verdict"], x["shard"], x["id"], x["current_site"] or "<none>",
            x["candidate_site"], x["events"], x["coverage_h"], x["placeholders"]))
    Path(args.report_text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--epg-root", required=True)
    p.add_argument("--all-id-csv", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--channels-out", required=True)
    p.add_argument("--plan-json", required=True)
    p.set_defaults(fn=plan)
    e = sub.add_parser("evaluate")
    e.add_argument("--plan-json", required=True)
    e.add_argument("--grabbed", required=True)
    e.add_argument("--report-json", required=True)
    e.add_argument("--report-text", required=True)
    e.set_defaults(fn=evaluate)
    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
