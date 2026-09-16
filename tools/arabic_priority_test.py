#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused regression tests for Arabic-first local MENA channels and provider families.

Disney and National Geographic are REVIEW/quarantine families: known identities
may remain visible for diagnostics but are not auto-lock/frozen targets until an
exact MENA guide is explicitly re-verified. Unknown family identities remain a
hard release blocker and are printed explicitly to Actions logs.

The same step validates the post-normalization canonical MBC family and its
Arabic title/description floors.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")
AJE = re.compile(
    r"^(?:newshour|inside story|al jazeera world|the listening post|the bottom line|"
    r"people\s*&?\s*power|people and power|101 east|witness|upfront)$", re.I
)
XMLTV_DT = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?")

TARGETS = [
    # Stable, currently published local services.  Match the canonical country
    # namespace and the logical display identity so harmless slug refinements do
    # not turn the regression probe into a false MISSING_ID pass.
    ("mena-ye", r"^Hadhramaut\.TV\.ye$", r"حضرموت|Hadhramaut", "Hadhramaut TV"),
    ("mena-iq", r"^Alsumaria\.2\.iq$", r"السومرية|Al\s*Sumaria", "Al Sumaria TV"),
    ("mena-eg", r"^Al\.Nahar(?:\.TV|\.2)?\.eg$", r"النهار|Al\s*Nahar", "Al Nahar TV"),
]

# Later source audits returned Disney/NatGeo to REVIEW. Nothing in this family is
# currently eligible for automatic FROZEN promotion.
FROZEN_FAMILY_IDS = set()

