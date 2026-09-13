#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exhaustive programme audit for every published MENA Cloud XMLTV ID.
Diagnostic only: no feed is modified.
"""
from __future__ import annotations

import argparse, csv, gzip, json, re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import xml.etree.ElementTree as ET

from bein_id_audit import build_profile, display_name, clean

COUNTRY = ("ae","sa","eg","qa","kw","bh","om","jo","lb","iq","ps","ye","dz","tn","ly","sd","sy","mr")
COUNTRY_SHARDS = {"mena-%s" % x: x for x in COUNTRY}
ARABIC_PROVIDERS = {"provider-mbc","provider-adm","provider-dmi","provider-rotana","provider-art","provider-ssc","provider-alkass"}
PREMIUM = {"provider-bein","provider-osn"}

# These are known source artefacts / impossible linear-channel IDs. Keep this
# deliberately strict so a legitimate brand such as "Logos TV" is not rejected.
TECH_PATTERNS = [
    re.compile(r"brand\s*logo", re.I),
    re.compile(r"logo\.svg", re.I),
    re.compile(r"(?:^|[-_ ])logo(?:[-_ .]|$)", re.I),
    re.compile(r"colour[-_ ]?blue", re.I),
    re.compile(r"\b200x200\b", re.I),
    re.compile(r"\bstacked\s+nov\b", re.I),
    re.compile(r"\bupdatez[-_ ]?ngw\b", re.I),
    re.compile(r"\bplaceholder\b", re.I),
    re.compile(r"\bdummy\b", re.I),
]
TECH_ALLOWLIST = {"logos.tv.ae"}  # Real channel/brand, not a logo filename.
KNOWN_BAD_IDS = {
    "bein sports66 digital -01.qa": "SUSPICIOUS_BEIN_SPORTS66_ID",
    "bein_sports66_digital_mono-01_en.bein": "SUSPICIOUS_BEIN_SPORTS66_ID",
}


def read_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def feed_country(cid):
    low = (cid or "").casefold()
    for cc in COUNTRY:
        if re.search(r"\.%s(?:@|$)" % cc, low):
            return cc
    return ""


def is_technical_id(cid):
    low = (cid or "").strip().casefold()
    if low in TECH_ALLOWLIST:
        return False
    if low.startswith(("logos-_", "logos_")):
        return True
    return any(p.search(cid or "") for p in TECH_PATTERNS)


def identity_name(v):
    s = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", clean(v))
    drop = {"hd","sd","uhd","4k","tv","channel","digital","mono","ar","en","english","arabic"}
    return " ".join(x for x in s.split() if x not in drop)


def similar(a, b):
    return SequenceMatcher(None, identity_name(a), identity_name(b)).ratio()


def grade(r):
    issues, warnings = [], []
    n, stem = r["events"], r["shard"]

    def bad(x):
        if x not in issues:
            issues.append(x)

    def warn(x):
        if x not in warnings:
            warnings.append(x)

    if n == 0:
        bad("NO_PROGRAMMES")
    if r["invalid"]:
        bad("INVALID_DURATION=%d" % r["invalid"])
    if r["overlaps"]:
        bad("OVERLAPS=%d" % r["overlaps"])
    if r["mixed_tz_days"]:
        bad("MIXED_TIMEZONE_DAYS=%d" % r["mixed_tz_days"])
    if r["long_gt_12h"]:
        bad("VERY_LONG_GT_12H=%d" % r["long_gt_12h"])
    elif r["long_gt_6h"]:
        warn("LONG_GT_6H=%d" % r["long_gt_6h"])
    if r["empty_title"]:
        bad("EMPTY_TITLE=%d" % r["empty_title"])
    if r["short_lt_2m"]:
        warn("SHORT_LT_2M=%d" % r["short_lt_2m"])
    if r["empty_desc"]:
        warn("EMPTY_DESC=%d(%.0f%%)" % (r["empty_desc"], r["empty_desc"] / float(max(1, n)) * 100))
    if r["desc_same_title"]:
        warn("DESC_EQUALS_TITLE=%d" % r["desc_same_title"])
    if r["placeholder"]:
        warn("PLACEHOLDER=%d" % r["placeholder"])
    if r["generic"]:
        warn("GENERIC_GUIDE=%.0f%%" % (r["generic"] / float(max(1, n)) * 100))
    if n >= 5 and r["top_title_pct"] >= 70:
        warn("REPEATED_TITLE=%.0f%%" % r["top_title_pct"])
    if n >= 8 and r["unique_title_pct"] <= 20:
        warn("LOW_TITLE_DIVERSITY=%.0f%%" % r["unique_title_pct"])
    if r["gaps_gt_2h"]:
        warn("GAPS_GT_2H=%d/TOTAL=%.1fh" % (r["gaps_gt_2h"], r["gap_hours"]))
    if 0 < r["coverage_hours"] < 6:
        warn("SPARSE_COVERAGE=%.1fh" % r["coverage_hours"])

    if is_technical_id(r["id"]):
        bad("TECHNICAL_OR_ASSET_ID")
    bad_reason = KNOWN_BAD_IDS.get((r["id"] or "").casefold())
    if bad_reason:
        bad(bad_reason)
    if r["id"].startswith(("logos-", "logos_")):
        warn("LOGO_PREFIX_ID")
    if re.search(r"(?:^|\D)20(?:1\d|2[0-5])(?:\D|$)", r["id"]) and "alkass" in r["id"].casefold():
        warn("STALE_YEAR_IN_ID")

    cc, expected = feed_country(r["id"]), COUNTRY_SHARDS.get(stem, "")
    r["feed_country"] = cc
    if cc and expected and cc != expected:
        warn("FOREIGN_FEED_ID=%s_EXPECTED=%s" % (cc.upper(), expected.upper()))

    if stem in PREMIUM:
        if n and r["title_has_latin_pct"] < 70:
            warn("PREMIUM_TITLE_LATIN_LOW=%.0f%%" % r["title_has_latin_pct"])
        if n and r["desc_ar_pct"] < 70:
            warn("PREMIUM_AR_DESC_LOW=%.0f%%" % r["desc_ar_pct"])
    elif stem in ARABIC_PROVIDERS:
        if n and r["title_has_ar_pct"] < 45:
            warn("ARABIC_PROVIDER_TITLE_AR_LOW=%.0f%%" % r["title_has_ar_pct"])
        if n and r["desc_ar_pct"] < 45 and r["empty_desc"] < n:
            warn("ARABIC_PROVIDER_DESC_AR_LOW=%.0f%%" % r["desc_ar_pct"])

    r["issues"], r["warnings"] = issues, warnings
    r["score"] = max(0, 100 - 35 * len(issues) - min(50, 8 * len(warnings)))
    r["verdict"] = "FAIL" if issues else "REVIEW" if warnings else "PASS"
    r["auto_lock_safe"] = bool(r["verdict"] == "PASS" and n and r["coverage_hours"] >= 6)


def apply_duplicate_verdicts(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["events"]:
            groups[r["fingerprint"]].append(r)
    duplicates = []
    for members in groups.values():
        if len(members) < 2:
            continue
        unrelated = False
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if similar(members[i]["name"], members[j]["name"]) < 0.45:
                    unrelated = True
                    break
            if unrelated:
                break
        duplicates.append({"ids": [x["id"] for x in members], "unrelated": unrelated})
        for r in members:
            group_tag = "DUPLICATE_TIMELINE_GROUP=%d" % len(members)
            if group_tag not in r["warnings"]:
                r["warnings"].append(group_tag)

            # Two IDs can be a legitimate alias/simulcast. Three or more unrelated
            # channel names carrying a byte-identical schedule is strong evidence
            # that programme rows were assigned to the wrong channels upstream.
            if unrelated and len(members) >= 3:
                issue = "WRONG_PROGRAMME_ASSIGNMENT_CLONED_TIMELINE=%d" % len(members)
                if issue not in r["issues"]:
                    r["issues"].append(issue)
                r["verdict"] = "FAIL"
                r["score"] = min(r["score"], 40)
                r["auto_lock_safe"] = False
            elif unrelated:
                if "DUPLICATE_TIMELINE_UNRELATED" not in r["warnings"]:
                    r["warnings"].append("DUPLICATE_TIMELINE_UNRELATED")
                if r["verdict"] == "PASS":
                    r["verdict"] = "REVIEW"
                    r["score"] = min(r["score"], 92)
                    r["auto_lock_safe"] = False
            elif r["verdict"] == "PASS":
                r["verdict"] = "REVIEW"
                r["score"] = min(r["score"], 92)
                r["auto_lock_safe"] = False
    return duplicates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--csv", required=True)
    a = ap.parse_args()
    root = Path(a.dir)
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    stems = list((manifest.get("shards") or {}).keys())
    rows, missing = [], []

    for stem in stems:
        p = root / (stem + ".xml.gz")
        if not p.exists():
            missing.append(stem)
            continue
        tv = read_root(p)
        channels, events = {}, defaultdict(list)
        for ch in tv.findall("channel"):
            cid = (ch.get("id") or "").strip()
            if cid:
                channels[cid] = display_name(ch)
        for ev in tv.findall("programme"):
            cid = (ev.get("channel") or "").strip()
            if cid:
                events[cid].append(ev)
        for cid in sorted(channels, key=str.casefold):
            r = build_profile(cid, channels[cid], events.get(cid, []))
            r["shard"] = stem
            grade(r)
            rows.append(r)

    duplicates = apply_duplicate_verdicts(rows)

    counts = Counter(r["verdict"] for r in rows)
    by = defaultdict(Counter)
    issue_counts = Counter()
    warning_counts = Counter()
    for r in rows:
        by[r["shard"]][r["verdict"]] += 1
        by[r["shard"]]["channels"] += 1
        by[r["shard"]]["programmes"] += r["events"]
        for x in r["issues"]:
            issue_counts[x.split("=")[0]] += 1
        for x in r["warnings"]:
            warning_counts[x.split("=")[0]] += 1

    out = {
        "schema": 2,
        "mode": "virtual-epgmanager-all-id-programme-audit",
        "summary": {
            "channels": len(rows),
            "programmes": sum(r["events"] for r in rows),
            "PASS": counts["PASS"],
            "REVIEW": counts["REVIEW"],
            "FAIL": counts["FAIL"],
            "auto_lock_safe": sum(r["auto_lock_safe"] for r in rows),
            "missing_shards": missing,
        },
        "issue_counts": dict(issue_counts),
        "warning_counts": dict(warning_counts),
        "by_shard": {k: dict(v) for k, v in sorted(by.items())},
        "channels": [{k: v for k, v in r.items() if k not in {"rows", "fingerprint"}} for r in rows],
        "duplicate_timeline_groups": duplicates,
    }
    Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with Path(a.csv).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["shard", "id", "name", "verdict", "score", "auto_lock", "events", "coverage_h", "span_h", "gaps_gt_2h", "empty_desc", "title_ar_pct", "title_latin_pct", "desc_ar_pct", "feed_country", "issues", "warnings", "preview_1", "preview_2"])
        for r in rows:
            pv = [x["title"] for x in r["preview"]]
            w.writerow([r["shard"], r["id"], r["name"], r["verdict"], r["score"], int(r["auto_lock_safe"]), r["events"], r["coverage_hours"], r["span_hours"], r["gaps_gt_2h"], r["empty_desc"], r["title_has_ar_pct"], r["title_has_latin_pct"], r["desc_ar_pct"], r["feed_country"], "; ".join(r["issues"]), "; ".join(r["warnings"]), pv[0] if pv else "", pv[1] if len(pv) > 1 else ""])

    lines = [
        "VIRTUAL EPGMANAGER - EXHAUSTIVE ALL-ID PROGRAMME AUDIT V2",
        "channels=%d programmes=%d PASS=%d REVIEW=%d FAIL=%d AUTO_LOCK_SAFE=%d" % (
            len(rows), sum(r["events"] for r in rows), counts["PASS"], counts["REVIEW"], counts["FAIL"], sum(r["auto_lock_safe"] for r in rows)
        ),
        "",
        "HARD ISSUE COUNTS",
    ]
    for k, v in issue_counts.most_common():
        lines.append("- %s: %d" % (k, v))
    lines += ["", "SHARD SUMMARY"]
    for s in stems:
        c = by[s]
        lines.append("- %s: channels=%d programmes=%d PASS=%d REVIEW=%d FAIL=%d" % (s, c["channels"], c["programmes"], c["PASS"], c["REVIEW"], c["FAIL"]))
    lines += ["", "ID-BY-ID RESULTS", ""]
    for i, r in enumerate(rows, 1):
        lines.append("%04d. [%s] %s | %s | shard=%s | score=%d | auto_lock=%s" % (i, r["verdict"], r["id"], r["name"], r["shard"], r["score"], "YES" if r["auto_lock_safe"] else "NO"))
        lines.append("      events=%d coverage=%.1fh span=%.1fh gaps>2h=%d overlaps=%d invalid=%d empty_desc=%d" % (r["events"], r["coverage_hours"], r["span_hours"], r["gaps_gt_2h"], r["overlaps"], r["invalid"], r["empty_desc"]))
        lines.append("      language: title_AR=%.0f%% title_Latin=%.0f%% desc_AR=%.0f%% feed_country=%s" % (r["title_has_ar_pct"], r["title_has_latin_pct"], r["desc_ar_pct"], r["feed_country"] or "?"))
        lines.append("      notes=%s" % (", ".join(r["issues"] + r["warnings"]) or "NONE"))
        for e in r["preview"][:2]:
            lines.append("      • %s | %s" % (e["start"], e["title"] or "<NO TITLE>"))
        lines.append("")
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
