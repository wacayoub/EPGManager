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
import mena_cloud_merge_safe as safe  # installs production logical-key normalization
import mena_integrity_guard as guard

SOURCES = [
    ("iptv-epg-eg", "eg", "https://iptv-epg.org/files/epg-eg.xml.gz"),
    ("iptv-epg-lb", "lb", "https://iptv-epg.org/files/epg-lb.xml.gz"),
    ("iptv-epg-ae", "ae", "https://iptv-epg.org/files/epg-ae.xml.gz"),
    # Arabic guide generated mainly from elCinema Arabic listings. Diagnostic only.
    ("ghaleb-arabic-epg", "mena", "https://raw.githubusercontent.com/GhalebAldoboni/EPG-Guide/master/ArabicEPG.xml"),
]
COUNTRY_RE = re.compile(r"\.([a-z]{2})(?:@|$)", re.I)
COUNTRY_SHARD_RE = re.compile(r"^mena-([a-z]{2})$", re.I)


def download(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "EPGManager-Recovery-Audit/1.1 (+https://github.com/wacayoub/EPGManager)",
        "Accept": "application/xml,application/gzip,*/*",
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
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


def logical_country_compatible(target_row, candidate, source_country):
    tc = target_country(target_row)
    cc = country_of(candidate.cid)
    if source_country != "mena":
        return bool(tc and tc == source_country)
    # Global Arabic recovery: require native-country agreement when both sides
    # expose country metadata. If either side has no suffix, exact ID is still
    # handled separately and logical matching stays conservative.
    return bool(tc and cc and tc == cc)


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
    for source_name, expected_country, url in SOURCES:
        report = {"source": source_name, "country": expected_country, "url": url}
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
                    candidates = [(clean_by_id[target_id], "EXACT_ID", 1.0)]
                else:
                    key = base.logical_key(target_id, target_name)
                    for c in clean_by_key.get(key, []):
                        if logical_country_compatible(target, c, expected_country):
                            candidates.append((c, "EXACT_LOGICAL_KEY", 1.0))

                    # Fuzzy is report-only and only within the same authoritative
                    # country. It can never be auto-integrated.
                    if not candidates and expected_country != "mena" and target_country(target) == expected_country:
                        ranked = []
                        for c in clean:
                            score = max(similarity(target_name, c.name), similarity(target_id, c.cid))
                            if score >= 0.90:
                                ranked.append((score, c))
                        ranked.sort(reverse=True, key=lambda x: x[0])
                        if ranked:
                            score, c = ranked[0]
                            candidates = [(c, "FUZZY_REVIEW", score)]

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
                    all_recoveries.append(dict(item, recovery_source=source_name))

            exact = sum(1 for x in recoveries if x["match_type"] != "FUZZY_REVIEW")
            fuzzy = sum(1 for x in recoveries if x["match_type"] == "FUZZY_REVIEW")
            report.update({
                "status": "ok",
                "bytes": len(data),
                "raw_current_candidates": len(raw),
                "clean_current_candidates": len(clean),
                "quarantined_candidates": int(findings.get("quarantined_candidates", 0) or 0),
                "recoveries": recoveries,
                "exact_recoveries": exact,
                "fuzzy_review": fuzzy,
            })
        except Exception as exc:
            report.update({"status": "error", "error": str(exc)[:400]})
        reports.append(report)

    out = {
        "schema": 2,
        "mode": "diagnostic-only-recovery-source-audit",
        "window_hours": args.window_hours,
        "no_epg_input": len(no_epg),
        "sources": reports,
        "recoveries": all_recoveries,
        "exact_recoveries_total": sum(1 for x in all_recoveries if x["match_type"] != "FUZZY_REVIEW"),
        "fuzzy_review_total": sum(1 for x in all_recoveries if x["match_type"] == "FUZZY_REVIEW"),
    }
    Path(args.output_json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "MENA RECOVERY SOURCE AUDIT V2 - DIAGNOSTIC ONLY",
        "NO_EPG input=%d exact_recoveries=%d fuzzy_review=%d" % (
            len(no_epg), out["exact_recoveries_total"], out["fuzzy_review_total"]),
        "",
    ]
    for r in reports:
        if r.get("status") != "ok":
            lines.append("- %s: ERROR %s" % (r["source"], r.get("error", "")))
            continue
        lines.append("- %s: raw=%d clean=%d quarantined=%d exact=%d fuzzy=%d" % (
            r["source"], r["raw_current_candidates"], r["clean_current_candidates"],
            r["quarantined_candidates"], r["exact_recoveries"], r["fuzzy_review"]))
        for x in r["recoveries"]:
            lines.append("    [%s %.3f] %s -> %s | country=%s/%s events=%d" % (
                x["match_type"], x["match_score"], x["target_id"], x["source_id"],
                x["target_country"] or "?", x["source_country"] or "?", x["events_48h"]))
    Path(args.output_text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
