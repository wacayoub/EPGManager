#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exhaustive virtual EPGManager audit for every beIN provider XMLTV ID.

This intentionally emulates the receiver-side questions we care about:
- does the ID really have usable 48h EPG;
- is its timeline structurally sane (durations, overlaps, gaps, timezone mix);
- are title/description languages appropriate for the channel variant;
- is the guide useful or mostly generic XTRA/MAX placeholders;
- is this ID a verified compatibility alias and, if so, does its timeline match
  the canonical ID exactly;
- does the exact same timeline appear under unrelated IDs;
- should Smart Mapping treat the ID as PASS, ALIAS_OK, REVIEW or FAIL.

The script is diagnostic only. It never modifies the feed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")
FR_HINT = re.compile(r"\b(?:le|la|les|des|du|de|avec|contre|journ[eé]e|magazine|football)\b", re.I)
PLACEHOLDER = re.compile(
    r"^(?:tba|no information|schedule unavailable|program(?:me)? schedule unavailable|"
    r"جدول البرامج غير متاح|beIN Sports MAX|beIN Sports XTRA|24/7)$", re.I)
GENERIC = re.compile(
    r"beIN\s+SPORTS\s+XTRA\s+For\s+Live\s+And\s+Exclusive\s+Coverage|"
    r"^beIN\s+Sports\s+(?:MAX|XTRA)(?:\s*-.*)?$|^24/7$",
    re.I,
)
OPAQUE_ID = re.compile(r"^bein\.com-\d+\.qa$", re.I)


def first(node, tag):
    for el in node.findall(tag):
        value = (el.text or "").strip()
        if value:
            return value, (el.get("lang") or "").lower()
    return "", ""


def display_name(channel):
    value, _ = first(channel, "display-name")
    return value or (channel.get("id") or "").strip()


