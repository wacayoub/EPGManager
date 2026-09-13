#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused regression test for Arabic-first local MENA channels."""
from __future__ import annotations

import argparse
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

TARGETS = [
    ("mena-ye", r"^Aden\.TV\.ae$", "Aden TV"),
    ("mena-iq", r"^Afaq\.TV\.ae$", "AFAQ TV"),
    ("mena-eg", r"^Al\.Nada\.TV\.ae$", "Al Nada TV"),
]


def root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def text(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def lang(value):
    ar = len(AR.findall(value or ""))
    en = len(LAT.findall(value or ""))
    if ar >= 2 and ar >= en:
        return "ar"
    if en >= 2:
        return "en"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    out = []
    lines = ["ARABIC PRIORITY REGRESSION TEST", ""]
    hard_fail = False

    for shard, id_rx, label in TARGETS:
        r = root(Path(args.dir) / (shard + ".xml.gz"))
        channels = [(c.get("id") or "").strip() for c in r.findall("channel")]
        ids = [cid for cid in channels if re.search(id_rx, cid, re.I)]
        cid = ids[0] if ids else ""
        events = [p for p in r.findall("programme") if (p.get("channel") or "").strip() == cid] if cid else []
        titles = [text(p, "title") for p in events if text(p, "title")]
        descs = [text(p, "desc") for p in events if text(p, "desc")]
        ar_title = sum(lang(x) == "ar" for x in titles)
        en_title = sum(lang(x) == "en" for x in titles)
        ar_desc = sum(lang(x) == "ar" for x in descs)
        title_ar_ratio = ar_title / float(len(titles)) if titles else 0.0
        desc_ar_ratio = ar_desc / float(len(descs)) if descs else 0.0
        aje_hits = sorted({x for x in titles if AJE.match(x)})

        if not cid:
            status = "MISSING_ID"
        elif not events:
            status = "NO_SAFE_EPG"
        elif title_ar_ratio >= 0.50:
            status = "PASS_ARABIC"
        else:
            status = "EN_FALLBACK"

        if label == "AFAQ TV" and len(aje_hits) >= 3:
            status = "FAIL_WRONG_AJE_GUIDE"
            hard_fail = True

        row = {
            "label": label, "shard": shard, "channel_id": cid, "events": len(events),
            "title_ar_ratio": round(title_ar_ratio, 4),
            "title_en_ratio": round(en_title / float(len(titles)), 4) if titles else 0.0,
            "desc_ar_ratio": round(desc_ar_ratio, 4),
            "status": status, "foreign_signature": aje_hits,
            "preview": [{"title": text(p, "title"), "desc": text(p, "desc")} for p in events[:8]],
        }
        out.append(row)
        lines.append("=== %s ===" % label)
        lines.append("shard=%s id=%s events=%d status=%s" % (shard, cid or "<missing>", len(events), status))
        lines.append("title AR=%.0f%% EN=%.0f%% | desc AR=%.0f%%" % (
            row["title_ar_ratio"] * 100, row["title_en_ratio"] * 100, row["desc_ar_ratio"] * 100))
        if aje_hits:
            lines.append("foreign_signature=%s" % ", ".join(aje_hits))
        for p in events[:8]:
            lines.append("- %s" % (text(p, "title") or "<NO TITLE>"))
            d = text(p, "desc")
            if d:
                lines.append("  %s" % d[:240])
        lines.append("")

    Path(args.json).write_text(json.dumps({"schema": 1, "targets": out}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(" | ".join("%s=%s" % (x["label"], x["status"]) for x in out))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
