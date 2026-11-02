#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MENA release-readiness: immutable XMLTV provenance, real per-ID guide quality.

Structural corruption fails closed BEFORE GitHub publishes a derived MENA feed.
Editorial gaps are WARN with exact IDs; never fabricate or delete channels.
Selected MAIN/B1/B2 merely denotes ROUTING, not that an EPG is healthy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.mena_source_snapshot import get_source_snapshot
    from tools.source_feed_pack import parse_xmltv_dt
    from tools.programme_authenticity import generated_placeholder, strict_arabic_channel, arabic_title
except ImportError:
    from mena_source_snapshot import get_source_snapshot
    from source_feed_pack import parse_xmltv_dt
    from programme_authenticity import generated_placeholder, strict_arabic_channel, arabic_title


def audit(derived_feed, derived_metadata, routing_report, feed_dir, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("MENA_AUDIT_NOW_MUST_HAVE_TIMEZONE")
    now = now.astimezone(timezone.utc)
    structural_errors = []
    warnings = []
    meta = json.loads(Path(derived_metadata).read_text(encoding="utf-8"))
    routing = json.loads(Path(routing_report).read_text(encoding="utf-8"))
    source_order = meta.get("priority", [])
    if meta.get("label") != "mena":
        structural_errors.append("INVALID_MENA_METADATA_LABEL")
    if (len(source_order) != 14 or len(set(source_order)) != 14 or
            "morocco" not in source_order or "tabie" not in source_order):
        structural_errors.append("INVALID_FOURTEEN_SOURCE_ORDER")
    try:
        expected_snapshot = get_source_snapshot(feed_dir, source_order)
        if meta.get("source_snapshot") != expected_snapshot:
            structural_errors.append("MENA_SOURCE_SNAPSHOT_OUTDATED")
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        structural_errors.append("SOURCE_MANIFEST_INTEGRITY_ERROR:" + str(exc)[:160])
        expected_snapshot = None

    compressed = Path(derived_feed).read_bytes()
    actual_sha = hashlib.sha256(compressed).hexdigest()
    if meta.get("sha256") != actual_sha:
        structural_errors.append("MENA_PUBLICATION_SHA_MISMATCH")
    try:
        root = ET.fromstring(gzip.decompress(compressed))
    except (ValueError, OSError, ET.ParseError) as exc:
        raise ValueError("MENA_GZIP_OR_XML_PARSE_FAILURE:" + str(exc)[:160]) from exc
    if root.tag != "tv":
        structural_errors.append("MENA_XML_ROOT_NOT_TV")
    channel_ids = [(c.get("id") or "").strip() for c in root.findall("channel")]
    declared_ids = set(channel_ids)
    counts = Counter(channel_ids)
    if "" in declared_ids or any(n > 1 for n in counts.values()):
        structural_errors.append("DUPLICATE_OR_BLANK_CHANNEL_IDS")
    if meta.get("channels") != len(channel_ids):
        structural_errors.append("MENA_CHANNEL_COUNT_MISMATCH")
    rows = root.findall("programme")
    if meta.get("programmes") != len(rows):
        structural_errors.append("MENA_PROGRAMME_COUNT_MISMATCH")
    route = routing.get("routing", {})
    owners = meta.get("owners", {})
    if set(route) != declared_ids or set(owners) != declared_ids:
        structural_errors.append("MENA_ROUTING_OWNER_OR_CHANNEL_SET_MISMATCH")
    if routing.get("channels") != len(route):
        structural_errors.append("MENA_ROUTING_COUNT_MISMATCH")

    programme_counts = Counter()
    future_counts = Counter()
    invalid_ids = Counter()
    editorial_ids = Counter()
    seen_events = set()
    duplicated_events = 0
    orphan_programmes = 0
    for p in rows:
        cid = (p.get("channel") or "").strip()
        programme_counts[cid] += 1
        if cid not in declared_ids:
            orphan_programmes += 1
            continue
        start = parse_xmltv_dt(p.get("start"))
        stop = parse_xmltv_dt(p.get("stop"))
        if start is None or stop is None or start >= stop:
            invalid_ids[cid] += 1
            continue
        if stop > now:
            future_counts[cid] += 1
            title_text = next(((x.text or "").strip() for x in p.findall("title") if x.text), "")
            if generated_placeholder(title_text) or (
                    strict_arabic_channel(cid) and not arabic_title(title_text)):
                editorial_ids[cid] += 1
        title = tuple((x.text or "").strip() for x in p.findall("title"))
        identity = (cid, p.get("start"), p.get("stop"), title)
        if identity in seen_events:
            duplicated_events += 1
        seen_events.add(identity)
    if orphan_programmes:
        structural_errors.append("ORPHAN_PROGRAMMES:" + str(orphan_programmes))
    if invalid_ids:
        structural_errors.append("INVALID_PROGRAMME_TIMES:" + str(sum(invalid_ids.values())))
    if duplicated_events:
        structural_errors.append("EXACT_DUPLICATE_PROGRAMMES:" + str(duplicated_events))

    detail = []
    for cid in sorted(declared_ids, key=str.casefold):
        selected = route.get(cid, {})
        candidates = selected.get("candidates", [])
        chosen = [c for c in candidates
                  if c.get("source") == selected.get("selected_source")
                  and c.get("id") == selected.get("selected_id")]
        if len(chosen) != 1:
            structural_errors.append("MISSING_OR_AMBIGUOUS_SELECTED_CANDIDATE:" + cid)
            chosen_item = {}
        else:
            chosen_item = chosen[0]
        if owners.get(cid) != selected.get("selected_source"):
            structural_errors.append("OWNER_SELECTED_SOURCE_MISMATCH:" + cid)
        qualified_when_built = bool(
            chosen_item.get("qualified")
            and not chosen_item.get("suspected_placeholder")
            and selected.get("extensions_qualified", True)
            and not editorial_ids[cid])
        has_future = future_counts[cid] > 0
        if not has_future:
            status = "NO_FUTURE_EPG"
        elif qualified_when_built:
            status = "QUALIFIED_AT_BUILD"
        else:
            status = "DEGRADED_WITH_EPG"
        # MAIN = routing preference, not a quality score; a zero-EPG MAIN
        # remains present for saved mappings but must never be advertised green.
        detail.append({
            "xmltv_id": cid,
            "owner": selected.get("selected_source"),
            "selected_role": selected.get("selected_role"),
            "selection_reason": selected.get("selection_reason"),
            "status": status,
            "qualified_when_built": qualified_when_built,
            "best_match_green_allowed": status == "QUALIFIED_AT_BUILD",
            "manual_mapping_visible": True,
            "actual_programmes": programme_counts[cid],
            "actual_future_programmes": future_counts[cid],
            "editorial_review_programmes": editorial_ids[cid],
            "suspected_placeholder": bool(chosen_item.get("suspected_placeholder")),
            "selected_source_reported_future_programmes":
                chosen_item.get("future_programmes", 0),
            "candidate_sources": [c.get("source") for c in candidates],
        })
    statuses = Counter(x["status"] for x in detail)
    roles = Counter(x["selected_role"] for x in detail)
    owners_count = Counter(x["owner"] for x in detail)
    if statuses["DEGRADED_WITH_EPG"] or statuses["NO_FUTURE_EPG"]:
        warnings.append("MENA_HAS_UNQUALIFIED_OR_EMPTY_GUIDES")
    if "tabie" not in owners_count:
        warnings.append("TABIE_ARCHIVE_EXCLUDED_UNTIL_GENUINE_FUTURE_PROGRAMMES")
    report = {
        "schema": 1,
        "source": "mena",
        "checked_utc": now.isoformat(),
        "source_generated_utc": meta.get("generated_utc"),
        "status": "INVALID" if structural_errors else
                  "READY_WITH_WARNINGS" if warnings else "READY",
        "structural_errors": sorted(set(structural_errors)),
        "warnings": warnings,
        "channels": len(channel_ids),
        "programmes": len(rows),
        "orphan_programmes": orphan_programmes,
        "exact_duplicate_programmes": duplicated_events,
        "invalid_programme_times": sum(invalid_ids.values()),
        "source_manifest_sha256_match": expected_snapshot == meta.get("source_snapshot"),
        "verified_source_count": len(expected_snapshot or {}),
        "mena_sha256": actual_sha,
        "selected_statuses": dict(statuses),
        "selected_roles": dict(roles),
        "selected_owners": dict(owners_count),
        "router_duplicate_groups": routing.get("duplicate_groups"),
        "router_backup_selected": routing.get("backup_selected"),
        "quality_policy": {
            "no_future_epg_is_never_green": True,
            "main_role_does_not_imply_high_quality": True,
            "keep_empty_ids_visible_for_saved_mapping": True,
            "no_synthetic_programmes": True,
            "release_blocking": "structural_integrity_only",
            "editorial_warnings_explicit": True,
            "enigma2_receiver_validated": False,
        },
        "ids": detail,
    }
    return report


def render_markdown(report):
    counts = report["selected_statuses"]
    md = [
        "# MENA — source snapshot and exact-ID readiness",
        "",
        f"Checked GMT+0: {report['checked_utc']}",
        f"Published GMT+0: {report['source_generated_utc']}",
        f"**Status:** {report['status']}",
        "",
        f"**{report['channels']} logical IDs · {report['programmes']} source programme records · "
        f"{report['verified_source_count']} source fingerprints checked**",
        "",
        "| Route quality | Channels |",
        "|---|---:|",
        f"| Qualified at build, still has future programmes | {counts.get('QUALIFIED_AT_BUILD', 0)} |",
        f"| Degraded but has future programmes | {counts.get('DEGRADED_WITH_EPG', 0)} |",
        f"| No future EPG | {counts.get('NO_FUTURE_EPG', 0)} |",
        "",
        "**Important:** MAIN/B1/B2/B3 is the preferred routing *position*, NOT proof "
        "of programme quality. A zero-EPG ID is retained for manual mapping but "
        "never marked quality verified.",
        "",
        "Structural errors: " + ("; ".join(report["structural_errors"]) or "none"),
        "Warnings: " + ("; ".join(report["warnings"]) or "none"),
        "",
        "| XMLTV ID | Selected source | Route | Readiness | Future events |",
        "|---|---|---|---|---:|",
    ]
    for row in report["ids"]:
        md.append(f"| {row['xmltv_id']} | {row['owner']} | {row['selected_role']} | "
                  f"{row['status']} | {row['actual_future_programmes']} |")
    return "\n".join(md) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--feed", default="feeds/mena.xml.gz")
    p.add_argument("--metadata", default="feeds/mena.json")
    p.add_argument("--routing", default="reports/canonical-source-routing.json")
    p.add_argument("--feed-dir", default="feeds")
    p.add_argument("--report", default="reports/mena-readiness.json")
    p.add_argument("--markdown", default="reports/mena-readiness.md")
    args = p.parse_args()
    report = audit(args.feed, args.metadata, args.routing, args.feed_dir)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
    print("MENA_READINESS", json.dumps({
        "status": report["status"], "channels": report["channels"],
        "programmes": report["programmes"], "counts": report["selected_statuses"],
        "errors": report["structural_errors"],
        "source_count": report["verified_source_count"],
    }, ensure_ascii=False))
    return 1 if report["structural_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
