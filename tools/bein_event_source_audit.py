#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit beIN MAX/XTRA event-channel timelines across all current remote MENA feeds.

Diagnostic only; never mutates production data.

Policy:
- MAX/XTRA are event-channel *sources* and must be retained even when their guide
  is generic/repetitive between live events;
- generic/cloned schedules reduce mapping confidence, but do not reject/remove
  the source;
- source rows with usable events are KEEP_SOURCE;
- source rows with zero current events remain REVIEW/standby, not REJECT;
- receiver auto-lock should prefer a real event/title match and avoid locking on
  a generic MAX/XTRA placeholder alone.
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
GENERIC_RE = re.compile(
    r"beIN\s+SPORTS\s+XTRA\s+For\s+Live\s+And\s+Exclusive\s+Coverage|"
    r"^beIN\s+Sports\s+(?:MAX|XTRA)(?:\s*-.*)?$|^24/7$",
    re.I,
)


def norm(v):
    v = WS_RE.sub(" ", v or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v).strip()


def title_of(p):
    n = p.find("title")
    return ((n.text or "").strip() if n is not None else "")


def is_generic(title):
    return bool(GENERIC_RE.search((title or "").strip()))


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
            generic_count = sum(1 for x in titles if is_generic(x))
            generic_pct = generic_count / float(max(1, len(titles))) * 100.0
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
                "generic_events": generic_count,
                "generic_pct": round(generic_pct, 1),
                "samples": titles[:4],
                "fingerprint": fp,
            })

    groups = defaultdict(list)
    for r in rows:
        if r["fingerprint"]:
            groups[(r["source"], r["fingerprint"])].append(r)

    verdicts = Counter()
    mapping_modes = Counter()
    for r in rows:
        clones = groups.get((r["source"], r["fingerprint"]), []) if r["fingerprint"] else []
        warnings = []
        if r["events"] == 0:
            warnings.append("NO_CURRENT_EVENTS")
        if r["events"] >= 5 and r["unique_title_pct"] < 20:
            warnings.append("LOW_TITLE_DIVERSITY")
        if r["events"] >= 5 and r["top_title_pct"] >= 80:
            warnings.append("REPEATED_GENERIC_TITLE")
        if r["generic_pct"] >= 70:
            warnings.append("GENERIC_OFF_EVENT_GUIDE")
        if len(clones) >= 3:
            warnings.append("CLONED_TIMELINE_GROUP=%d" % len(clones))

        # Source retention and mapping quality are deliberately separate.
        # A repetitive event-channel guide is not a reason to delete the source.
        if r["events"] > 0:
            verdict = "KEEP_SOURCE"
        else:
            verdict = "REVIEW"

        if r["events"] == 0:
            mapping_mode = "standby_no_autolock"
        elif any(x in warnings for x in ("LOW_TITLE_DIVERSITY", "REPEATED_GENERIC_TITLE", "GENERIC_OFF_EVENT_GUIDE")) or len(clones) >= 3:
            mapping_mode = "event_only_or_manual"
        else:
            mapping_mode = "normal_candidate"

        r["verdict"] = verdict
        r["warnings"] = warnings
        r["mapping_mode"] = mapping_mode
        r["source_retained"] = True
        r["auto_lock_safe"] = mapping_mode == "normal_candidate"
        r["clone_ids"] = sorted(x["id"] for x in clones)
        r.pop("fingerprint", None)
        verdicts[verdict] += 1
        mapping_modes[mapping_mode] += 1

    out = {
        "schema": 2,
        "mode": "bein-max-xtra-source-retention-audit",
        "policy": "MAX/XTRA are retained event-channel sources; generic/cloned off-event guides lower mapping confidence but never cause source rejection",
        "summary": dict(verdicts),
        "mapping_modes": dict(mapping_modes),
        "source_errors": source_errors,
        "rows": rows,
        "normal_candidates": [r for r in rows if r["mapping_mode"] == "normal_candidate"],
        "event_only_or_manual": [r for r in rows if r["mapping_mode"] == "event_only_or_manual"],
    }
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "beIN MAX/XTRA REMOTE SOURCE RETENTION AUDIT",
        "rows=%d KEEP_SOURCE=%d REVIEW=%d REJECT=0" % (
            len(rows), verdicts["KEEP_SOURCE"], verdicts["REVIEW"]),
        "policy=keep MAX/XTRA as sources; generic/off-event rows are warnings, not rejection",
        "",
        "NORMAL MAPPING CANDIDATES",
    ]
    for r in out["normal_candidates"]:
        lines.append("- [KEEP_SOURCE] %s | %s | events=%d unique=%.1f%% top=%.1f%% | %s" % (
            r["source"], r["id"], r["events"], r["unique_title_pct"], r["top_title_pct"],
            " | ".join(r["samples"][:3])))
    lines += ["", "EVENT-ONLY / MANUAL MAPPING (SOURCE STILL KEPT)"]
    for r in rows:
        if r["mapping_mode"] == "normal_candidate":
            continue
        lines.append("- [%s] %s | %s | events=%d | mapping=%s | %s" % (
            r["verdict"], r["source"], r["id"], r["events"], r["mapping_mode"],
            "; ".join(r["warnings"])))
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])


if __name__ == "__main__":
    main()
