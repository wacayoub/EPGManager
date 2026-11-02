#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill selected ElCinema zero-EPG IDs from verified fallbacks.

Rules:
- never replace a target that already has future ElCinema programmes;
- prefer existing production feeds before external fallbacks;
- EPGShare is last-resort and only used for exact verified channel matches;
- production-excluded IDs are omitted from fallback targets;
- ATV Kuwait and Salam TV Libya are intentionally left untouched until a
  reliable schedule source exists.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

EPGSHARE_URL = "https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz"
ALMANAR_URL = "https://www.manartv.com.lb/programs-schedule/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

LOCAL_CANDIDATES = {
    "AbuDhabiTV.ae@SD": [
        ("feeds/osn.xml.gz", "AbuDhabiTV.ae@SD", "osn:Abu Dhabi TV"),
    ],
    "DiscoveryChannelMiddleEastAfrica.us@SD": [
        ("feeds/osn.xml.gz", "DiscoveryChannelMiddleEastAfrica.us@SD", "osn:Discovery Channel"),
        ("feeds/stctv.xml.gz", "stctv.0e734380e4d7", "stctv:Discovery"),
    ],
    "FoxActionMoviesMENA.hk@SD": [
        ("feeds/bein.xml.gz", "FoxActionMoviesMENA.hk@SD", "bein:Fox Action Movies"),
    ],
    "NationalGeographicMiddleEast.uk@SD": [
        ("feeds/stctv.xml.gz", "stctv.ddec643117e3", "stctv:Nat Geo"),
    ],
    "StarMoviesMiddleEast.ae@SD": [
        ("feeds/stctv.xml.gz", "stctv.5c3e1fd60f8e", "stctv:Star Movies"),
        ("feeds/bein.xml.gz", "StarMoviesMiddleEast.ae@SD", "bein:Star Movies"),
    ],
    "2MMonde.ma@SD": [
        ("feeds/starzplay.xml.gz", "starzplay.460869672233", "starzplay:2M Monde"),
    ],
    "BahrainTV.bh@SD": [
        ("feeds/stctv.xml.gz", "BahrainTV.bh@SD", "stctv:Bahrain TV"),
        ("feeds/osn.xml.gz", "BahrainTV.bh@SD", "osn:Bahrain TV"),
    ],
    "CBCSofra.eg@SD": [
        ("feeds/stctv.xml.gz", "stctv.e74c791a4abb", "stctv:CBC Sofra"),
    ],
    "NationalGeographicAbuDhabi.ae@SD": [
        ("feeds/starzplay.xml.gz", "NationalGeographicAbuDhabi.ae@SD", "starzplay:NatGeo Abu Dhabi"),
        ("feeds/stctv.xml.gz", "stctv.28fae963e5e8", "stctv:NatGeo Abu Dhabi"),
    ],
}

EPGSHARE_CANDIDATES = {
    "2MMonde.ma@SD": "2M.Monde.ae",
    "BahrainTV.bh@SD": "Bahrain.TV.HD.ae",
    "CBCSofra.eg@SD": "CBC.Sofra.ae",
    "KTV1.kw@SD": "Kuwait.TV.1.HD.ae",
    "NationalGeographicAbuDhabi.ae@SD": "Nat.Geo.Abu.Dhabi.HD.ae",
    "QatarTelevision.qa@SD": "Qatar.TV.HD.ae",
}

EXPECTED_ZERO_IDS = {
    "AbuDhabiTV.ae@SD",
    "DiscoveryChannelMiddleEastAfrica.us@SD",
    "FoxActionMoviesMENA.hk@SD",
    "NationalGeographicMiddleEast.uk@SD",
    "StarMoviesMiddleEast.ae@SD",
    "2MMonde.ma@SD",
    "ATV.kw@SD",
    "BahrainTV.bh@SD",
    "CBCSofra.eg@SD",
    "KTV1.kw@SD",
    "NationalGeographicAbuDhabi.ae@SD",
    "QatarTelevision.qa@SD",
    "SalamTV.ly@SD",
}

DT_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")
TIME_RE = re.compile(
    r"(?:(?P<title>.*?)\s*)?"
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*(?P<sa>[صم])\s*[-–]\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2})\s*(?P<ea>[صم])"
)


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


def fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def read_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def root_from_bytes(raw: bytes) -> ET.Element:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def future_count(root: ET.Element, cid: str) -> int:
    now = datetime.now(timezone.utc)
    total = 0
    for p in root.findall("programme"):
        if (p.get("channel") or "").strip() != cid:
            continue
        stop = parse_dt(p.get("stop") or p.get("start"))
        if stop and stop > now:
            total += 1
    return total


def source_rows(root: ET.Element, cid: str) -> list[ET.Element]:
    return [
        p for p in root.findall("programme")
        if (p.get("channel") or "").strip() == cid
    ]


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


