#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Correct per-channel time offsets in the ElCinema feed.

ElCinema publishes some channels hours away from their real broadcast time
while others are right (13 Nov 2026: Rotana Cinema, Rotana Classic, beIN
Movies 1-3, MBC2, MBC Max, Oman TV were 5 h early; MBC1/3/4/5, MBC Drama,
Dubai One were correct). The offset is stable per channel, not per run.

For every ElCinema channel that also exists, under the same XMLTV ID, in a
trusted production feed, the programmes are matched by title and the time
difference is measured:

- a shift is accepted only with >= MIN_MATCHES title matches of which
  >= MIN_SHARE agree on the same 15-minute-rounded shift, or, for channels
  whose titles repeat (news), >= WEAK_SHARE agreement confirmed by >= ALIGN_SHARE
  of all programme start times lining up at that same shift and clearly
  better than at +-1 h (an hourly grid lines up at every whole hour);
- trusted references that disagree with each other leave the channel alone;
- every accepted shift is recorded in config/elcinema-time-offsets.json for
  review, but only shifts proven in the same run are applied: the same
  channel came 5 h early in one run and on time in another (MBC Drama,
  13 Nov 2026 12:21 vs 23:46 UTC), so an old shift could break a correct row;
- channels without any evidence are left exactly as they are.

Only programme start/stop attributes change. Nothing is invented or removed.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

# OSN and the MENA aggregate are deliberately excluded: the MENA feed copies
# ElCinema rows, and OSN disagreed with Shahid (MBC's own platform) on MBC3,
# MBC Iraq and MBC Masr 2 on 13 Nov 2026.
DEFAULT_REFERENCES = (
    "feeds/rotana.xml.gz", "feeds/bein.xml.gz", "feeds/shahid.xml.gz", "feeds/stctv.xml.gz",
    "feeds/starzplay.xml.gz", "feeds/dubaiplus.xml.gz", "feeds/alkass.xml.gz",
    "feeds/aljazeera.xml.gz",
)
MIN_MATCHES = 6
MIN_SHARE = 0.75
WEAK_SHARE = 0.5
ALIGN_SHARE = 0.8
ALIGN_MARGIN = 0.3  # alignment must beat the +-1 h neighbours (hourly grids align anywhere)
MAX_WINDOW = 14 * 3600
STEP = 900


def parse_time(value: str) -> datetime:
    value = (value or "").strip()
    base = datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    rest = value[14:].strip()
    offset = timedelta(0)
    if rest[:1] in "+-" and len(rest) >= 5:
        offset = timedelta(hours=int(rest[1:3]), minutes=int(rest[3:5]))
        if rest[0] == "-":
            offset = -offset
    return (base - offset).replace(tzinfo=timezone.utc)


def fmt_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def norm_title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[ً-ْـ]", "", text)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        text = text.replace(a, b)
    return re.sub(r"[\W_]+", "", text)


def read_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def index_programmes(root: ET.Element) -> dict:
    """channel_id(lower) -> [(start_epoch, {normalised titles})]."""
    out = collections.defaultdict(list)
    for prog in root.iter("programme"):
        try:
            start = parse_time(prog.get("start")).timestamp()
        except Exception:
            continue
        titles = {norm_title(t.text) for t in prog.findall("title") if t.text}
        titles = {t for t in titles if len(t) >= 3}
        if titles:
            out[str(prog.get("channel") or "").lower()].append((start, titles))
    return out


def measure(elc_rows, ref_rows):
    """Count, per 15-minute shift, the ElCinema programmes that have a reference
    programme with the same title at that shift. Every matching shift counts
    (not only the nearest), so a channel that re-airs episodes every few hours
    shows the ambiguity instead of silently picking the nearest repeat.
    Returns (Counter shift->programmes, programmes with any match)."""
    diffs = collections.Counter()
    total = 0
    for start, titles in elc_rows:
        seen = set()
        for ref_start, ref_titles in ref_rows:
            if titles & ref_titles:
                delta = ref_start - start
                if abs(delta) <= MAX_WINDOW:
                    seen.add(int(round(delta / STEP)) * STEP)
        if seen:
            total += 1
            for delta in seen:
                diffs[delta] += 1
    return diffs, total


def alignment(elc_rows, ref_rows, shift):
    """Share of ElCinema starts that land on a reference start (+-60 s) after shifting."""
    starts = sorted({int(r[0]) for r in ref_rows})
    if not elc_rows or not starts:
        return 0.0
    lo, hi = starts[0], starts[-1]
    inside = [int(r[0]) + shift for r in elc_rows if lo <= int(r[0]) + shift <= hi]
    if not inside:
        return 0.0
    ref = set(starts)
    hits = sum(1 for s in inside if s in ref or any((s + d) in ref for d in (-60, 60)))
    return hits / float(len(inside))


