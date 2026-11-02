#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely prune exactly five known untitled XMLTV events from public feeds.

No fabrication, scraping, receiver communication, remapping or timezone change.
No '--apply': dry-run only. Publication is restricted to two exact SHA-locked
2026-10-14 source payloads. A changed feed refuses modification. The original
source generation time is preserved, so this cannot launder an old feed as new.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

EXPECTED = {
    "shahid": {
        "sha256": "fe7f72602f1e5f0cb656c6ed93a371610cdba5cd42b536b745bab56cb1c91223",
        "empty_titles": 3,
        "programmes": 4546,
        "channels": 71,
    },
    "elcinema": {
        "sha256": "2cca49ed4cb83391af274dc5cd7f7fbd683709deac4d8b11f35df825ce008456",
        "empty_titles": 2,
        "programmes": 3990,
        "channels": 97,
    },
}
MAX_GZIP = 3 * 1024 * 1024
MAX_XML = 25 * 1024 * 1024


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_decompress(payload):
    if not 0 < len(payload) <= MAX_GZIP:
        raise ValueError("Compressed feed exceeds bounded size")
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
        plain = stream.read(MAX_XML + 1)
    if len(plain) > MAX_XML:
        raise ValueError("Uncompressed XMLTV too large")
    return plain


def _time_utc(raw):
    if not raw:
        raise ValueError("Programme lacks timestamp")
    try:
        value = datetime.strptime(raw, "%Y%m%d%H%M%S %z")
    except ValueError as exc:
        raise ValueError("Invalid timestamp in published feed") from exc
    return value.astimezone(timezone.utc)


def create_repair(source, compressed, metadata, now=None, expected=EXPECTED):
    """Pure transformation, returns new bytes and in-memory JSON; never writes."""
    if source not in expected:
        raise ValueError("Source not allowlisted")
    rule = expected[source]
    if digest(compressed) != rule["sha256"]:
        raise ValueError("Source has changed since audit; refuse stale repair")
    if (metadata.get("label") != source
            or metadata.get("sha256") != rule["sha256"]
            or int(metadata.get("size_bytes", -1)) != len(compressed)
            or int(metadata.get("programmes", -1)) != rule["programmes"]
            or int(metadata.get("channels", -1)) != rule["channels"]):
        raise ValueError("Feed metadata disagrees with audited source")
    generated = metadata.get("generated_utc")
    if not generated or not isinstance(generated, str):
        raise ValueError("Missing original generation time")
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(_safe_decompress(compressed))
    if root.tag != "tv":
        raise ValueError("Not an XMLTV file")
    channel_ids = [c.get("id") for c in root.findall("channel")]
    if len(channel_ids) != rule["channels"] or len(set(channel_ids)) != len(channel_ids) or any(not x for x in channel_ids):
        raise ValueError("Channel ID catalogue changed unexpectedly")
    programmes = root.findall("programme")
    if len(programmes) != rule["programmes"]:
        raise ValueError("Programme count differs from verified baseline")

    removed = 0
    for item in programmes:
        if item.get("channel") not in channel_ids:
            raise ValueError("Orphan programme")
        start = _time_utc(item.get("start"))
        stop = _time_utc(item.get("stop"))
        if stop <= start:
            raise ValueError("Invalid programme duration")
        if not any((t.text or "").strip() for t in item.findall("title")):
            root.remove(item)
            removed += 1
    if removed != rule["empty_titles"]:
        raise ValueError("Expected exactly %s untitled rows, found %s" % (rule["empty_titles"], removed))

    surviving = root.findall("programme")
    onair, active = set(), set()
    for item in surviving:
        cid = item.get("channel")
        active.add(cid)
        if _time_utc(item.get("start")) <= now < _time_utc(item.get("stop")):
            onair.add(cid)
    if len(active) < rule["channels"] * 0.95:
        raise ValueError("Too many active channel IDs lost")
    # Preserve XMLTV title/lang/desc, ID, start/stop and metadata verbatim.
    # Re-serializing the XML may alter formatting, not programme semantics.
    updated_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    updated_compressed = gzip.compress(updated_xml, compresslevel=9, mtime=0)
    meta = dict(metadata)
    meta.update({
        "programmes": len(surviving),
        "active_channels": len(active),
        "zero_epg_channels": len(channel_ids) - len(active),
        "zero_epg_ids": sorted(set(channel_ids) - active),
        "coverage_pct": round(len(active) * 100.0 / len(channel_ids), 1),
        "currently_on_air_channels": len(onair),
        "current_coverage_pct": round(100.0 * len(onair) / len(channel_ids), 1),
        "currently_uncovered_ids": sorted(set(channel_ids) - onair),
        "size_bytes": len(updated_compressed),
        "sha256": digest(updated_compressed),
        "rejected_missing_title": int(metadata.get("rejected_missing_title") or 0) + removed,
        "original_published_sha256": rule["sha256"],
        "repacked_utc": now.isoformat(),
        # Keep original generation: a re-serialization is NOT a provider refresh.
        "generated_utc": generated,
    })
    report = {
        "source": source, "operation": "prune_only_verified_empty_titles",
        "removed_untitled_programmes": removed,
        "remaining_programmes": len(surviving),
        "remaining_active_channels": len(active),
        "original_sha256": rule["sha256"],
        "replacement_sha256": meta["sha256"],
        "preserved_source_generated_utc": generated,
        "repacked_utc": now.isoformat(),
        "receiver_modified": False,
        "fabricated_programmes": 0,
    }
    return updated_compressed, meta, report


def run(source, root, apply=False):
    root = Path(root)
    compressed = (root / "feeds" / (source + ".xml.gz")).read_bytes()
    metadata = json.loads((root / "feeds" / (source + ".json")).read_text(encoding="utf-8"))
    body, new_meta, report = create_repair(source, compressed, metadata)
    if apply:
        (root / "feeds" / (source + ".xml.gz")).write_bytes(body)
        (root / "feeds" / (source + ".json")).write_text(
            json.dumps(new_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "reports" / (source + "-title-repair.json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true",
                        help="Write verified repaired files in checkout; never contact receiver")
    a = parser.parse_args()
    try:
        result = run(a.source, a.root, apply=a.apply)
    except (OSError, ValueError, TypeError, gzip.BadGzipFile, ET.ParseError) as exc:
        print(json.dumps({"source": a.source, "status": "REFUSED",
                          "error_type": type(exc).__name__,
                          "receiver_modified": False}))
        return 2
    result["applied"] = a.apply
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
