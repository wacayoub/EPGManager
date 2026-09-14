#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit raw remote MENA EPG sources for cross-channel cloned schedules.
Diagnostic only. It does not modify production data.

The report also carries targeted probes for Al Kass and SSC so we can decide
whether a candidate source has real, channel-specific 48-hour EPG before it is
allowed into production.
"""
from __future__ import annotations

import argparse, gzip, json, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import xml.etree.ElementTree as ET

import mena_cloud_merge as base

FOCUS = {
    "alkass": re.compile(
        r"(?:^|[^a-z0-9])(?:al\s*kass|alkass)[\s._-]*(?:[1-8]|one|two|three|four|five|six|seven|eight)(?:[^a-z0-9]|$)",
        re.I,
    ),
    "ssc": re.compile(
        r"(?:^|[^a-z0-9])ssc(?:[\s._-]*(?:[1-5]|extra(?:[\s._-]*[1-3])?|news))?(?:[^a-z0-9]|$)",
        re.I,
    ),
}


def norm(v):
    v = (v or "").casefold()
    v = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v)
    return " ".join(v.split())


def simple_name(v):
    v = norm(v)
    drop = {"hd","sd","uhd","fhd","tv","channel","digital","mono","ar","en","english","arabic"}
    return " ".join(x for x in v.split() if x not in drop)


def similar(a, b):
    return SequenceMatcher(None, simple_name(a), simple_name(b)).ratio()


def fingerprint(rows):
    out = []
    for p in rows:
        start = (p.get("start") or "").strip()
        stop = (p.get("stop") or "").strip()
        title = p.find("title")
        title = (title.text or "").strip() if title is not None else ""
        if start and title:
            out.append((start, stop, norm(title)))
    out.sort()
    return tuple(out)


def parse(data):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def first_titles(rows, limit=5):
    out = []
    for p in sorted(rows, key=lambda x: ((x.get("start") or ""), (x.get("stop") or ""))):
        t = p.find("title")
        title = (t.text or "").strip() if t is not None else ""
        if title:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def focus_rows(names, programs):
    out = {k: [] for k in FOCUS}
    for cid, name in sorted(names.items(), key=lambda kv: kv[0].casefold()):
        probe = "%s %s" % (cid, name)
        for key, rx in FOCUS.items():
            if not rx.search(probe):
                continue
            rows = programs.get(cid, [])
            out[key].append({
                "id": cid,
                "name": name,
                "current": bool(rows),
                "events": len(rows),
                "sample_titles": first_titles(rows),
            })
    return {k: v for k, v in out.items() if v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--output-text", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=args.window_hours)
    results = []
    all_bad = []

    for source_name, origin, url in base.REMOTE_SOURCES:
        row = {"source": source_name, "origin": origin, "url": url}
        try:
            data = base.download(url)
            root = parse(data)
            names = {}
            for c in root.findall("channel"):
                cid = (c.get("id") or "").strip()
                if cid:
                    names[cid] = base.display_name(c) or cid
            programs = defaultdict(list)
            for p in root.findall("programme"):
                cid = (p.get("channel") or "").strip()
                if cid and base.in_window(p, now, end):
                    programs[cid].append(p)

            fps = defaultdict(list)
            for cid, progs in programs.items():
                fp = fingerprint(progs)
                if len(fp) >= 2:
                    fps[fp].append(cid)

            bad_groups = []
            bad_ids = set()
            for fp, ids in fps.items():
                if len(ids) < 3:
                    continue
                unrelated = False
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        if similar(names.get(ids[i], ids[i]), names.get(ids[j], ids[j])) < 0.45:
                            unrelated = True
                            break
                    if unrelated:
                        break
                if not unrelated:
                    continue
                bad_ids.update(ids)
                bad_groups.append({
                    "count": len(ids),
                    "ids": sorted(ids, key=str.casefold),
                    "names": [names.get(x, x) for x in sorted(ids, key=str.casefold)],
                    "sample_titles": [x[2] for x in list(fp)[:5]],
                })

            row.update({
                "status": "ok",
                "bytes": len(data),
                "channels": len(names),
                "channels_with_current_epg": len(programs),
                "programmes": sum(len(v) for v in programs.values()),
                "cloned_unrelated_groups": len(bad_groups),
                "cloned_unrelated_channels": len(bad_ids),
                "groups": sorted(bad_groups, key=lambda x: -x["count"]),
                "focus": focus_rows(names, programs),
            })
            if bad_ids:
                all_bad.append((source_name, len(bad_ids)))
        except Exception as exc:
            row.update({"status": "error", "error": str(exc)[:300]})
        results.append(row)

    out = {
        "schema": 2,
        "window_hours": args.window_hours,
        "sources": results,
        "sources_with_cloned_unrelated_channels": sorted(all_bad, key=lambda x: -x[1]),
    }
    Path(args.output_json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["RAW SOURCE INTEGRITY AUDIT V2", ""]
    for r in results:
        if r.get("status") != "ok":
            lines.append("- %s: ERROR %s" % (r["source"], r.get("error", "")))
            continue
        lines.append("- %s: channels=%d current=%d programmes=%d cloned_unrelated_groups=%d cloned_unrelated_channels=%d" % (
            r["source"], r["channels"], r["channels_with_current_epg"], r["programmes"],
            r["cloned_unrelated_groups"], r["cloned_unrelated_channels"]))
        for g in r["groups"][:5]:
            lines.append("    group=%d sample_ids=%s" % (g["count"], "; ".join(g["ids"][:8])))
            lines.append("      titles=%s" % " | ".join(g["sample_titles"][:3]))
        for focus_name, items in (r.get("focus") or {}).items():
            lines.append("    FOCUS %s:" % focus_name.upper())
            for item in items:
                lines.append("      %s | current=%s events=%d | %s" % (
                    item["id"], "YES" if item["current"] else "NO", item["events"],
                    " | ".join(item["sample_titles"][:3]) or "<NO CURRENT PROGRAMMES>"))
    Path(args.output_text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("source integrity:", sorted(all_bad, key=lambda x: -x[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
