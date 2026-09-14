#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health gate for the upstream source adapters already accepted by MENA Cloud.

This is intentionally an adapter-level regression check, not a per-channel EPG
quality policy. Provider-specific gates (MBC, beIN, OSN, Rotana) remain the
final authority for exact channel quality. Here we only block publication when a
previously accepted source becomes absent or severely degraded across the current
primary grab.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

# Minimum selected IDs that must return at least two programme rows in the fresh
# primary grab. Thresholds are deliberately below normal observed health so
# transient single-channel 429/empty days can be recovered by per-channel LKG.
SOURCE_POLICY = {
    "shahid.mbc.net": {"min_healthy_ids": 12, "role": "MBC/Arabic official + targeted donor"},
    "osn.com": {"min_healthy_ids": 20, "role": "OSN official + selected MBC/premium"},
    "elcinema.com": {"min_healthy_ids": 12, "role": "Arabic local schedules + MBC Masr Drama primary"},
    "bein.com": {"min_healthy_ids": 12, "role": "beIN MENA primary"},
    "beinsports.com": {"min_healthy_ids": 2, "role": "beIN sports/event supplemental"},
    "rotana.net": {"min_healthy_ids": 8, "role": "official Rotana core"},
    "roya-tv.com": {"min_healthy_ids": 4, "role": "Roya official"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    catalogue = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    site_ids = defaultdict(set)
    for row in catalogue.get("channels", []):
        cid = row.get("xmltv_id")
        site = row.get("site")
        if cid and site:
            site_ids[site].add(cid)

    root = ET.parse(args.raw).getroot()
    event_counts = defaultdict(int)
    for programme in root.findall("programme"):
        cid = (programme.get("channel") or "").strip()
        if cid:
            event_counts[cid] += 1

    results = []
    errors = []
    for site, policy in SOURCE_POLICY.items():
        selected = sorted(site_ids.get(site, set()), key=str.casefold)
        healthy = [cid for cid in selected if event_counts.get(cid, 0) >= 2]
        with_any = [cid for cid in selected if event_counts.get(cid, 0) >= 1]
        zero = [cid for cid in selected if event_counts.get(cid, 0) == 0]
        events = sum(event_counts.get(cid, 0) for cid in selected)
        minimum = int(policy["min_healthy_ids"])
        status = "PASS" if selected and len(healthy) >= minimum else "FAIL"
        if not selected:
            errors.append("SOURCE_NOT_SELECTED=%s" % site)
        elif len(healthy) < minimum:
            errors.append("SOURCE_DEGRADED=%s healthy=%d<%d selected=%d" % (
                site, len(healthy), minimum, len(selected)))
        results.append({
            "site": site,
            "role": policy["role"],
            "status": status,
            "selected_ids": len(selected),
            "healthy_ids_ge2_events": len(healthy),
            "ids_with_any_event": len(with_any),
            "zero_event_ids": len(zero),
            "programme_rows": events,
            "minimum_healthy_ids": minimum,
            "zero_preview": zero[:12],
        })

    status = "FAIL" if errors else "PASS"
    payload = {
        "schema": 1,
        "status": status,
        "sources": results,
        "errors": errors,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["MENA FROZEN SOURCE HEALTH GATE: %s" % status, ""]
    for row in results:
        lines.append("[%s] %-18s selected=%d healthy=%d any=%d zero=%d events=%d min=%d | %s" % (
            row["status"], row["site"], row["selected_ids"], row["healthy_ids_ge2_events"],
            row["ids_with_any_event"], row["zero_event_ids"], row["programme_rows"],
            row["minimum_healthy_ids"], row["role"]))
    if errors:
        lines.extend(["", "Errors:"] + ["- " + e for e in errors])
    else:
        lines.extend(["", "- all accepted upstream adapters are above their regression floor"])
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