def try_local(dst: ET.Element, target: str, report: dict) -> bool:
    for path_s, source_id, label in LOCAL_CANDIDATES.get(target, []):
        path = Path(path_s)
        if not path.exists():
            continue
        try:
            src = read_root(path)
        except Exception as exc:
            report["errors"].append(f"{target}:{label}:read:{exc}")
            continue
        rows = source_rows(src, source_id)
        if not rows:
            continue
        added = append_rows(dst, rows, target)
        if added and future_count(dst, target):
            report["applied"][target] = {
                "source": label,
                "programmes_added": added,
                "future_programmes": future_count(dst, target),
            }
            return True
    return False


def fetch_epgshare(report: dict) -> ET.Element | None:
    try:
        r = requests.get(
            EPGSHARE_URL,
            headers={"User-Agent": UA, "Accept": "application/xml,application/gzip,*/*"},
            timeout=40,
        )
        r.raise_for_status()
        report["external"]["epgshare"] = {
            "status": r.status_code,
            "bytes": len(r.content),
            "url": EPGSHARE_URL,
        }
        return root_from_bytes(r.content)
    except Exception as exc:
        report["external"]["epgshare"] = {"error": str(exc), "url": EPGSHARE_URL}
        return None


def try_epgshare(dst: ET.Element, target: str, epgshare: ET.Element | None, report: dict) -> bool:
    source_id = EPGSHARE_CANDIDATES.get(target)
    if not source_id or epgshare is None:
        return False
    rows = source_rows(epgshare, source_id)
    if not rows:
        return False
    added = append_rows(dst, rows, target)
    if added and future_count(dst, target):
        report["applied"][target] = {
            "source": f"epgshare:AE1:{source_id}",
            "programmes_added": added,
            "future_programmes": future_count(dst, target),
        }
        return True
    return False


def to_24h(hour: int, ampm: str) -> int:
    hour %= 12
    if ampm == "م":
        hour += 12
    return hour


def almanar_rows(report: dict) -> list[ET.Element]:
    try:
        r = requests.get(
            ALMANAR_URL,
            headers={"User-Agent": UA, "Accept-Language": "ar,en;q=0.6"},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as exc:
        report["external"]["almanar"] = {"error": str(exc), "url": ALMANAR_URL}
        return []

    soup = BeautifulSoup(r.text, "lxml")
    parts = [" ".join(x.split()) for x in soup.stripped_strings]
    tz = ZoneInfo("Asia/Beirut")
    day = datetime.now(tz).date()
    out: list[ET.Element] = []
    previous = ""

    for part in parts:
        m = TIME_RE.search(part)
        if not m:
            if part not in {"إعادة", "يعرض الآن"} and len(part) <= 220:
                previous = part
            continue

        title = (m.group("title") or "").strip(" -–:")
        if not title:
            title = previous.strip(" -–:")
        if not title or title in {"إعادة", "يعرض الآن", "جدول البرامج"}:
            continue

        sh = to_24h(int(m.group("sh")), m.group("sa"))
        eh = to_24h(int(m.group("eh")), m.group("ea"))
        start = datetime(day.year, day.month, day.day, sh, int(m.group("sm")), tzinfo=tz)
        stop = datetime(day.year, day.month, day.day, eh, int(m.group("em")), tzinfo=tz)
        if stop <= start:
            stop += timedelta(days=1)

        p = ET.Element("programme", {
            "channel": "AlManar.lb@SD",
            "start": fmt_utc(start),
            "stop": fmt_utc(stop),
        })
        ET.SubElement(p, "title", {"lang": "ar"}).text = title
        out.append(p)

    report["external"]["almanar"] = {
        "status": r.status_code,
        "bytes": len(r.content),
        "programmes_parsed": len(out),
        "schedule_date": str(day),
        "timezone": "Asia/Beirut",
        "url": ALMANAR_URL,
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    root = read_root(Path(args.input))
    before = {cid: future_count(root, cid) for cid in sorted(EXPECTED_ZERO_IDS)}
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "ElCinema first; local production fallback; EPGShare last resort; official Al-Manar; never replace healthy ElCinema EPG",
        "before_future": before,
        "applied": {},
        "external": {},
        "errors": [],
    }

    epgshare = None
    need_epgshare = False

    for target in sorted(EXPECTED_ZERO_IDS):
        if future_count(root, target):
            continue
        if target == "AlManar.lb@SD":
            rows = almanar_rows(report)
            added = append_rows(root, rows, target)
            if added and future_count(root, target):
                report["applied"][target] = {
                    "source": "official:manartv.com.lb",
                    "programmes_added": added,
                    "future_programmes": future_count(root, target),
                }
            continue
        if try_local(root, target, report):
            continue
        if target in EPGSHARE_CANDIDATES:
            need_epgshare = True

    if need_epgshare:
        epgshare = fetch_epgshare(report)
        for target in sorted(EPGSHARE_CANDIDATES):
            if future_count(root, target):
                continue
            try_epgshare(root, target, epgshare, report)

    report["after_future"] = {cid: future_count(root, cid) for cid in sorted(EXPECTED_ZERO_IDS)}
    report["resolved"] = [cid for cid, n in report["after_future"].items() if n > 0]
    report["unresolved"] = [cid for cid, n in report["after_future"].items() if n == 0]

    ET.indent(root, space="  ")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
