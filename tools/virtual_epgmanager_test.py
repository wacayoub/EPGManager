#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Virtual EPGManager test harness for real MENA Cloud shards.

The goal is to emulate the important receiver-side decision path without Enigma2:
- discover EPG IDs for a receiver channel inside the expected shard
- profile the real 48h programmes behind every candidate ID
- score language/schedule/metadata quality
- select a canonical candidate only when it is safe
- print the exact programme presentation a receiver would import

Diagnostic only. It never mutates the XMLTV feed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")
WS_RE = re.compile(r"\s+")
MBC_META_RE = re.compile(
    r"(?:\bseason\s*\d+|\bepisode\s*\d+|\bep\.?\s*\d+|\bs\d{1,2}e\d{1,3}\b|(?:الموسم|موسم)\s*[0-9٠-٩]+|(?:الحلقة|حلقة)\s*[0-9٠-٩]+)",
    re.I,
)

TARGETS = [
    {
        "key": "mbc5", "receiver": "MBC 5 HD", "label": "MBC5",
        "shard": "provider-mbc", "policy": "arabic",
        "include": [r"\bMBC\s*5\b"], "exclude": [],
    },
    {
        "key": "mbc1", "receiver": "MBC 1 HD", "label": "MBC1",
        "shard": "provider-mbc", "policy": "arabic",
        "include": [r"\bMBC\s*1\b"], "exclude": [r"Masr|Egypt|USA"],
    },
    {
        "key": "mbc2", "receiver": "MBC 2 HD", "label": "MBC2",
        "shard": "provider-mbc", "policy": "arabic",
        "include": [r"\bMBC\s*2\b"], "exclude": [],
    },
    {
        "key": "mbc_action", "receiver": "MBC ACTION HD", "label": "MBC Action",
        "shard": "provider-mbc", "policy": "arabic",
        "include": [r"MBC\s*Action"], "exclude": [],
    },
    {
        "key": "bein1_ar", "receiver": "beIN SPORTS 1 AR", "label": "beIN Sports 1 Arabic",
        "shard": "provider-bein", "policy": "premium",
        "include": [r"beIN\s*SPORTS\s*1\b", r"beINSports1\b"],
        "exclude": [r"ENGLISH|FRENCH|\bEN\b|\bFR\b"],
    },
    {
        "key": "bein2_ar", "receiver": "beIN SPORTS 2 AR", "label": "beIN Sports 2 Arabic",
        "shard": "provider-bein", "policy": "premium",
        "include": [r"beIN\s*SPORTS\s*2\b", r"beINSports2\b"],
        "exclude": [r"ENGLISH|FRENCH|\bEN\b|\bFR\b"],
    },
    {
        "key": "adm1", "receiver": "Abu Dhabi Sports 1 HD", "label": "Abu Dhabi Sports 1",
        "shard": "provider-adm", "policy": "arabic",
        "include": [r"Abu\s*Dhabi\s*Sports\s*1", r"AD\s*Sports\s*1"],
        "exclude": [r"Premium\s*2|Extra"],
    },
    {
        "key": "osn_action", "receiver": "OSN Movies Action", "label": "OSN Movies Action",
        "shard": "provider-osn", "policy": "premium",
        "include": [r"OSN.*Movies.*Action", r"OSN.*Action"], "exclude": [],
    },
    {
        "key": "osn_premiere", "receiver": "OSN Movies Premiere", "label": "OSN Movies Premiere",
        "shard": "provider-osn", "policy": "premium",
        "include": [r"OSN.*Movies.*Premiere", r"OSN.*Premiere"], "exclude": [],
    },
    {
        "key": "alkass1", "receiver": "Al Kass 1 HD", "label": "Al Kass 1",
        "shard": "provider-alkass", "policy": "arabic",
        "include": [r"Al\s*Kass\s*(?:One|1)\b", r"Alkass\s*(?:One|1)\b", r"2023\s*Alkass\s*1"],
        "exclude": [],
    },
]


def compact(value: str) -> str:
    return WS_RE.sub(" ", value or "").strip()


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c))


def norm(value: str) -> str:
    value = strip_accents(compact(value)).casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"\b(?:hd|fhd|uhd|4k|sd)\b", " ", value)
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def looks_ar(text: str, lang: str = "") -> bool:
    if (lang or "").lower().startswith("ar"):
        return True
    ar = len(AR_RE.findall(text or ""))
    lat = len(LAT_RE.findall(text or ""))
    return ar >= 3 and ar >= lat


def looks_en(text: str, lang: str = "") -> bool:
    if (lang or "").lower().startswith("en"):
        return True
    ar = len(AR_RE.findall(text or ""))
    lat = len(LAT_RE.findall(text or ""))
    return lat >= 3 and lat > ar


def parse_time(raw: str):
    raw = compact(raw)
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
            continue
    return None, None


def first(node: ET.Element, tag: str):
    for el in node.findall(tag):
        txt = compact(el.text or "")
        if txt:
            return txt, (el.get("lang") or "").lower()
    return "", ""


