#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test a verified external alias catalog as a bridge to clean Arabic EPG.

Diagnostic only. The external alias list never overrides a schedule; it only
proposes identity equivalence. The target schedule must independently pass the
normal source-integrity guard and contain current programmes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe
import mena_integrity_guard as guard

ALIAS_URL = "https://raw.githubusercontent.com/Saudi23723/EPG-Guide/master/channel_aliases.json"
EPG_URL = "https://raw.githubusercontent.com/GhalebAldoboni/EPG-Guide/master/ArabicEPG.xml"


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "EPGManager-Alias-Audit/1.0"})
    with urllib.request.urlopen(req, timeout=50) as resp:
        return resp.read()


def norm(v):
    v = safe._compact(v or "")
    v = re.sub(r"\b(?:ar|stc|gobx|myhd)\b", " ", v)
    v = re.sub(r"\b(?:uhd|fhd|full hd|hd|sd|4k|hevc|raw)\b", " ", v)
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


def load_no_epg(path):
    out = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("verdict") or "") == "NO_EPG":
                out.append(row)
    return out


def parse_source(now, end):
    root = ET.fromstring(download(EPG_URL))
    raw = base.load_candidates(root, "alias-audit", "ghaleb-arabic-epg", {}, now, end)
    clean, findings = guard.sanitize_candidate_rows(raw, source_name="ghaleb-arabic-epg", detect_clones=True)
    return clean, findings


def best_source(alias_key, alias_data, clean):
    names = [alias_key] + list(alias_data.get("names") or []) + list(alias_data.get("ids") or [])
    ranked = []
    for c in clean:
        s = max([score(x, c.name) for x in names] + [score(x, c.cid) for x in names])
        if s >= 0.86:
            ranked.append((s, len(c.programmes), c))
    ranked.sort(reverse=True, key=lambda x: (x[0], x[1]))
    return ranked[0] if ranked else None


def match_alias(target, aliases):
    tid = target.get("id", "")
    tname = target.get("name", "") or tid
    exact = []
    review = []
    for key, data in aliases.items():
        ids = [str(x) for x in data.get("ids", [])]
        names = [key] + [str(x) for x in data.get("names", [])]
        if tid in ids:
            exact.append((1.0, key, data, "EXACT_ALIAS_ID"))
            continue
        s = max([score(tname, x) for x in names] + [score(tid, x) for x in names + ids])
        if s >= 0.93:
            review.append((s, key, data, "ALIAS_NAME_REVIEW"))
    if exact:
        return sorted(exact, reverse=True, key=lambda x: x[0])[0]
    if review:
        return sorted(review, reverse=True, key=lambda x: x[0])[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    args = ap.parse_args()

    no_epg = load_no_epg(args.csv)
    aliases = json.loads(download(ALIAS_URL).decode("utf-8"))
    now = datetime.now(timezone.utc)
    clean, findings = parse_source(now, now + timedelta(hours=args.window_hours))

    results = []
    for target in no_epg:
        am = match_alias(target, aliases)
        if not am:
            continue
        alias_score, key, data, match_type = am
        sm = best_source(key, data, clean)
        if not sm:
            continue
        source_score, _events, candidate = sm
        if match_type == "EXACT_ALIAS_ID" and source_score >= 0.93:
            verdict = "SAFE_ALIAS_RECOVERY"
        else:
            verdict = "REVIEW_ALIAS_RECOVERY"
        titles = []
        for p in candidate.programmes[:3]:
            n = p.find("title")
            if n is not None and (n.text or "").strip():
                titles.append((n.text or "").strip())
        results.append({
            "target_shard": target.get("shard", ""),
            "target_id": target.get("id", ""),
            "target_name": target.get("name", ""),
            "alias_key": key,
            "alias_match_type": match_type,
            "alias_score": round(alias_score, 3),
            "source_id": candidate.cid,
            "source_name": candidate.name,
            "source_score": round(source_score, 3),
            "events": len(candidate.programmes),
            "sample_titles": titles,
            "verdict": verdict,
        })

    safe_rows = [x for x in results if x["verdict"] == "SAFE_ALIAS_RECOVERY"]
    review_rows = [x for x in results if x["verdict"] == "REVIEW_ALIAS_RECOVERY"]
    out = {
        "schema": 1,
        "mode": "verified-external-alias-bridge-audit",
        "no_epg_input": len(no_epg),
        "alias_entries": len(aliases),
        "clean_source_channels": len(clean),
        "source_quarantined": int(findings.get("quarantined_candidates", 0) or 0),
        "safe": len(safe_rows),
        "review": len(review_rows),
        "matches": results,
    }
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "EXTERNAL ALIAS BRIDGE AUDIT",
        "NO_EPG=%d aliases=%d clean_source=%d SAFE=%d REVIEW=%d" % (
            len(no_epg), len(aliases), len(clean), len(safe_rows), len(review_rows)),
        "",
    ]
    for x in results:
        lines.append("- [%s] %s | %s -> %s / %s | alias=%.3f source=%.3f events=%d" % (
            x["verdict"], x["target_shard"], x["target_id"], x["alias_key"], x["source_id"],
            x["alias_score"], x["source_score"], x["events"]))
        if x["sample_titles"]:
            lines.append("    titles=%s" % " | ".join(x["sample_titles"]))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
