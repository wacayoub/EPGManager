#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit beIN MAX/XTRA event-channel timelines across all current remote MENA feeds.
Diagnostic only; never mutates production data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import mena_cloud_merge as base

TARGET_RE = re.compile(r"\bbein\b.*\b(?:max|xtra)\b|\b(?:max|xtra)\b.*\bbein\b", re.I)
WS_RE = re.compile(r"\s+")


def norm(v):
    v = WS_RE.sub(" ", v or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v).strip()


def title_of(p):
    n = p.find("title")
    return ((n.text or "").strip() if n is not None else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    a = ap.parse_args()

    now = datetime.now(timezone.utc) - timedelta(hours=2)
    end = now + timedelta(hours=a.window_hours + 4)
    rows = []
    source_errors = []

    for source_name, origin, url in base.REMOTE_SOURCES:
        try:
            tv = base.read_xml_bytes(base.download(url))
        except Exception as exc:
            source_errors.append({"source": source_name, "error": str(exc)[:180]})
            continue
        names = {}
        for c in tv.findall("channel"):
            cid = (c.get("id") or "").strip()
            name = base.display_name(c)
            if TARGET_RE.search("%s %s" % (cid, name)):
                names[cid] = name
        events = defaultdict(list)
        for p in tv.findall("programme"):
            cid = (p.get("channel") or "").strip()
            if cid not in names:
                continue
            start = base.parse_xmltv_dt(p.get("start") or "")
            stop = base.parse_xmltv_dt(p.get("stop") or "")
            if start is None or stop is None or stop <= now or start >= end:
                continue
            events[cid].append(p)
        for cid, name in names.items():
            ps = sorted(events.get(cid, []), key=lambda p: p.get("start") or "")
            titles = [title_of(p) for p in ps if title_of(p)]
            unique = {norm(x) for x in titles if norm(x)}
            top = Counter(norm(x) for x in titles if norm(x)).most_common(1)
            top_pct = (top[0][1] / float(max(1, len(titles))) * 100.0) if top else 0.0
            fp = tuple((p.get("start") or "", p.get("stop") or "", norm(title_of(p))) for p in ps)
            rows.append({
                "source": source_name,
                "origin": origin,
                "id": cid,
                "name": name,
                "events": len(ps),
                "unique_titles": len(unique),
                "unique_title_pct": round(len(unique) / float(max(1, len(ps))) * 100.0, 1),
                "top_title_pct": round(top_pct, 1),
                "samples": titles[:4],
                "fingerprint": fp,
            })

    groups = defaultdict(list)
    for r in rows:
        if r["fingerprint"]:
            groups[(r["source"], r["fingerprint"])].append(r)

    verdicts = Counter()
    for r in rows:
        clones = groups.get((r["source"], r["fingerprint"]), []) if r["fingerprint"] else []
        reasons = []
        if r["events"] == 0:
            reasons.append("NO_EVENTS")
        if r["events"] >= 5 and r["unique_title_pct"] < 20:
            reasons.append("LOW_TITLE_DIVERSITY")
        if r["events"] >= 5 and r["top_title_pct"] >= 80:
            reasons.append("REPEATED_GENERIC_TITLE")
        if len(clones) >= 3:
            reasons.append("CLONED_TIMELINE_GROUP=%d" % len(clones))
        if reasons:
            verdict = "REJECT"
        elif r["events"] >= 3 and r["unique_title_pct"] >= 20:
            verdict = "CANDIDATE"
        else:
            verdict = "REVIEW"
        r["verdict"] = verdict
        r["reasons"] = reasons
        r["clone_ids"] = sorted(x["id"] for x in clones)
        r.pop("fingerprint", None)
        verdicts[verdict] += 1

    out = {
        "schema": 1,
        "mode": "bein-max-xtra-source-audit",
        "summary": dict(verdicts),
        "source_errors": source_errors,
        "rows": rows,
        "candidates": [r for r in rows if r["verdict"] == "CANDIDATE"],
    }
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "beIN MAX/XTRA REMOTE SOURCE AUDIT",
        "rows=%d CANDIDATE=%d REVIEW=%d REJECT=%d" % (
            len(rows), verdicts["CANDIDATE"], verdicts["REVIEW"], verdicts["REJECT"]),
        "",
        "CANDIDATES",
    ]
    for r in out["candidates"]:
        lines.append("- %s | %s | events=%d unique=%.1f%% top=%.1f%% | %s" % (
            r["source"], r["id"], r["events"], r["unique_title_pct"], r["top_title_pct"],
            " | ".join(r["samples"][:3])))
    lines += ["", "REJECT/REVIEW"]
    for r in rows:
        if r["verdict"] == "CANDIDATE":
            continue
        lines.append("- [%s] %s | %s | events=%d | %s" % (
            r["verdict"], r["source"], r["id"], r["events"], "; ".join(r["reasons"])))
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])


if __name__ == "__main__":
    main()
