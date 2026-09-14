#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve NO_EPG aggregator IDs against live official catalogue identities.

Diagnostic only. The authoritative candidate identity must exist in the current
iptv-org-derived catalogue AND have a non-failing live timetable in the current
published EPGManager CSV. Protected service variants (English, News, Sport,
channel numbers, etc.) must match exactly so family channels cannot collapse.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata

COUNTRY_SHARD_RE = re.compile(r"^mena-([a-z]{2})$", re.I)
COUNTRY_ID_RE = re.compile(r"\.([a-z]{2})(?:@|$)", re.I)
NOISE = {"hd", "sd", "uhd", "fhd", "4k", "tv", "channel", "digital", "mono", "raw", "hevc"}
PROTECTED = {
    "english", "arabic", "international", "intl", "documentary", "news", "sport", "sports",
    "quran", "koran", "sunnah", "kids", "junior", "cinema", "drama", "movies", "movie",
    "series", "action", "family", "business", "extra", "xtra", "max", "music", "live",
    "kurd", "kurdi", "turkuman", "syriac", "french", "france", "mobasher", "portrait",
}


def as_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def country_from_shard(shard):
    m = COUNTRY_SHARD_RE.match((shard or "").strip())
    return m.group(1).lower() if m else ""


def country_from_id(cid):
    m = COUNTRY_ID_RE.search((cid or "").strip())
    return m.group(1).lower() if m else ""


def norm(v):
    v = unicodedata.normalize("NFKD", v or "").casefold()
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    v = re.sub(r"\.(?:ae|sa|qa|eg|kw|bh|om|jo|lb|iq|ps|ye|dz|tn|ly|sd|sy|mr)(?:@.*)?$", " ", v)
    v = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v)
    words = [x for x in v.split() if x not in NOISE]
    return " ".join(words)


def variants(v):
    n = norm(v)
    words = set(n.split())
    out = {x for x in PROTECTED if x in words}
    out |= {"num:%s" % x for x in re.findall(r"\b\d+\b", n)}
    return out


def compatible_variants(a, b):
    return variants(a) == variants(b)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a.split()), set(b.split())
    contain = len(sa & sb) / float(max(1, max(len(sa), len(sb))))
    return max(SequenceMatcher(None, a, b).ratio(), contain)


def probes(row):
    return [row.get("id", ""), row.get("name", ""), "%s %s" % (row.get("id", ""), row.get("name", ""))]


def score(a, b):
    return max((sim(x, y) for x in probes(a) for y in probes(b)), default=0.0)


def load_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    rows = load_csv(args.csv)
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    live_by_id = {
        r.get("id", ""): r for r in rows
        if r.get("id") and as_int(r.get("events")) > 0 and (r.get("verdict") or "") != "FAIL"
    }

    official_live = []
    for c in catalog.get("channels", []) or []:
        cid = (c.get("xmltv_id") or "").strip()
        live = live_by_id.get(cid)
        if not live:
            continue
        official_live.append({
            "id": cid,
            "name": (c.get("name") or cid).strip(),
            "site": c.get("site", ""),
            "country": country_from_id(cid),
            "live_shard": live.get("shard", ""),
            "events": as_int(live.get("events")),
            "live_verdict": live.get("verdict", ""),
        })

    targets = [r for r in rows if (r.get("verdict") or "") == "NO_EPG"]
    results = []
    counts = Counter()
    for t in targets:
        expected = country_from_shard(t.get("shard", ""))
        provider = (t.get("shard") or "") if (t.get("shard") or "").startswith("provider-") else ""
        ranked = []
        tprobe = "%s %s" % (t.get("id", ""), t.get("name", ""))
        for c in official_live:
            if expected and c["country"] and c["country"] != expected:
                continue
            if provider and c["live_shard"] != provider:
                continue
            cprobe = "%s %s" % (c["id"], c["name"])
            if not compatible_variants(tprobe, cprobe):
                continue
            s = score(t, c)
            if s >= 0.82:
                ranked.append((s, c["events"], c))
        ranked.sort(reverse=True, key=lambda x: (x[0], x[1]))
        if not ranked:
            continue
        s, _events, best = ranked[0]
        exact = norm(t.get("name", "")) == norm(best["name"]) or norm(t.get("id", "")) == norm(best["id"])
        same_country = bool(expected and best["country"] == expected)
        if (same_country or provider) and (exact or s >= 0.98):
            verdict = "SAFE_OFFICIAL_CANONICAL"
        elif s >= 0.94:
            verdict = "REVIEW_OFFICIAL_CANONICAL"
        else:
            verdict = "WEAK_OFFICIAL_CANONICAL"
        counts[verdict] += 1
        results.append({
            "target_shard": t.get("shard", ""),
            "target_id": t.get("id", ""),
            "target_name": t.get("name", ""),
            "expected_country": expected,
            "canonical_id": best["id"],
            "canonical_name": best["name"],
            "canonical_site": best["site"],
            "canonical_shard": best["live_shard"],
            "canonical_events": best["events"],
            "canonical_verdict": best["live_verdict"],
            "score": round(s, 3),
            "variants": sorted(variants(tprobe)),
            "verdict": verdict,
        })

    out = {
        "schema": 1,
        "mode": "official-catalog-to-live-canonical-audit",
        "no_epg_input": len(targets),
        "official_live_candidates": len(official_live),
        "counts": dict(counts),
        "matches": results,
        "safe": [x for x in results if x["verdict"] == "SAFE_OFFICIAL_CANONICAL"],
        "review": [x for x in results if x["verdict"] == "REVIEW_OFFICIAL_CANONICAL"],
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "OFFICIAL CATALOG CANONICAL AUDIT",
        "NO_EPG=%d official_live=%d safe=%d review=%d weak=%d" % (
            len(targets), len(official_live), counts["SAFE_OFFICIAL_CANONICAL"],
            counts["REVIEW_OFFICIAL_CANONICAL"], counts["WEAK_OFFICIAL_CANONICAL"]),
        "",
        "SAFE",
    ]
    for x in out["safe"]:
        lines.append("- %s | %s -> %s | score=%.3f site=%s events=%d variants=%s" % (
            x["target_shard"], x["target_id"], x["canonical_id"], x["score"],
            x["canonical_site"], x["canonical_events"], ",".join(x["variants"]) or "base"))
    lines += ["", "REVIEW"]
    for x in out["review"]:
        lines.append("- %s | %s -> %s | score=%.3f site=%s events=%d variants=%s" % (
            x["target_shard"], x["target_id"], x["canonical_id"], x["score"],
            x["canonical_site"], x["canonical_events"], ",".join(x["variants"]) or "base"))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
