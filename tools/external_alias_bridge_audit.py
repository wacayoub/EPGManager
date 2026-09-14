#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge NO_EPG identities to already-live EPGManager canonicals via verified aliases.

Diagnostic only. The external catalogue supplies identity aliases only. Programme
content must already exist in the current audited EPGManager CSV. This prevents
an external EPG feed from bypassing the normal integrity/source-quality gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import urllib.request

import mena_cloud_merge_safe as safe

ALIAS_URL = "https://raw.githubusercontent.com/Saudi23723/EPG-Guide/master/channel_aliases.json"
PROTECTED_WORDS = {
    "english", "arabic", "international", "intl", "documentary", "news",
    "sport", "sports", "quran", "koran", "kids", "junior", "cinema",
    "drama", "movies", "movie", "series", "action", "family", "business",
    "extra", "xtra", "max", "music", "live", "kurd", "kurdi", "turkuman",
    "syriac", "french", "france", "mobasher", "portrait",
}


def download_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "EPGManager-Alias-Audit/2.0"})
    with urllib.request.urlopen(req, timeout=50) as resp:
        return json.loads(resp.read().decode("utf-8"))


def as_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def norm(v):
    v = safe._compact(v or "")
    v = re.sub(r"\b(?:ar|stc|gobx|myhd)\b", " ", v)
    v = re.sub(r"\b(?:uhd|fhd|full hd|hd|sd|4k|hevc|raw|digital|mono)\b", " ", v)
    v = re.sub(r"\.(?:ae|sa|qa|eg|kw|bh|om|jo|lb|iq|ps|ye|dz|tn|ly|sd|sy|mr)(?:@.*)?$", " ", v)
    v = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", v.casefold())
    return " ".join(v.split())


def score(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a.split()), set(b.split())
    token = len(sa & sb) / float(max(1, max(len(sa), len(sb))))
    return max(SequenceMatcher(None, a, b).ratio(), token)


def variants(value):
    n = norm(value)
    words = set(n.split())
    out = {x for x in PROTECTED_WORDS if x in words}
    nums = {"num:%s" % x for x in re.findall(r"\b\d+\b", n)}
    return out | nums


def variant_compatible(a, b):
    va, vb = variants(a), variants(b)
    if va == vb:
        return True
    # Base identities may omit all variant tokens, but never silently collapse
    # one explicit variant into another.
    if not va and not vb:
        return True
    return False


def load_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def shard_compatible(target, candidate):
    ts = (target.get("shard") or "").strip().lower()
    cs = (candidate.get("shard") or "").strip().lower()
    if ts.startswith("provider-"):
        return ts == cs
    if ts.startswith("mena-") and ts != "mena-other":
        return ts == cs
    return True


def alias_match(target, aliases):
    tid = target.get("id", "")
    tname = target.get("name", "") or tid
    ranked = []
    for key, data in aliases.items():
        ids = [str(x) for x in (data.get("ids") or [])]
        names = [key] + [str(x) for x in (data.get("names") or [])]
        exact_id = tid in ids
        exact_name = norm(tname) in {norm(x) for x in names if norm(x)}
        s = max([score(tname, x) for x in names] + [score(tid, x) for x in names + ids])
        if exact_id or exact_name:
            s = 1.0
        if s >= 0.92:
            ranked.append((s, exact_id, exact_name, key, data))
    ranked.sort(reverse=True, key=lambda x: (x[1], x[2], x[0]))
    return ranked[0] if ranked else None


