#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative beIN provider repair layer.

Safe operations only:
- enrich known canonical Movies/Series IDs from exact-title Arabic donor IDs;
- enrich beIN Drama from its verified same-slot Arabic guide twin;
- normalize proven replay/title defects without guessing generic Live state;
- repair >6h bogus stops only when the exact same title has a plausible reference duration;
- strip generic XTRA filler while preserving real event programmes.

No descriptions are fabricated. Series QA/EG schedules are deliberately not bridged.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")
SPACE_RE = re.compile(r"\s+")
ARABIC_BEIN_RE = re.compile(r"بي\s*[إا]ن")
GENERIC_DESC_RE = re.compile(
    r"^(?:مباراة\.?|برنامج رياضي ضمن تغطية قنوات بي إن سبورتس\.?|"
    r"لا توجد معلومات|جدول البرامج غير متاح|no information|schedule unavailable)$",
    re.I,
)
GENERIC_XTRA_RE = re.compile(
    r"beIN\s+SPORTS\s+XTRA\s+For\s+Live\s+And\s+Exclusive\s+Coverage|"
    r"^beIN\s+Sports\s+(?:MAX|XTRA)(?:\s*-.*)?$|^24/7$",
    re.I,
)
FULHAM_REPLAY_RE = re.compile(r"^\s*Live\s*:\s*Liverpool\s+v(?:s)?\s+Fulham\b", re.I)

# Canonical English/Latin ID -> rich Arabic exact-title donor.
EXACT_TITLE_DONORS = {
    "BEIN MOVIES ACTION.eg": "بي إن موفيز أكشن.eg",
    "BEIN MOVIES DRAMA.eg": "بي إن موفيز دراما.eg",
    "BEIN MOVIES FAMILY.eg": "بي إن موفيز فاميلي.eg",
    "BEIN MOVIES PREMIERE.eg": "بي إن موفيز بريمير.eg",
    "BeIn Series HD 1.eg": "بي إن سيريس.eg",
    "beIN Series HD 2.eg": "بي إن سيريس إتش دي 2.eg",
}

# Same service and exact current slots, but different title language/transliteration.
SLOT_DESC_DONORS = {
    "beIN Drama.eg": "beINDrama1.qa@SD",
}

# Proven guide-language/compatibility aliases. Series1/Series2 QA are intentionally
# absent because their timelines differ from the EG services.
RECOMMENDED_COMPAT_ALIASES = {
    "بي إن موفيز أكشن.eg": "BEIN MOVIES ACTION.eg",
    "بي إن موفيز دراما.eg": "BEIN MOVIES DRAMA.eg",
    "بي إن موفيز فاميلي.eg": "BEIN MOVIES FAMILY.eg",
    "بي إن موفيز بريمير.eg": "BEIN MOVIES PREMIERE.eg",
    "بي إن سيريس.eg": "BeIn Series HD 1.eg",
    "بي إن سيريس إتش دي 2.eg": "beIN Series HD 2.eg",
    "beINDrama1.qa@SD": "beIN Drama.eg",
    "beINMovies1Premiere.qa@SD": "BEIN MOVIES PREMIERE.eg",
    "beINMovies2Action.qa@SD": "BEIN MOVIES ACTION.eg",
    "beINMovies3Drama.qa@SD": "BEIN MOVIES DRAMA.eg",
    "beINMovies4Family.qa@SD": "BEIN MOVIES FAMILY.eg",
    "NEWS_DIGITAL_Mono_EN.bein": "NEWS_DIGITAL_Mono_AR.bein",
}


def copy_element(node):
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def looks_bein_id(cid):
    cid = cid or ""
    return "bein" in cid.casefold() or bool(ARABIC_BEIN_RE.search(cid))


def text_of(node, role):
    for child in node.findall(role):
        value = (child.text or "").strip()
        if value:
            return value
    return ""


def normalized_title(value):
    return SPACE_RE.sub(" ", (value or "").casefold()).strip()


def is_arabic(value):
    return len(AR_RE.findall(value or "")) >= 2


def good_arabic_desc(value, title=""):
    value = (value or "").strip()
    if len(value) < 20 or not is_arabic(value) or GENERIC_DESC_RE.match(value):
        return False
    return not title or normalized_title(value) != normalized_title(title)


def replace_single(node, role, value, lang):
    if not value:
        return False
    nodes = list(node.findall(role))
    current = (nodes[0].text or "").strip() if nodes else ""
    current_lang = (nodes[0].get("lang") or "").lower() if nodes else ""
    if current == value and current_lang == lang:
        return False
    if nodes:
        dst = nodes[0]
        for extra in nodes[1:]:
            node.remove(extra)
    else:
        dst = ET.SubElement(node, role)
    dst.text = value
    if lang:
        dst.set("lang", lang)
    elif "lang" in dst.attrib:
        del dst.attrib["lang"]
    return True


