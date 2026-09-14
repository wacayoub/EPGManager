#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify published NO_EPG identities before searching for new sources.

The goal is to separate real active channels that need source work from aliases,
dormant event IDs and foreign/legacy aggregator identities. Diagnostic only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe  # installs safe logical-key normalization

BEIN_EVENT_RE = re.compile(
    r"(?:bein.*sports.*(?:max|xtra)|bein\.com[-_ ]?\d+|sports[_ .-]*xtra)", re.I
)
STALE_YEAR_RE = re.compile(r"(?:^|\D)20(?:1\d|2[0-5])(?:\D|$)")
COUNTRY_SHARD_RE = re.compile(r"^mena-([a-z]{2})$", re.I)


def as_int(value):
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def load_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_alias_map(path):
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for group in report.get("aliases", []) or []:
        canonical = (group.get("canonical_id") or "").strip()
        if not canonical:
            continue
        for alias in group.get("aliases", []) or []:
            aid = (alias.get("id") or "").strip()
            if aid:
                out[aid] = canonical
    return out


def expected_country(shard):
    m = COUNTRY_SHARD_RE.match((shard or "").strip())
    return m.group(1).lower() if m else ""


def provider_family(shard):
    s = (shard or "").strip().lower()
    return s if s.startswith("provider-") else ""


def row_key(row):
    return base.logical_key(row.get("id", ""), row.get("name", "") or row.get("id", ""))


def classify(row, live_by_id, live_by_key, alias_map):
    cid = row.get("id", "")
    name = row.get("name", "") or cid
    shard = row.get("shard", "")
    probe = "%s %s" % (cid, name)

    canonical = alias_map.get(cid)
    if canonical and canonical in live_by_id:
        return "ALIAS_TO_LIVE_CANONICAL", canonical, "merge-report alias points to a live EPG identity"

    key = row_key(row)
    logical = []
    for other in live_by_key.get(key, []):
        # Provider aliases can cross country suffixes. Country shards must remain
        # in the same authoritative country to avoid repeating AE1-style mistakes.
        if provider_family(shard):
            logical.append(other)
        elif expected_country(shard) and expected_country(shard) == expected_country(other.get("shard", "")):
            logical.append(other)
    if logical:
        best = max(logical, key=lambda x: as_int(x.get("events")))
        return "LOGICAL_ALIAS_TO_LIVE", best.get("id", ""), "same normalized channel identity already has EPG"

    if (row.get("shard") or "").lower() == "provider-bein" and BEIN_EVENT_RE.search(probe):
        return "DORMANT_EVENT_OR_COMPAT_ID", "", "beIN MAX/XTRA/bein.com event/compatibility identity with no current schedule"

    if STALE_YEAR_RE.search(cid) and "alkass" in cid.casefold():
        return "STALE_COMPAT_ID", "", "year-stamped compatibility identity"

    fc = (row.get("feed_country") or "").strip().lower()
    ec = expected_country(shard)
    if fc and ec and fc != ec:
        return "FOREIGN_AGGREGATOR_ID_REVIEW", "", "feed suffix does not match authoritative country shard"

    # AE1 historically bundled many non-UAE Arab channels under .ae suffixes.
    if fc == "ae" and ec and ec != "ae":
        return "FOREIGN_AGGREGATOR_ID_REVIEW", "", "EPGShare-style UAE suffix on another country identity"

    return "ACTIVE_CHANNEL_NEEDS_SOURCE", "", "no live canonical/alias found"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--merge-report", required=False)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    rows = load_csv(args.csv)
    alias_map = load_alias_map(args.merge_report) if args.merge_report else {}
    live = [r for r in rows if as_int(r.get("events")) > 0]
    no_epg = [r for r in rows if (r.get("verdict") or "").strip() == "NO_EPG"]

    live_by_id = {r.get("id", ""): r for r in live if r.get("id")}
    live_by_key = defaultdict(list)
    for r in live:
        k = row_key(r)
        if k:
            live_by_key[k].append(r)

    triaged = []
    counts = Counter()
    by_shard = defaultdict(Counter)
    for r in no_epg:
        category, target, reason = classify(r, live_by_id, live_by_key, alias_map)
        item = {
            "shard": r.get("shard", ""),
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "feed_country": r.get("feed_country", ""),
            "category": category,
            "canonical_target": target,
            "reason": reason,
        }
        triaged.append(item)
        counts[category] += 1
        by_shard[item["shard"]][category] += 1

    out = {
        "schema": 1,
        "mode": "no-epg-triage-diagnostic",
        "total_no_epg": len(no_epg),
        "category_counts": dict(counts),
        "by_shard": {k: dict(v) for k, v in sorted(by_shard.items())},
        "channels": triaged,
        "source_hunting_queue": [x for x in triaged if x["category"] == "ACTIVE_CHANNEL_NEEDS_SOURCE"],
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "NO_EPG TRIAGE - DIAGNOSTIC",
        "total=%d source_hunting=%d" % (len(no_epg), counts["ACTIVE_CHANNEL_NEEDS_SOURCE"]),
        "",
        "CATEGORY COUNTS",
    ]
    for k, v in counts.most_common():
        lines.append("- %s: %d" % (k, v))
    lines += ["", "BY SHARD"]
    for shard in sorted(by_shard):
        c = by_shard[shard]
        lines.append("- %s: %s" % (shard, ", ".join("%s=%d" % (k, c[k]) for k in sorted(c))))
    lines += ["", "SOURCE HUNTING QUEUE"]
    for x in out["source_hunting_queue"]:
        lines.append("- %s | %s | %s" % (x["shard"], x["id"], x["name"]))
    lines += ["", "ALIASES / DORMANT / REVIEW"]
    for x in triaged:
        if x["category"] == "ACTIVE_CHANNEL_NEEDS_SOURCE":
            continue
        tail = " -> %s" % x["canonical_target"] if x["canonical_target"] else ""
        lines.append("- [%s] %s | %s%s" % (x["category"], x["shard"], x["id"], tail))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