def parse_dt(raw):
    raw = (raw or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", raw)
    if not m:
        return None, "invalid"
    digits, off = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits, fmt)
    except Exception:
        return None, "invalid"
    if off == "Z" or not off:
        tz = timezone.utc
        off_label = off or "implicit-UTC"
    else:
        sign = 1 if off[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        off_label = off
    return dt.replace(tzinfo=tz).astimezone(timezone.utc), off_label


def script_profile(value):
    value = value or ""
    ar = len(AR.findall(value))
    lat = len(LAT.findall(value))
    return ar, lat


def has_ar(value, lang=""):
    ar, _ = script_profile(value)
    return (lang or "").startswith("ar") or ar >= 2


def has_latin(value, lang=""):
    _, lat = script_profile(value)
    return (lang or "").startswith(("en", "fr")) or lat >= 2


def dominant_lang(value, declared=""):
    declared = (declared or "").lower()
    if declared.startswith("ar"):
        return "ar"
    if declared.startswith("fr"):
        return "fr"
    ar, lat = script_profile(value)
    if ar >= 2 and lat >= 2:
        return "hybrid"
    if ar >= 2:
        return "ar"
    if lat >= 2:
        return "fr" if FR_HINT.search(value or "") else "en"
    return "other"


def clean(value):
    value = (value or "").casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def identity_family(cid, name):
    probe = clean("%s %s" % (cid, name))
    # Explicit language variants first.
    explicit_en = bool(re.search(r"english|(?:^|[_ .-])en(?:[._ -]|$)", probe))
    explicit_fr = bool(re.search(r"french|(?:^|[_ .-])fr(?:[._ -]|$)", probe))

    m = re.search(r"(?:bein\s*[_ .-]*sports|beinsports)\s*[_ .-]*(\d+)", probe)
    if m:
        suffix = " EN" if explicit_en else " FR" if explicit_fr else " AR/MENA"
        return "beIN Sports %s%s" % (m.group(1), suffix), "sports_en" if explicit_en else "sports_fr" if explicit_fr else "sports_ar"
    m = re.search(r"xtra\s*[_ .-]*0*(\d+)", probe)
    if m:
        return "beIN XTRA %d" % int(m.group(1)), "xtra"
    m = re.search(r"max\s*[_ .-]*0*(\d+)", probe)
    if m:
        return "beIN MAX %d" % int(m.group(1)), "max"
    if "sports news" in probe or "news_digital" in probe or re.search(r"\bnews\b", probe):
        return "beIN Sports News", "news"
    if "4k" in probe:
        return "beIN 4K", "sports_ar"
    if "sports fta" in probe or "fta digital" in probe:
        return "beIN Sports FTA", "sports_ar"
    if "boxoffice" in probe:
        return "beIN Boxoffice", "boxoffice"
    if OPAQUE_ID.match(cid):
        return "Opaque beIN.com alias", "opaque"
    if "movies" in probe or "movie" in probe:
        return "beIN Movies", "movies"
    if "series" in probe:
        return "beIN Series", "series"
    if "drama" in probe:
        return "beIN Drama", "drama"
    return name or cid, "other"


def duration_hours(start, stop):
    if start is None or stop is None:
        return None
    return (stop - start).total_seconds() / 3600.0


def timeline_fingerprint(rows):
    h = hashlib.sha1()
    for row in rows:
        # Channel ID intentionally excluded: aliases should fingerprint equally.
        payload = "%s\x1f%s\x1f%s\x1f%s\n" % (
            row["start_raw"], row["stop_raw"], clean(row["title"]), clean(row["desc"]))
        h.update(payload.encode("utf-8", "replace"))
    return h.hexdigest()


def union_coverage(rows):
    intervals = sorted((r["start"], r["stop"]) for r in rows if r["start"] is not None and r["stop"] is not None and r["stop"] > r["start"])
    if not intervals:
        return 0.0, 0.0, 0, 0.0
    covered = 0.0
    gap_total = 0.0
    gaps_gt_2h = 0
    cur_s, cur_e = intervals[0]
    first_s = cur_s
    last_e = cur_e
    for s, e in intervals[1:]:
        if s <= cur_e:
            if e > cur_e:
                cur_e = e
        else:
            covered += (cur_e - cur_s).total_seconds() / 3600.0
            gap = (s - cur_e).total_seconds() / 3600.0
            gap_total += gap
            if gap > 2.0:
                gaps_gt_2h += 1
            cur_s, cur_e = s, e
        if e > last_e:
            last_e = e
    covered += (cur_e - cur_s).total_seconds() / 3600.0
    span = (last_e - first_s).total_seconds() / 3600.0
    return covered, span, gaps_gt_2h, gap_total


def build_profile(cid, name, events):
    family, kind = identity_family(cid, name)
    rows = []
    invalid = overlaps = long6 = long12 = short2 = empty_title = empty_desc = 0
    desc_same_title = placeholders = generic = 0
    title_has_ar = title_has_latin = title_hybrid = 0
    desc_has_ar = desc_has_latin = 0
    offsets_by_day = defaultdict(set)
    title_counter = Counter()

    for p in events:
        title, tlang = first(p, "title")
        desc, dlang = first(p, "desc")
        start, soff = parse_dt(p.get("start") or "")
        stop, eoff = parse_dt(p.get("stop") or "")
        if not title:
            empty_title += 1
        else:
            title_counter[clean(title)] += 1
            a = has_ar(title, tlang)
            l = has_latin(title, tlang)
            title_has_ar += int(a)
            title_has_latin += int(l)
            title_hybrid += int(a and l)
            placeholders += int(bool(PLACEHOLDER.match(title.strip())))
            generic += int(bool(GENERIC.search(title.strip())))
        if not desc:
            empty_desc += 1
        else:
            desc_has_ar += int(has_ar(desc, dlang))
            desc_has_latin += int(has_latin(desc, dlang))
            if clean(desc) == clean(title) and title:
                desc_same_title += 1
        if start is not None:
            day = start.strftime("%Y-%m-%d")
            if soff:
                offsets_by_day[day].add(soff)
            if eoff:
                offsets_by_day[day].add(eoff)
        dur = duration_hours(start, stop)
        if start is None or stop is None or dur is None or dur <= 0:
            invalid += 1
        else:
            long6 += int(dur > 6.0)
            long12 += int(dur > 12.0)
            short2 += int(dur < (2.0 / 60.0))
        rows.append({
            "start": start, "stop": stop,
            "start_raw": (p.get("start") or "").strip(), "stop_raw": (p.get("stop") or "").strip(),
            "title": title, "desc": desc, "title_lang": tlang, "desc_lang": dlang,
        })

    valid = sorted((r for r in rows if r["start"] is not None and r["stop"] is not None and r["stop"] > r["start"]), key=lambda r: (r["start"], r["stop"]))
    active_end = None
    for r in valid:
        if active_end is not None and r["start"] < active_end:
            overlaps += 1
        if active_end is None or r["stop"] > active_end:
            active_end = r["stop"]

    n = len(rows)
    desc_n = max(1, n - empty_desc)
    covered, span, gaps_gt_2h, gap_total = union_coverage(valid)
    top_title_count = title_counter.most_common(1)[0][1] if title_counter else 0
    top_title_pct = (top_title_count / float(max(1, n))) * 100.0
    unique_title_pct = (len(title_counter) / float(max(1, n))) * 100.0
    mixed_tz_days = sum(1 for vals in offsets_by_day.values() if len(vals) > 1)
    all_offsets = sorted({x for vals in offsets_by_day.values() for x in vals})

    return {
        "id": cid, "name": name, "family": family, "kind": kind,
        "events": n, "rows": valid,
        "fingerprint": timeline_fingerprint(rows),
        "coverage_hours": round(covered, 2), "span_hours": round(span, 2),
        "gaps_gt_2h": gaps_gt_2h, "gap_hours": round(gap_total, 2),
        "invalid": invalid, "overlaps": overlaps,
        "long_gt_6h": long6, "long_gt_12h": long12, "short_lt_2m": short2,
        "empty_title": empty_title, "empty_desc": empty_desc,
        "desc_same_title": desc_same_title, "placeholder": placeholders, "generic": generic,
        "title_has_ar_pct": round(title_has_ar / float(max(1, n)) * 100.0, 1),
        "title_has_latin_pct": round(title_has_latin / float(max(1, n)) * 100.0, 1),
        "title_hybrid_pct": round(title_hybrid / float(max(1, n)) * 100.0, 1),
        "desc_ar_pct": round(desc_has_ar / float(desc_n) * 100.0, 1) if n else 0.0,
        "desc_latin_pct": round(desc_has_latin / float(desc_n) * 100.0, 1) if n else 0.0,
        "top_title_pct": round(top_title_pct, 1), "unique_title_pct": round(unique_title_pct, 1),
        "mixed_tz_days": mixed_tz_days, "offsets": all_offsets,
        "preview": [{"start": r["start_raw"], "stop": r["stop_raw"], "title": r["title"], "desc": r["desc"][:180]} for r in valid[:4]],
    }


def initial_verdict(row, non_recommended):
    issues = []
    warnings = []
    cid = row["id"]
    kind = row["kind"]
    n = row["events"]

    if n == 0:
        issues.append("NO_PROGRAMMES")
    if row["invalid"]:
        issues.append("INVALID_DURATION=%d" % row["invalid"])
    if row["overlaps"]:
        issues.append("OVERLAPS=%d" % row["overlaps"])
    if row["mixed_tz_days"]:
        issues.append("MIXED_TIMEZONE_DAYS=%d" % row["mixed_tz_days"])
    if row["long_gt_12h"]:
        issues.append("VERY_LONG_12H=%d" % row["long_gt_12h"])
    elif row["long_gt_6h"]:
        warnings.append("LONG_6H=%d" % row["long_gt_6h"])
    if row["empty_title"]:
        issues.append("EMPTY_TITLE=%d" % row["empty_title"])
    if row["short_lt_2m"]:
        warnings.append("SHORT_LT_2M=%d" % row["short_lt_2m"])
    if row["empty_desc"]:
        warnings.append("EMPTY_DESC=%d" % row["empty_desc"])
    if row["desc_same_title"]:
        warnings.append("DESC_EQUALS_TITLE=%d" % row["desc_same_title"])
    if row["gaps_gt_2h"]:
        warnings.append("GAPS_GT_2H=%d" % row["gaps_gt_2h"])

    # Language / usefulness policy.
    if kind in {"sports_ar", "news"}:
        if row["desc_ar_pct"] < 60:
            issues.append("AR_DESC_LOW=%.0f%%" % row["desc_ar_pct"])
        elif row["desc_ar_pct"] < 90:
            warnings.append("AR_DESC=%.0f%%" % row["desc_ar_pct"])
        # Qatar1-style titles are allowed to be Arabic, English or hybrid, but
        # the Arabic/MENA service should show Arabic enrichment on a meaningful share.
        if row["title_has_ar_pct"] < 25:
            warnings.append("TITLE_AR_ENRICHMENT_LOW=%.0f%%" % row["title_has_ar_pct"])
    elif kind == "sports_en":
        if row["title_has_latin_pct"] < 80:
            warnings.append("EN_TITLE_LOW=%.0f%%" % row["title_has_latin_pct"])
        if row["desc_ar_pct"] < 75:
            warnings.append("AR_DESC_LOW=%.0f%%" % row["desc_ar_pct"])
    elif kind == "sports_fr":
        if row["title_has_latin_pct"] < 80:
            warnings.append("FR_TITLE_LOW=%.0f%%" % row["title_has_latin_pct"])
        if row["desc_ar_pct"] < 75:
            warnings.append("AR_DESC_LOW=%.0f%%" % row["desc_ar_pct"])
    elif kind in {"movies", "series", "drama", "boxoffice"}:
        if row["desc_ar_pct"] < 75:
            warnings.append("AR_DESC_LOW=%.0f%%" % row["desc_ar_pct"])

    generic_pct = row["generic"] / float(max(1, n)) * 100.0
    if generic_pct >= 70:
        warnings.append("GENERIC_GUIDE=%.0f%%" % generic_pct)
    elif row["placeholder"]:
        warnings.append("PLACEHOLDER=%d" % row["placeholder"])
    if n >= 5 and row["top_title_pct"] >= 70:
        warnings.append("REPEATED_TITLE=%.0f%%" % row["top_title_pct"])
    if n >= 8 and row["unique_title_pct"] <= 20:
        warnings.append("LOW_TITLE_DIVERSITY=%.0f%%" % row["unique_title_pct"])

    low = cid.casefold()
    if "sports66" in low:
        issues.append("SUSPICIOUS_SPORTS66_ID")
    if OPAQUE_ID.match(cid):
        warnings.append("OPAQUE_BEIN_COM_ID")
    if "boxoffice" in low:
        warnings.append("BOXOFFICE_NOT_SPORTS_LINEAR")
    if cid.startswith("logos-") or cid.startswith("logos_"):
        warnings.append("LOGO_PREFIX_ID")
    if cid in non_recommended:
        warnings.append("NON_RECOMMENDED_ID")

    # Structural errors make an ID unsafe. Long >12h is treated as structural
    # because previous feeds contained multi-day bogus events.
    if issues:
        verdict = "FAIL"
    elif warnings:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    return verdict, issues, warnings


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

    channels = {}
    for ch in root.findall("channel"):
        cid = (ch.get("id") or "").strip()
        if cid:
            channels[cid] = display_name(ch)
    events = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            events[cid].append(p)

    # Read strict sharder metadata when available so the virtual receiver can
    # verify the exact compatibility aliases currently published.
    manifest_path = Path(args.xml).with_name("shards.json")
    compat = {}
    non_recommended = set()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            b = (manifest.get("shards") or {}).get("provider-bein") or {}
            compat = {a: v.get("canonical") for a, v in (b.get("compat_aliases") or {}).items() if v.get("canonical")}
            non_recommended = set(b.get("non_recommended_ids") or [])
        except Exception:
            pass

    rows = [build_profile(cid, channels[cid], events.get(cid, [])) for cid in sorted(channels, key=str.casefold)]
    by_id = {r["id"]: r for r in rows}

    # First-pass verdicts.
    for r in rows:
        verdict, issues, warnings = initial_verdict(r, non_recommended)
        r["verdict"] = verdict
        r["issues"] = issues
        r["warnings"] = warnings
        r["compat_canonical"] = compat.get(r["id"])
        r["alias_exact_match"] = None

    # Exact compatibility validation: old ID must have byte-equivalent logical
    # programme payload (times/titles/descriptions) to its canonical service.
    for alias, canonical in compat.items():
        a = by_id.get(alias)
        c = by_id.get(canonical)
        if not a or not c:
            if a:
                a["issues"].append("CANONICAL_MISSING=%s" % canonical)
                a["verdict"] = "FAIL"
            continue
        same = a["fingerprint"] == c["fingerprint"] and a["events"] == c["events"]
        a["alias_exact_match"] = same
        if same:
            a["verdict"] = "ALIAS_OK"
            a["warnings"] = [w for w in a["warnings"] if w not in {"NON_RECOMMENDED_ID", "LOGO_PREFIX_ID"}]
        else:
            a["issues"].append("ALIAS_TIMELINE_MISMATCH=%s" % canonical)
            a["verdict"] = "FAIL"

    # Detect exact same timelines under unrelated IDs. Known compat pairs are fine;
    # everything else deserves review because this often exposes wrong guide reuse.
    fp_groups = defaultdict(list)
    for r in rows:
        if r["events"]:
            fp_groups[r["fingerprint"]].append(r)
    duplicate_groups = []
    for fp, members in fp_groups.items():
        if len(members) < 2:
            continue
        ids = [m["id"] for m in members]
        fams = sorted(set(m["family"] for m in members))
        duplicate_groups.append({"ids": ids, "families": fams})
        for m in members:
            expected = compat.get(m["id"])
            known_pair = expected in ids if expected else any(compat.get(x) == m["id"] for x in ids)
            if len(fams) > 1 and not known_pair:
                tag = "DUPLICATE_TIMELINE_OTHER_FAMILY"
                if tag not in m["warnings"]:
                    m["warnings"].append(tag)
                if m["verdict"] == "PASS":
                    m["verdict"] = "REVIEW"

    counts = Counter(r["verdict"] for r in rows)
    lines = [
        "VIRTUAL EPGMANAGER - EXHAUSTIVE beIN ID-BY-ID AUDIT",
        "channels=%d programmes=%d PASS=%d ALIAS_OK=%d REVIEW=%d FAIL=%d" % (
            len(rows), sum(r["events"] for r in rows), counts["PASS"], counts["ALIAS_OK"], counts["REVIEW"], counts["FAIL"]),
        "Every provider-bein XMLTV ID is tested exactly as a receiver mapping target.",
        "",
    ]

    for idx, r in enumerate(rows, 1):
        notes = r["issues"] + r["warnings"]
        alias = " -> %s" % r["compat_canonical"] if r["compat_canonical"] else ""
        lines.append("%02d. [%s] %s%s" % (idx, r["verdict"], r["id"], alias))
        lines.append("    name=%s | family=%s | kind=%s" % (r["name"], r["family"], r["kind"]))
        lines.append("    events=%d coverage=%.1fh span=%.1fh gaps>2h=%d gap_total=%.1fh overlaps=%d invalid=%d >6h=%d >12h=%d" % (
            r["events"], r["coverage_hours"], r["span_hours"], r["gaps_gt_2h"], r["gap_hours"],
            r["overlaps"], r["invalid"], r["long_gt_6h"], r["long_gt_12h"]))
        lines.append("    title: AR-presence=%.0f%% Latin=%.0f%% hybrid=%.0f%% unique=%.0f%% | desc: AR=%.0f%% empty=%d same-as-title=%d" % (
            r["title_has_ar_pct"], r["title_has_latin_pct"], r["title_hybrid_pct"], r["unique_title_pct"],
            r["desc_ar_pct"], r["empty_desc"], r["desc_same_title"]))
        lines.append("    notes=%s" % (", ".join(notes) if notes else "NONE"))
        for ev in r["preview"][:2]:
            lines.append("    • %s" % (ev["title"] or "<NO TITLE>"))
        lines.append("")

    lines.append("EXACT DUPLICATE TIMELINE GROUPS")
    for group in duplicate_groups:
        lines.append("- %s | families=%s" % (" ; ".join(group["ids"]), " ; ".join(group["families"])))

    payload = {
        "schema": 2,
        "mode": "virtual-epgmanager-exhaustive-bein",
        "summary": {"channels": len(rows), "programmes": sum(r["events"] for r in rows), "counts": dict(counts)},
        "compat_aliases": compat,
        "non_recommended_ids": sorted(non_recommended, key=str.casefold),
        "channels": [{k: v for k, v in r.items() if k != "rows"} for r in rows],
        "duplicate_timeline_groups": duplicate_groups,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
