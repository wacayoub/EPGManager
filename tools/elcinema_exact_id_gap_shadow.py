#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only comparison + shadow-only ElCinema gap extension by exact XMLTV ID.

Donors are the real published Shahid and Dubai+ programmes; no synthetic rows,
ID guessing, changed programme times or replacement of existing ElCinema data.
This tool has NO production --apply, no receiver operations and no network access.
The only optional output is under output/shadow/ in a GitHub checkout.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

# All IDs are actual literal identical XMLTV IDs in both published catalogues.
EXACT_DONORS = {
    "shahid": (
        "MBC1.ae@SD", "MBC3.ae@SD", "MBC4.ae@SD", "MBC5.ae@SD",
        "MBCAction.ae@SD", "MBCBollywood.ae@SD", "MBCDrama.ae@SD",
        "MBCIraq.iq@SD", "MBCMasr.eg@SD", "MBCMasr2.eg@SD",
        "MBCPlusDrama.sa@SD",
    ),
    "dubaiplus": ("DubaiOne.ae@SD",),
}
MAX_COMPRESSED = 4 * 1024 * 1024
MAX_UNPACKED = 45 * 1024 * 1024
FUTURE_HOURS = 48


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dt(value):
    try:
        parsed = datetime.strptime(value or "", "%Y%m%d%H%M%S %z")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def payload(root, name, now):
    root = Path(root)
    compressed = (root / "feeds" / (name + ".xml.gz")).read_bytes()
    metadata = json.loads((root / "feeds" / (name + ".json")).read_text(encoding="utf-8"))
    if not 0 < len(compressed) <= MAX_COMPRESSED or len(compressed) != int(metadata.get("size_bytes", -1)):
        raise ValueError("Size invalid for " + name)
    if sha(compressed) != metadata.get("sha256") or metadata.get("label") != name:
        raise ValueError("Published source fingerprint mismatch for " + name)
    try:
        generated = datetime.fromisoformat(str(metadata.get("generated_utc", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("No valid source freshness for " + name) from exc
    if not generated.tzinfo or generated > now + timedelta(hours=1) or now - generated > timedelta(hours=72):
        raise ValueError("Stale/future source " + name)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gz:
        plain = gz.read(MAX_UNPACKED + 1)
    if len(plain) > MAX_UNPACKED:
        raise ValueError("Decompressed source oversized")
    xml = ET.fromstring(plain)
    if xml.tag != "tv":
        raise ValueError("Invalid XMLTV root")
    ids = [(ch.get("id") or "").strip() for ch in xml.findall("channel")]
    if (len(ids) != int(metadata.get("channels", -1)) or
            len(ids) != len(set(ids)) or not all(ids) or
            len(xml.findall("programme")) != int(metadata.get("programmes", -1))):
        raise ValueError("Source metadata does not match XMLTV " + name)
    return xml, metadata, set(ids)


def intervals(root, channel):
    ret = []
    for p in root.findall("programme"):
        if p.get("channel") != channel:
            continue
        start, stop = dt(p.get("start")), dt(p.get("stop"))
        if not start or not stop or stop <= start:
            continue
        ret.append((start, stop))
    return ret


def valid_real_event(p, now):
    start, stop = dt(p.get("start")), dt(p.get("stop"))
    if not start or not stop or stop <= start or stop - start > timedelta(hours=24):
        return None
    if not any((title.text or "").strip() for title in p.findall("title")):
        return None
    if start < now - timedelta(hours=2) or start >= now + timedelta(hours=FUTURE_HOURS):
        return None
    if stop <= now:
        return None
    return start, stop


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def build(root, now=None, shadow=False):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC-aware now required")
    base, meta, base_ids = payload(root, "elcinema", now)
    before = len(base.findall("programme"))
    results = {"analysis": "ElCinema exact-ID verified donor gap extension",
               "read_only_receiver": True, "production_feeds_modified": False,
               "xmltv_id_rewrites": 0, "synthetic_programmes": 0,
               "original_programmes_kept": before,
               "source_original_sha256": meta["sha256"],
               "source_generated_utc": meta["generated_utc"],
               "donor_rows_appended": 0,
               "channels_extended": [],
               "by_id": {}, "donors": {}}
    for donor, targets in EXACT_DONORS.items():
        feed, info, donor_ids = payload(root, donor, now)
        results["donors"][donor] = {
            "generated_utc": info["generated_utc"], "published_sha256": info["sha256"],
        }
        for cid in targets:
            if cid not in base_ids or cid not in donor_ids:
                raise ValueError("Exact identity missing from ElCinema or " + donor + ": " + cid)
            occupied = intervals(base, cid)
            candidates = sorted((p for p in feed.findall("programme")
                                 if p.get("channel") == cid),
                                key=lambda p: p.get("start") or "")
            added, rejected_overlap, rejected_invalid = 0, 0, 0
            for p in candidates:
                iv = valid_real_event(p, now)
                if not iv:
                    rejected_invalid += 1
                    continue
                if any(overlap(iv, old) for old in occupied):
                    rejected_overlap += 1
                    continue
                new = deepcopy(p)
                # The original source's programme element and times remain
                # untouched; only an entirely nonoverlapping real donor row is appended.
                base.append(new)
                occupied.append(iv)
                added += 1
            results["by_id"][cid] = {
                "donor": donor, "added_real_programmes": added,
                "overlap_skipped": rejected_overlap, "other_skipped": rejected_invalid,
                "original_intervals_unchanged": True,
            }
            if added:
                results["channels_extended"].append(cid)
                results["donor_rows_appended"] += added
    assert len(base.findall("programme")) == before + results["donor_rows_appended"]
    results["channels_extended"].sort()
    results["channels_extended_count"] = len(results["channels_extended"])
    results["shadow_programmes"] = len(base.findall("programme"))
    if shadow:
        directory = Path(root) / "output" / "shadow"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "elcinema-verified-gap-shadow.xml.gz"
        body = gzip.compress(ET.tostring(base, encoding="utf-8", xml_declaration=True),
                             mtime=0, compresslevel=9)
        target.write_bytes(body)
        results["shadow_output"] = "output/shadow/" + target.name
        results["shadow_sha256"] = sha(body)
        results["shadow_size_bytes"] = len(body)
        results["production_feeds_modified"] = False
        (directory / "elcinema-verified-gap-report.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--shadow", action="store_true",
                        help="Write only output/shadow/ in this repo; NEVER publish")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.root, shadow=args.shadow), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, ET.ParseError, gzip.BadGzipFile) as exc:
        print(json.dumps({"analysis": "ElCinema exact-ID verified gap shadow",
                          "status": "REFUSED", "error_type": type(exc).__name__,
                          "production_feeds_modified": False}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
