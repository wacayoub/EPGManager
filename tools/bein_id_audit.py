#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit every beIN provider XMLTV ID and its real 48h programme payload."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")
SPORT_HINT = re.compile(r"sport|xtra|max|news|4k|boxoffice|bein\.com-|digital_mono|sports\d", re.I)
PLACEHOLDER = re.compile(r"^(?:tba|no information|schedule unavailable|program(?:me)? schedule unavailable|جدول البرامج غير متاح)$", re.I)


def txt(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def parse_dt(raw):
    raw = (raw or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", raw)
    if not m:
        return None
    digits, off = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if off == "Z" or not off:
        tz = timezone.utc
    else:
        sign = 1 if off[0] == "+" else -1
        from datetime import timedelta
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def lang(s):
    ar = len(AR.findall(s or ""))
    en = len(LAT.findall(s or ""))
    if ar >= 2 and ar >= en * 0.5:
        return "ar"
    if en >= 2:
        return "en"
    return "other"


def norm_id(cid, name):
    s = (cid or name or "").casefold()
    s = re.sub(r"\.(?:qa|ae|sa|eg|bein)(?:@.*)?$", "", s)
    s = re.sub(r"logos[_\- ]*", "", s)
    s = re.sub(r"\b(?:digital|mono|hd|sd|uhd|fhd)\b", " ", s)
    s = re.sub(r"[_./|:+\-]+", " ", s)
    s = re.sub(r"\bbein\s*sports\s*(\d+)\b", r"bein sports \1", s)
    s = re.sub(r"\bbeinsports\s*(\d+)\b", r"bein sports \1", s)
    s = re.sub(r"\b(en|eng|english)\b", " english ", s)
    s = re.sub(r"\b(fr|fra|french)\b", " french ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def family(cid, name):
    n = norm_id(cid, name)
    m = re.search(r"bein sports\s*(\d+)\b", n)
    if m:
        suffix = ""
        if "english" in n:
            suffix = " EN"
        elif "french" in n:
            suffix = " FR"
        return "beIN Sports %s%s" % (m.group(1), suffix)
    m = re.search(r"xtra\s*(\d+)\b", n)
    if m:
        return "beIN XTRA %s" % m.group(1)
    m = re.search(r"max\s*(\d+)\b", n)
    if m:
        return "beIN MAX %s" % m.group(1)
    if "news" in n:
        return "beIN Sports News"
    if "4k" in n:
        return "beIN 4K"
    if "fta" in n:
        return "beIN Sports FTA"
    if "boxoffice" in n:
        return "beIN Boxoffice"
    if re.match(r"bein com \d+", n):
        return "UNKNOWN bein.com alias"
    return n or cid


def profile(cid, name, events):
    rows = []
    invalid = overlaps = long6 = empty_title = empty_desc = placeholders = 0
    title_ar = title_en = desc_ar = desc_en = 0
    for p in events:
        title = txt(p, "title")
        desc = txt(p, "desc")
        start = parse_dt(p.get("start"))
        stop = parse_dt(p.get("stop"))
        if not title:
            empty_title += 1
        else:
            title_ar += lang(title) == "ar"
            title_en += lang(title) == "en"
            placeholders += bool(PLACEHOLDER.match(title))
        if not desc:
            empty_desc += 1
        else:
            desc_ar += lang(desc) == "ar"
            desc_en += lang(desc) == "en"
        if start is None or stop is None or stop <= start:
            invalid += 1
            continue
        dur = (stop - start).total_seconds()
        if dur > 6 * 3600:
            long6 += 1
        rows.append((start, stop, title))
    rows.sort(key=lambda x: (x[0], x[1]))
    prev = None
    for r in rows:
        if prev is not None and r[0] < prev[1]:
            overlaps += 1
            if r[1] > prev[1]:
                prev = r
        else:
            prev = r
    n = len(events)
    broken = []
    if n == 0:
        broken.append("NO_PROGRAMMES")
    if invalid:
        broken.append("INVALID_DURATION=%d" % invalid)
    if overlaps:
        broken.append("OVERLAPS=%d" % overlaps)
    if placeholders:
        broken.append("PLACEHOLDER=%d" % placeholders)
    if long6 >= max(2, n // 3 if n else 2):
        broken.append("VERY_LONG=%d" % long6)
    if empty_title:
        broken.append("EMPTY_TITLE=%d" % empty_title)

    # ID quality / Smart Mapping safety flags.
    suspicious = []
    low = cid.casefold()
    if "sports66" in low or "sports66" in name.casefold():
        suspicious.append("SUSPICIOUS_CHANNEL_66")
    if "boxoffice" in low:
        suspicious.append("BOXOFFICE_NOT_LINEAR_SPORT")
    if re.match(r"^bein\.com-\d+\.qa$", cid, re.I):
        suspicious.append("OPAQUE_BEIN_COM_ID")
    if cid.startswith("logos-") or cid.startswith("logos_"):
        suspicious.append("LOGO_PREFIX_ID")
    if "digital_mono" in low:
        suspicious.append("GUIDE_ALIAS_ID")

    if broken:
        status = "BROKEN"
    elif suspicious:
        status = "ALIAS/REVIEW"
    elif n < 3:
        status = "WEAK"
    else:
        status = "OK"

    return {
        "id": cid,
        "name": name,
        "family": family(cid, name),
        "sports_like": bool(SPORT_HINT.search(cid + " " + name)),
        "events": n,
        "status": status,
        "issues": broken + suspicious,
        "empty_desc": empty_desc,
        "title_ar_pct": round((title_ar / n * 100.0), 1) if n else 0.0,
        "title_en_pct": round((title_en / n * 100.0), 1) if n else 0.0,
        "desc_ar_pct": round((desc_ar / max(1, n-empty_desc) * 100.0), 1) if n else 0.0,
        "desc_en_pct": round((desc_en / max(1, n-empty_desc) * 100.0), 1) if n else 0.0,
        "overlaps": overlaps,
        "invalid": invalid,
        "long_gt_6h": long6,
        "preview": [r[2] for r in rows[:5]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    data = Path(args.xml).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    names = {}
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            names[cid] = txt(c, "display-name") or cid
    events = defaultdict(list)
    for p in root.findall("programme"):
        events[(p.get("channel") or "").strip()].append(p)

    rows = [profile(cid, names[cid], events.get(cid, [])) for cid in sorted(names, key=str.casefold)]
    sports = [r for r in rows if r["sports_like"]]
    groups = defaultdict(list)
    for r in sports:
        groups[r["family"]].append(r)

    # A family should expose one preferred working canonical ID; other working
    # members are aliases, and broken members should never be suggested first.
    family_summary = []
    for fam in sorted(groups, key=str.casefold):
        members = groups[fam]
        ranked = sorted(members, key=lambda r: (
            0 if r["status"] == "OK" else 1 if r["status"] == "ALIAS/REVIEW" else 2 if r["status"] == "WEAK" else 3,
            -r["events"], r["id"].casefold()
        ))
        family_summary.append({"family": fam, "preferred": ranked[0]["id"], "members": [r["id"] for r in ranked]})

    counts = defaultdict(int)
    for r in sports:
        counts[r["status"]] += 1

    lines = [
        "BEIN SPORTS ID AUDIT - REAL 48H PROVIDER SHARD",
        "channels_total=%d sports_like=%d OK=%d REVIEW=%d WEAK=%d BROKEN=%d" % (
            len(rows), len(sports), counts["OK"], counts["ALIAS/REVIEW"], counts["WEAK"], counts["BROKEN"]),
        "",
    ]
    for r in sports:
        lines.append("[%s] %s | %s | events=%d | empty_desc=%d | title EN=%.0f%% AR=%.0f%% | desc AR=%.0f%%" % (
            r["status"], r["id"], r["family"], r["events"], r["empty_desc"], r["title_en_pct"], r["title_ar_pct"], r["desc_ar_pct"]))
        if r["issues"]:
            lines.append("  issues: %s" % ", ".join(r["issues"]))
        for title in r["preview"][:3]:
            lines.append("  - %s" % title)
    lines.append("")
    lines.append("LOGICAL FAMILIES / PREFERRED IDS")
    for f in family_summary:
        lines.append("%s -> %s" % (f["family"], f["preferred"]))
        for x in f["members"][1:]:
            lines.append("  alias: %s" % x)

    payload = {"schema": 1, "counts": dict(counts), "sports": sports, "families": family_summary}
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