def display_name(ch: ET.Element) -> str:
    value, _ = first(ch, "display-name")
    return value or compact(ch.get("id") or "")


def read_root(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b" or path.suffix == ".gz":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def target_match(target, cid: str, name: str) -> bool:
    probe = "%s | %s" % (cid, name)
    if not any(re.search(p, probe, re.I) for p in target["include"]):
        return False
    if any(re.search(p, probe, re.I) for p in target["exclude"]):
        return False
    return True


def profile_candidate(cid: str, cname: str, events: list[ET.Element], policy: str):
    rows = []
    ar_title = en_title = ar_desc = en_desc = empty_desc = 0
    nonpositive = overlaps = short_duplicates = mbc_meta = 0
    offsets_by_day = defaultdict(set)

    for p in events:
        title, tlang = first(p, "title")
        desc, dlang = first(p, "desc")
        start, soff = parse_time(p.get("start") or "")
        stop, eoff = parse_time(p.get("stop") or "")
        if looks_ar(title, tlang): ar_title += 1
        if looks_en(title, tlang): en_title += 1
        if not desc: empty_desc += 1
        else:
            if looks_ar(desc, dlang): ar_desc += 1
            if looks_en(desc, dlang): en_desc += 1
        if MBC_META_RE.search(title or ""):
            mbc_meta += 1
        if start is not None:
            day = start.strftime("%Y-%m-%d")
            if soff: offsets_by_day[day].add(soff)
            if eoff: offsets_by_day[day].add(eoff)
        if start is not None and stop is not None and stop <= start:
            nonpositive += 1
        rows.append({
            "start": start, "stop": stop, "start_raw": p.get("start") or "", "stop_raw": p.get("stop") or "",
            "title": title, "title_lang": tlang, "desc": desc, "desc_lang": dlang,
        })

    valid_rows = [r for r in rows if r["start"] is not None and r["stop"] is not None]
    valid_rows.sort(key=lambda r: (r["start"], r["stop"]))
    prev = None
    for row in valid_rows:
        if prev is not None and row["start"] < prev["stop"]:
            overlaps += 1
            same_title = norm(row["title"]) and norm(row["title"]) == norm(prev["title"])
            duration = (row["stop"] - row["start"]).total_seconds()
            contained = row["stop"] <= prev["stop"]
            if same_title and (duration <= 10 * 60 or contained):
                short_duplicates += 1
            if row["stop"] <= prev["stop"]:
                continue
        prev = row

    n = max(1, len(rows))
    desc_nonempty = max(1, len(rows) - empty_desc)
    title_ar_pct = ar_title / n
    title_en_pct = en_title / n
    desc_ar_pct = ar_desc / desc_nonempty if len(rows) - empty_desc else 0.0
    desc_en_pct = en_desc / desc_nonempty if len(rows) - empty_desc else 0.0
    empty_desc_pct = empty_desc / n
    mixed_tz_days = sum(1 for vals in offsets_by_day.values() if len(vals) > 1)

    score = 100.0
    reasons = []
    if not rows:
        score -= 80
        reasons.append("no programmes")
    if policy == "arabic":
        score -= (1.0 - title_ar_pct) * 28
        score -= (1.0 - desc_ar_pct) * 27
        if title_ar_pct < 0.75: reasons.append("Arabic title coverage %.0f%%" % (title_ar_pct * 100))
        if desc_ar_pct < 0.75: reasons.append("Arabic description coverage %.0f%%" % (desc_ar_pct * 100))
    elif policy == "premium":
        score -= (1.0 - title_en_pct) * 22
        score -= (1.0 - desc_ar_pct) * 33
        if title_en_pct < 0.75: reasons.append("English title coverage %.0f%%" % (title_en_pct * 100))
        if desc_ar_pct < 0.75: reasons.append("Arabic description coverage %.0f%%" % (desc_ar_pct * 100))
    score -= min(25.0, overlaps * 2.5)
    score -= min(12.0, short_duplicates * 4.0)
    score -= min(18.0, nonpositive * 9.0)
    score -= min(15.0, mixed_tz_days * 7.5)
    score -= empty_desc_pct * 18
    score -= min(10.0, mbc_meta * 2.0)
    if overlaps: reasons.append("%d overlap(s)" % overlaps)
    if short_duplicates: reasons.append("%d contained/short duplicate(s)" % short_duplicates)
    if nonpositive: reasons.append("%d invalid duration(s)" % nonpositive)
    if mixed_tz_days: reasons.append("mixed timezone offsets on %d day(s)" % mixed_tz_days)
    if empty_desc: reasons.append("%d empty description(s)" % empty_desc)
    if mbc_meta: reasons.append("%d title(s) still contain season/episode" % mbc_meta)

    name_quality = 0
    if cname:
        nn = norm(cname)
        ci = norm(cid)
        if (nn and nn in ci) or (ci and ci in nn):
            name_quality = 1
    score += name_quality * 1.5
    score = max(0.0, min(100.0, score))

    critical = bool(nonpositive or mixed_tz_days >= 2 or overlaps >= 3)
    language_ok = (title_ar_pct >= 0.75 and desc_ar_pct >= 0.75) if policy == "arabic" else (title_en_pct >= 0.75 and desc_ar_pct >= 0.75)
    if score >= 85 and not critical and language_ok and not short_duplicates:
        status = "PASS"
    elif score >= 65 and not nonpositive:
        status = "REVIEW"
    else:
        status = "FAIL"

    return {
        "id": cid, "name": cname, "events": len(rows), "score": round(score, 1), "status": status,
        "title_ar_pct": round(title_ar_pct * 100, 1), "title_en_pct": round(title_en_pct * 100, 1),
        "desc_ar_pct": round(desc_ar_pct * 100, 1), "desc_en_pct": round(desc_en_pct * 100, 1),
        "empty_desc": empty_desc, "overlaps": overlaps, "short_duplicates": short_duplicates,
        "nonpositive": nonpositive, "mixed_tz_days": mixed_tz_days, "mbc_meta_titles": mbc_meta,
        "reasons": reasons, "rows": valid_rows,
    }


def format_local(dt):
    if dt is None:
        return "?"
    if ZoneInfo is None:
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    return dt.astimezone(ZoneInfo("Africa/Casablanca")).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--events", type=int, default=5)
    args = ap.parse_args()
    base = Path(args.dir)
    output = {"schema": 1, "mode": "virtual-epgmanager", "targets": []}
    lines = ["VIRTUAL EPGMANAGER - REAL MENA CLOUD TEST", "Timezone display: Africa/Casablanca", ""]

    roots = {}
    for target in TARGETS:
        shard = target["shard"]
        path = base / (shard + ".xml.gz")
        if shard not in roots:
            roots[shard] = read_root(path)
        root = roots[shard]
        channels = {}
        events_by_id = defaultdict(list)
        for ch in root.findall("channel"):
            cid = compact(ch.get("id") or "")
            if cid:
                channels[cid] = display_name(ch)
        for p in root.findall("programme"):
            cid = compact(p.get("channel") or "")
            if cid:
                events_by_id[cid].append(p)

        candidates = []
        for cid, cname in channels.items():
            if target_match(target, cid, cname):
                candidates.append(profile_candidate(cid, cname, events_by_id.get(cid, []), target["policy"]))
        candidates.sort(key=lambda x: (-x["score"], -x["events"], x["id"].casefold()))
        best = candidates[0] if candidates else None
        safe_lock = bool(best and best["status"] == "PASS")
        entry = {
            "key": target["key"], "label": target["label"], "receiver": target["receiver"],
            "shard": target["shard"], "policy": target["policy"], "safe_lock": safe_lock,
            "best_id": best["id"] if best else None, "best_status": best["status"] if best else "NO_MATCH",
            "candidates": [{k: v for k, v in c.items() if k != "rows"} for c in candidates],
        }
        output["targets"].append(entry)

        lines.append("=== %s ===" % target["label"])
        lines.append("Receiver: %s" % target["receiver"])
        lines.append("Shard: %s | policy=%s" % (target["shard"], target["policy"]))
        if not candidates:
            lines.append("RESULT: FAIL - no candidate ID\n")
            continue
        for idx, c in enumerate(candidates, 1):
            lines.append("CANDIDATE %d: %s | %s | score=%.1f | events=%d" % (idx, c["id"], c["status"], c["score"], c["events"]))
            lines.append("  language: title AR %.0f%% / EN %.0f%% ; desc AR %.0f%% / EN %.0f%%" % (
                c["title_ar_pct"], c["title_en_pct"], c["desc_ar_pct"], c["desc_en_pct"]))
            lines.append("  schedule: overlaps=%d short_dup=%d bad_duration=%d mixed_tz_days=%d empty_desc=%d" % (
                c["overlaps"], c["short_duplicates"], c["nonpositive"], c["mixed_tz_days"], c["empty_desc"]))
            if c["reasons"]:
                lines.append("  notes: %s" % "; ".join(c["reasons"]))
        lines.append("BEST: %s [%s]" % (best["id"], best["status"]))
        lines.append("AUTO-LOCK: %s" % ("YES" if safe_lock else "NO - keep REVIEW until cleaned"))
        lines.append("PROGRAMME PREVIEW (best candidate):")
        rows = best["rows"]
        now = datetime.now(timezone.utc)
        future = [r for r in rows if r["stop"] and r["stop"] >= now]
        preview = (future or rows)[: max(1, args.events)]
        for r in preview:
            lines.append("  %s -> %s | %s" % (format_local(r["start"]), format_local(r["stop"]), r["title"] or "<EMPTY TITLE>"))
            lines.append("    %s" % ((r["desc"] or "<EMPTY DESCRIPTION>")[:360]))
        lines.append("")

    Path(args.json).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
