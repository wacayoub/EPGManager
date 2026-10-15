#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only authenticity gate for published EPG-Scrapers XMLTV feeds.

Checks the *published compressed XMLTV*, not only its summary JSON.
No receiver writes, no publish, no EPG mapping changes. Python stdlib only.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

BASE = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/feeds/"
SOURCES = ("morocco", "bein", "osn", "elcinema", "starzplay", "stctv",
           "shahid", "dubaiplus", "alkass", "sport24", "rotana",
           "aljazeera", "tabie", "tunisiatv")
MAX_GZIP = 8 * 1024 * 1024
MAX_XML = 100 * 1024 * 1024
TIME_RE = re.compile(r"^\d{14} [+-]\d{4}$")


def parse_time(value):
    if not TIME_RE.fullmatch(value or ""):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S %z")
    except ValueError:
        return None


def fetch(url, max_bytes):
    with urlopen(Request(url, headers={"User-Agent": "EPGManager-source-audit/1.0"}),
                 timeout=35) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Feed larger than safe limit")
    return data


def audit_xml(raw, metadata, now, sample_limit=5):
    if len(raw) != int(metadata.get("size_bytes", -1)):
        raise ValueError("XMLTV gzip size differs from published metadata")
    if hashlib.sha256(raw).hexdigest() != metadata.get("sha256"):
        raise ValueError("XMLTV gzip SHA256 differs from published metadata")
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        xml = compressed.read(MAX_XML + 1)
    if len(xml) > MAX_XML:
        raise ValueError("Uncompressed XMLTV exceeds safe limit")
    channels = set()
    programme_channels = set()
    samples = {}
    count = 0
    bad_time = 0
    missing_title = 0
    active_now = set()
    genuine_future = set()
    root = None
    for event, node in ET.iterparse(io.BytesIO(xml), events=("start", "end")):
        if root is None and event == "start":
            if node.tag != "tv":
                raise ValueError("XMLTV root is not <tv>")
            root = node
        if event != "end":
            continue
        if node.tag == "channel":
            cid = node.attrib.get("id", "")
            if not cid or cid in channels:
                raise ValueError("Missing or duplicate XMLTV channel ID: " + cid)
            channels.add(cid)
            node.clear()
            root.clear()
        elif node.tag == "programme":
            count += 1
            cid = node.attrib.get("channel", "")
            title = (node.findtext("title") or "").strip()
            desc = (node.findtext("desc") or "").strip()
            start = parse_time(node.attrib.get("start"))
            stop = parse_time(node.attrib.get("stop"))
            if not start or not stop or stop <= start:
                bad_time += 1
            if not title:
                missing_title += 1
            programme_channels.add(cid)
            # Candidate matching needs exact per-ID evidence, not a blanket
            # source score. An invalid timestamp, blank title or orphan ID
            # must never count as a usable real programme on this SAT channel.
            if cid in channels and title and start and stop and stop > start:
                if start <= now < stop:
                    active_now.add(cid)
                if stop > now:
                    genuine_future.add(cid)
            if cid not in samples and len(samples) < sample_limit and title:
                samples[cid] = {
                    "id": cid, "title": title[:110],
                    "description_present": bool(desc),
                    "start_utc": start.isoformat() if start else None,
                    "stop_utc": stop.isoformat() if stop else None,
                }
            node.clear()
            root.clear()
    declared_count = int(metadata.get("channels", -1))
    expected_count = int(metadata.get("programmes", -1))
    expected_active = int(metadata.get("active_channels", -1))
    issues = []
    if len(channels) != declared_count:
        issues.append("declared_channel_count_mismatch")
    if count != expected_count:
        issues.append("declared_programme_count_mismatch")
    if len(programme_channels) != expected_active:
        issues.append("declared_active_count_mismatch")
    if programme_channels - channels:
        issues.append("programmes_reference_unknown_channels")
    if bad_time:
        issues.append("bad_xmltv_times")
    if missing_title:
        issues.append("empty_programme_titles")
    gen_raw = metadata.get("generated_utc", "")
    try:
        generated = datetime.fromisoformat(gen_raw.replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError("naive timestamp")
        age_hours = round((now - generated).total_seconds() / 3600.0, 1)
        if age_hours > 72 or age_hours < -2:
            issues.append("stale_or_future_generated_feed")
    except (ValueError, TypeError):
        age_hours = None
        issues.append("invalid_generated_utc")
    if expected_active < 1 or expected_active < 0.6 * max(1, declared_count):
        issues.append("insufficient_channel_coverage")
    if len(active_now) < 0.6 * max(1, len(programme_channels)):
        issues.append("insufficient_current_time_coverage")
    return {
        "channels": len(channels), "programmes": count,
        "active_channels": len(programme_channels),
        "channels_with_current_programme": len(active_now),
        "current_real_xmltv_ids": sorted(active_now),
        "future_real_xmltv_ids": sorted(genuine_future),
        "past_only_xmltv_ids": sorted(programme_channels - genuine_future),
        "observed_zero_epg_ids": sorted(channels - programme_channels),
        "per_id_evidence_observed_utc": now.isoformat(),
        "missing_now_sample": sorted(programme_channels - active_now)[:12],
        "sampled_real_programmes": list(samples.values()),
        "bad_times": bad_time, "missing_titles": missing_title,
        "age_hours": age_hours, "zero_epg_ids": metadata.get("zero_epg_ids", []),
        "issues": issues, "pass": not issues,
    }


def audit_source(name, now, reader=fetch):
    metadata = json.loads(reader(BASE + name + ".json", 1024 * 1024).decode("utf-8"))
    if metadata.get("label") != name:
        raise ValueError("Feed metadata label mismatch")
    compressed = reader(BASE + name + ".xml.gz", MAX_GZIP)
    return audit_xml(compressed, metadata, now)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.sources.split(",") if n.strip()]
    if not names or any(n not in SOURCES for n in names):
        parser.error("Source must be one of: " + ", ".join(SOURCES))
    now = datetime.now(timezone.utc)
    results = {}
    for name in names:
        try:
            results[name] = audit_source(name, now)
        except (OSError, ValueError, ET.ParseError, EOFError) as exc:
            results[name] = {"pass": False, "issues": ["read_or_parse_failure"],
                             "error": str(exc)[:250]}
    passed = sum(bool(r.get("pass")) for r in results.values())
    report = {"checked_utc": now.isoformat(), "passed": passed,
              "checked": len(names), "strict_release_ready": passed == len(names),
              "feeds": results}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)
    return 2 if args.strict and passed < len(names) else 0


if __name__ == "__main__":
    sys.exit(main())
