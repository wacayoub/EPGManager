#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative cross-feed metadata bridge for verified beIN guide-language twins.

The bridge never changes channel IDs, programme channel references, start/stop times,
or programme counts. It only fills/normalizes programme title/description metadata
when two explicitly verified IDs carry the exact same start+stop slot.

Policy:
- premium titles: prefer the trusted Latin/English guide title for Movies/Series twins;
- beIN Drama keeps each feed's title because the Latin feed is transliteration, not
  necessarily an English editorial title;
- descriptions: only copy a real Arabic synopsis (Arabic script, minimum length,
  not equal to title) into a missing/non-Arabic description;
- never fabricate a synopsis from a title and never bridge unmatched slots.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LAT_RE = re.compile(r"[A-Za-z]")
GENERIC_DESC_RE = re.compile(
    r"^(?:مباراة\.?|برنامج رياضي ضمن تغطية قنوات بي إن سبورتس\.?|"
    r"لا توجد معلومات|جدول البرامج غير متاح|no information|schedule unavailable)$",
    re.I,
)

# (Latin/English guide ID, Arabic/native guide ID, title policy)
VERIFIED_TWINS = [
    ("beIN Drama.eg", "beINDrama1.qa@SD", "preserve"),
    ("BEIN MOVIES PREMIERE.eg", "beINMovies1Premiere.qa@SD", "english"),
    ("BEIN MOVIES ACTION.eg", "beINMovies2Action.qa@SD", "english"),
    ("BEIN MOVIES DRAMA.eg", "beINMovies3Drama.qa@SD", "english"),
    ("BEIN MOVIES FAMILY.eg", "beINMovies4Family.qa@SD", "english"),
    ("BeIn Series HD 1.eg", "beINSeries1.qa@SD", "english"),
    ("beIN Series HD 2.eg", "beINSeries2.qa@SD", "english"),
]


def read_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def write_root(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="  ")
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    else:
        path.write_bytes(raw)


def text_of(node: ET.Element, role: str) -> str:
    for child in node.findall(role):
        value = (child.text or "").strip()
        if value:
            return value
    return ""


def is_arabic(value: str) -> bool:
    return len(AR_RE.findall(value or "")) >= 2


def is_latin_title(value: str) -> bool:
    return len(LAT_RE.findall(value or "")) >= 3


def good_arabic_desc(value: str, title: str = "") -> bool:
    value = (value or "").strip()
    if len(value) < 20 or not is_arabic(value):
        return False
    if GENERIC_DESC_RE.match(value):
        return False
    if title and value.casefold() == title.strip().casefold():
        return False
    return True


def replace_single(node: ET.Element, role: str, value: str, lang: str) -> bool:
    if not value:
        return False
    children = list(node.findall(role))
    current = (children[0].text or "").strip() if children else ""
    current_lang = (children[0].get("lang") or "").lower() if children else ""
    if current == value and current_lang == lang:
        return False
    if children:
        dst = children[0]
        for extra in children[1:]:
            node.remove(extra)
    else:
        dst = ET.SubElement(node, role)
    dst.text = value
    if lang:
        dst.set("lang", lang)
    elif "lang" in dst.attrib:
        del dst.attrib["lang"]
    return True


def slot_key(p: ET.Element):
    return ((p.get("start") or "").strip(), (p.get("stop") or "").strip())


def choose_latin_title(a: ET.Element, b: ET.Element) -> str:
    # The left-hand member of VERIFIED_TWINS is the trusted Latin/English guide.
    for p in (a, b):
        title = text_of(p, "title")
        if is_latin_title(title):
            return title
    return ""


def choose_arabic_desc(a: ET.Element, b: ET.Element) -> str:
    candidates = []
    for p in (b, a):  # native/Arabic guide first
        desc = text_of(p, "desc")
        title = text_of(p, "title")
        if good_arabic_desc(desc, title):
            candidates.append(desc)
    return max(candidates, key=len) if candidates else ""


