#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill only authentic Dubai TV programme gaps from exact OSN broadcast ID.

Dubai+ AR-native output is primary; already published OSN with the EXACT
DubaiTV.ae@SD ID can supply non-overlapping real programmes only after
SHA-256, official 48h PASS and per-channel XML timeline verification.
No generated titles, translations, guessed times or fuzzy channel matches.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.sport24_verified_donors import audited_donor, valid_window, overlap, timestamp
    from tools.source_feed_pack import parse_xmltv_dt
except ModuleNotFoundError:
    from sport24_verified_donors import audited_donor, valid_window, overlap, timestamp
    from source_feed_pack import parse_xmltv_dt

DUBAI_TV_ID = "DubaiTV.ae@SD"
OSN_SOURCE = "osn"
OSN_TV_ID = "DubaiTV.ae@SD"


def extend(root, feed_dir, now):
    if root.tag != "tv":
        raise ValueError("Dubai+ input must be XMLTV")
    ids = [x.get("id") for x in root.findall("channel")]
    report = {
        "source": "dubaiplus", "generated_utc": now.isoformat(),
        "strategy": "Dubai+ native programme first; exact OSN identity only in uncovered intervals",
        "source_id": DUBAI_TV_ID, "donor_source": OSN_SOURCE,
        "donor_id": OSN_TV_ID, "added": 0, "overlap_skipped": 0,
        "status": "NOT_APPLICABLE", "synthetic_programmes_added": 0,
    }
    if ids.count(DUBAI_TV_ID) != 1 or len(ids) != len(set(ids)):
        report["status"] = "INVALID_OR_MISSING_DUBAIPLUS_EXACT_CHANNEL_ID"
        return report

    # Detect damaged original schedules: DO NOT silently replace valid source.
    primary = []
    for p in root.findall("programme"):
        if p.get("channel") != DUBAI_TV_ID:
            continue
        start = parse_xmltv_dt(p.get("start"))
        stop = parse_xmltv_dt(p.get("stop"))
        if (not start or not stop or stop <= start or
                stop - start > timedelta(hours=24) or
                not any((t.text or "").strip() for t in p.findall("title"))):
            report["status"] = "INVALID_DUBAIPLUS_PRIMARY_PROGRAMME"
            return report
        # Old published XMLTV may contain past broadcasts. They are authentic
        # records, never damaged rows; leave them intact and exclude from
        # the future interval overlap test.
        iv = valid_window(p, now)
        if iv:
            primary.append(iv)
    if not primary:
        report["status"] = "PRIMARY_DUBAIPLUS_CHANNEL_EMPTY_MANUAL_REVIEW"
        return report

    # Do not allow a failed/rejected or unverified OSN guide to fill.
    donor_rows, reason = audited_donor(Path(feed_dir), OSN_SOURCE, OSN_TV_ID, now)
    if not donor_rows:
        report["status"] = reason
        return report

    occupied = list(primary)
    # Use exact intervals for overlap; never clip or invent boundary times.
    for start, stop, p in donor_rows:
        if overlap((start, stop), occupied):
            report["overlap_skipped"] += 1
            continue
        copied = ET.fromstring(ET.tostring(p, encoding="utf-8"))
        copied.set("channel", DUBAI_TV_ID)
        root.append(copied)
        occupied.append((start, stop))
        report["added"] += 1

    report["status"] = ("VERIFIED_GAPS_FILLED" if report["added"] else
                        "VERIFIED_NO_NONOVERLAPPING_GAPS")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--feed-dir", default="feeds")
    ap.add_argument("--report", required=True)
    ap.add_argument("--now", default="")
    a = ap.parse_args()
    now = timestamp(a.now) if a.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("Invalid --now (requires an aware ISO timestamp)")
    root = ET.parse(a.input).getroot()
    report = extend(root, Path(a.feed_dir), now)
    ET.indent(root, space="  ")
    Path(a.output).write_bytes(ET.tostring(root, encoding="utf-8",
                                           xml_declaration=True))
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n",
                              encoding="utf-8")
    print(json.dumps({"status": report["status"], "added": report["added"],
                      "overlap_skipped": report["overlap_skipped"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
