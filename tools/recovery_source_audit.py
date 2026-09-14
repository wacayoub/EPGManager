#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic audit for candidate MENA recovery XMLTV feeds.

Nothing here is consumed by production. The scanner answers one question:
which current EPGManager NO_EPG identities could a candidate source recover
without accepting cloned/placeholder/technical schedules?
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import csv
import gzip
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

import mena_cloud_merge as base
import mena_cloud_merge_safe as safe
import mena_integrity_guard as guard

SOURCES = [
    {"name": "iptv-epg-eg", "scope": "country", "country": "eg", "trust": "candidate", "url": "https://iptv-epg.org/files/epg-eg.xml.gz"},
    {"name": "iptv-epg-lb", "scope": "country", "country": "lb", "trust": "candidate", "url": "https://iptv-epg.org/files/epg-lb.xml.gz"},
    {"name": "iptv-epg-ae", "scope": "country", "country": "ae", "trust": "candidate", "url": "https://iptv-epg.org/files/epg-ae.xml.gz"},
    {"name": "ghaleb-arabic-epg", "scope": "mena", "country": "", "trust": "candidate", "url": "https://raw.githubusercontent.com/GhalebAldoboni/EPG-Guide/master/ArabicEPG.xml"},
    # Legacy/global sources are review-only even on an exact identity match.
    {"name": "legacy-guidearab", "scope": "mena", "country": "", "trust": "review", "url": "http://195.154.221.171/epg/guidearab.xml.gz"},
    {"name": "epgpw-lite", "scope": "global", "country": "", "trust": "review", "url": "https://epg.pw/xmltv/epg_lite.xml.gz"},
]
COUNTRY_RE = re.compile(r"\.([a-z]{2})(?:@|$)", re.I)
COUNTRY_SHARD_RE = re.compile(r"^mena-([a-z]{2})$", re.I)


def download(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "EPGManager-Recovery-Audit/1.2 (+https://github.com/wacayoub/EPGManager)",
        "Accept": "application/xml,application/gzip,*/*",
    })
    with urllib.request.urlopen(req, timeout=55) as resp:
        return resp.read()


def parse(data):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def country_of(cid):
    m = COUNTRY_RE.search((cid or "").strip())
    return m.group(1).lower() if m else ""


def target_country(row):
    shard = (row.get("shard") or "").strip().lower()
    m = COUNTRY_SHARD_RE.match(shard)
    if m:
        return m.group(1)
    return country_of(row.get("id", ""))


def compact(value):
    value = safe._compact(value or "")
    drop = {"hd", "sd", "uhd", "fhd", "tv", "channel", "digital", "mono", "ar", "en"}
    return " ".join(x for x in value.split() if x not in drop)


def similarity(a, b):
    aa, bb = compact(a), compact(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def source_candidate_rows(root, source_name, now, end):
    rows = base.load_candidates(root, "recovery-audit", source_name, {}, now, end)
    clean, findings = guard.sanitize_candidate_rows(rows, source_name=source_name, detect_clones=True)
    return rows, clean, findings


def load_no_epg(csv_path):
    rows = []
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("verdict") or "").strip() == "NO_EPG":
                rows.append(row)
    return rows


def country_compatible(target_row, candidate, source):
    tc = target_country(target_row)
    cc = country_of(candidate.cid)
    scope = source["scope"]
    if scope == "country":
        return bool(tc and tc == source["country"])
    if scope == "mena":
        return bool(tc and cc and tc == cc)
    # Global feeds may omit country metadata; no automatic country assumption.
    return False


