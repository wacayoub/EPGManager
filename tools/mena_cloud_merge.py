#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge MENA XMLTV sources into one conservative 48-hour candidate feed.

Policy:
- primary iptv-org/official data remains the canonical identity when available;
- OpenEPG and selected fresh EPGShare MENA feeds enrich coverage;
- TV only, no radio/data-like services;
- conservative logical-channel de-duplication;
- Arabic-first metadata for ordinary Arabic channels;
- beIN/OSN premium channels prefer English titles + Arabic descriptions;
- programmes are clipped to a configurable rolling window (48h by default).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
RADIO_RE = re.compile(r"(?:^|[\W_])(?:radio|fm)(?:[\W_]|$)", re.I)
RESOLUTION_RE = re.compile(r"(?:^|\s)(?:uhd|4k|fhd|full\s*hd|hd|sd)(?:\s|$)", re.I)
LANG_TAG_RE = re.compile(r"^(?:ar|ara|arabic|en|eng|english)\s*[:|_-]\s*", re.I)
COUNTRY_SUFFIX_RE = re.compile(r"\.(?:ae|sa|qa|eg|bh|kw|om|jo|lb|iq|ps|ye|mena)(?:@.*)?$", re.I)
SPACE_RE = re.compile(r"\s+")
PREMIUM_RE = re.compile(r"\b(?:bein|osn)\b", re.I)

OFFICIAL_SITES = {
    "shahid.mbc.net", "rotana.net", "roya-tv.com", "aljazeera.com",
    "artonline.tv", "ayn.om", "bein.com", "beinsports.com",
    "saudiatv.sa", "sba.net.ae", "dmi.gov.ae", "osn.com",
}

# Morocco has its own dedicated cloud feed and is intentionally not duplicated here.
REMOTE_SOURCES = [
    ("openepg-egypt1", "openepg", "https://www.open-epg.com/files/egypt1.xml.gz"),
    ("openepg-egypt2", "openepg", "https://www.open-epg.com/files/egypt2.xml.gz"),
    ("openepg-palestine1", "openepg", "https://www.open-epg.com/files/palestine1.xml.gz"),
    ("openepg-qatar1", "openepg", "https://www.open-epg.com/files/qatar1.xml.gz"),
    ("openepg-qatar2", "openepg", "https://www.open-epg.com/files/qatar2.xml.gz"),
    ("openepg-qatar3", "openepg", "https://www.open-epg.com/files/qatar3.xml.gz"),
    ("openepg-qatar4", "openepg", "https://www.open-epg.com/files/qatar4.xml.gz"),
    ("openepg-qatar5", "openepg", "https://www.open-epg.com/files/qatar5.xml.gz"),
    ("openepg-qatar6", "openepg", "https://www.open-epg.com/files/qatar6.xml.gz"),
    ("openepg-saudi1", "openepg", "https://www.open-epg.com/files/saudiarabia1.xml.gz"),
    ("openepg-saudi2", "openepg", "https://www.open-epg.com/files/saudiarabia2.xml.gz"),
    ("openepg-saudi3", "openepg", "https://www.open-epg.com/files/saudiarabia3.xml.gz"),
    ("openepg-saudi4", "openepg", "https://www.open-epg.com/files/saudiarabia4.xml.gz"),
    ("openepg-saudi5", "openepg", "https://www.open-epg.com/files/saudiarabia5.xml.gz"),
    ("openepg-saudi6", "openepg", "https://www.open-epg.com/files/saudiarabia6.xml.gz"),
    ("openepg-uae6", "openepg", "https://www.open-epg.com/files/uae6.xml.gz"),
    ("epgshare-ae1", "epgshare", "https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz"),
    ("epgshare-bein1", "epgshare", "https://epgshare01.online/epgshare01/epg_ripper_BEIN1.xml.gz"),
    ("epgshare-sa2", "epgshare", "https://epgshare01.online/epgshare01/epg_ripper_SA2.xml.gz"),
    ("epgshare-aljazeera1", "epgshare", "https://epgshare01.online/epgshare01/epg_ripper_ALJAZEERA1.xml.gz"),
]

