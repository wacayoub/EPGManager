#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit every programme in the exclusive MENA Cloud shards.

This audit never mutates generated XMLTV. It checks structural quality,
scheduling conflicts, suspicious duplicates, language policy, MBC title
cleanliness and obvious placeholder/bad metadata.

It also runs the exhaustive all-ID auditor as a synchronous release gate. The
build is rejected if any published ID receives a FAIL verdict. The generated
all-id-audit.{txt,json,csv} files are written beside the global audit so a
post-build publisher can expose exactly the same production truth.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")
WS_RE = re.compile(r"\s+")
MBC_META_RE = re.compile(
    r"(?:\bseason\s*\d+|\bepisode\s*\d+|\bep\.?\s*\d+|\bs\d{1,2}e\d{1,3}\b|(?:الموسم|موسم)\s*[0-9٠-٩]+|(?:الحلقة|حلقة)\s*[0-9٠-٩]+)",
    re.I,
)
GENERIC_TITLES = {
    "tba", "to be announced", "no information", "no info", "unknown",
    "programme", "program", "programming", "broadcast", "live", "test",
}
ARABIC_PROVIDER_SHARDS = {
    "provider-mbc", "provider-adm", "provider-dmi", "provider-rotana",
    "provider-art", "provider-ssc", "provider-alkass",
}
PREMIUM_EN_TITLE_AR_DESC = {"provider-bein", "provider-osn"}


def compact_text(value: str) -> str:
    return WS_RE.sub(" ", value or "").strip()


def norm_title(value: str) -> str:
    value = compact_text(value).casefold()
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def text_stats(text: str):
    ar = len(AR_RE.findall(text or ""))
    lat = len(LAT_RE.findall(text or ""))
    return ar, lat


def looks_arabic(text: str, lang: str = "") -> bool:
    if (lang or "").lower().startswith("ar"):
        return True
    ar, lat = text_stats(text)
    return ar >= 4 and ar >= lat


def looks_english(text: str, lang: str = "") -> bool:
    if (lang or "").lower().startswith("en"):
        return True
    ar, lat = text_stats(text)
    return lat >= 4 and lat > ar


def is_hybrid_desc(text: str) -> bool:
    ar, lat = text_stats(text)
    return ar >= 20 and lat >= 20 and min(ar, lat) / max(ar, lat) >= 0.12


def parse_xmltv_time(value: str):
    raw = (value or "").strip()
    if not raw:
        return None, None
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M %z", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
                offset = "implicit-UTC"
            else:
                offset = dt.strftime("%z")
            return dt.astimezone(timezone.utc), offset
        except ValueError:
            pass
    return None, None


def first_text(node: ET.Element, tag: str):
    items = []
    for el in node.findall(tag):
        text = compact_text(el.text or "")
        if text:
            items.append((text, (el.get("lang") or "").lower()))
    return items[0] if items else ("", "")