def best_live(target, key, data, live_rows):
    names = [key] + [str(x) for x in (data.get("names") or [])]
    ids = [str(x) for x in (data.get("ids") or [])]
    ranked = []
    for row in live_rows:
        if not shard_compatible(target, row):
            continue
        probe = "%s %s" % (row.get("id", ""), row.get("name", ""))
        target_probe = "%s %s" % (target.get("id", ""), target.get("name", ""))
        if not variant_compatible(target_probe, probe):
            continue
        exact_id = row.get("id", "") in ids
        exact_name = norm(row.get("name", "")) in {norm(x) for x in names if norm(x)}
        s = max([score(row.get("name", ""), x) for x in names] +
                [score(row.get("id", ""), x) for x in names + ids])
        if exact_id or exact_name:
            s = 1.0
        if s >= 0.86:
            ranked.append((s, exact_id, exact_name, as_int(row.get("events")), row))
    ranked.sort(reverse=True, key=lambda x: (x[1], x[2], x[0], x[3]))
    return ranked[0] if ranked else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    rows = load_rows(args.csv)
    no_epg = [r for r in rows if (r.get("verdict") or "") == "NO_EPG"]
    live = [r for r in rows if as_int(r.get("events")) > 0 and (r.get("verdict") or "") != "FAIL"]
    aliases = download_json(ALIAS_URL)

    results = []
    counts = Counter()
    for target in no_epg:
        am = alias_match(target, aliases)
        if not am:
            continue
        alias_score, exact_alias_id, exact_alias_name, key, data = am
        lm = best_live(target, key, data, live)
        if not lm:
            continue
        live_score, exact_live_id, exact_live_name, events, canonical = lm

        target_probe = "%s %s" % (target.get("id", ""), target.get("name", ""))
        canonical_probe = "%s %s" % (canonical.get("id", ""), canonical.get("name", ""))
        variants_ok = variant_compatible(target_probe, canonical_probe)

        if (variants_ok and (exact_alias_id or exact_alias_name)
                and (exact_live_id or exact_live_name) and live_score >= 0.94):
            verdict = "SAFE_ALIAS_TO_LIVE"
        elif variants_ok and alias_score >= 0.94 and live_score >= 0.94:
            verdict = "REVIEW_ALIAS_TO_LIVE"
        else:
            verdict = "REJECT_ALIAS_TO_LIVE"
        counts[verdict] += 1
        results.append({
            "target_shard": target.get("shard", ""),
            "target_id": target.get("id", ""),
            "target_name": target.get("name", ""),
            "alias_key": key,
            "alias_score": round(alias_score, 3),
            "exact_alias_id": exact_alias_id,
            "exact_alias_name": exact_alias_name,
            "canonical_shard": canonical.get("shard", ""),
            "canonical_id": canonical.get("id", ""),
            "canonical_name": canonical.get("name", ""),
            "canonical_events": events,
            "canonical_verdict": canonical.get("verdict", ""),
            "live_score": round(live_score, 3),
            "exact_live_id": exact_live_id,
            "exact_live_name": exact_live_name,
            "variants_target": sorted(variants(target_probe)),
            "variants_canonical": sorted(variants(canonical_probe)),
            "verdict": verdict,
        })

    out = {
        "schema": 2,
        "mode": "verified-alias-to-existing-live-canonical-audit",
        "no_epg_input": len(no_epg),
        "live_input": len(live),
        "alias_entries": len(aliases),
        "counts": dict(counts),
        "matches": results,
        "safe": [x for x in results if x["verdict"] == "SAFE_ALIAS_TO_LIVE"],
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "EXTERNAL ALIAS -> EXISTING LIVE CANONICAL AUDIT V2",
        "NO_EPG=%d live=%d aliases=%d safe=%d review=%d rejected=%d" % (
            len(no_epg), len(live), len(aliases), counts["SAFE_ALIAS_TO_LIVE"],
            counts["REVIEW_ALIAS_TO_LIVE"], counts["REJECT_ALIAS_TO_LIVE"]),
        "",
        "SAFE",
    ]
    for x in out["safe"]:
        lines.append("- %s | %s -> %s | alias=%.3f live=%.3f events=%d variants=%s" % (
            x["target_shard"], x["target_id"], x["canonical_id"], x["alias_score"],
            x["live_score"], x["canonical_events"], ",".join(x["variants_target"]) or "base"))
    lines += ["", "REVIEW/REJECT"]
    for x in results:
        if x["verdict"] == "SAFE_ALIAS_TO_LIVE":
            continue
        lines.append("- [%s] %s | %s -> %s | alias=%.3f live=%.3f variants=%s/%s" % (
            x["verdict"], x["target_shard"], x["target_id"], x["canonical_id"],
            x["alias_score"], x["live_score"],
            ",".join(x["variants_target"]) or "base",
            ",".join(x["variants_canonical"]) or "base"))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
