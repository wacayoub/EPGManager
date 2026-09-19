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

    def monitor_status(row):
        state = (row.get("now_status") or "").strip()
        if state == "NOW":
            return "🟢 ON"
        return "🔴 OFF"

    def off_reason(row):
        state = (row.get("now_status") or "").strip()
        if state == "NOW":
            return "EPG current"
        if state == "NO_XMLTV_ID":
            return "No XMLTV ID"
        if state == "NOT_PUBLISHED":
            return "ID not present in final feed"
        if state == "NEXT_ONLY":
            return "No current event; future EPG exists"
        if state == "NO_CURRENT_EVENT":
            return "Published ID but no current event"
        return state or "Unknown"

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
        "<table>",
        "<thead><tr>",
        '<th width="170">Status</th>',
        '<th width="260">Channel</th>',
        '<th width="250">XMLTV ID</th>',
        '<th width="170">Source</th>',
        '<th width="90">Candidates</th>',
        '<th width="170">Winner</th>',
        '<th width="260">Receiver canonical ID</th>',
        '<th width="320">Now title</th>',
        '<th width="520">Now description</th>',
        '<th width="260">OFF reason</th>',
        "</tr></thead>",
        "<tbody>",
    ]
    for r in rows:
        out.append(
            "<tr>"
            f"<td><b>{esc(monitor_status(r))}</b></td>"
            f"<td><b>{esc(r.get('channel_name') or '')}</b></td>"
            f"<td><code>{esc((r.get('xmltv_id') or '').strip() or '—')}</code></td>"
            f"<td>{esc(r.get('source') or '')}</td>"
            f"<td align=\"center\">{esc(r.get('candidate_count') or '')}</td>"
            f"<td>{esc(r.get('winner_source') or '')}</td>"
            f"<td><code>{esc(r.get('receiver_canonical_id') or '')}</code></td>"
            f"<td>{esc(r.get('now_title') or '—')}</td>"
            f"<td>{esc(r.get('now_desc') or '—')}</td>"
            f"<td>{esc(off_reason(r))}</td>"
            "</tr>"
        )
    out += ["</tbody>", "</table>"]

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"MENA_MASTER_MD PASS rows={total} unresolved={unresolved} multi={multi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