def parse_dt(raw):
    raw = (raw or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", raw)
    if not m:
        return None
    digits, offset = m.groups()
    try:
        dt = datetime.strptime(digits, "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M")
    except Exception:
        return None
    if offset == "Z" or not offset:
        tz = timezone.utc
    else:
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def format_like(raw, dt):
    raw = (raw or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", raw)
    if not m:
        return raw
    digits, offset = m.groups()
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
        local = dt.astimezone(tz)
    else:
        local = dt.astimezone(timezone.utc)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    return local.strftime(fmt) + ((" " + offset) if offset else "")


def slot_key(programme):
    return ((programme.get("start") or "").strip(), (programme.get("stop") or "").strip())


def donor_desc_index(rows):
    index = defaultdict(set)
    for programme in rows:
        title = text_of(programme, "title")
        desc = text_of(programme, "desc")
        if title and good_arabic_desc(desc, title):
            index[normalized_title(title)].add(desc)
    return index


def fill_exact_title_descriptions(programmes, report):
    for target, donor in EXACT_TITLE_DONORS.items():
        target_rows = programmes.get(target, [])
        donor_rows = programmes.get(donor, [])
        index = donor_desc_index(donor_rows)
        fills = 0
        ambiguous = 0
        for programme in target_rows:
            if good_arabic_desc(text_of(programme, "desc"), text_of(programme, "title")):
                continue
            choices = index.get(normalized_title(text_of(programme, "title")), set())
            if len(choices) == 1:
                if replace_single(programme, "desc", next(iter(choices)), "ar"):
                    fills += 1
            elif len(choices) > 1:
                ambiguous += 1
        report["exact_title_pairs"].append({
            "target": target, "donor": donor,
            "target_events": len(target_rows), "donor_events": len(donor_rows),
            "desc_fills": fills, "ambiguous_titles": ambiguous,
        })
        report["summary"]["arabic_desc_fills"] += fills


def fill_same_slot_descriptions(programmes, report):
    for target, donor in SLOT_DESC_DONORS.items():
        target_rows = programmes.get(target, [])
        donor_rows = programmes.get(donor, [])
        target_by = {slot_key(p): p for p in target_rows if all(slot_key(p))}
        donor_by = {slot_key(p): p for p in donor_rows if all(slot_key(p))}
        common = sorted(set(target_by) & set(donor_by))
        overlap_ratio = len(common) / float(max(1, min(len(target_by), len(donor_by))))
        fills = 0
        if overlap_ratio >= 0.90:
            for key in common:
                target_p = target_by[key]
                donor_p = donor_by[key]
                desc = text_of(donor_p, "desc")
                if (not good_arabic_desc(text_of(target_p, "desc"), text_of(target_p, "title"))
                        and good_arabic_desc(desc, text_of(donor_p, "title"))):
                    if replace_single(target_p, "desc", desc, "ar"):
                        fills += 1
        report["slot_pairs"].append({
            "target": target, "donor": donor,
            "target_events": len(target_rows), "donor_events": len(donor_rows),
            "matched_slots": len(common), "overlap_ratio": round(overlap_ratio, 3),
            "desc_fills": fills,
        })
        report["summary"]["arabic_desc_fills"] += fills


def normalize_known_replay_titles(programmes, report):
    changed = 0
    for cid, rows in programmes.items():
        if not looks_bein_id(cid):
            continue
        for programme in rows:
            title = text_of(programme, "title")
            if not title:
                continue
            # Fix the truncated Liverpool token everywhere, including a genuine live event.
            new = re.sub(r"\brpool\b", "Liverpool", title, flags=re.I)
            # Liverpool-Fulham was played on 12 Sep 2026. Any occurrence in this
            # rolling guide after that fixture is replay/highlight, not a live event.
            if FULHAM_REPLAY_RE.search(new):
                new = FULHAM_REPLAY_RE.sub("Liverpool vs Fulham", new, count=1)
            if new != title and replace_single(programme, "title", new, "en" if LAT_RE.search(new) else ""):
                changed += 1
    report["summary"]["known_replay_title_fixes"] += changed


def repair_long_events(programmes, report):
    references = defaultdict(list)
    for cid, rows in programmes.items():
        if not looks_bein_id(cid):
            continue
        for programme in rows:
            start = parse_dt(programme.get("start"))
            stop = parse_dt(programme.get("stop"))
            title = normalized_title(text_of(programme, "title"))
            if start and stop and title:
                duration = (stop - start).total_seconds() / 3600.0
                if 0.5 <= duration <= 4.0:
                    references[title].append((cid, start, duration))

    repairs = []
    for cid, rows in programmes.items():
        if not looks_bein_id(cid):
            continue
        for programme in rows:
            start = parse_dt(programme.get("start"))
            stop = parse_dt(programme.get("stop"))
            title = normalized_title(text_of(programme, "title"))
            if not start or not stop or not title:
                continue
            old_hours = (stop - start).total_seconds() / 3600.0
            if old_hours <= 6.0:
                continue
            choices = references.get(title, [])
            if not choices:
                continue
            same_start = [x for x in choices if abs((x[1] - start).total_seconds()) <= 120]
            pool = same_start or choices
            rounded_minutes = [round(x[2] * 60) for x in pool]
            counts = Counter(rounded_minutes)
            minutes, count = counts.most_common(1)[0]
            # Without a same-start reference, require consensus when references disagree.
            if not same_start and len(counts) > 1 and count < 2:
                continue
            new_stop = start + timedelta(minutes=minutes)
            if new_stop >= stop:
                continue
            programme.set("stop", format_like(programme.get("stop") or "", new_stop))
            repairs.append({
                "channel": cid, "title": text_of(programme, "title"),
                "old_hours": round(old_hours, 2), "new_hours": round(minutes / 60.0, 2),
                "reference_channels": sorted({x[0] for x in pool if round(x[2] * 60) == minutes}),
            })
    report["long_event_repairs"] = repairs
    report["summary"]["long_event_repairs"] += len(repairs)


def strip_generic_xtra(programmes, report):
    removed = []
    for cid in list(programmes):
        if not looks_bein_id(cid):
            continue
        low = cid.casefold()
        if "xtra" not in low and "max" not in low:
            continue
        keep = []
        for programme in programmes[cid]:
            title = text_of(programme, "title")
            if title and GENERIC_XTRA_RE.search(title.strip()):
                removed.append({"channel": cid, "start": programme.get("start") or "", "title": title})
            else:
                keep.append(programme)
        programmes[cid] = keep
    report["generic_xtra_removed"] = removed
    report["summary"]["generic_xtra_removed"] += len(removed)


def repair_programme_map(ids, programmes, copy_fn=copy_element):
    ids = set(ids)
    repaired = {cid: [copy_fn(p) for p in programmes.get(cid, [])] for cid in ids}
    report = {
        "schema": 2,
        "policy": "beIN provider conservative repair; no fabricated descriptions; Series QA/EG not bridged",
        "exact_title_pairs": [], "slot_pairs": [], "long_event_repairs": [], "generic_xtra_removed": [],
        "summary": {"arabic_desc_fills": 0, "known_replay_title_fixes": 0, "long_event_repairs": 0, "generic_xtra_removed": 0},
    }
    fill_exact_title_descriptions(repaired, report)
    fill_same_slot_descriptions(repaired, report)
    normalize_known_replay_titles(repaired, report)
    repair_long_events(repaired, report)
    strip_generic_xtra(repaired, report)
    return repaired, report


def read_root(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def write_root(root, path):
    ET.indent(root, space="  ")
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0) if str(path).endswith(".gz") else raw)


def bridge_root(root):
    channels = {c.get("id"): copy_element(c) for c in root.findall("channel") if c.get("id")}
    programmes = defaultdict(list)
    for p in root.findall("programme"):
        if p.get("channel") in channels:
            programmes[p.get("channel")].append(p)
    repaired, report = repair_programme_map(channels, programmes)
    out = ET.Element("tv", root.attrib)
    for cid in channels:
        out.append(channels[cid])
    for cid in channels:
        for programme in repaired.get(cid, []):
            out.append(programme)
    return out, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    source = read_root(args.input)
    before_ids = [c.get("id") for c in source.findall("channel")]
    before_programmes = len(source.findall("programme"))
    out, report = bridge_root(source)
    after_ids = [c.get("id") for c in out.findall("channel")]
    after_programmes = len(out.findall("programme"))
    if before_ids != after_ids:
        raise SystemExit("beIN repair invariant failed: channel IDs changed")
    expected = before_programmes - report["summary"]["generic_xtra_removed"]
    if after_programmes != expected:
        raise SystemExit("beIN repair invariant failed: unexpected programme count change %d -> %d expected %d" % (
            before_programmes, after_programmes, expected))
    report["invariants"] = {
        "channel_ids_unchanged": True,
        "programmes_before": before_programmes,
        "programmes_after": after_programmes,
        "only_generic_xtra_removed": True,
    }
    write_root(out, args.output)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