def match_label(kind, source):
    if source["trust"] == "review":
        return kind + "_REVIEW"
    return kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-id-csv", required=True)
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--output-text", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    args = ap.parse_args()

    no_epg = load_no_epg(args.all_id_csv)
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, args.window_hours))

    reports = []
    all_recoveries = []
    for source in SOURCES:
        source_name, url = source["name"], source["url"]
        report = {"source": source_name, "scope": source["scope"], "country": source["country"], "trust": source["trust"], "url": url}
        try:
            data = download(url)
            root = parse(data)
            raw, clean, findings = source_candidate_rows(root, source_name, now, end)
            clean_by_id = {c.cid: c for c in clean}
            clean_by_key = defaultdict(list)
            for c in clean:
                if c.key:
                    clean_by_key[c.key].append(c)

            recoveries = []
            for target in no_epg:
                target_id = target.get("id", "")
                target_name = target.get("name", "") or target_id
                candidates = []
                if target_id in clean_by_id:
                    candidates = [(clean_by_id[target_id], match_label("EXACT_ID", source), 1.0)]
                else:
                    key = base.logical_key(target_id, target_name)
                    for c in clean_by_key.get(key, []):
                        if country_compatible(target, c, source):
                            candidates.append((c, match_label("EXACT_LOGICAL_KEY", source), 1.0))
                        elif source["scope"] == "global":
                            candidates.append((c, "GLOBAL_LOGICAL_REVIEW", 1.0))

                    if not candidates:
                        ranked = []
                        for c in clean:
                            score = max(similarity(target_name, c.name), similarity(target_id, c.cid))
                            compatible = country_compatible(target, c, source)
                            threshold = 0.90 if compatible else (0.97 if source["scope"] == "global" else 1.01)
                            if score >= threshold:
                                ranked.append((score, c, compatible))
                        ranked.sort(reverse=True, key=lambda x: x[0])
                        if ranked:
                            score, c, compatible = ranked[0]
                            kind = "FUZZY_REVIEW" if compatible else "GLOBAL_FUZZY_REVIEW"
                            candidates = [(c, kind, score)]

                for c, match_type, score in candidates[:1]:
                    item = {
                        "target_id": target_id,
                        "target_name": target_name,
                        "target_shard": target.get("shard", ""),
                        "target_country": target_country(target),
                        "source_id": c.cid,
                        "source_name": c.name,
                        "source_country": country_of(c.cid),
                        "match_type": match_type,
                        "match_score": round(score, 3),
                        "events_48h": len(c.programmes),
                    }
                    recoveries.append(item)
                    all_recoveries.append(dict(item, recovery_source=source_name, recovery_trust=source["trust"]))

            safe_exact = sum(1 for x in recoveries if x["match_type"] in {"EXACT_ID", "EXACT_LOGICAL_KEY"})
            review = len(recoveries) - safe_exact
            report.update({
                "status": "ok",
                "bytes": len(data),
                "raw_current_candidates": len(raw),
                "clean_current_candidates": len(clean),
                "quarantined_candidates": int(findings.get("quarantined_candidates", 0) or 0),
                "recoveries": recoveries,
                "safe_exact_recoveries": safe_exact,
                "review_candidates": review,
            })
        except Exception as exc:
            report.update({"status": "error", "error": str(exc)[:400]})
        reports.append(report)

    safe_total = sum(1 for x in all_recoveries if x["match_type"] in {"EXACT_ID", "EXACT_LOGICAL_KEY"})
    review_total = len(all_recoveries) - safe_total
    out = {
        "schema": 3,
        "mode": "diagnostic-only-recovery-source-audit",
        "window_hours": args.window_hours,
        "no_epg_input": len(no_epg),
        "sources": reports,
        "recoveries": all_recoveries,
        "safe_exact_recoveries_total": safe_total,
        "review_candidates_total": review_total,
    }
    Path(args.output_json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "MENA RECOVERY SOURCE AUDIT V3 - DIAGNOSTIC ONLY",
        "NO_EPG input=%d safe_exact=%d review_candidates=%d" % (len(no_epg), safe_total, review_total),
        "",
    ]
    for r in reports:
        if r.get("status") != "ok":
            lines.append("- %s: ERROR %s" % (r["source"], r.get("error", "")))
            continue
        lines.append("- %s [%s/%s]: raw=%d clean=%d quarantined=%d safe_exact=%d review=%d" % (
            r["source"], r["scope"], r["trust"], r["raw_current_candidates"],
            r["clean_current_candidates"], r["quarantined_candidates"],
            r["safe_exact_recoveries"], r["review_candidates"]))
        for x in r["recoveries"]:
            lines.append("    [%s %.3f] %s -> %s | country=%s/%s events=%d" % (
                x["match_type"], x["match_score"], x["target_id"], x["source_id"],
                x["target_country"] or "?", x["source_country"] or "?", x["events_48h"]))
    Path(args.output_text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