def bridge(root: ET.Element):
    programmes = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid:
            programmes[cid].append(p)

    report = {
        "schema": 1,
        "policy": "exact-slot metadata-only bridge; no IDs/times/programme counts changed; no fabricated descriptions",
        "pairs": [],
        "summary": {
            "pairs_declared": len(VERIFIED_TWINS),
            "pairs_present": 0,
            "matched_slots": 0,
            "unmatched_left": 0,
            "unmatched_right": 0,
            "title_updates": 0,
            "arabic_desc_fills": 0,
        },
    }

    for left_id, right_id, title_policy in VERIFIED_TWINS:
        left_rows = programmes.get(left_id, [])
        right_rows = programmes.get(right_id, [])
        left_by = {slot_key(p): p for p in left_rows if all(slot_key(p))}
        right_by = {slot_key(p): p for p in right_rows if all(slot_key(p))}
        common = sorted(set(left_by) & set(right_by))
        left_only = sorted(set(left_by) - set(right_by))
        right_only = sorted(set(right_by) - set(left_by))
        row = {
            "left": left_id,
            "right": right_id,
            "title_policy": title_policy,
            "left_events": len(left_rows),
            "right_events": len(right_rows),
            "matched_slots": len(common),
            "left_only_slots": len(left_only),
            "right_only_slots": len(right_only),
            "title_updates": 0,
            "arabic_desc_fills": 0,
            "examples": [],
        }
        if left_rows and right_rows:
            report["summary"]["pairs_present"] += 1

        for key in common:
            left = left_by[key]
            right = right_by[key]
            latin_title = choose_latin_title(left, right)
            arabic_desc = choose_arabic_desc(left, right)
            changed_title = 0
            changed_desc = 0

            if title_policy == "english" and latin_title:
                changed_title += int(replace_single(left, "title", latin_title, "en"))
                changed_title += int(replace_single(right, "title", latin_title, "en"))

            if arabic_desc:
                for p in (left, right):
                    current = text_of(p, "desc")
                    if not good_arabic_desc(current, text_of(p, "title")):
                        changed_desc += int(replace_single(p, "desc", arabic_desc, "ar"))

            row["title_updates"] += changed_title
            row["arabic_desc_fills"] += changed_desc
            if (changed_title or changed_desc) and len(row["examples"]) < 4:
                row["examples"].append({
                    "start": key[0],
                    "stop": key[1],
                    "title": latin_title or text_of(left, "title"),
                    "desc_filled": bool(arabic_desc and changed_desc),
                })

        report["summary"]["matched_slots"] += len(common)
        report["summary"]["unmatched_left"] += len(left_only)
        report["summary"]["unmatched_right"] += len(right_only)
        report["summary"]["title_updates"] += row["title_updates"]
        report["summary"]["arabic_desc_fills"] += row["arabic_desc_fills"]
        report["pairs"].append(row)
    return report


def invariant_signature(root: ET.Element):
    channels = sorted((c.get("id") or "").strip() for c in root.findall("channel"))
    slots = sorted(((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip()) for p in root.findall("programme"))
    return channels, slots


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    src = read_root(Path(args.input))
    before_channels, before_slots = invariant_signature(src)
    report = bridge(src)
    after_channels, after_slots = invariant_signature(src)
    if before_channels != after_channels:
        raise SystemExit("beIN metadata bridge invariant failed: channel IDs changed")
    if before_slots != after_slots:
        raise SystemExit("beIN metadata bridge invariant failed: programme slots changed")
    report["invariants"] = {
        "channel_ids_unchanged": True,
        "programme_slots_unchanged": True,
        "channels": len(before_channels),
        "programmes": len(before_slots),
    }
    write_root(src, Path(args.output))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("beIN metadata bridge: pairs=%d matched=%d title_updates=%d arabic_desc_fills=%d channels=%d programmes=%d" % (
        report["summary"]["pairs_present"], report["summary"]["matched_slots"], report["summary"]["title_updates"],
        report["summary"]["arabic_desc_fills"], len(before_channels), len(before_slots)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
