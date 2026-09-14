#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PLACEHOLDER_RE = re.compile(
    r"^(?:schedule unavailable|programme schedule unavailable|program schedule unavailable|"
    r"no information|no info|tba|جدول البرامج غير متاح|لا توجد معلومات|لا يوجد برنامج)$",
    re.I,
)


def load(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data), len(data)


def text(node, tag):
    e = node.find(tag)
    return (e.text or "").strip() if e is not None else ""


def events(root):
    """Index logical programme identity without stop time.

    Removing a placeholder can legitimately change the previous programme stop
    boundary. That is not a programme deletion, so A/B identity is channel +
    start + title. The node is retained to report boundary-only changes.
    """
    out = {}
    for p in root.findall("programme"):
        k = ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), text(p, "title"))
        out[k] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--before-audit", required=True)
    ap.add_argument("--after-audit", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    a, a_bytes = load(args.before)
    b, b_bytes = load(args.after)
    ae, be = events(a), events(b)
    a_only = sorted(set(ae) - set(be))
    b_only = sorted(set(be) - set(ae))
    bad_removed = [k for k in a_only if not PLACEHOLDER_RE.match(k[2] or "")]
    placeholders_before = [k for k in ae if PLACEHOLDER_RE.match(k[2] or "")]
    placeholders_after = [k for k in be if PLACEHOLDER_RE.match(k[2] or "")]
    by_channel = Counter(k[0] for k in a_only if PLACEHOLDER_RE.match(k[2] or ""))
    common = set(ae) & set(be)
    stop_changes = []
    for k in sorted(common):
        old_stop = (ae[k].get("stop") or "").strip()
        new_stop = (be[k].get("stop") or "").strip()
        if old_stop != new_stop:
            stop_changes.append((k[0], k[1], k[2], old_stop, new_stop))

    before_audit = json.loads(Path(args.before_audit).read_text(encoding="utf-8"))
    after_audit = json.loads(Path(args.after_audit).read_text(encoding="utf-8"))
    bs = before_audit["summary"]
    ass = after_audit["summary"]

    # Hard safety gates: no placeholder survives; no real programme identity may
    # disappear; boundary changes are allowed and reported separately.
    assert not placeholders_after, "placeholder rows survived: %d" % len(placeholders_after)
    assert not bad_removed, "non-placeholder programme identities removed: %r" % (bad_removed[:10],)
    assert ass["FAIL"] == 0, ass
    assert ass["FAIL"] <= bs["FAIL"], (bs, ass)

    report = {
        "before_channels": len(a.findall("channel")),
        "after_channels": len(b.findall("channel")),
        "before_events": len(ae),
        "after_events": len(be),
        "before_xml_bytes": a_bytes,
        "after_xml_bytes": b_bytes,
        "placeholders_before": len(placeholders_before),
        "placeholders_after": len(placeholders_after),
        "removed_programme_identities": len(a_only),
        "added_programme_identities": len(b_only),
        "non_placeholder_removed": len(bad_removed),
        "stop_boundary_changes": len(stop_changes),
        "placeholder_removed_by_channel": dict(by_channel.most_common()),
        "before_audit": bs,
        "after_audit": ass,
        "added_examples": [list(k) for k in b_only[:25]],
        "removed_examples": [list(k) for k in a_only[:25]],
        "stop_change_examples": [list(k) for k in stop_changes[:25]],
    }
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "PLACEHOLDER PRUNE A/B",
        "before_channels=%d after_channels=%d" % (report["before_channels"], report["after_channels"]),
        "before_events=%d after_events=%d" % (report["before_events"], report["after_events"]),
        "placeholders_before=%d placeholders_after=%d" % (report["placeholders_before"], report["placeholders_after"]),
        "removed_identities=%d added_identities=%d non_placeholder_removed=%d stop_boundary_changes=%d" % (
            report["removed_programme_identities"], report["added_programme_identities"],
            report["non_placeholder_removed"], report["stop_boundary_changes"]),
        "xml_saved_bytes=%d" % (a_bytes - b_bytes),
        "before_audit=%r" % bs,
        "after_audit=%r" % ass,
        "",
        "REMOVED PLACEHOLDERS BY CHANNEL",
    ]
    for cid, n in by_channel.most_common():
        lines.append("- %s: %d" % (cid, n))
    if b_only:
        lines += ["", "ADDED PROGRAMMES AFTER PLACEHOLDER REMOVAL"]
        for k in b_only[:25]:
            lines.append("- %s | %s | %s" % (k[0], k[1], k[2]))
    if stop_changes:
        lines += ["", "BOUNDARY-ONLY CHANGES"]
        for k in stop_changes[:25]:
            lines.append("- %s | %s | %s | %s -> %s" % k)
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