# Conservative spelling aliases only. Broad semantic aliases belong in receiver mapping logic.
ALIASES = {
    "abu dhabi sport": "abu dhabi sports",
    "ad sport": "abu dhabi sports",
    "bbc arabic news": "bbc news arabic",
    "bbc arabic": "bbc news arabic",
    "bein sports english 1": "bein sports 1 english",
    "bein sports en 1": "bein sports 1 english",
    "bein sports en1": "bein sports 1 english",
    "bein movies 1 premier": "bein movies 1",
    "bein movies1 premier": "bein movies 1",
    "bein movies 2 action": "bein movies 2",
    "bein movies2 action": "bein movies 2",
    "cartoon network arabic1": "cartoon network arabic",
}

def copy_element(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))

def read_xml_bytes(data: bytes) -> ET.Element:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)

def read_xml_file(path: Path) -> ET.Element:
    return read_xml_bytes(path.read_bytes())

def download(url: str, attempts: int = 3) -> bytes:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "EPGManager-MENA-Cloud/3.0 (+https://github.com/wacayoub/EPGManager)",
                    "Accept": "application/xml,application/gzip,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("%s: %s" % (url, last))

def parse_xmltv_dt(value: str):
    value = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value)
    if not m:
        return None
    digits, offset = m.group(1), m.group(2)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if offset == "Z":
        tz = timezone.utc
    elif offset:
        sign = 1 if offset[0] == "+" else -1
        hh, mm = int(offset[1:3]), int(offset[3:5])
        tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    else:
        tz = timezone.utc
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)

def display_name(channel: ET.Element) -> str:
    names = channel.findall("display-name")
    for node in names:
        text = (node.text or "").strip()
        if text and (AR_RE.search(text) or LATIN_RE.search(text)):
            return text
    return (names[0].text or "").strip() if names else (channel.get("id") or "")

def is_radio(cid: str, name: str) -> bool:
    probe = "%s %s" % (cid or "", name or "")
    if RADIO_RE.search(probe):
        return True
    return any(x in (name or "") for x in ("إذاعة", "راديو"))