def read_root(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b" or path.suffix == ".gz":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def issue_sample(stem, cid, cname, p, title, desc, extra=None):
    out = {
        "shard": stem,
        "channel_id": cid,
        "channel": cname,
        "start": p.get("start") or "",
        "stop": p.get("stop") or "",
        "title": title[:240],
        "desc": desc[:320],
    }
    if extra:
        out.update(extra)
    return out


def run_all_id_release_gate(root_dir: Path, manifest_path: Path, out_dir: Path):
    """Generate exhaustive truth from exactly the shards being released."""
    all_id_script = Path(__file__).with_name("all_id_audit.py")
    out_json = out_dir / "all-id-audit.json"
    out_text = out_dir / "all-id-audit.txt"
    out_csv = out_dir / "all-id-audit.csv"
    subprocess.run([
        sys.executable, str(all_id_script),
        "--dir", str(root_dir),
        "--manifest", str(manifest_path),
        "--json", str(out_json),
        "--text", str(out_text),
        "--csv", str(out_csv),
    ], check=True)
    report = json.loads(out_json.read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("published_channels")
    if summary.get("missing_shards"):
        raise SystemExit("ALL-ID RELEASE GATE: missing shards %s" % summary["missing_shards"])
    if expected is not None and int(summary.get("channels", -1)) != int(expected):
        raise SystemExit("ALL-ID RELEASE GATE: channel count mismatch %s != %s" % (
            summary.get("channels"), expected))
    if int(summary.get("FAIL", 0) or 0) != 0:
        raise SystemExit("ALL-ID RELEASE GATE: FAIL=%d; publication blocked" % int(summary.get("FAIL", 0)))
    print("ALL-ID RELEASE GATE: PASS channels=%d programmes=%d PASS=%d REVIEW=%d NO_EPG=%d FAIL=0" % (
        int(summary.get("channels", 0)), int(summary.get("programmes", 0)),
        int(summary.get("PASS", 0)), int(summary.get("REVIEW", 0)), int(summary.get("NO_EPG", 0))))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--sample-limit", type=int, default=30)
    args = ap.parse_args()

    root_dir = Path(args.dir)
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stems = list((manifest.get("shards") or {}).keys())

    counts = Counter()
    by_shard = defaultdict(Counter)
    samples = defaultdict(list)
    total_channels = 0
    total_programmes = 0

    def add(kind, stem, sample, severity="warning"):
        counts[kind] += 1
        counts["severity_" + severity] += 1
        by_shard[stem][kind] += 1
        if len(samples[kind]) < args.sample_limit:
            samples[kind].append(sample)

    for stem in stems:
        path = root_dir / (stem + ".xml.gz")
        if not path.exists():
            add("missing_shard", stem, {"shard": stem}, "critical")
            continue
        try:
            tv = read_root(path)
        except Exception as exc:
            add("invalid_xml", stem, {"shard": stem, "error": str(exc)}, "critical")
            continue

        channel_names = {}
        for c in tv.findall("channel"):
            cid = compact_text(c.get("id") or "")
            names = [compact_text(x.text or "") for x in c.findall("display-name") if compact_text(x.text or "")]
            channel_names[cid] = names[0] if names else cid
        total_channels += len(channel_names)

        per_channel = defaultdict(list)
        offsets = defaultdict(lambda: defaultdict(set))
        for p in tv.findall("programme"):
            total_programmes += 1
            cid = compact_text(p.get("channel") or "")
            cname = channel_names.get(cid, cid)
            title, title_lang = first_text(p, "title")
            desc, desc_lang = first_text(p, "desc")
            start, start_offset = parse_xmltv_time(p.get("start") or "")
            stop, stop_offset = parse_xmltv_time(p.get("stop") or "")
            sample = issue_sample(stem, cid, cname, p, title, desc)

            if not cid or cid not in channel_names:
                add("orphan_programme", stem, sample, "critical")
            if not title:
                add("empty_title", stem, sample, "critical")
            elif norm_title(title) in GENERIC_TITLES:
                add("generic_title", stem, sample)
            if not desc:
                add("empty_description", stem, sample)
            elif norm_title(desc) == norm_title(title) and len(desc) > 2:
                add("description_equals_title", stem, sample)
            elif norm_title(desc) == norm_title(cname) and len(desc) > 2:
                add("description_is_channel_name", stem, sample)
            if desc and is_hybrid_desc(desc):
                add("hybrid_arabic_latin_description", stem, sample)

            if start is None or stop is None:
                add("invalid_time", stem, sample, "critical")
            else:
                dur = (stop - start).total_seconds()
                sample["duration_minutes"] = round(dur / 60.0, 2)
                if dur <= 0:
                    add("non_positive_duration", stem, sample, "critical")
                elif dur < 5 * 60:
                    add("very_short_event_lt_5m", stem, sample)
                elif dur > 6 * 3600:
                    add("very_long_event_gt_6h", stem, sample)
                per_channel[cid].append((start, stop, title, desc, p, sample))
                day = start.strftime("%Y-%m-%d")
                if start_offset:
                    offsets[cid][day].add(start_offset)
                if stop_offset:
                    offsets[cid][day].add(stop_offset)

            if stem == "provider-mbc" and title and MBC_META_RE.search(title):
                add("mbc_season_episode_left_in_title", stem, sample)

            if stem in PREMIUM_EN_TITLE_AR_DESC:
                if title and not looks_english(title, title_lang):
                    add("premium_title_not_english", stem, sample)
                if desc and not looks_arabic(desc, desc_lang):
                    add("premium_description_not_arabic", stem, sample)
            elif stem in ARABIC_PROVIDER_SHARDS:
                if title and not looks_arabic(title, title_lang):
                    add("arabic_provider_title_not_arabic", stem, sample)
                if desc and not looks_arabic(desc, desc_lang):
                    add("arabic_provider_description_not_arabic", stem, sample)

        for cid, rows in per_channel.items():
            rows.sort(key=lambda x: (x[0], x[1]))
            prev = None
            for row in rows:
                if prev is not None:
                    ps, pe, pt, pd, pp, psm = prev
                    cs, ce, ct, cd, cp, csm = row
                    if cs < pe:
                        overlap = (pe - cs).total_seconds()
                        same = norm_title(pt) == norm_title(ct) and bool(norm_title(ct))
                        extra = {
                            "overlap_minutes": round(overlap / 60.0, 2),
                            "previous_title": pt[:240],
                            "previous_start": pp.get("start") or "",
                            "previous_stop": pp.get("stop") or "",
                        }
                        sev = "critical" if overlap >= 10 * 60 else "warning"
                        add("overlapping_programmes", stem,
                            issue_sample(stem, cid, channel_names.get(cid, cid), cp, ct, cd, extra), sev)
                        if same and ((ce - cs).total_seconds() <= 10 * 60 or ce <= pe):
                            add("contained_or_short_duplicate_title", stem,
                                issue_sample(stem, cid, channel_names.get(cid, cid), cp, ct, cd, extra))
                    if pe >= ce:
                        continue
                prev = row

        for cid, days in offsets.items():
            for day, vals in days.items():
                clean = {x for x in vals if x}
                if len(clean) > 1:
                    add("mixed_timezone_offsets_same_day", stem, {
                        "shard": stem,
                        "channel_id": cid,
                        "channel": channel_names.get(cid, cid),
                        "day": day,
                        "offsets": sorted(clean),
                    })

    result = {
        "schema": 2,
        "scope": "all exclusive MENA country/provider shards + synchronous all-ID release gate",
        "channels_scanned": total_channels,
        "programmes_scanned": total_programmes,
        "issue_counts": dict(sorted(counts.items())),
        "by_shard": {k: dict(sorted(v.items())) for k, v in sorted(by_shard.items())},
        "samples": dict(samples),
    }
    Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "MENA GLOBAL EPG AUDIT",
        "channels_scanned=%d" % total_channels,
        "programmes_scanned=%d" % total_programmes,
        "critical=%d" % counts.get("severity_critical", 0),
        "warnings=%d" % counts.get("severity_warning", 0),
        "",
        "ISSUE COUNTS",
    ]
    for key, val in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if key.startswith("severity_"):
            continue
        lines.append("%s=%d" % (key, val))
    lines.extend(["", "TOP SAMPLES"])
    for kind in sorted(samples):
        lines.append("\n[%s] count=%d" % (kind, counts[kind]))
        for item in samples[kind][:10]:
            lines.append("- %s | %s | %s | %s" % (
                item.get("shard", ""), item.get("channel", ""),
                item.get("start", item.get("day", "")), item.get("title", "")))
            if item.get("previous_title"):
                lines.append("  prev: %s" % item["previous_title"])
            if item.get("desc"):
                lines.append("  desc: %s" % item["desc"][:220])
            if item.get("offsets"):
                lines.append("  offsets: %s" % ",".join(item["offsets"]))

    all_id_summary = run_all_id_release_gate(root_dir, manifest_path, Path(args.json).parent)
    result["all_id_release_gate"] = all_id_summary
    Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines += ["", "ALL-ID RELEASE GATE", json.dumps(all_id_summary, ensure_ascii=False, sort_keys=True)]
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("GLOBAL EPG AUDIT: channels=%d programmes=%d critical=%d warnings=%d issue_types=%d" % (
        total_channels, total_programmes, counts.get("severity_critical", 0),
        counts.get("severity_warning", 0), len([k for k in counts if not k.startswith("severity_")])) )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
