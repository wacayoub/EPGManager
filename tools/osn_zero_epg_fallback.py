#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill selected OSN zero-EPG IDs from verified fallbacks.

OSN remains authoritative. A fallback is used only when the target has no
future OSN programmes after Android + Legacy/Other scraping.

Priority:
1. local official production feeds (currently Al Kass)
2. OpenEPG exact channel matches
3. EPGShare exact channel matches

No synthetic programme rows are created.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
OPENEPG_URL = "https://www.open-epg.com/files/saudiarabia5.xml"
EPGSHARE_URL = "https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz"

LOCAL_CANDIDATES = {
    "osn.2501": [
        ("feeds/alkass.xml.gz", "AlkassOne.qa@SD", "official:alkass:Al Kass 1"),
    ],
}

OPENEPG_CANDIDATES = {
    "Cinema1.ae@SD": "Alfa Cinema 1.sa",
    "Cinema2.ae@SD": "Alfa Cinema 2.sa",
    "MusicNow.ae@SD": "Alfa Music HD.sa",
}

EPGSHARE_CANDIDATES = {
    "osn.9993": "Oman.TV.Sport.HD.ae",
}

TRACKED_ZERO_IDS = {
    "Cinema1.ae@SD",
    "Cinema2.ae@SD",
    "CineMo.ph",
    "GMALifeTV.ph",
    "GMANewsTV.ph",
    "GMAPinoyTVMiddleEast.ph",
    "MusicNow.ae@SD",
    "osn.216",
    "osn.217",
    "osn.2501",
    "osn.9993",
    "osn.9994",
    "OSNNow.ae@SD",
    "OSNPopUp2.ae@SD",
    "SaudiThaqafiyaTV.sa@SD",
}

DT_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")


def parse_dt(value: str | None):
    m = DT_RE.match((value or "").strip())
    if not m:
        return None
    digits, off = m.group(1), m.group(2)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if not off or off == "Z":
        return dt.replace(tzinfo=timezone.utc)
    sign = 1 if off[0] == "+" else -1
    mins = sign * (int(off[1:3]) * 60 + int(off[3:5]))
    return dt.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)


def read_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def root_from_bytes(raw: bytes) -> ET.Element:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def catalogue_future_health(root: ET.Element) -> dict[str, int]:
    """Every OSN advertised ID is audited, not only a historical fallback list.

    Preserve unavailable entries as diagnostics; do not fabricate schedules
    or remove receiver mappings when their official provider has no guide.
    """
    cids = sorted({
        (c.get("id") or "").strip()
        for c in root.findall("channel")
        if (c.get("id") or "").strip()
    })
    return {cid: future_count(root, cid) for cid in cids}


def future_count(root: ET.Element, cid: str) -> int:
    now = datetime.now(timezone.utc)
    return sum(
        1
        for p in root.findall("programme")
        if (p.get("channel") or "").strip() == cid
        and (parse_dt(p.get("stop") or p.get("start")) or datetime.min.replace(tzinfo=timezone.utc)) > now
    )


def source_rows(root: ET.Element, cid: str, hours: int = 60) -> list[ET.Element]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours)
    out = []
    for p in root.findall("programme"):
        if (p.get("channel") or "").strip() != cid:
            continue
        stop = parse_dt(p.get("stop") or p.get("start"))
        start = parse_dt(p.get("start"))
        if stop and stop <= now:
            continue
        if start and start > end:
            continue
        out.append(p)
    return out


def append_rows(dst: ET.Element, rows: list[ET.Element], target: str) -> int:
    seen = {
        (
            (p.get("channel") or "").strip(),
            (p.get("start") or "").strip(),
            (p.get("stop") or "").strip(),
            "|".join((x.text or "").strip() for x in p.findall("title")),
        )
        for p in dst.findall("programme")
    }
    added = 0
    for p in rows:
        q = deepcopy(p)
        q.set("channel", target)
        key = (
            target,
            (q.get("start") or "").strip(),
            (q.get("stop") or "").strip(),
            "|".join((x.text or "").strip() for x in q.findall("title")),
        )
        if key in seen:
            continue
        dst.append(q)
        seen.add(key)
        added += 1
    return added


def fetch_xml(url: str, report: dict, key: str) -> ET.Element | None:
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Accept": "application/xml,application/gzip,*/*"},
            timeout=45,
        )
        r.raise_for_status()
        report["external"][key] = {"status": r.status_code, "bytes": len(r.content), "url": url}
        return root_from_bytes(r.content)
    except Exception as exc:
        report["external"][key] = {"error": str(exc), "url": url}
        return None


def apply_source(dst: ET.Element, target: str, src: ET.Element, source_id: str, label: str, report: dict) -> bool:
    rows = source_rows(src, source_id)
    if not rows:
        return False
    added = append_rows(dst, rows, target)
    future = future_count(dst, target)
    if added and future:
        report["applied"][target] = {
            "source": label,
            "source_id": source_id,
            "programmes_added": added,
            "future_programmes": future,
        }
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    root = read_root(Path(args.input))
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "OSN official first; local official fallback; OpenEPG; EPGShare last; never replace healthy OSN EPG",
        "before_future": {cid: future_count(root, cid) for cid in sorted(TRACKED_ZERO_IDS)},
        "official_catalogue_future_before": catalogue_future_health(root),
        "applied": {},
        "external": {},
        "errors": [],
    }

    # 1) Local official feeds.
    for target, candidates in LOCAL_CANDIDATES.items():
        if future_count(root, target):
            continue
        for path_s, source_id, label in candidates:
            path = Path(path_s)
            if not path.exists():
                continue
            try:
                src = read_root(path)
            except Exception as exc:
                report["errors"].append(f"{target}:{label}:read:{exc}")
                continue
            if apply_source(root, target, src, source_id, label, report):
                break

    # 2) OpenEPG exact matches.
    if any(not future_count(root, cid) for cid in OPENEPG_CANDIDATES):
        src = fetch_xml(OPENEPG_URL, report, "openepg")
        if src is not None:
            for target, source_id in OPENEPG_CANDIDATES.items():
                if future_count(root, target):
                    continue
                apply_source(root, target, src, source_id, f"openepg:saudiarabia5:{source_id}", report)

    # 3) EPGShare exact matches, last resort.
    if any(not future_count(root, cid) for cid in EPGSHARE_CANDIDATES):
        src = fetch_xml(EPGSHARE_URL, report, "epgshare")
        if src is not None:
            for target, source_id in EPGSHARE_CANDIDATES.items():
                if future_count(root, target):
                    continue
                apply_source(root, target, src, source_id, f"epgshare:AE1:{source_id}", report)

    report["after_future"] = {cid: future_count(root, cid) for cid in sorted(TRACKED_ZERO_IDS)}
    full_after = catalogue_future_health(root)
    full_before = report.pop("official_catalogue_future_before")
    report["catalogue_id_count"] = len(full_after)
    report["missing_ids_before"] = sorted(cid for cid, n in full_before.items() if not n)
    report["missing_ids_after"] = sorted(cid for cid, n in full_after.items() if not n)
    report["missing_ids_restored"] = sorted(
        cid for cid in full_before if not full_before[cid] and full_after.get(cid, 0) > 0
    )
    # Legacy tracked rows are retained for backwards-compatible debug output.
    report["resolved"] = [cid for cid, n in report["after_future"].items() if n > 0]
    report["unresolved"] = [cid for cid, n in report["after_future"].items() if n == 0]

    ET.indent(root, space="  ")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("OSN_ZERO_EPG_FALLBACK", json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
