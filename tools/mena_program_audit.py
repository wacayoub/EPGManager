#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate human-readable programme samples for key MENA channels.

This step also runs the exhaustive alias-aware all-ID audit as a pre-publication
release gate. Wrong cloned timelines, technical/asset IDs and known impossible
IDs must be zero before the MENA data branch can be updated. Explicit receiver
compatibility aliases are intentionally allowed and never auto-locked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")

TARGETS = [
    {
        "key": "mbc5",
        "label": "MBC5",
        "shard": "provider-mbc",
        "include": [r"\bMBC\s*5\b"],
        "exclude": [],
    },
    {
        "key": "bein_sports_1_ar",
        "label": "beIN Sports 1 Arabic",
        "shard": "provider-bein",
        "include": [r"beIN\s*SPORTS\s*1\b", r"beINSports1\b"],
        "exclude": [r"ENGLISH", r"\bEN\b", r"FRENCH", r"\bFR\b"],
    },
    {
        "key": "abu_dhabi_sports_1",
        "label": "Abu Dhabi Sports 1",
        "shard": "provider-adm",
        "include": [r"Abu\s*Dhabi\s*Sports\s*1", r"AD\s*Sports\s*1"],
        "exclude": [r"Premium\s*2", r"Extra"],
    },
    {
        "key": "osn_action",
        "label": "OSN Movies Action",
        "shard": "provider-osn",
        "include": [r"OSN.*Movies.*Action", r"OSN.*Action"],
        "exclude": [],
    },
]

HARD_RELEASE_BLOCKERS = {
    "WRONG_PROGRAMME_ASSIGNMENT_CLONED_TIMELINE",
    "TECHNICAL_OR_ASSET_ID",
    "SUSPICIOUS_BEIN_SPORTS66_ID",
}


def lang_of(text: str, declared: str = "") -> str:
    d = (declared or "").lower()
    if d.startswith("ar"):
        return "ar"
    if d.startswith("en"):
        return "en"
    text = text or ""
    ar = len(AR_RE.findall(text))
    lat = len(LATIN_RE.findall(text))
    if ar >= 2 and ar >= lat * 0.5:
        return "ar"
    if lat >= 2:
        return "en"
    return "other"


def texts(node, role):
    out = []
    for child in node.findall(role):
        txt = (child.text or "").strip()
        if txt:
            out.append({"text": txt, "lang": lang_of(txt, child.get("lang") or "")})
    return out


def display_name(channel):
    vals = texts(channel, "display-name")
    return vals[0]["text"] if vals else (channel.get("id") or "")


def parse_dt(value):
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", (value or "").strip())
    if not m:
        return None
    digits, off = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if off == "Z" or not off:
        tz = timezone.utc
    else:
        sign = 1 if off[0] == "+" else -1
        hh, mm = int(off[1:3]), int(off[3:5])
        from datetime import timedelta
        tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def load(path):
    return ET.fromstring(gzip.decompress(Path(path).read_bytes()))


def match_target(target, cid, name):
    probe = "%s | %s" % (cid, name)
    if not any(re.search(p, probe, re.I) for p in target["include"]):
        return False
    if any(re.search(p, probe, re.I) for p in target["exclude"]):
        return False
    return True


