#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused regression tests for Arabic-first local MENA channels and frozen families.

Disney and National Geographic were audited ID-by-ID on the receiver-facing
48-hour production output. Their validated Arabic MENA feeds are frozen here so
future upstream changes cannot silently change language/feed identity or add a
new unaudited family member. Weak alternate feeds stay quarantined until an
explicit future audit promotes them.

The same production step also launches the exhaustive MBC/Shahid audit and
source-pin regression gate. This keeps the freeze blocking publication without
adding another receiver-side or workflow dependency.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")
AJE = re.compile(
    r"^(?:newshour|inside story|al jazeera world|the listening post|the bottom line|"
    r"people\s*&?\s*power|people and power|101 east|witness|upfront)$", re.I
)
XMLTV_DT = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?")

TARGETS = [
    ("mena-ye", r"^Aden\.TV\.ae$", "Aden TV"),
    ("mena-iq", r"^Afaq\.TV\.ae$", "AFAQ TV"),
    ("mena-eg", r"^Al\.Nada\.TV\.ae$", "Al Nada TV"),
]

# Receiver-facing IDs verified clean on 2026-09-14. These are deliberately
# exact: a renamed/replaced feed must be reviewed instead of silently inherited.
FROZEN_FAMILY_IDS = {
    ("mena-other", "Disney Channel.sa"),
    ("mena-other", "Disney Junior.sa"),
    ("mena-other", "Nat. Geo. AD.sa"),
    ("mena-other", "Nat. Geo. Wild HD.sa"),
    ("mena-other", "Nat. Geographic.sa"),
}

# These variants are known but intentionally not trusted as canonical MENA
# mapping targets. Their current defects are respectively gaps/no descriptions,
# sparse coverage/no descriptions, and an English/no-description ADM feed.
QUARANTINED_FAMILY_IDS = {
    ("mena-other", "Nat geo hd.qa"),
    ("mena-other", "NationalGeographicMiddleEast.uk@SD"),
    ("provider-adm", "Nat.Geo.Abu.Dhabi.HD.ae"),
}

FAMILY_SHARDS = ("mena-other", "provider-adm")
MIN_FROZEN_COVERAGE_HOURS = 24.0
MIN_FROZEN_AR_RATIO = 0.90


