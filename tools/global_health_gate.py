#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-feed health audit for the published EPGManager MENA + Morocco feeds.

Shadow mode is deliberately non-mutating and never blocks publishing. It emits one
coherent PASS/REVIEW/FAIL report that can later be promoted to an enforceable gate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics
import unicodedata
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")
WS_RE = re.compile(r"\s+")

MOROCCO_REQUIRED = {
    "AlAoula", "2M", "Chada TV", "MEDI1TV_AR.ma", "Arryadia_HD",
}
MOROCCO_2M_FR_ALLOW = {"info soir", "meteo", "eco news"}
KNOWN_BAD_TITLES = {
    "programme", "program", "programming", "broadcast", "unknown", "no info",
    "no information", "tba", "to be announced", "test", "برنامج", "برامج",
}
KNOWN_BAD_2M = {"برنامج على 2m", "programme sur 2m", "program on 2m"}
NAMESPACE_RULES = (
    ("beIN.", ".qa"),
    ("OSN.", ".ae"),
    ("MBC.", ".mena"),
    ("Rotana.", ".mena"),
    ("DMI.", ".ae"),
    ("ADM.", ".ae"),
)
SEVERITY_RANK = {"PASS": 0, "REVIEW": 1, "FAIL": 2}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compact(value: str) -> str:
    return WS_RE.sub(" ", value or "").strip()


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", compact(value)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def parse_xmltv_time(value: str):
    raw = compact(value)
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M %z", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def parse_iso_time(value):
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_xml(path: Path):
    data = path.read_bytes()
    raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    return data, raw, ET.fromstring(raw)


def first_text(node: ET.Element, tag: str):
    el = node.find(tag)
    if el is None:
        return "", ""
    return compact(el.text or ""), compact(el.get("lang") or "").lower()


def looks_arabic(text: str, lang: str = "") -> bool:
    if lang.startswith("ar"):
        return True
    ar = len(AR_RE.findall(text or ""))
    lat = len(LAT_RE.findall(text or ""))
    return ar >= 2 and ar >= lat


def feed_status(checks, feed: str) -> str:
    level = 0
    for row in checks:
        if row.get("feed") == feed:
            level = max(level, SEVERITY_RANK[row["severity"]])
    return ("PASS", "REVIEW", "FAIL")[level]


def add_check(checks, severity: str, feed: str, code: str, message: str, sample=None):
    row = {"severity": severity, "feed": feed, "code": code, "message": message}
    if sample is not None:
        row["sample"] = sample
    checks.append(row)


