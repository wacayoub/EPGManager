#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified-only Arabic metadata enrichment for National Geographic Abu Dhabi.

The EPG timeline and channel identity are never replaced. This tool changes
only title/description when the repository cache contains an explicitly
reviewed ``verified: true`` row. Network discoveries are REVIEW candidates and
can never enter production without a second validation step.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

TARGET_ID = "NationalGeographicAbuDhabi.ae"
TARGET_IDS = {
    TARGET_ID,
    "Nat.Geo.Abu.Dhabi.HD.ae",
    "NationalGeographicAbuDhabi.ae@SD",
}
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data" / "natgeo_ad_arabic_metadata.json"
AR_RE = re.compile(r"[\u0600-\u06ff]")
SPACE_RE = re.compile(r"\s+")

# Dedicated NatGeo freeze policy. Keep it independent from legacy ADM ID cleanup:
# metadata quality can freeze while canonical receiver-ID migration is handled separately.
MIN_COVERAGE_HOURS = 29.0
MIN_TITLE_AR_PCT = 85.0
MIN_DESC_AR_PCT = 80.0
MAX_EMPTY_DESC_PCT = 20.0


def _clean_text(value):
    return SPACE_RE.sub(" ", html.unescape(value or "")).strip()


def normalize_title(value):
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _arabic_key(value):
    text = unicodedata.normalize("NFKC", _clean_text(value))
    text = re.sub(r"[^\u0600-\u06ff0-9]+", " ", text)
    return " ".join(text.split())