def root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def text(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def display_name(node):
    names = [(el.text or "").strip() for el in node.findall("display-name")]
    return next((x for x in names if x), "")


def lang(value):
    ar = len(AR.findall(value or ""))
    en = len(LAT.findall(value or ""))
    if ar >= 2 and ar >= en:
        return "ar"
    if en >= 2:
        return "en"
    return "other"


def parse_xmltv_dt(value):
    m = XMLTV_DT.match((value or "").strip())
    if not m:
        return None
    stamp, offset = m.groups()
    try:
        if offset:
            return datetime.strptime(stamp + offset, "%Y%m%d%H%M%S%z")
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def family_match(cid, name):
    probe = ("%s %s" % (cid or "", name or "")).casefold()
    compact = re.sub(r"[^a-z0-9]+", "", probe)
    return (
        "disney" in probe
        or "nat geo" in probe
        or "nat. geo" in probe
        or "nat geographic" in probe
        or "nat. geographic" in probe
        or "national geographic" in probe
        or "nationalgeographic" in compact
    )


def family_profile(shard, cid, name, events):
    rows = []
    invalid = 0
    empty_title = 0
    empty_desc = 0
    titles = []
    descs = []
    for p in events:
        start = parse_xmltv_dt(p.get("start"))
        stop = parse_xmltv_dt(p.get("stop"))
        title = text(p, "title")
        desc = text(p, "desc")
        if not title:
            empty_title += 1
        else:
            titles.append(title)
        if not desc:
            empty_desc += 1
        else:
            descs.append(desc)
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        rows.append((start, stop))

    rows.sort(key=lambda x: x[0])
    coverage_seconds = sum((stop - start).total_seconds() for start, stop in rows)
    gaps_gt_2h = 0
    gap_hours = 0.0
    overlaps = 0
    previous_stop = None
    for start, stop in rows:
        if previous_stop is not None:
            delta = (start - previous_stop).total_seconds()
            if delta > 7200:
                gaps_gt_2h += 1
                gap_hours += delta / 3600.0
            elif delta < 0:
                overlaps += 1
        if previous_stop is None or stop > previous_stop:
            previous_stop = stop

    title_ar_ratio = (
        sum(lang(x) == "ar" for x in titles) / float(len(titles)) if titles else 0.0
    )
    desc_ar_ratio = (
        sum(lang(x) == "ar" for x in descs) / float(len(descs)) if descs else 0.0
    )
    return {
        "shard": shard,
        "channel_id": cid,
        "name": name,
        "events": len(events),
        "coverage_hours": round(coverage_seconds / 3600.0, 3),
        "gaps_gt_2h": gaps_gt_2h,
        "gap_hours": round(gap_hours, 3),
        "overlaps": overlaps,
        "invalid": invalid,
        "empty_title": empty_title,
        "empty_desc": empty_desc,
        "title_ar_ratio": round(title_ar_ratio, 4),
        "desc_ar_ratio": round(desc_ar_ratio, 4),
    }


def frozen_issues(row):
    issues = []
    if row["events"] <= 0:
        issues.append("NO_PROGRAMMES")
    if row["coverage_hours"] < MIN_FROZEN_COVERAGE_HOURS:
        issues.append("LOW_COVERAGE=%.1fh" % row["coverage_hours"])
    if row["invalid"]:
        issues.append("INVALID=%d" % row["invalid"])
    if row["overlaps"]:
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row["gaps_gt_2h"]:
        issues.append("GAPS_GT_2H=%d/TOTAL=%.1fh" % (row["gaps_gt_2h"], row["gap_hours"]))
    if row["empty_title"]:
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])
    if row["empty_desc"]:
        issues.append("EMPTY_DESC=%d" % row["empty_desc"])
    if row["title_ar_ratio"] < MIN_FROZEN_AR_RATIO:
        issues.append("TITLE_AR_LOW=%.0f%%" % (row["title_ar_ratio"] * 100.0))
    if row["desc_ar_ratio"] < MIN_FROZEN_AR_RATIO:
        issues.append("DESC_AR_LOW=%.0f%%" % (row["desc_ar_ratio"] * 100.0))
    return issues


def audit_frozen_families(directory):
    indexed = {}
    discovered = set()
    for shard in FAMILY_SHARDS:
        path = Path(directory) / (shard + ".xml.gz")
        if not path.exists():
            continue
        r = root(path)
        names = {
            (c.get("id") or "").strip(): display_name(c)
            for c in r.findall("channel")
            if (c.get("id") or "").strip()
        }
        events = {}
        for p in r.findall("programme"):
            cid = (p.get("channel") or "").strip()
            if cid:
                events.setdefault(cid, []).append(p)
        for cid, name in names.items():
            if not family_match(cid, name):
                continue
            key = (shard, cid)
            discovered.add(key)
            indexed[key] = family_profile(shard, cid, name, events.get(cid, []))

    expected = FROZEN_FAMILY_IDS | QUARANTINED_FAMILY_IDS
    new_unaudited = sorted(discovered - expected, key=lambda x: (x[0], x[1].casefold()))
    frozen_rows = []
    quarantine_rows = []
    errors = []

    for key in sorted(FROZEN_FAMILY_IDS, key=lambda x: (x[0], x[1].casefold())):
        row = indexed.get(key)
        if row is None:
            errors.append("MISSING_FROZEN_ID=%s/%s" % key)
            frozen_rows.append({"shard": key[0], "channel_id": key[1], "status": "MISSING"})
            continue
        issues = frozen_issues(row)
        row = dict(row)
        row["issues"] = issues
        row["status"] = "PASS_FROZEN" if not issues else "FAIL_FROZEN"
        frozen_rows.append(row)
        if issues:
            errors.append("FROZEN_REGRESSION=%s/%s:%s" % (key[0], key[1], ",".join(issues)))

    for key in sorted(QUARANTINED_FAMILY_IDS, key=lambda x: (x[0], x[1].casefold())):
        row = indexed.get(key)
        if row is None:
            quarantine_rows.append({
                "shard": key[0], "channel_id": key[1], "status": "QUARANTINE_HIDDEN"
            })
            continue
        issues = frozen_issues(row)
        row = dict(row)
        row["issues"] = issues
        # Even if an upstream feed becomes clean later, explicit review is required
        # before promotion so a transient source change cannot bypass the freeze.
        row["status"] = "QUARANTINE_REVIEW" if issues else "QUARANTINE_PENDING_PROMOTION"
        quarantine_rows.append(row)

    for shard, cid in new_unaudited:
        errors.append("NEW_UNAUDITED_FAMILY_ID=%s/%s" % (shard, cid))

    return {
        "frozen": frozen_rows,
        "quarantined": quarantine_rows,
        "new_unaudited": [
            {"shard": shard, "channel_id": cid} for shard, cid in new_unaudited
        ],
        "errors": errors,
        "status": "FAIL" if errors else "PASS",
    }


