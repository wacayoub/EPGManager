#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def key_of(w):
    return (w or "").split("=", 1)[0].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--json", required=True)
    a = ap.parse_args()
    src = json.loads(Path(a.audit).read_text(encoding="utf-8"))
    rows = [r for r in src.get("channels", []) if r.get("verdict") == "REVIEW"]
    counts = Counter()
    by_warning = defaultdict(list)
    for r in rows:
        for w in r.get("warnings", []):
            k = key_of(w)
            counts[k] += 1
            by_warning[k].append({
                "id": r.get("id"), "name": r.get("name"), "shard": r.get("shard"),
                "events": r.get("events"), "coverage_h": r.get("coverage_hours"),
                "warning": w, "score": r.get("score"),
            })
    dangerous_keys = {
        "PLACEHOLDER", "DUPLICATE_TIMELINE_UNRELATED", "DUPLICATE_TIMELINE_GROUP",
        "GENERIC_GUIDE", "VERY_LONG_GT_12H", "OVERLAPS", "INVALID_DURATION",
    }
    dangerous = []
    for r in rows:
        ws = [key_of(x) for x in r.get("warnings", [])]
        if any(x in dangerous_keys for x in ws):
            dangerous.append({
                "id": r.get("id"), "name": r.get("name"), "shard": r.get("shard"),
                "events": r.get("events"), "score": r.get("score"),
                "warnings": r.get("warnings", []),
            })
    dangerous.sort(key=lambda x: (x["score"], x["shard"] or "", x["id"] or ""))
    out = {
        "review_channels": len(rows),
        "warning_counts": dict(counts.most_common()),
        "dangerous_review_count": len(dangerous),
        "dangerous_reviews": dangerous,
        "by_warning": {k: v for k, v in by_warning.items()},
    }
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["REVIEW PRIORITY REPORT", "review_channels=%d dangerous_review=%d" % (len(rows), len(dangerous)), "", "WARNING COUNTS"]
    for k, n in counts.most_common():
        lines.append("- %s: %d" % (k, n))
    lines += ["", "HIGH-RISK REVIEW IDS"]
    for r in dangerous:
        lines.append("- [%s] %s | events=%s score=%s | %s" % (
            r["shard"], r["id"], r["events"], r["score"], "; ".join(r["warnings"])))
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:80]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
