#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a source-by-source MENA EPG ID health report from published audit data."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def as_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()

    root = Path(args.data_dir)
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    merge = json.loads((root / "merge-report.json").read_text(encoding="utf-8"))
    audit_rows = load_csv(root / "all-id-audit.csv")
    audit_by_id = {r["id"]: r for r in audit_rows}

    alias_to_canonical = {}
    alias_source = {}
    for group in merge.get("aliases", []):
        canonical = group.get("canonical_id", "")
        for a in group.get("aliases", []):
            aid = a.get("id", "")
            if aid:
                alias_to_canonical[aid] = canonical
                alias_source[aid] = a.get("source", "")

    rows = []
    for ch in catalog.get("channels", []):
        cid = ch.get("xmltv_id", "")
        canonical = cid
        relation = "DIRECT"
        audit = audit_by_id.get(cid)
        if audit is None and cid in alias_to_canonical:
            canonical = alias_to_canonical[cid]
            audit = audit_by_id.get(canonical)
            relation = "ALIAS"
        if audit is None:
            verdict = "NOT_PUBLISHED"
            events = 0
            operational = "NO"
            shard = ""
            score = ""
            coverage = ""
            issues = "NOT_IN_FINAL_AUDIT"
            warnings = ""
            p1 = p2 = ""
            auto_lock = "0"
        else:
            verdict = audit.get("verdict", "")
            events = as_int(audit.get("events"))
            operational = "YES" if events > 0 and verdict in {"PASS", "REVIEW"} else "NO"
            shard = audit.get("shard", "")
            score = audit.get("score", "")
            coverage = audit.get("coverage_h", "")
            issues = audit.get("issues", "")
            warnings = audit.get("warnings", "")
            p1 = audit.get("preview_1", "")
            p2 = audit.get("preview_2", "")
            auto_lock = audit.get("auto_lock", "0")
        rows.append({
            "source": ch.get("site", ""),
            "source_id": ch.get("site_id", ""),
            "xmltv_id": cid,
            "channel_name": ch.get("name", ""),
            "relation": relation,
            "canonical_id": canonical,
            "operational": operational,
            "verdict": verdict,
            "events": events,
            "coverage_h": coverage,
            "score": score,
            "auto_lock": auto_lock,
            "shard": shard,
            "issues": issues,
            "warnings": warnings,
            "programme_1": p1,
            "programme_2": p2,
            "override_site": ch.get("override_site", ""),
            "priority": ch.get("priority", ""),
        })

    # Secondary/merge-source contribution rows: aliases from each upstream source.
    secondary = defaultdict(list)
    for aid, source in alias_source.items():
        canonical = alias_to_canonical.get(aid, "")
        audit = audit_by_id.get(canonical)
        secondary[source].append({
            "source": source,
            "alias_id": aid,
            "canonical_id": canonical,
            "verdict": audit.get("verdict", "NOT_PUBLISHED") if audit else "NOT_PUBLISHED",
            "events": as_int(audit.get("events")) if audit else 0,
            "shard": audit.get("shard", "") if audit else "",
            "programme_1": audit.get("preview_1", "") if audit else "",
            "programme_2": audit.get("preview_2", "") if audit else "",
        })

    source_stats = {x.get("name", ""): x for x in merge.get("source_stats", [])}
    quarantine_by_source = {}
    integrity = merge.get("integrity_quarantine", {})
    for s in integrity.get("sources", []):
        quarantine_by_source[s.get("source", "")] = s

    summaries = []
    by_source = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)
    for source, items in sorted(by_source.items()):
        c = Counter(r["verdict"] for r in items)
        working = sum(r["operational"] == "YES" for r in items)
        summaries.append({
            "source": source,
            "type": "PRIMARY",
            "ids": len(items),
            "working": working,
            "not_working": len(items) - working,
            "pass": c["PASS"],
            "review": c["REVIEW"],
            "no_epg": c["NO_EPG"],
            "fail": c["FAIL"],
            "not_published": c["NOT_PUBLISHED"],
            "coverage_pct": round(100.0 * working / max(1, len(items)), 1),
            "current_48h_candidates": "",
            "quarantined_identity_only": "",
            "retired_dropped": "",
        })

    for source, stat in sorted(source_stats.items()):
        q = quarantine_by_source.get(source, {})
        summaries.append({
            "source": source,
            "type": "MERGE_UPSTREAM",
            "ids": "",
            "working": "",
            "not_working": "",
            "pass": "",
            "review": "",
            "no_epg": "",
            "fail": "",
            "not_published": "",
            "coverage_pct": "",
            "current_48h_candidates": stat.get("channels_with_current_48h_epg", 0),
            "quarantined_identity_only": stat.get("quarantined_identity_only", q.get("identity_only_candidates", 0)),
            "retired_dropped": stat.get("retired_ssc_dropped", q.get("retired_ssc_dropped", 0)),
        })

    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    detail_cols = [
        "source","source_id","xmltv_id","channel_name","relation","canonical_id",
        "operational","verdict","events","coverage_h","score","auto_lock","shard",
        "issues","warnings","programme_1","programme_2","override_site","priority"
    ]
    with (prefix.with_suffix(".csv")).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=detail_cols)
        w.writeheader(); w.writerows(rows)

    sec_cols = ["source","alias_id","canonical_id","verdict","events","shard","programme_1","programme_2"]
    with Path(str(prefix) + "-secondary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=sec_cols)
        w.writeheader()
        for source in sorted(secondary):
            for row in sorted(secondary[source], key=lambda x: x["alias_id"].casefold()):
                w.writerow(row)

    summary_cols = ["source","type","ids","working","not_working","pass","review","no_epg","fail","not_published","coverage_pct","current_48h_candidates","quarantined_identity_only","retired_dropped"]
    with Path(str(prefix) + "-summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=summary_cols)
        w.writeheader(); w.writerows(summaries)

    out_json = {
        "schema": 1,
        "definition": {
            "working": "final audit has >0 programmes and verdict PASS or REVIEW",
            "not_working": "NO_EPG, FAIL, NOT_PUBLISHED, or zero programmes",
            "review": "EPG exists but requires quality/manual review",
            "secondary": "OpenEPG/EPGShare contribution is reported separately from official/primary catalogue source",
        },
        "primary_summary": [x for x in summaries if x["type"] == "PRIMARY"],
        "merge_source_summary": [x for x in summaries if x["type"] == "MERGE_UPSTREAM"],
        "primary_ids": rows,
        "secondary_alias_contributions": {k: v for k, v in sorted(secondary.items())},
    }
    Path(str(prefix) + ".json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append("# EPGManager MENA — Source / ID Status Report")
    md.append("")
    md.append("**Working** = programmes > 0 and verdict PASS/REVIEW. REVIEW is operational but not auto-lock safe. NO_EPG/FAIL/NOT_PUBLISHED are counted as not working.")
    md.append("")
    md.append("## Primary source summary")
    md.append("")
    md.append("| Source | IDs | Working | Not working | PASS | REVIEW | NO_EPG | FAIL | Not published | Coverage |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in [x for x in summaries if x["type"] == "PRIMARY"]:
        md.append("| {source} | {ids} | {working} | {not_working} | {pass} | {review} | {no_epg} | {fail} | {not_published} | {coverage_pct}% |".format(**s))
    md.append("")
    md.append("## Detailed IDs by primary source")
    for source in sorted(by_source):
        md.append("")
        md.append("### %s" % source)
        md.append("")
        md.append("| ID | Channel | State | Verdict | Events | Coverage h | Relation | Canonical | Issue/Warning | Programme examples |")
        md.append("|---|---|---|---|---:|---:|---|---|---|---|")
        for r in sorted(by_source[source], key=lambda x: x["xmltv_id"].casefold()):
            problem = "; ".join(x for x in [r["issues"], r["warnings"]] if x).replace("|", "/")
            examples = " → ".join(x for x in [r["programme_1"], r["programme_2"]] if x).replace("|", "/")
            name = r["channel_name"].replace("|", "/")
            md.append("| {id} | {name} | {op} | {verdict} | {events} | {cov} | {rel} | {canon} | {prob} | {ex} |".format(
                id=r["xmltv_id"].replace("|", "/"), name=name, op=r["operational"], verdict=r["verdict"],
                events=r["events"], cov=r["coverage_h"], rel=r["relation"], canon=r["canonical_id"].replace("|", "/"),
                prob=problem, ex=examples))
    md.append("")
    md.append("## Merge / secondary source health")
    md.append("")
    md.append("| Source | Current 48h candidates | Quarantined identity-only | Retired IDs dropped | Alias contributions to final mapping |")
    md.append("|---|---:|---:|---:|---:|")
    for s in [x for x in summaries if x["type"] == "MERGE_UPSTREAM"]:
        md.append("| {source} | {current_48h_candidates} | {quarantined_identity_only} | {retired_dropped} | {aliases} |".format(
            aliases=len(secondary.get(s["source"], [])), **s))
    Path(str(prefix) + ".md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("primary_ids=%d primary_sources=%d secondary_sources=%d" % (len(rows), len(by_source), len(source_stats)))
    for s in [x for x in summaries if x["type"] == "PRIMARY"]:
        print("%-22s ids=%3s working=%3s no=%3s PASS=%3s REVIEW=%3s NO_EPG=%3s FAIL=%s coverage=%s%%" % (
            s["source"], s["ids"], s["working"], s["not_working"], s["pass"], s["review"], s["no_epg"], s["fail"], s["coverage_pct"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