def language_of(text: str) -> str:
    text = text or ""
    ar = len(AR_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    if ar >= 2 and ar >= latin * 0.5:
        return "ar"
    if latin >= 2:
        return "en"
    return "other"

def clean_name(value: str) -> str:
    value = (value or "").strip()
    value = LANG_TAG_RE.sub("", value)
    value = COUNTRY_SUFFIX_RE.sub("", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[_./|:+\-]+", " ", value)
    value = RESOLUTION_RE.sub(" ", value)
    value = SPACE_RE.sub(" ", value).strip().casefold()
    return ALIASES.get(value, value)

def logical_key(cid: str, name: str) -> str:
    base = clean_name(name)
    if len(base) < 3:
        base = clean_name(cid)
    if PREMIUM_RE.search(base):
        base = re.sub(r"(?:^|\s)(?:arabic|arab|english|eng)(?:\s|$)", " ", base, flags=re.I)
        base = SPACE_RE.sub(" ", base).strip()
    if base in {"sports", "sport", "movie", "movies", "news", "tv", "channel"}:
        return "id:" + (cid or base).casefold()
    return base

def is_premium(cid: str, name: str) -> bool:
    return bool(PREMIUM_RE.search("%s %s" % (cid or "", name or "")))

def node_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""

def programme_quality(p: ET.Element, want: str, role: str) -> int:
    text = node_text(p, role)
    lang = language_of(text)
    score = 0
    if want == "ar":
        score += 120 if lang == "ar" else 0
    elif want == "en":
        score += 120 if lang == "en" else 0
    if text:
        score += min(len(text), 200) // 20
    if role == "desc" and len(text) >= 20:
        score += 10
    return score

def source_base(origin: str, site: str) -> int:
    if origin == "primary":
        return 100 if site in OFFICIAL_SITES else 78
    if origin == "openepg":
        return 82
    if origin == "epgshare":
        return 74
    return 60

class Candidate:
    __slots__ = ("cid", "name", "key", "origin", "source_name", "site", "channel", "programmes")
    def __init__(self, cid, name, origin, source_name, site, channel, programmes):
        self.cid = cid
        self.name = name
        self.key = logical_key(cid, name)
        self.origin = origin
        self.source_name = source_name
        self.site = site or ""
        self.channel = channel
        self.programmes = programmes

def slot_epoch(p: ET.Element):
    dt = parse_xmltv_dt(p.get("start") or "")
    return int(dt.timestamp()) if dt is not None else None

def in_window(p: ET.Element, now: datetime, end: datetime) -> bool:
    start = parse_xmltv_dt(p.get("start") or "")
    stop = parse_xmltv_dt(p.get("stop") or "")
    if start is None:
        return False
    if stop is not None and stop <= now - timedelta(minutes=5):
        return False
    if start >= end:
        return False
    if stop is None and start < now - timedelta(hours=6):
        return False
    return True

def load_candidates(root: ET.Element, origin: str, source_name: str, site_by_id: dict, now, end):
    channels = {}
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            channels[cid] = c
    programmes = defaultdict(list)
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid and in_window(p, now, end):
            programmes[cid].append(p)
    out = []
    for cid, rows in programmes.items():
        c = channels.get(cid)
        if c is None:
            c = ET.Element("channel", {"id": cid})
            ET.SubElement(c, "display-name").text = cid
        name = display_name(c) or cid
        if is_radio(cid, name):
            continue
        out.append(Candidate(cid, name, origin, source_name, site_by_id.get(cid, ""), c, rows))
    return out

def cluster_programmes(candidates):
    rows = []
    for cand in candidates:
        for p in cand.programmes:
            ts = slot_epoch(p)
            if ts is not None:
                rows.append((ts, cand, p))
    rows.sort(key=lambda x: x[0])
    clusters = []
    for ts, cand, p in rows:
        if clusters and abs(ts - clusters[-1][0]) <= 120:
            clusters[-1][1].append((cand, p))
        else:
            clusters.append([ts, [(cand, p)]])
    return clusters

def choose_canonical(candidates):
    def score(c):
        base = source_base(c.origin, c.site)
        if c.origin == "primary":
            base += 60
        base += min(len(c.programmes), 20)
        return (base, -len(c.cid), c.cid.casefold())
    return max(candidates, key=score)

def choose_event(entries, premium: bool):
    template_c, template_p = max(
        entries,
        key=lambda x: (
            source_base(x[0].origin, x[0].site) + (50 if x[0].origin == "primary" else 0),
            programme_quality(x[1], "en" if premium else "ar", "title"),
        ),
    )
    out = copy_element(template_p)
    if premium:
        title_c, title_p = max(entries, key=lambda x: (
            programme_quality(x[1], "en", "title"), source_base(x[0].origin, x[0].site)))
        desc_c, desc_p = max(entries, key=lambda x: (
            programme_quality(x[1], "ar", "desc"), source_base(x[0].origin, x[0].site)))
        title = node_text(title_p, "title")
        desc = node_text(desc_p, "desc")
        if title and language_of(title) == "en":
            t = out.find("title")
            if t is None:
                t = ET.SubElement(out, "title")
            t.text = title
            t.set("lang", "en")
        if desc and language_of(desc) == "ar":
            d = out.find("desc")
            if d is None:
                d = ET.SubElement(out, "desc")
            d.text = desc
            d.set("lang", "ar")
    else:
        title_c, title_p = max(entries, key=lambda x: (
            programme_quality(x[1], "ar", "title"), source_base(x[0].origin, x[0].site)))
        desc_c, desc_p = max(entries, key=lambda x: (
            programme_quality(x[1], "ar", "desc"), source_base(x[0].origin, x[0].site)))
        title = node_text(title_p, "title")
        desc = node_text(desc_p, "desc")
        if title and language_of(title) == "ar":
            t = out.find("title")
            if t is None:
                t = ET.SubElement(out, "title")
            t.text = title
            t.set("lang", "ar")
        if desc and language_of(desc) == "ar":
            d = out.find("desc")
            if d is None:
                d = ET.SubElement(out, "desc")
            d.text = desc
            d.set("lang", "ar")
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--window-hours", type=int, default=48)
    ap.add_argument("--no-network", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, args.window_hours))
    catalog = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    meta = {x.get("xmltv_id", ""): x for x in catalog.get("channels", [])}
    site_by_id = {cid: row.get("site", "") for cid, row in meta.items() if cid}

    primary_root = read_xml_file(Path(args.primary))
    candidates = load_candidates(primary_root, "primary", "primary", site_by_id, now, end)

    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    source_stats = []
    failed = []
    for source_name, origin, url in REMOTE_SOURCES:
        target = cache / (source_name + ".xml.gz")
        try:
            if not args.no_network:
                target.write_bytes(download(url))
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError("cache file missing")
            root = read_xml_bytes(target.read_bytes())
            rows = load_candidates(root, origin, source_name, {}, now, end)
            candidates.extend(rows)
            source_stats.append({
                "name": source_name,
                "origin": origin,
                "channels_with_current_48h_epg": len(rows),
                "bytes": target.stat().st_size,
            })
        except Exception as exc:
            failed.append({"name": source_name, "url": url, "error": str(exc)[:220]})

    groups = defaultdict(list)
    for cand in candidates:
        if cand.key:
            groups[cand.key].append(cand)

    out = ET.Element("tv", {
        "generator-info-name": "EPGManager MENA Cloud Merge",
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    channel_rows = []
    programme_rows = []
    alias_rows = []
    stats = Counter()
    premium_hybrid = 0

    for key in sorted(groups, key=str.casefold):
        group = groups[key]
        canonical = choose_canonical(group)
        premium = is_premium(canonical.cid, canonical.name) or any(is_premium(c.cid, c.name) for c in group)
        cnode = copy_element(canonical.channel)
        cnode.set("id", canonical.cid)
        out.append(cnode)
        channel_rows.append(cnode)

        if len(group) > 1:
            alias_rows.append({
                "canonical_id": canonical.cid,
                "canonical_name": canonical.name,
                "logical_key": key,
                "aliases": sorted(
                    [{"id": c.cid, "name": c.name, "source": c.source_name} for c in group if c.cid != canonical.cid],
                    key=lambda x: (x["id"].casefold(), x["source"]),
                ),
            })
            stats["logical_duplicate_groups"] += 1

        for _ts, entries in cluster_programmes(group):
            p = choose_event(entries, premium)
            p.set("channel", canonical.cid)
            programme_rows.append(p)
            if premium and language_of(node_text(p, "title")) == "en" and language_of(node_text(p, "desc")) == "ar":
                premium_hybrid += 1

        stats["premium_channels" if premium else "regular_channels"] += 1
        stats["source_" + canonical.origin] += 1

    seen = set()
    unique_programmes = []
    for p in sorted(programme_rows, key=lambda x: ((x.get("channel") or "").casefold(), x.get("start") or "", x.get("stop") or "")):
        key = ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        unique_programmes.append(p)
        out.append(p)

    if len(channel_rows) < 25 or len(unique_programmes) < 100:
        raise SystemExit("Merged MENA output too small: %d channels / %d programmes" % (len(channel_rows), len(unique_programmes)))

    ET.indent(out, space="  ")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(ET.tostring(out, encoding="utf-8", xml_declaration=True))

    report = {
        "schema": 1,
        "generated": now.isoformat(),
        "window_hours": args.window_hours,
        "sources_attempted": 1 + len(REMOTE_SOURCES),
        "remote_sources_ok": len(source_stats),
        "remote_sources_failed": failed,
        "source_stats": source_stats,
        "candidate_channel_rows": len(candidates),
        "unique_logical_channels": len(channel_rows),
        "programmes": len(unique_programmes),
        "premium_hybrid_events": premium_hybrid,
        "stats": dict(stats),
        "aliases": alias_rows,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("MENA merge: %d logical channels / %d programmes / %d premium EN-title+AR-desc events / %d failed remotes" %
          (len(channel_rows), len(unique_programmes), premium_hybrid, len(failed)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