def _parse_xmltv_ts(value):
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M %z", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def load_cache(path=DEFAULT_CACHE):
    p = Path(path)
    if not p.exists():
        return {"version": 2, "target_id": TARGET_ID, "entries": {}, "quarantine": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    if not isinstance(data.get("quarantine"), dict):
        data["quarantine"] = {}
    return data


def _verified_row(row):
    if not isinstance(row, dict) or row.get("verified") is not True:
        return False
    title = _clean_text(row.get("title_ar") or "")
    desc = _clean_text(row.get("description_ar") or "")
    return bool(title and desc and AR_RE.search(title) and AR_RE.search(desc))


def _build_arabic_index(entries):
    index = {}
    for row in entries.values():
        if not _verified_row(row):
            continue
        key = _arabic_key(row.get("title_ar") or "")
        if key:
            index[key] = row
    return index


def _lookup_row(current_title, entries, arabic_index):
    latin_key = normalize_title(current_title)
    row = entries.get(latin_key) or {}
    if _verified_row(row):
        return row, latin_key, "english_key"
    ar_key = _arabic_key(current_title)
    row = arabic_index.get(ar_key) or {}
    if _verified_row(row):
        return row, latin_key, "arabic_key"
    return {}, latin_key, None


def _enrich_programme(programme, entries, quarantine, arabic_index, copy_element):
    cp = copy_element(programme)
    title_node = cp.find("title")
    current_title = _clean_text(title_node.text if title_node is not None else "")
    row, latin_key, lookup_mode = _lookup_row(current_title, entries, arabic_index)
    state = "unmatched"
    source = None

    if _verified_row(row):
        if title_node is None:
            title_node = ET.SubElement(cp, "title")
        title_node.text = _clean_text(row["title_ar"])
        title_node.set("lang", "ar")
        desc_node = cp.find("desc")
        if desc_node is None:
            desc_node = ET.SubElement(cp, "desc")
        desc_node.text = _clean_text(row["description_ar"])
        desc_node.set("lang", "ar")
        state = "matched" if lookup_mode == "english_key" else "already_verified"
        source = _clean_text(row.get("source") or "unknown")
    elif latin_key in quarantine:
        state = "quarantine"

    return cp, current_title, state, source


def _quality_status(total, coverage_hours, title_ar, desc_ar, empty_desc):
    title_pct = round(100.0 * title_ar / total, 1) if total else 0.0
    desc_pct = round(100.0 * desc_ar / total, 1) if total else 0.0
    empty_pct = round(100.0 * empty_desc / total, 1) if total else 100.0
    checks = {
        "coverage_ok": coverage_hours >= MIN_COVERAGE_HOURS,
        "title_ar_ok": title_pct >= MIN_TITLE_AR_PCT,
        "desc_ar_ok": desc_pct >= MIN_DESC_AR_PCT,
        "empty_desc_ok": empty_pct <= MAX_EMPTY_DESC_PCT,
    }
    status = "FROZEN" if total and all(checks.values()) else "REVIEW"
    return status, title_pct, desc_pct, empty_pct, checks


def apply_cached_metadata(programmes, target_id=TARGET_ID, cache_path=DEFAULT_CACHE, copy_element=None):
    """Compatibility API: enrich one programme-map identity with VERIFIED rows."""
    if copy_element is None:
        copy_element = copy.deepcopy
    cache = load_cache(cache_path)
    entries = cache.get("entries") or {}
    quarantine = cache.get("quarantine") or {}
    arabic_index = _build_arabic_index(entries)
    source = list(programmes.get(target_id, []))
    out = dict(programmes)
    enriched = []
    matched = already_verified = title_ar = desc_ar = empty_desc = 0
    unmatched = Counter()
    quarantined = Counter()

    for programme in source:
        cp, original_title, state, _ = _enrich_programme(
            programme, entries, quarantine, arabic_index, copy_element
        )
        if state == "matched":
            matched += 1
        elif state == "already_verified":
            already_verified += 1
        elif state == "quarantine":
            quarantined[original_title or "<EMPTY>"] += 1
        else:
            unmatched[original_title or "<EMPTY>"] += 1
        title_node = cp.find("title")
        desc_node = cp.find("desc")
        final_title = _clean_text(title_node.text if title_node is not None else "")
        final_desc = _clean_text(desc_node.text if desc_node is not None else "")
        title_ar += bool(AR_RE.search(final_title))
        desc_ar += bool(AR_RE.search(final_desc))
        empty_desc += not bool(final_desc)
        enriched.append(cp)

    if source:
        out[target_id] = enriched
    total = len(source)
    # Programme-map compatibility mode has no cross-event timeline span, so only
    # metadata statistics are returned here. XML mode below owns FROZEN status.
    report = {
        "target_id": target_id,
        "events": total,
        "metadata_matches_new": matched,
        "metadata_matches_existing": already_verified,
        "metadata_verified_total": matched + already_verified,
        "verified_pct": round(100.0 * (matched + already_verified) / total, 1) if total else 0.0,
        "title_ar_pct": round(100.0 * title_ar / total, 1) if total else 0.0,
        "desc_ar_pct": round(100.0 * desc_ar / total, 1) if total else 0.0,
        "empty_desc_pct": round(100.0 * empty_desc / total, 1) if total else 100.0,
        "verified_cache_entries": sum(_verified_row(x) for x in entries.values()),
        "quarantine_entries": len(quarantine),
        "quarantined_titles_seen": dict(quarantined.most_common()),
        "unmatched_titles": dict(unmatched.most_common()),
        "timeline_replaced": False,
        "production_policy": "VERIFIED_ONLY",
    }
    return out, report


def _load_xml(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def _write_xml(root, path):
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix == ".gz":
        with p.open("wb") as fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
                gz.write(payload)
    else:
        p.write_bytes(payload)


def enrich_xml(input_path, output_path, cache_path=DEFAULT_CACHE):
    root = _load_xml(input_path)
    cache = load_cache(cache_path)
    entries = cache.get("entries") or {}
    quarantine = cache.get("quarantine") or {}
    arabic_index = _build_arabic_index(entries)
    total = matched = already_verified = title_ar = desc_ar = empty_desc = 0
    unmatched = Counter()
    quarantined = Counter()
    matched_sources = Counter()
    ids_seen = Counter()
    starts = []
    stops = []

    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in TARGET_IDS:
            continue
        ids_seen[cid] += 1
        total += 1
        start = _parse_xmltv_ts(p.get("start"))
        stop = _parse_xmltv_ts(p.get("stop"))
        if start is not None:
            starts.append(start)
        if stop is not None:
            stops.append(stop)

        cp, original_title, state, source = _enrich_programme(
            p, entries, quarantine, arabic_index, copy.deepcopy
        )

        # Replace only the event payload, keeping original schedule attributes and ID.
        p.clear()
        p.attrib.update(cp.attrib)
        p.extend(list(cp))

        if state == "matched":
            matched += 1
            matched_sources[source or "unknown"] += 1
        elif state == "already_verified":
            already_verified += 1
            matched_sources[source or "unknown"] += 1
        elif state == "quarantine":
            quarantined[original_title or "<EMPTY>"] += 1
        else:
            unmatched[original_title or "<EMPTY>"] += 1

        final_title = p.find("title")
        final_desc = p.find("desc")
        title_text = _clean_text(final_title.text if final_title is not None else "")
        desc_text = _clean_text(final_desc.text if final_desc is not None else "")
        title_ar += bool(AR_RE.search(title_text))
        desc_ar += bool(AR_RE.search(desc_text))
        empty_desc += not bool(desc_text)

    coverage_hours = 0.0
    if starts and stops:
        try:
            coverage_hours = round((max(stops) - min(starts)).total_seconds() / 3600.0, 3)
        except TypeError:
            coverage_hours = 0.0

    status, title_pct, desc_pct, empty_pct, checks = _quality_status(
        total, coverage_hours, title_ar, desc_ar, empty_desc
    )

    _write_xml(root, output_path)
    return {
        "target_id": TARGET_ID,
        "accepted_target_ids": sorted(TARGET_IDS),
        "target_ids_seen": dict(ids_seen),
        "events": total,
        "coverage_hours": coverage_hours,
        "metadata_matches_new": matched,
        "metadata_matches_existing": already_verified,
        "metadata_verified_total": matched + already_verified,
        "verified_pct": round(100.0 * (matched + already_verified) / total, 1) if total else 0.0,
        "title_ar_pct": title_pct,
        "desc_ar_pct": desc_pct,
        "empty_desc": empty_desc,
        "empty_desc_pct": empty_pct,
        "verified_cache_entries": sum(_verified_row(x) for x in entries.values()),
        "quarantine_entries": len(quarantine),
        "matched_sources": dict(matched_sources),
        "quarantined_titles_seen": dict(quarantined.most_common()),
        "unmatched_titles": dict(unmatched.most_common()),
        "freeze_thresholds": {
            "min_coverage_hours": MIN_COVERAGE_HOURS,
            "min_title_ar_pct": MIN_TITLE_AR_PCT,
            "min_desc_ar_pct": MIN_DESC_AR_PCT,
            "max_empty_desc_pct": MAX_EMPTY_DESC_PCT,
        },
        "freeze_checks": checks,
        "status": status,
        "auto_lock": status == "FROZEN",
        "timeline_replaced": False,
        "identity_replaced": False,
        "production_policy": "VERIFIED_ONLY",
    }


def _write_report(report, json_path=None, text_path=None):
    if json_path:
        Path(json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "NATGEO ABU DHABI VERIFIED ARABIC ENRICHMENT: %s" % report.get("status", "METADATA_ONLY"),
        "events=%d coverage=%.1fh verified=%d (%.1f%%) title_AR=%.1f%% desc_AR=%.1f%% empty_desc=%.1f%%" % (
            report["events"], report.get("coverage_hours", 0.0), report.get("metadata_verified_total", 0),
            report.get("verified_pct", 0.0), report["title_ar_pct"], report["desc_ar_pct"],
            report.get("empty_desc_pct", 0.0)),
        "cache=%d quarantine=%d timeline_replaced=NO identity_replaced=NO policy=%s auto_lock=%s" % (
            report["verified_cache_entries"], report["quarantine_entries"], report["production_policy"],
            "YES" if report.get("auto_lock") else "NO"),
    ]
    if report.get("target_ids_seen"):
        lines.append("ids_seen=" + json.dumps(report["target_ids_seen"], ensure_ascii=False, sort_keys=True))
    if report.get("freeze_checks"):
        lines.append("freeze_checks=" + json.dumps(report["freeze_checks"], ensure_ascii=False, sort_keys=True))
    if report.get("matched_sources"):
        lines.append("sources=" + json.dumps(report["matched_sources"], ensure_ascii=False, sort_keys=True))
    if report.get("quarantined_titles_seen"):
        lines.append("quarantine_seen=" + json.dumps(report["quarantined_titles_seen"], ensure_ascii=False, sort_keys=True))
    if report.get("unmatched_titles"):
        lines.append("unmatched=" + json.dumps(report["unmatched_titles"], ensure_ascii=False, sort_keys=True))
    text = "\n".join(lines) + "\n"
    if text_path:
        Path(text_path).write_text(text, encoding="utf-8")
    print(text, end="")


def audit_cache(cache_path=DEFAULT_CACHE):
    data = load_cache(cache_path)
    entries = data.get("entries") or {}
    quarantine = data.get("quarantine") or {}
    bad = []
    verified = 0
    arabic_seen = {}
    duplicate_ar = []
    for key, row in entries.items():
        if row.get("verified") is True:
            verified += 1
            if not _verified_row(row):
                bad.append(key)
            ar_key = _arabic_key(row.get("title_ar") or "")
            if ar_key in arabic_seen and arabic_seen[ar_key] != key:
                duplicate_ar.append((arabic_seen[ar_key], key))
            elif ar_key:
                arabic_seen[ar_key] = key
    overlap = sorted(set(entries) & set(quarantine))
    print("NatGeo Arabic cache: entries=%d verified=%d quarantine=%d bad=%d overlap=%d duplicate_ar=%d" % (
        len(entries), verified, len(quarantine), len(bad), len(overlap), len(duplicate_ar)))
    if bad:
        print("BAD_VERIFIED:", ", ".join(sorted(bad)))
    if overlap:
        print("ENTRY_QUARANTINE_OVERLAP:", ", ".join(overlap))
    if duplicate_ar:
        print("DUPLICATE_ARABIC_TITLE:", ", ".join("%s<->%s" % x for x in duplicate_ar))
    return 1 if bad or overlap or duplicate_ar else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    e = sub.add_parser("enrich")
    e.add_argument("--xml", required=True)
    e.add_argument("--output")
    e.add_argument("--cache", default=str(DEFAULT_CACHE))
    e.add_argument("--report-json")
    e.add_argument("--report-text")

    a = sub.add_parser("audit-cache")
    a.add_argument("--cache", default=str(DEFAULT_CACHE))

    args = ap.parse_args(argv)
    if args.command == "audit-cache":
        return audit_cache(args.cache)

    output = args.output or args.xml
    report = enrich_xml(args.xml, output, args.cache)
    _write_report(report, args.report_json, args.report_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