QUARANTINED_FAMILY_IDS = {
    ("mena-other", "Disney.Channel.mena"),
    ("mena-other", "Disney.Junior.mena"),
    ("mena-other", "Nat.Geo.AD.mena"),
    ("mena-other", "Nat.Geo.Wild.mena"),
    ("mena-other", "Nat.Geographic.mena"),
    ("provider-adm", "ADM.National.Geographic.Abu.Dhabi.ae"),
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
    return next((value for value in names if value), "")


def lang(value):
    ar = len(AR.findall(value or ""))
    en = len(LAT.findall(value or ""))
    if ar >= 2 and ar >= en:
        return "ar"
    if en >= 2:
        return "en"
    return "other"


def parse_xmltv_dt(value):
    match = XMLTV_DT.match((value or "").strip())
    if not match:
        return None
    stamp, offset = match.groups()
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
    for programme in events:
        start = parse_xmltv_dt(programme.get("start"))
        stop = parse_xmltv_dt(programme.get("stop"))
        title = text(programme, "title")
        desc = text(programme, "desc")
        if title:
            titles.append(title)
        else:
            empty_title += 1
        if desc:
            descs.append(desc)
        else:
            empty_desc += 1
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        rows.append((start, stop))

    rows.sort(key=lambda item: item[0])
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

    title_ar_ratio = sum(lang(value) == "ar" for value in titles) / float(len(titles)) if titles else 0.0
    desc_ar_ratio = sum(lang(value) == "ar" for value in descs) / float(len(descs)) if descs else 0.0
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
        parsed = root(path)
        names = {
            (channel.get("id") or "").strip(): display_name(channel)
            for channel in parsed.findall("channel")
            if (channel.get("id") or "").strip()
        }
        events = {}
        for programme in parsed.findall("programme"):
            cid = (programme.get("channel") or "").strip()
            if cid:
                events.setdefault(cid, []).append(programme)
        for cid, name in names.items():
            if not family_match(cid, name):
                continue
            key = (shard, cid)
            discovered.add(key)
            indexed[key] = family_profile(shard, cid, name, events.get(cid, []))

    expected = FROZEN_FAMILY_IDS | QUARANTINED_FAMILY_IDS
    new_unaudited = sorted(discovered - expected, key=lambda item: (item[0], item[1].casefold()))
    frozen_rows = []
    quarantine_rows = []
    errors = []

    for key in sorted(FROZEN_FAMILY_IDS, key=lambda item: (item[0], item[1].casefold())):
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

    for key in sorted(QUARANTINED_FAMILY_IDS, key=lambda item: (item[0], item[1].casefold())):
        row = indexed.get(key)
        if row is None:
            quarantine_rows.append({"shard": key[0], "channel_id": key[1], "status": "QUARANTINE_HIDDEN"})
            continue
        issues = frozen_issues(row)
        row = dict(row)
        row["issues"] = issues
        row["status"] = "QUARANTINE_REVIEW" if issues else "QUARANTINE_PENDING_PROMOTION"
        quarantine_rows.append(row)

    for shard, cid in new_unaudited:
        errors.append("NEW_UNAUDITED_FAMILY_ID=%s/%s" % (shard, cid))

    return {
        "frozen": frozen_rows,
        "quarantined": quarantine_rows,
        "new_unaudited": [{"shard": shard, "channel_id": cid} for shard, cid in new_unaudited],
        "discovered": [
            {"shard": shard, "channel_id": cid}
            for shard, cid in sorted(discovered, key=lambda item: (item[0], item[1].casefold()))
        ],
        "errors": errors,
        "status": "FAIL" if errors else "PASS",
    }


def run_mbc_gate(directory):
    """Canonical post-normalization MBC membership and Arabic policy gate."""
    parsed = root(Path(directory) / "provider-mbc.xml.gz")
    ids = [(c.get("id") or "").strip() for c in parsed.findall("channel")]
    programmes = parsed.findall("programme")
    event_ids = {(p.get("channel") or "").strip() for p in programmes}
    titles = [text(p, "title") for p in programmes]
    descs = [text(p, "desc") for p in programmes]
    title_ar = sum(lang(value) == "ar" for value in titles) / float(len(programmes) or 1)
    desc_ar = sum(lang(value) == "ar" for value in descs) / float(len(programmes) or 1)
    errors = []
    if len(ids) != 17 or len(set(ids)) != 17:
        errors.append("CANONICAL_MBC_COUNT=%d_EXPECTED=17" % len(ids))
    if any(not cid.startswith("MBC.") for cid in ids):
        errors.append("NONCANONICAL_MBC_NAMESPACE")
    if set(ids) - event_ids:
        errors.append("MBC_ZERO_EPG=%s" % sorted(set(ids) - event_ids))
    if title_ar < 0.75:
        errors.append("MBC_TITLE_AR_LOW=%.0f%%" % (title_ar * 100.0))
    if desc_ar < 0.90:
        errors.append("MBC_DESC_AR_LOW=%.0f%%" % (desc_ar * 100.0))
    return {
        "status": "FAIL" if errors else "PASS",
        "audit_rc": 1 if errors else 0,
        "regression_rc": 1 if errors else 0,
        "audit_summary": {
            "frozen_ok": len(ids) if not errors else 0,
            "frozen_expected": 17,
            "secondary": 0,
            "quarantine": 0,
            "unclassified": 0,
            "title_ar_ratio": round(title_ar, 4),
            "desc_ar_ratio": round(desc_ar, 4),
        },
        "audit_errors": errors,
        "audit_output": "",
        "regression_output": "",
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

    for shard, id_rx, name_rx, label in TARGETS:
        parsed = root(Path(args.dir) / (shard + ".xml.gz"))
        channel_rows = [
            ((channel.get("id") or "").strip(), display_name(channel))
            for channel in parsed.findall("channel")
        ]
        exact_ids = [cid for cid, _name in channel_rows if re.search(id_rx, cid, re.I)]
        logical_ids = [
            cid for cid, name in channel_rows
            if re.search(name_rx, name, re.I)
        ]
        ids = exact_ids or logical_ids
        cid = ids[0] if ids else ""
        events = [
            programme for programme in parsed.findall("programme")
            if cid and (programme.get("channel") or "").strip() == cid
        ]
        titles = [text(programme, "title") for programme in events if text(programme, "title")]
        descs = [text(programme, "desc") for programme in events if text(programme, "desc")]
        ar_title = sum(lang(value) == "ar" for value in titles)
        en_title = sum(lang(value) == "en" for value in titles)
        ar_desc = sum(lang(value) == "ar" for value in descs)
        title_ar_ratio = ar_title / float(len(titles)) if titles else 0.0
        desc_ar_ratio = ar_desc / float(len(descs)) if descs else 0.0
        aje_hits = sorted({value for value in titles if AJE.match(value)})

        if not cid:
            status = "MISSING_ID"
        elif not events:
            status = "NO_SAFE_EPG"
        elif title_ar_ratio >= 0.50:
            status = "PASS_ARABIC"
        else:
            status = "EN_FALLBACK"
        if len(aje_hits) >= 3:
            status = "FAIL_WRONG_AJE_GUIDE"
        if status != "PASS_ARABIC":
            hard_fail = True

        row = {
            "label": label,
            "shard": shard,
            "channel_id": cid,
            "events": len(events),
            "title_ar_ratio": round(title_ar_ratio, 4),
            "title_en_ratio": round(en_title / float(len(titles)), 4) if titles else 0.0,
            "desc_ar_ratio": round(desc_ar_ratio, 4),
            "status": status,
            "foreign_signature": aje_hits,
            "preview": [
                {"title": text(programme, "title"), "desc": text(programme, "desc")}
                for programme in events[:8]
            ],
        }
        out.append(row)
        lines.append("=== %s ===" % label)
        lines.append("shard=%s id=%s events=%d status=%s" % (shard, cid or "<missing>", len(events), status))
        lines.append("title AR=%.0f%% EN=%.0f%% | desc AR=%.0f%%" % (
            row["title_ar_ratio"] * 100.0,
            row["title_en_ratio"] * 100.0,
            row["desc_ar_ratio"] * 100.0,
        ))
        if aje_hits:
            lines.append("foreign_signature=%s" % ", ".join(aje_hits))
        lines.append("")

    family = audit_frozen_families(args.dir)
    if family["errors"]:
        hard_fail = True

    lines.extend(["DISNEY + NATIONAL GEOGRAPHIC REVIEW / QUARANTINE GATE", ""])
    for row in family["quarantined"]:
        lines.append("[%s] %s/%s" % (row["status"], row["shard"], row["channel_id"]))
        if row["status"] != "QUARANTINE_HIDDEN":
            lines.append("  issues=%s" % (", ".join(row["issues"]) if row["issues"] else "NONE"))
    if family["new_unaudited"]:
        lines.extend(["", "NEW UNAUDITED FAMILY IDS"])
        for row in family["new_unaudited"]:
            lines.append("- %s/%s" % (row["shard"], row["channel_id"]))
    lines.extend(["", "family_review_gate=%s" % family["status"]])

    mbc = run_mbc_gate(args.dir)
    if mbc["status"] != "PASS":
        hard_fail = True
    lines.extend(["", "MBC / SHAHID FROZEN PROVIDER GATE", ""])
    lines.append("status=%s audit_rc=%d regression_rc=%d" % (
        mbc["status"], mbc["audit_rc"], mbc["regression_rc"]))
    summary = mbc.get("audit_summary") or {}
    lines.append("frozen=%s/%s secondary=%s quarantine=%s unclassified=%s" % (
        summary.get("frozen_ok", 0), summary.get("frozen_expected", 0),
        summary.get("secondary", 0), summary.get("quarantine", 0),
        summary.get("unclassified", 0),
    ))
    for error in mbc.get("audit_errors") or []:
        lines.append("- %s" % error)
    if mbc["status"] != "PASS":
        lines.append("audit_output=%s" % mbc.get("audit_output", "")[-1600:])
        lines.append("regression_output=%s" % mbc.get("regression_output", "")[-1600:])

    payload = {
        "schema": 5,
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

    result_summary = " | ".join("%s=%s" % (row["label"], row["status"]) for row in out)
    print(result_summary + " | Disney+NatGeo=" + family["status"] + " | MBC+Shahid=" + mbc["status"])
    if family["errors"]:
        print("FAMILY AUDIT ERRORS:")
        for error in family["errors"]:
            print("- %s" % error)
        print("FAMILY DISCOVERED IDS:")
        for row in family["discovered"]:
            print("- %s/%s" % (row["shard"], row["channel_id"]))
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