def run_all_id_release_gate(base: Path) -> None:
    # Compatibility aliases deliberately duplicate a canonical timeline. The
    # alias-aware wrapper distinguishes those from genuinely unrelated clones.
    script = Path(__file__).with_name("all_id_audit_alias_aware.py")
    manifest = base / "shards.json"
    if not script.exists() or not manifest.exists():
        raise RuntimeError("alias-aware all-ID release gate prerequisites missing")

    json_path = base / "all-id-audit.json"
    text_path = base / "all-id-audit.txt"
    csv_path = base / "all-id-audit.csv"
    subprocess.run([
        sys.executable, str(script),
        "--dir", str(base),
        "--manifest", str(manifest),
        "--json", str(json_path),
        "--text", str(text_path),
        "--csv", str(csv_path),
    ], check=True)

    audit = json.loads(json_path.read_text(encoding="utf-8"))
    issue_counts = audit.get("issue_counts") or {}
    blockers = {k: int(issue_counts.get(k, 0) or 0) for k in HARD_RELEASE_BLOCKERS}
    blockers = {k: v for k, v in blockers.items() if v > 0}
    summary = audit.get("summary") or {}
    print("ALL-ID RELEASE GATE: channels=%s PASS=%s REVIEW=%s NO_EPG=%s FAIL=%s blockers=%s" % (
        summary.get("channels"), summary.get("PASS"), summary.get("REVIEW"),
        summary.get("NO_EPG"), summary.get("FAIL"), blockers or "none"))
    if blockers:
        raise SystemExit("RELEASE BLOCKED: hard all-ID programme integrity failures remain: %s" % blockers)
    if int(summary.get("FAIL", 0) or 0) > 0:
        raise SystemExit("RELEASE BLOCKED: alias-aware all-ID audit still has FAIL=%s" % summary.get("FAIL"))
    if int(summary.get("NO_EPG", 0) or 0) > 0:
        raise SystemExit("RELEASE BLOCKED: receiver-facing shards still contain NO_EPG=%s" % summary.get("NO_EPG"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--events", type=int, default=5)
    args = ap.parse_args()

    base = Path(args.dir)
    now = datetime.now(timezone.utc)
    report = {"generated": now.isoformat(), "targets": []}
    lines = []

    for target in TARGETS:
        root = load(base / (target["shard"] + ".xml.gz"))
        channels = {}
        for ch in root.findall("channel"):
            cid = (ch.get("id") or "").strip()
            if cid:
                channels[cid] = display_name(ch)

        matched = [(cid, name) for cid, name in channels.items() if match_target(target, cid, name)]
        events_by_id = {cid: [] for cid, _ in matched}
        for p in root.findall("programme"):
            cid = (p.get("channel") or "").strip()
            if cid not in events_by_id:
                continue
            start = parse_dt(p.get("start") or "")
            stop = parse_dt(p.get("stop") or "")
            if start is None:
                continue
            if stop is not None and stop < now:
                continue
            events_by_id[cid].append((start, p))

        entry = {"key": target["key"], "label": target["label"], "shard": target["shard"], "matches": []}
        lines.append("=== %s [%s] ===" % (target["label"], target["shard"]))
        if not matched:
            lines.append("NO MATCH\n")
        for cid, name in sorted(matched, key=lambda x: x[0].casefold()):
            rows = sorted(events_by_id.get(cid, []), key=lambda x: x[0])[: max(1, args.events)]
            item = {"id": cid, "name": name, "events": []}
            lines.append("ID: %s" % cid)
            lines.append("Name: %s" % name)
            for start, p in rows:
                titles = texts(p, "title")
                descs = texts(p, "desc")
                ev = {
                    "start": p.get("start") or "",
                    "stop": p.get("stop") or "",
                    "titles": titles,
                    "descriptions": descs,
                }
                item["events"].append(ev)
                title = titles[0] if titles else {"text": "", "lang": "other"}
                desc = descs[0] if descs else {"text": "", "lang": "other"}
                lines.append("  %s -> %s" % (p.get("start") or "?", p.get("stop") or "?"))
                lines.append("    TITLE[%s]: %s" % (title["lang"], title["text"]))
                lines.append("    DESC[%s]: %s" % (desc["lang"], desc["text"][:500]))
            if not rows:
                lines.append("  NO UPCOMING PROGRAMMES")
            lines.append("")
            entry["matches"].append(item)
        report["targets"].append(entry)

    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    virtual_script = Path(__file__).with_name("virtual_epgmanager_test.py")
    if virtual_script.exists():
        subprocess.run([
            sys.executable, str(virtual_script),
            "--dir", str(base),
            "--json", str(base / "virtual-epgmanager.json"),
            "--text", str(base / "virtual-epgmanager.txt"),
            "--events", str(max(1, args.events)),
        ], check=True)

    run_all_id_release_gate(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
