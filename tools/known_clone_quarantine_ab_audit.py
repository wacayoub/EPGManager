#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

TARGETS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}


def load(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data), len(data)


def ids(root):
    return {(c.get("id") or "").strip() for c in root.findall("channel")}


def event_keys(root):
    return {
        (
            (p.get("channel") or "").strip(),
            (p.get("start") or "").strip(),
            ((p.findtext("title") or "").strip()),
        )
        for p in root.findall("programme")
    }


def parse_start(value):
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value or "")
    if not m:
        return None
    digits, offset = m.group(1), m.group(2)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if offset == "Z" or not offset:
        tz = timezone.utc
    else:
        sign = 1 if offset[0] == "+" else -1
        hh, mm = int(offset[1:3]), int(offset[3:5])
        tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def outside_boundary(keys, cutoff):
    out = []
    for k in keys:
        dt = parse_start(k[1])
        # Unknown timestamps are never waived. Only the rolling-window boundary
        # near "now" may legitimately differ between sequential finalizer runs.
        if dt is None or dt > cutoff:
            out.append(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--before-audit", required=True)
    ap.add_argument("--after-audit", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    a = ap.parse_args()

    br, bb = load(a.before)
    ar, ab = load(a.after)
    bi, ai = ids(br), ids(ar)
    be, ae = event_keys(br), event_keys(ar)
    removed_ids = sorted(bi - ai)
    added_ids = sorted(ai - bi)
    removed_events = be - ae
    added_events = ae - be
    expected_removed = sorted(TARGETS & bi)
    non_target_removed_ids = [x for x in removed_ids if x not in TARGETS]

    # Two sequential finalizers can straddle the rolling-window boundary. Allow
    # non-target event identity differences only for starts already at least one
    # hour behind the comparison time; everything else must remain identical.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    non_target_removed = [k for k in removed_events if k[0] not in TARGETS]
    non_target_added = [k for k in added_events if k[0] not in TARGETS]
    unsafe_removed = outside_boundary(non_target_removed, cutoff)
    unsafe_added = outside_boundary(non_target_added, cutoff)

    before = json.loads(Path(a.before_audit).read_text(encoding="utf-8"))["summary"]
    after = json.loads(Path(a.after_audit).read_text(encoding="utf-8"))["summary"]

    assert len(expected_removed) >= 3, expected_removed
    assert removed_ids == expected_removed, (removed_ids, expected_removed)
    assert not non_target_removed_ids, non_target_removed_ids
    assert not added_ids, added_ids
    assert not unsafe_removed, unsafe_removed[:20]
    assert not unsafe_added, unsafe_added[:20]
    assert after["FAIL"] == 0, after
    assert after["channels"] == before["channels"] - len(expected_removed), (before, after)
    assert after["REVIEW"] <= before["REVIEW"] - len(expected_removed), (before, after)

    target_removed_events = [k for k in removed_events if k[0] in TARGETS]
    report = {
        "before_channels": len(bi),
        "after_channels": len(ai),
        "before_events": len(be),
        "after_events": len(ae),
        "expected_removed_ids": expected_removed,
        "removed_ids": removed_ids,
        "target_removed_events": len(target_removed_events),
        "non_target_boundary_removed": len(non_target_removed) - len(unsafe_removed),
        "non_target_boundary_added": len(non_target_added) - len(unsafe_added),
        "unsafe_non_target_removed": len(unsafe_removed),
        "unsafe_non_target_added": len(unsafe_added),
        "xml_saved_bytes": bb - ab,
        "before_audit": before,
        "after_audit": after,
        "removed_event_examples": [list(x) for x in sorted(target_removed_events)[:30]],
    }
    Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "KNOWN CLONE QUARANTINE A/B",
        "before_channels=%d after_channels=%d removed_ids=%d" % (len(bi), len(ai), len(removed_ids)),
        "before_events=%d after_events=%d target_removed_events=%d" % (len(be), len(ae), len(target_removed_events)),
        "boundary_only_non_target_removed=%d added=%d" % (
            report["non_target_boundary_removed"], report["non_target_boundary_added"]),
        "unsafe_non_target_removed=%d added=%d" % (
            report["unsafe_non_target_removed"], report["unsafe_non_target_added"]),
        "xml_saved_bytes=%d" % (bb - ab),
        "before_audit=%r" % before,
        "after_audit=%r" % after,
        "",
        "REMOVED IDS",
    ] + ["- " + x for x in removed_ids]
    Path(a.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