def audit_feed(xml_path: Path, manifest_path: Path, feed: str, checks, min_future_hours: float):
    profile = {
        "feed": feed,
        "xml": str(xml_path),
        "manifest": str(manifest_path),
        "channels": 0,
        "programmes": 0,
        "zero_epg_channels": 0,
        "median_future_hours": 0.0,
        "manifest_age_hours": None,
    }
    if not xml_path.is_file():
        add_check(checks, "FAIL", feed, "MISSING_XML", f"Missing {xml_path.name}")
        return profile, set()
    if not manifest_path.is_file():
        add_check(checks, "FAIL", feed, "MISSING_MANIFEST", f"Missing {manifest_path.name}")
        return profile, set()

    try:
        compressed, raw, root = read_xml(xml_path)
    except Exception as exc:
        add_check(checks, "FAIL", feed, "INVALID_XML", str(exc))
        return profile, set()
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        add_check(checks, "FAIL", feed, "INVALID_MANIFEST", str(exc))
        return profile, set()

    channels = []
    display = {}
    for ch in root.findall("channel"):
        cid = compact(ch.get("id") or "")
        channels.append(cid)
        name = cid
        dn = ch.find("display-name")
        if dn is not None and compact(dn.text or ""):
            name = compact(dn.text or "")
        display[cid] = name
    ids = set(channels)
    programmes = root.findall("programme")
    profile["channels"] = len(channels)
    profile["programmes"] = len(programmes)

    if len(channels) != len(ids):
        dupes = [cid for cid, count in Counter(channels).items() if count > 1]
        add_check(checks, "FAIL", feed, "DUPLICATE_CHANNEL_IDS", f"{len(dupes)} duplicated channel IDs", dupes[:10])

    per_channel = defaultdict(list)
    bad_times = []
    empty_titles = []
    generic_titles = []
    bad_2m = []
    namespace_bad = []
    morocco_2m_lang_bad = []
    orphan = []
    tnow = now_utc()

    for p in programmes:
        cid = compact(p.get("channel") or "")
        title, title_lang = first_text(p, "title")
        desc, desc_lang = first_text(p, "desc")
        start = parse_xmltv_time(p.get("start") or "")
        stop = parse_xmltv_time(p.get("stop") or "")
        if cid not in ids:
            orphan.append(cid)
        if not title:
            empty_titles.append((cid, p.get("start") or ""))
        else:
            nt = norm(title)
            if nt in KNOWN_BAD_TITLES:
                generic_titles.append((cid, title, p.get("start") or ""))
            if cid == "2M" and nt in KNOWN_BAD_2M:
                bad_2m.append((title, p.get("start") or ""))
            if feed == "morocco" and cid == "2M" and nt not in MOROCCO_2M_FR_ALLOW and not looks_arabic(title, title_lang):
                morocco_2m_lang_bad.append((title, title_lang, p.get("start") or ""))
        if start is None or stop is None or stop <= start:
            bad_times.append((cid, p.get("start") or "", p.get("stop") or ""))
        else:
            per_channel[cid].append((start, stop, title))

    if orphan:
        add_check(checks, "FAIL", feed, "ORPHAN_PROGRAMMES", f"{len(orphan)} programmes reference unknown IDs", sorted(set(orphan))[:10])
    if empty_titles:
        add_check(checks, "FAIL", feed, "EMPTY_TITLES", f"{len(empty_titles)} programmes have empty titles", empty_titles[:10])
    if bad_times:
        add_check(checks, "FAIL", feed, "INVALID_TIMELINE", f"{len(bad_times)} programmes have invalid start/stop", bad_times[:10])
    if generic_titles:
        add_check(checks, "REVIEW", feed, "GENERIC_TITLES", f"{len(generic_titles)} generic titles remain", generic_titles[:10])
    if bad_2m:
        add_check(checks, "FAIL", feed, "2M_PLACEHOLDER_TITLE", f"{len(bad_2m)} known 2M placeholder titles remain", bad_2m[:10])
    if morocco_2m_lang_bad:
        add_check(checks, "REVIEW", feed, "2M_TITLE_LANGUAGE", f"{len(morocco_2m_lang_bad)} 2M titles are outside Arabic/French-first policy", morocco_2m_lang_bad[:10])

    if feed == "mena":
        for cid in sorted(ids):
            for prefix, suffix in NAMESPACE_RULES:
                if cid.startswith(prefix) and not cid.endswith(suffix):
                    namespace_bad.append(cid)
                    break
        if namespace_bad:
            add_check(checks, "FAIL", feed, "NAMESPACE_MISMATCH", f"{len(namespace_bad)} provider IDs violate canonical namespace", namespace_bad[:20])

    event_ids = {cid for cid in per_channel}
    zero = sorted(ids - event_ids)
    profile["zero_epg_channels"] = len(zero)
    if zero:
        add_check(checks, "FAIL", feed, "ZERO_EPG_IDS", f"{len(zero)} receiver-facing channels have no valid programmes", zero[:20])

    overlap_samples = []
    future_hours = []
    for cid, rows in per_channel.items():
        rows.sort(key=lambda row: (row[0], row[1]))
        prev_stop = None
        for start, stop, title in rows:
            if prev_stop is not None and start < prev_stop:
                overlap = (prev_stop - start).total_seconds() / 60.0
                if overlap >= 10:
                    overlap_samples.append((cid, round(overlap, 1), title, start.isoformat()))
            if prev_stop is None or stop > prev_stop:
                prev_stop = stop
        future_stops = [stop for start, stop, title in rows if stop > tnow]
        if future_stops:
            future_hours.append(max(0.0, (max(future_stops) - tnow).total_seconds() / 3600.0))
    if overlap_samples:
        add_check(checks, "FAIL", feed, "OVERLAPS_GE_10M", f"{len(overlap_samples)} overlaps are >=10 minutes", overlap_samples[:20])

    median_future = statistics.median(future_hours) if future_hours else 0.0
    profile["median_future_hours"] = round(median_future, 2)
    if not future_hours:
        add_check(checks, "FAIL", feed, "NO_FUTURE_EPG", "No future programme coverage remains")
    elif median_future < 6:
        add_check(checks, "FAIL", feed, "CRITICAL_LOW_COVERAGE", f"Median future coverage is only {median_future:.2f}h")
    elif median_future < min_future_hours:
        add_check(checks, "REVIEW", feed, "LOW_COVERAGE", f"Median future coverage {median_future:.2f}h < {min_future_hours:.2f}h")

    generated = parse_iso_time(manifest.get("generated"))
    if generated:
        age = max(0.0, (tnow - generated).total_seconds() / 3600.0)
        profile["manifest_age_hours"] = round(age, 2)
        if age > 72:
            add_check(checks, "FAIL", feed, "STALE_MANIFEST", f"Manifest is {age:.1f}h old")
        elif age > 30:
            add_check(checks, "REVIEW", feed, "AGING_MANIFEST", f"Manifest is {age:.1f}h old")

    expected_channels = manifest.get("channels")
    expected_programmes = manifest.get("programmes")
    if expected_channels is not None and int(expected_channels) != len(channels):
        add_check(checks, "FAIL", feed, "MANIFEST_CHANNEL_MISMATCH", f"manifest={expected_channels}, xml={len(channels)}")
    if expected_programmes is not None and int(expected_programmes) != len(programmes):
        add_check(checks, "FAIL", feed, "MANIFEST_PROGRAMME_MISMATCH", f"manifest={expected_programmes}, xml={len(programmes)}")

    expected_sha = manifest.get("sha256")
    if expected_sha:
        actual_sha = hashlib.sha256(compressed).hexdigest()
        if actual_sha != expected_sha:
            add_check(checks, "FAIL", feed, "MANIFEST_SHA256_MISMATCH", f"manifest={expected_sha}, actual={actual_sha}")
    expected_size = manifest.get("size_bytes", manifest.get("size"))
    if expected_size is not None and int(expected_size) != len(compressed):
        add_check(checks, "FAIL", feed, "MANIFEST_SIZE_MISMATCH", f"manifest={expected_size}, actual={len(compressed)}")

    if feed == "morocco":
        missing = sorted(MOROCCO_REQUIRED - ids)
        if missing:
            add_check(checks, "FAIL", feed, "MISSING_REQUIRED_IDS", f"Missing {len(missing)} stable Morocco IDs", missing)

    return profile, ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mena-dir", required=True)
    ap.add_argument("--morocco-dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--mode", choices=("shadow", "enforce"), default="shadow")
    ap.add_argument("--minimum-mena-future-hours", type=float, default=30.0)
    ap.add_argument("--minimum-morocco-future-hours", type=float, default=30.0)
    args = ap.parse_args()

    checks = []
    mena_dir = Path(args.mena_dir)
    morocco_dir = Path(args.morocco_dir)
    mena, mena_ids = audit_feed(
        mena_dir / "mena-arabic.xml.gz", mena_dir / "manifest.json", "mena", checks,
        args.minimum_mena_future_hours,
    )
    morocco, morocco_ids = audit_feed(
        morocco_dir / "morocco.xml.gz", morocco_dir / "manifest.json", "morocco", checks,
        args.minimum_morocco_future_hours,
    )

    collisions = sorted(mena_ids & morocco_ids)
    if collisions:
        add_check(checks, "REVIEW", "global", "CROSS_FEED_ID_COLLISION", f"{len(collisions)} exact IDs exist in both feeds", collisions[:20])

    counts = Counter(row["severity"] for row in checks)
    rank = max([SEVERITY_RANK[row["severity"]] for row in checks] or [0])
    status = ("PASS", "REVIEW", "FAIL")[rank]
    result = {
        "schema": 1,
        "checked_at": now_utc().isoformat(),
        "mode": args.mode,
        "status": status,
        "publish_blocked": args.mode == "enforce" and status == "FAIL",
        "summary": {
            "PASS": counts.get("PASS", 0),
            "REVIEW": counts.get("REVIEW", 0),
            "FAIL": counts.get("FAIL", 0),
            "checks": len(checks),
        },
        "feeds": {
            "mena": {**mena, "status": feed_status(checks, "mena")},
            "morocco": {**morocco, "status": feed_status(checks, "morocco")},
        },
        "checks": checks,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "EPGMANAGER GLOBAL HEALTH GATE v1",
        f"mode={args.mode}",
        f"status={status}",
        f"FAIL={counts.get('FAIL', 0)} REVIEW={counts.get('REVIEW', 0)}",
        "",
        "FEEDS",
        "MENA: status=%s channels=%s programmes=%s zero_epg=%s median_future=%.2fh age=%s" % (
            result["feeds"]["mena"]["status"], mena["channels"], mena["programmes"],
            mena["zero_epg_channels"], mena["median_future_hours"], mena["manifest_age_hours"]),
        "MOROCCO: status=%s channels=%s programmes=%s zero_epg=%s median_future=%.2fh age=%s" % (
            result["feeds"]["morocco"]["status"], morocco["channels"], morocco["programmes"],
            morocco["zero_epg_channels"], morocco["median_future_hours"], morocco["manifest_age_hours"]),
        "",
        "ISSUES",
    ]
    if not checks:
        lines.append("- PASS: no issue detected")
    else:
        for row in sorted(checks, key=lambda r: (-SEVERITY_RANK[r["severity"]], r["feed"], r["code"])):
            lines.append(f"- {row['severity']} [{row['feed']}] {row['code']}: {row['message']}")
            if row.get("sample"):
                lines.append("  sample=" + json.dumps(row["sample"], ensure_ascii=False))
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))

    if args.mode == "enforce" and status == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
