#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find NO_EPG foreign/aggregate IDs that can safely resolve to a live canonical.

Diagnostic only. It never mutates XMLTV. A candidate is considered SAFE only
when the target country shard agrees and channel identity similarity is very
high; weaker matches are emitted as REVIEW.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe

COUNTRY_SHARD_RE = re.compile(r"^mena-([a-z]{2})$", re.I)
NOISE = {
    "hd", "sd", "uhd", "fhd", "4k", "tv", "channel", "digital", "mono",
    "arabic", "ar", "en", "english", "international", "intl",
}


def as_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def country(shard):
    m = COUNTRY_SHARD_RE.match((shard or "").strip())
    return m.group(1).lower() if m else ""


def norm(v):
    v = unicodedata.normalize("NFKD", v or "").casefold()
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    v = re.sub(r"\.(?:ae|sa|qa|eg|kw|bh|om|jo|lb|iq|ps|ye|dz|tn|ly|sd|sy|mr)(?:@.*)?$", " ", v)
    v = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v)
    words = [x for x in v.split() if x not in NOISE]
    return " ".join(words)


def probes(row):
    vals = {
        norm(row.get("name", "")),
        norm(row.get("id", "")),
        norm(safe._compact("%s %s" % (row.get("id", ""), row.get("name", "")))),
    }
    return [x for x in vals if x]


def sim(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Token containment is strong for names such as "Iraq 24" vs "Iraq 24 HD".
    sa, sb = set(a.split()), set(b.split())
    contain = min(len(sa & sb) / float(max(1, len(sa))), len(sa & sb) / float(max(1, len(sb))))
    return max(SequenceMatcher(None, a, b).ratio(), contain)


def best_score(a, b):
    return max((sim(x, y) for x in probes(a) for y in probes(b)), default=0.0)


def load_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    rows = load_rows(args.csv)
    live_by_country = defaultdict(list)
    for r in rows:
        cc = country(r.get("shard", ""))
        if cc and as_int(r.get("events")) > 0:
            live_by_country[cc].append(r)

    foreign = []
    for r in rows:
        if (r.get("verdict") or "") != "NO_EPG":
            continue
        cc = country(r.get("shard", ""))
        fc = (r.get("feed_country") or "").strip().lower()
        if cc and fc and cc != fc:
            foreign.append(r)

    matches = []
    counts = Counter()
    for target in foreign:
        cc = country(target.get("shard", ""))
        ranked = []
        tkey = base.logical_key(target.get("id", ""), target.get("name", "") or target.get("id", ""))
        for candidate in live_by_country.get(cc, []):
            ckey = base.logical_key(candidate.get("id", ""), candidate.get("name", "") or candidate.get("id", ""))
            score = best_score(target, candidate)
            same_key = bool(tkey and ckey and tkey == ckey)
            if same_key:
                score = max(score, 0.995)
            ranked.append((score, same_key, as_int(candidate.get("events")), candidate))
        ranked.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
        if ranked:
            score, same_key, _events, best = ranked[0]
        else:
            score, same_key, best = 0.0, False, None

        if best is not None and (same_key or score >= 0.94):
            verdict = "SAFE_CANONICAL_REPAIR"
        elif best is not None and score >= 0.82:
            verdict = "REVIEW_CANONICAL_REPAIR"
        else:
            verdict = "NO_CANONICAL_MATCH"
        counts[verdict] += 1
        matches.append({
            "target_shard": target.get("shard", ""),
            "target_id": target.get("id", ""),
            "target_name": target.get("name", ""),
            "target_feed_country": target.get("feed_country", ""),
            "canonical_id": best.get("id", "") if best else "",
            "canonical_name": best.get("name", "") if best else "",
            "canonical_events": as_int(best.get("events")) if best else 0,
            "score": round(score, 3),
            "same_logical_key": same_key,
            "verdict": verdict,
        })

    out = {
        "schema": 1,
        "mode": "foreign-id-canonical-repair-audit",
        "foreign_no_epg": len(foreign),
        "counts": dict(counts),
        "matches": matches,
        "safe_repairs": [x for x in matches if x["verdict"] == "SAFE_CANONICAL_REPAIR"],
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "FOREIGN ID CANONICAL REPAIR AUDIT",
        "foreign_no_epg=%d safe=%d review=%d unmatched=%d" % (
            len(foreign), counts["SAFE_CANONICAL_REPAIR"], counts["REVIEW_CANONICAL_REPAIR"], counts["NO_CANONICAL_MATCH"]),
        "",
        "SAFE REPAIRS",
    ]
    for x in out["safe_repairs"]:
        lines.append("- %s | %s -> %s | score=%.3f events=%d" % (
            x["target_shard"], x["target_id"], x["canonical_id"], x["score"], x["canonical_events"]))
    lines += ["", "REVIEW / UNMATCHED"]
    for x in matches:
        if x["verdict"] == "SAFE_CANONICAL_REPAIR":
            continue
        lines.append("- [%s] %s | %s -> %s | score=%.3f" % (
            x["verdict"], x["target_shard"], x["target_id"], x["canonical_id"] or "<none>", x["score"]))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