def run_mbc_gate(directory):
    directory = Path(directory)
    tools = Path(__file__).resolve().parent
    audit_json = directory / "mbc-id-audit-policy.json"
    audit_text = directory / "mbc-id-audit-policy.txt"
    regression_text = directory / "mbc-final-regression.txt"
    provider_xml = directory / "provider-mbc.xml.gz"
    catalogue = directory.parent / "catalog.json"

    audit_cmd = [
        sys.executable, str(tools / "mbc_id_audit_policy.py"),
        "--xml", str(provider_xml),
        "--json", str(audit_json),
        "--text", str(audit_text),
    ]
    audit_run = subprocess.run(audit_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    regression_cmd = [
        sys.executable, str(tools / "mbc_final_regression.py"),
        "--xml", str(provider_xml),
        "--audit-json", str(audit_json),
        "--catalog-manifest", str(catalogue),
        "--text", str(regression_text),
    ]
    if audit_json.exists():
        regression_run = subprocess.run(
            regression_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    else:
        regression_run = None

    audit_payload = {}
    if audit_json.exists():
        try:
            audit_payload = json.loads(audit_json.read_text(encoding="utf-8"))
        except Exception:
            audit_payload = {}

    regression_output = regression_run.stdout.strip() if regression_run is not None else "audit JSON missing"
    regression_rc = regression_run.returncode if regression_run is not None else 1
    status = "PASS" if audit_run.returncode == 0 and regression_rc == 0 else "FAIL"
    return {
        "status": status,
        "audit_rc": audit_run.returncode,
        "regression_rc": regression_rc,
        "audit_summary": audit_payload.get("summary") or {},
        "audit_errors": audit_payload.get("errors") or [],
        "audit_output": audit_run.stdout.strip(),
        "regression_output": regression_output,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    out = []
    lines = ["ARABIC PRIORITY REGRESSION TEST", ""]
    hard_fail = False

    for shard, id_rx, label in TARGETS:
        r = root(Path(args.dir) / (shard + ".xml.gz"))
        channels = [(c.get("id") or "").strip() for c in r.findall("channel")]
        ids = [cid for cid in channels if re.search(id_rx, cid, re.I)]
        cid = ids[0] if ids else ""
        events = [p for p in r.findall("programme") if (p.get("channel") or "").strip() == cid] if cid else []
        titles = [text(p, "title") for p in events if text(p, "title")]
        descs = [text(p, "desc") for p in events if text(p, "desc")]
        ar_title = sum(lang(x) == "ar" for x in titles)
        en_title = sum(lang(x) == "en" for x in titles)
        ar_desc = sum(lang(x) == "ar" for x in descs)
        title_ar_ratio = ar_title / float(len(titles)) if titles else 0.0
        desc_ar_ratio = ar_desc / float(len(descs)) if descs else 0.0
        aje_hits = sorted({x for x in titles if AJE.match(x)})

        if not cid:
            status = "MISSING_ID"
        elif not events:
            status = "NO_SAFE_EPG"
        elif title_ar_ratio >= 0.50:
            status = "PASS_ARABIC"
        else:
            status = "EN_FALLBACK"

        if label == "AFAQ TV" and len(aje_hits) >= 3:
            status = "FAIL_WRONG_AJE_GUIDE"
            hard_fail = True

        row = {
            "label": label, "shard": shard, "channel_id": cid, "events": len(events),
            "title_ar_ratio": round(title_ar_ratio, 4),
            "title_en_ratio": round(en_title / float(len(titles)), 4) if titles else 0.0,
            "desc_ar_ratio": round(desc_ar_ratio, 4),
            "status": status, "foreign_signature": aje_hits,
            "preview": [{"title": text(p, "title"), "desc": text(p, "desc")} for p in events[:8]],
        }
        out.append(row)
        lines.append("=== %s ===" % label)
        lines.append("shard=%s id=%s events=%d status=%s" % (shard, cid or "<missing>", len(events), status))
        lines.append("title AR=%.0f%% EN=%.0f%% | desc AR=%.0f%%" % (
            row["title_ar_ratio"] * 100, row["title_en_ratio"] * 100, row["desc_ar_ratio"] * 100))
        if aje_hits:
            lines.append("foreign_signature=%s" % ", ".join(aje_hits))
        for p in events[:8]:
            lines.append("- %s" % (text(p, "title") or "<NO TITLE>"))
            d = text(p, "desc")
            if d:
                lines.append("  %s" % d[:240])
        lines.append("")

    family = audit_frozen_families(args.dir)
    if family["errors"]:
        hard_fail = True

    lines.extend(["DISNEY + NATIONAL GEOGRAPHIC FROZEN FAMILY GATE", ""])
    for row in family["frozen"]:
        lines.append("[%s] %s/%s" % (row["status"], row["shard"], row["channel_id"]))
        if row["status"] != "MISSING":
            lines.append(
                "  events=%d coverage=%.1fh gaps>2h=%d overlaps=%d invalid=%d title_AR=%.0f%% desc_AR=%.0f%% empty_desc=%d"
                % (
                    row["events"], row["coverage_hours"], row["gaps_gt_2h"], row["overlaps"],
                    row["invalid"], row["title_ar_ratio"] * 100.0,
                    row["desc_ar_ratio"] * 100.0, row["empty_desc"],
                )
            )
            lines.append("  issues=%s" % (", ".join(row["issues"]) if row["issues"] else "NONE"))
    lines.append("")
    for row in family["quarantined"]:
        lines.append("[%s] %s/%s" % (row["status"], row["shard"], row["channel_id"]))
        if row["status"] != "QUARANTINE_HIDDEN":
            lines.append("  issues=%s" % (", ".join(row["issues"]) if row["issues"] else "NONE"))
    if family["new_unaudited"]:
        lines.append("")
        lines.append("NEW UNAUDITED FAMILY IDS")
        for row in family["new_unaudited"]:
            lines.append("- %s/%s" % (row["shard"], row["channel_id"]))
    lines.append("")
    lines.append("frozen_family_gate=%s" % family["status"])

    mbc = run_mbc_gate(args.dir)
    if mbc["status"] != "PASS":
        hard_fail = True
    lines.extend(["", "MBC / SHAHID FROZEN PROVIDER GATE", ""])
    lines.append("status=%s audit_rc=%d regression_rc=%d" % (
        mbc["status"], mbc["audit_rc"], mbc["regression_rc"]))
    s = mbc.get("audit_summary") or {}
    lines.append("frozen=%s/%s secondary=%s quarantine=%s unclassified=%s" % (
        s.get("frozen_ok", 0), s.get("frozen_expected", 0), s.get("secondary", 0),
        s.get("quarantine", 0), s.get("unclassified", 0)))
    for error in mbc.get("audit_errors") or []:
        lines.append("- %s" % error)
    if mbc["status"] != "PASS":
        lines.append("audit_output=%s" % mbc.get("audit_output", "")[-1200:])
        lines.append("regression_output=%s" % mbc.get("regression_output", "")[-1200:])

    payload = {
        "schema": 3,
        "targets": out,
        "frozen_families": family,
        "mbc_shahid_gate": {
            "status": mbc["status"],
            "audit_rc": mbc["audit_rc"],
            "regression_rc": mbc["regression_rc"],
            "audit_summary": mbc.get("audit_summary") or {},
            "audit_errors": mbc.get("audit_errors") or [],
        },
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = " | ".join("%s=%s" % (x["label"], x["status"]) for x in out)
    print(summary + " | Disney+NatGeo=" + family["status"] + " | MBC+Shahid=" + mbc["status"])
    if mbc["status"] != "PASS":
        print("MBC AUDIT DETAILS:")
        print(mbc.get("audit_output", ""))
        print("MBC REGRESSION DETAILS:")
        print(mbc.get("regression_output", ""))
        if mbc.get("audit_errors"):
            print("MBC AUDIT ERRORS:")
            for error in mbc["audit_errors"]:
                print("- %s" % error)
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