def evaluate(elc_rows, ref_rows):
    """One reference's verdict: (shift, agreeing, total) or None when unproven."""
    diffs, total = measure(elc_rows, ref_rows)
    if total < MIN_MATCHES or not diffs:
        return None
    top = max(diffs.values())
    candidates = sorted(sh for sh, count in diffs.items() if count >= 0.75 * top)

    def discriminative(shift):
        # On an hourly grid every whole-hour shift lines up: confirms nothing.
        here = alignment(elc_rows, ref_rows, shift)
        near = max(alignment(elc_rows, ref_rows, shift - 3600), alignment(elc_rows, ref_rows, shift + 3600))
        return here if here - near >= ALIGN_MARGIN else 0.0

    if len(candidates) == 1:
        shift = candidates[0]
        share = diffs[shift] / float(total)
        if share >= MIN_SHARE or (share >= WEAK_SHARE and discriminative(shift) >= ALIGN_SHARE):
            return shift, diffs[shift], total
        return None
    # Several shifts fit the titles (repeats): start-time alignment must pick
    # one clearly, otherwise the channel stays as it is.
    scored = sorted(((alignment(elc_rows, ref_rows, sh), sh) for sh in candidates), reverse=True)
    (best_align, best), (second_align, _) = scored[0], scored[1]
    if (best_align >= ALIGN_SHARE and best_align - second_align >= ALIGN_MARGIN
            and diffs[best] / float(total) >= WEAK_SHARE):
        return best, diffs[best], total
    return None


def decide(measurements):
    """measurements: [(ref, shift, agreeing, total)] of proven per-reference verdicts.
    Returns (shift, ref, agreeing, total), ('conflict', rows) or None."""
    if not measurements:
        return None
    if len({m[1] for m in measurements}) > 1:
        return ("conflict", measurements)
    best = max(measurements, key=lambda m: (m[2], m[3]))
    return (best[1], best[0], best[2], best[3])


def calibrate(root: ET.Element, references: dict, learned: dict, now: datetime) -> dict:
    elc = index_programmes(root)
    report = {"generated_utc": now.isoformat(), "calibrated": {}, "learned_applied": {},
              "conflicts": {}, "unchanged_with_reference": [], "no_reference": 0}
    shifts = {}
    for cid, rows in sorted(elc.items()):
        measurements = []
        for ref_name, ref_index in references.items():
            ref_rows = ref_index.get(cid)
            if ref_rows:
                verdict = evaluate(rows, ref_rows)
                if verdict:
                    measurements.append((ref_name,) + verdict)
        verdict = decide(measurements) if measurements else None
        if verdict and verdict[0] == "conflict":
            report["conflicts"][cid] = [list(m) for m in verdict[1]]
            continue
        if verdict:
            shift, ref_name, agreeing, total = verdict
            learned[cid] = {"shift_minutes": shift // 60, "reference": ref_name,
                            "matches": "%d/%d" % (agreeing, total), "verified_utc": now.isoformat()}
            if shift:
                shifts[cid] = shift
                report["calibrated"][cid] = learned[cid]
            else:
                report["unchanged_with_reference"].append(cid)
            continue
        # No proof in this run: leave the rows exactly as ElCinema sent them.
        # A remembered shift is not reused (offsets change between runs).
        report["no_reference"] += 1

    moved = 0
    for prog in root.iter("programme"):
        shift = shifts.get(str(prog.get("channel") or "").lower())
        if not shift:
            continue
        for attr in ("start", "stop"):
            value = prog.get(attr)
            if value:
                prog.set(attr, fmt_time(parse_time(value) + timedelta(seconds=shift)))
        moved += 1
    report["programmes_moved"] = moved
    report["channels_shifted"] = len(shifts)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--reference", action="append", default=None)
    ap.add_argument("--learned", default="config/elcinema-time-offsets.json")
    ap.add_argument("--report", default="reports/elcinema-time-calibration.json")
    args = ap.parse_args()

    root = read_root(Path(args.input))
    references = {}
    for ref in args.reference or DEFAULT_REFERENCES:
        path = Path(ref)
        if path.is_file():
            try:
                references[path.name.split(".")[0]] = index_programmes(read_root(path))
            except Exception as exc:
                print("ELCINEMA_TIME_CALIBRATION reference unreadable:", ref, exc)
    learned_path = Path(args.learned)
    try:
        learned = json.loads(learned_path.read_text(encoding="utf-8")) if learned_path.is_file() else {}
    except Exception:
        learned = {}
    report = calibrate(root, references, learned, datetime.now(timezone.utc))
    report["references"] = sorted(references)

    ET.ElementTree(root).write(args.output, encoding="utf-8", xml_declaration=True)
    learned_path.parent.mkdir(parents=True, exist_ok=True)
    learned_path.write_text(json.dumps(dict(sorted(learned.items())), ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("ELCINEMA_TIME_CALIBRATION channels_shifted=%d programmes_moved=%d conflicts=%d references=%s" % (
        report["channels_shifted"], report["programmes_moved"], len(report["conflicts"]),
        ",".join(report["references"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
