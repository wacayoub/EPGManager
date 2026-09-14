#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two exhaustive All-ID audit JSON reports channel by channel."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

RANK = {"FAIL": 0, "NO_EPG": 1, "REVIEW": 2, "PASS": 3}


def key(row):
    return (row.get("shard") or "", row.get("id") or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    a = ap.parse_args()

    before = json.loads(Path(a.before).read_text(encoding="utf-8"))
    after = json.loads(Path(a.after).read_text(encoding="utf-8"))
    b = {key(x): x for x in before.get("channels", [])}
    n = {key(x): x for x in after.get("channels", [])}

    transitions = Counter()
    improved, regressed, added, removed, changed_programmes = [], [], [], [], []
    for k in sorted(set(b) | set(n), key=lambda x: (x[0], x[1].casefold())):
        old, new = b.get(k), n.get(k)
        if old is None:
            added.append(new)
            continue
        if new is None:
            removed.append(old)
            continue
        ov, nv = old.get("verdict", ""), new.get("verdict", "")
        transitions[(ov, nv)] += 1
        row = {
            "shard": k[0], "id": k[1], "name": new.get("name") or old.get("name") or "",
            "before": ov, "after": nv,
            "before_score": old.get("score"), "after_score": new.get("score"),
            "before_events": old.get("events", 0), "after_events": new.get("events", 0),
            "before_warnings": old.get("warnings", []), "after_warnings": new.get("warnings", []),
        }
        if RANK.get(nv, -1) > RANK.get(ov, -1):
            improved.append(row)
        elif RANK.get(nv, -1) < RANK.get(ov, -1):
            regressed.append(row)
        elif old.get("events", 0) != new.get("events", 0):
            changed_programmes.append(row)

    out = {
        "schema": 1,
        "before_summary": before.get("summary", {}),
        "after_summary": after.get("summary", {}),
        "transitions": {"%s->%s" % k: v for k, v in sorted(transitions.items())},
        "improved": improved,
        "regressed": regressed,
        "added": [{"shard": x.get("shard"), "id": x.get("id"), "verdict": x.get("verdict")} for x in added],
        "removed": [{"shard": x.get("shard"), "id": x.get("id"), "verdict": x.get("verdict")} for x in removed],
        "same_verdict_changed_programme_count": len(changed_programmes),
    }
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "ALL-ID BEFORE/AFTER COMPARISON",
        "improved=%d regressed=%d added=%d removed=%d same_verdict_event_changes=%d" % (
            len(improved), len(regressed), len(added), len(removed), len(changed_programmes)),
        "before=%s" % before.get("summary", {}),
        "after=%s" % after.get("summary", {}),
        "",
        "REGRESSIONS",
    ]
    for x in regressed:
        lines.append("- %s | %s | %s -> %s | events %s -> %s | score %s -> %s" % (
            x["shard"], x["id"], x["before"], x["after"], x["before_events"], x["after_events"],
            x["before_score"], x["after_score"]))
    lines += ["", "IMPROVEMENTS"]
    for x in improved:
        lines.append("- %s | %s | %s -> %s | events %s -> %s | score %s -> %s" % (
            x["shard"], x["id"], x["before"], x["after"], x["before_events"], x["after_events"],
            x["before_score"], x["after_score"]))
    lines += ["", "ADDED"]
    for x in added:
        lines.append("- %s | %s | %s" % (x.get("shard"), x.get("id"), x.get("verdict")))
    lines += ["", "REMOVED"]
    for x in removed:
        lines.append("- %s | %s | %s" % (x.get("shard"), x.get("id"), x.get("verdict")))
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])


if __name__ == "__main__":
    main()
