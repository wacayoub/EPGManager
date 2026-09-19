#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render docs/mena-source-id-master.csv as a GitHub-friendly monitoring page."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def esc(value: str) -> str:
    return str(value or "").replace("|", r"\|").replace("\r", " ").replace("\n", "<br>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--md", required=True)
    args = ap.parse_args()

    src = Path(args.csv)
    dst = Path(args.md)
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    total = len(rows)
    resolved = sum(1 for r in rows if (r.get("xmltv_id") or "").strip())
    unresolved = total - resolved
    multi = sum(1 for r in rows if int((r.get("candidate_count") or "0") or 0) > 1)
    counts = {}
    for r in rows:
        s = (r.get("now_status") or "").strip() or "UNKNOWN"
        counts[s] = counts.get(s, 0) + 1
    snapshot = next(((r.get("epg_snapshot_utc") or "").strip() for r in rows if (r.get("epg_snapshot_utc") or "").strip()), "")

    out = [
        "# MENA Source ID Monitoring",
        "",
        "> Auto-generated monitoring page from `docs/mena-source-id-master.csv`.  ",
        "> Rule: **1 real channel → 1 canonical XMLTV ID → 1 winning source**.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Source rows | {total} |",
        f"| Rows with XMLTV ID | {resolved} |",
        f"| Rows without XMLTV ID | {unresolved} |",
        f"| Multi-source candidates | {multi} |",
    ]
    for key in ("NOW", "NEXT_ONLY", "NO_CURRENT_EVENT", "NOT_PUBLISHED", "NO_XMLTV_ID", "UNKNOWN"):
        if counts.get(key):
            out.append(f"| {key} | {counts[key]} |")
    out += [
        f"| Snapshot UTC | {esc(snapshot)} |",
        "",
        "## All monitored IDs",
        "",
        "| XMLTV ID | Channel | Source | Candidates | Winner source | Receiver canonical ID | EPG status | Now title | Now description |",
        "|---|---|---|---:|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc((r.get("xmltv_id") or "").strip() or "—"),
                esc(r.get("channel_name") or ""),
                esc(r.get("source") or ""),
                esc(r.get("candidate_count") or ""),
                esc(r.get("winner_source") or ""),
                esc(r.get("receiver_canonical_id") or ""),
                esc(r.get("now_status") or ""),
                esc(r.get("now_title") or ""),
                esc(r.get("now_desc") or ""),
            )
        )

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"MENA_MASTER_MD PASS rows={total} unresolved={unresolved} multi={multi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
