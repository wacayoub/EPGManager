#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-rate STC TV public metadata XMLTV source.

Receiver policy:
- keep every TV channel ID exposed by the STC public channel catalogue;
- fetch one public batch schedule per UTC day instead of per-channel fan-out;
- channels with no programme in the current window stay in XMLTV/index and are
  reported as zero-EPG instead of being deleted;
- timestamps come from STC epoch milliseconds and are emitted as UTC +0000.

Uses only public guest metadata loaded by web.stctv.com. No playback, DRM,
account login or entitlement bypass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
APP_KEY = "webB2CGDMPrdExy0sVDlZMzNDdUyZ"
SCHEDULE_KEY = "GDMPrdExy0sVDlZMzNDdUyZ"
CHANNELS_API = f"https://jawwy2-prod-cdn.intigral-ott.net/bolt/v2/{APP_KEY}/channels"
SCHEDULE_BASE = "https://prod-cdn-content-api.intigral-ott.net/content-api-3.0.1/channels/schedules"
AR = re.compile(r"[\u0600-\u06ff]")
NONWORD = re.compile(r"[^a-z0-9]+")

ALIASES = {
    "starzplay sports 1": "StarzplaySports1.sa@HD",
    "starzplay sports 2": "StarzplaySports2.sa@HD",
    "starzplay sports 3": "StarzplaySports3.sa@HD",
    "mbc": "MBC1.sa@HD",
    "mbc 2": "MBC2.sa@HD",
    "mbc 3": "MBC3.sa@HD",
    "mbc 4": "MBC4.sa@HD",
    "mbc action": "MBCAction.sa@HD",
    "mbc max": "MBCMax.sa@HD",
    "mbc bollywood": "MBCBollywood.sa@HD",
    "mbc drama": "MBCDrama.sa@HD",
    "mbc masr": "MBCMasr.eg@HD",
    "mbc masr 2": "MBCMasr2.eg@HD",
    "wanasah": "Wanasah.ae@SD",
    "al arabiya": "AlArabiya.sa@HD",
}
HYBRID_TOKENS = (
    "starzplay sports",
    "mbc 2",
    "mbc max",
    "mbc 4",
    "mbc action",
    "mbc bollywood",
    "mbc variety",
    "discovery",
    "cnn",
    "star action",
    "star movies",
    "star world",
    "star plus",
    "bbc",
    "bloomberg",
    "national geographic",
    "nat geo",
    "animal planet",
    "history",
    "tlc",
    "nickelodeon",
    "nick jr",
    "nicktoons",
)
ARABIC_NATIVE_TOKENS = (
    "asharq discovery",
    "asharq bloomberg",
    "national geographic abu dhabi",
    "nat geo abu dhabi",
)
CANONICAL_OVERRIDE = {
    "starzplay sports 1": "StarzplaySports1.sa@HD",
    "starzplay sports 2": "StarzplaySports2.sa@HD",
    "starzplay sports 3": "StarzplaySports3.sa@HD",
    "al arabiya": "AlArabiya.sa@HD",
    "al arabiya business": "AlArabiyaBusiness.ae@SD",
    "wanasah": "Wanasah.ae@SD",
}

# STARZPLAY's public site currently advertises 50+ live/digital channels.
# Keep a provider catalogue even when the direct STARZPLAY EPG endpoints return
# 403 from GitHub Actions. Aliases below are normalized STC names used only to
# borrow schedule metadata; unmatched official STARZPLAY channels remain visible
# as zero-EPG instead of disappearing from the source.
STARZPLAY_LIVE_CATALOGUE = (
    {"name": "STARZPLAY Sports 1", "aliases": ("starzplay sports 1",)},
    {"name": "STARZPLAY Sports 2", "aliases": ("starzplay sports 2",)},
    {"name": "STARZPLAY Sports 3", "aliases": ("starzplay sports 3",)},
    {"name": "StarzPlay Movie", "aliases": ("starzplay movie",)},
    {"name": "StarzPlay Rugby", "aliases": ("starzplay rugby",)},
    {"name": "STARZPLAY Cricket", "aliases": ("starzplay cricket",)},
    {"name": "Abu Dhabi Sports 1", "aliases": ("abu dhabi sports 1", "ad sports 1")},
    {"name": "Abu Dhabi Sports 2", "aliases": ("abu dhabi sports 2", "ad sports 2")},
    {"name": "AD Sports Extra", "aliases": ("ad sports extra",)},
    {"name": "DAZN Ringside", "aliases": ("dazn ringside",)},
    {"name": "FIFA+ Live", "aliases": ("fifa live", "fifa plus live")},
    {"name": "Red Bull TV Live", "aliases": ("red bull tv live", "red bull tv")},
    {"name": "AD Fight", "aliases": ("ad fight",)},
    {"name": "Yas", "aliases": ("yas", "yas tv")},
    {"name": "YAS TV Extra", "aliases": ("yas tv extra",)},
    {"name": "Abu Dhabi TV", "aliases": ("abu dhabi tv", "abu dhabi")},
    {"name": "Asianet News", "aliases": ("asianet news",)},
    {"name": "Sky News Arabia - Live", "aliases": ("sky news arabia live", "sky news arabia", "sky news arabia hd")},
    {"name": "Asianet Cinema", "aliases": ("asianet cinema",)},
    {"name": "Saudi Quran", "aliases": ("saudi quran",)},
    {"name": "National Geographic Abu Dhabi", "aliases": ("national geographic abu dhabi", "nat geo ad", "nat geo abu dhabi")},
    {"name": "Spacetoon", "aliases": ("spacetoon",)},
    {"name": "Majid TV", "aliases": ("majid tv", "majid")},
    {"name": "Al Emarat TV", "aliases": ("al emarat tv", "al emarat")},
    {"name": "Cartoon Network Arabic", "aliases": ("cartoon network arabic", "cn arabic")},
    {"name": "Sky News", "aliases": ("sky news",)},
    {"name": "Zee Aflam", "aliases": ("zee aflam",)},
    {"name": "Zee Alwan", "aliases": ("zee alwan",)},
    {"name": "Saudi 1", "aliases": ("saudi 1",)},
    {"name": "Geo TV", "aliases": ("geo tv",)},
    {"name": "Baynounah TV", "aliases": ("baynounah tv",)},
    {"name": "Looney Tunes By CN", "aliases": ("looney tunes by cn",)},
    {"name": "Sab TV", "aliases": ("sab tv",)},
    {"name": "Manorama News", "aliases": ("manorama news",)},
    {"name": "Al Jadeed", "aliases": ("al jadeed",)},
    {"name": "India Today TV", "aliases": ("india today tv",)},
    {"name": "Reporter TV", "aliases": ("reporter tv",)},
    {"name": "Rotana Comedy", "aliases": ("rotana comedy",)},
    {"name": "Rotana Drama", "aliases": ("rotana drama",)},
    {"name": "Rotana Classic", "aliases": ("rotana classic",)},
    {"name": "Star Plus HD", "aliases": ("star plus hd", "star plus")},
    {"name": "SET Max", "aliases": ("set max",)},
    {"name": "Zee Bangla", "aliases": ("zee bangla",)},
    {"name": "Emasala Simply South", "aliases": ("emasala simply south",)},
    {"name": "ARY Digital", "aliases": ("ary digital",)},
    {"name": "eJunior", "aliases": ("ejunior", "e junior")},
    {"name": "Colors HD", "aliases": ("colors hd", "colors tv")},
    {"name": "Zee Keralam", "aliases": ("zee keralam",)},
    {"name": "Sony TV", "aliases": ("sony tv",)},
    {"name": "Asianet Movies HD", "aliases": ("asianet movies hd", "asianet movies")},
    {"name": "Flowers International", "aliases": ("flowers international",)},
    {"name": "Mazhavil Manorama", "aliases": ("mazhavil manorama",)},
    {"name": "Asianet ME", "aliases": ("asianet me",)},
)


def norm(s):
    return " ".join(NONWORD.sub(" ", (s or "").casefold()).split())


def read_index(path):
    out = {}
    p = Path(path) if path else None
    if not p or not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "|" not in line:
            continue
        cid, name = line.split("|", 1)
        if cid.strip() and name.strip():
            out.setdefault(norm(name), cid.strip())
    return out


def xml_id(name, index, sid=""):
    n = norm(name)
    if n in CANONICAL_OVERRIDE:
        return CANONICAL_OVERRIDE[n]
    if n in index:
        return index[n]
    if n in ALIASES:
        return ALIASES[n]
    for k, v in ALIASES.items():
        if n == k or n.startswith(k + " "):
            return v
    # Use STC's stable source ID in the fallback so identically named regional
    # variants cannot collapse onto the same XMLTV ID.
    seed = n + "|" + str(sid or "")
    return "stctv." + hashlib.sha1(seed.encode()).hexdigest()[:12]


def starzplay_catalogue_match(name):
    n = norm(name)
    for item in STARZPLAY_LIVE_CATALOGUE:
        if n in item["aliases"]:
            return item
    return None


def starzplay_catalogue_id(item, index):
    for alias in item["aliases"]:
        if alias in index:
            return index[alias]
    stable = hashlib.sha1(norm(item["name"]).encode("utf-8")).hexdigest()[:14]
    return "starzplay." + stable


def get_json(s, url, params, timeout):
    r = s.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_channels(data):
    d = data.get("data") if isinstance(data, dict) else None
    return d.get("channels", []) if isinstance(d, dict) and isinstance(d.get("channels"), list) else []


def rows(data):
    out = []
    if isinstance(data, list):
        for b in data:
            if isinstance(b, dict) and isinstance(b.get("listings"), list):
                out.extend(b["listings"])
    return out


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--id-index", default="feeds/mena.txt")
    ap.add_argument("--window-hours", type=int, default=48)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument(
        "--max-channels",
        type=int,
        default=0,
        help="0 keeps the complete STC source catalogue; positive values are diagnostic caps only",
    )
    ap.add_argument(
        "--profile",
        choices=("all", "starzplay-sports", "starzplay-live"),
        default="all",
        help="all keeps every STC channel; starzplay-live projects the official STARZPLAY live catalogue onto available STC schedule metadata",
    )
    ap.add_argument(
        "--catalogue-only",
        action="store_true",
        help="fetch the complete STC channel catalogue but do not request per-channel schedules",
    )
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, args.window_hours))
    days = []
    d = now.date()
    while datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) < end + timedelta(days=1):
        days.append(d.isoformat())
        d += timedelta(days=1)

    index = read_index(args.id_index)
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.7",
            "Origin": "https://web.stctv.com",
            "Referer": "https://web.stctv.com/",
        }
    )

    root = ET.Element(
        "tv",
        {
            "generator-info-name": "EPGManager STC TV public low-rate",
            "generator-info-url": "https://web.stctv.com/",
        },
    )
    stats = {
        "generated_utc": now.isoformat(),
        "channels": 0,
        "channels_with_epg": 0,
        "programmes": 0,
        "requests": 0,
        "source_channels": 0,
        "source_metadata_issues": [],
        "zero_epg": [],
        "hybrid_channels": [],
        "samples": {},
        "timezone_output": "UTC +0000",
    }

    try:
        data = get_json(
            sess,
            CHANNELS_API,
            {"country": "SA", "deviceType": "web", "filter": "", "device": "PC", "appKey": APP_KEY},
            args.timeout,
        )
        stats["requests"] += 1
        targets = list(get_channels(data))
        stats["source_channels"] = len(targets)

        if args.profile == "starzplay-sports":
            targets = [
                ch
                for ch in targets
                if norm(str(ch.get("channelTitle") or ch.get("channelTitleAr") or "")).startswith(
                    "starzplay sports "
                )
            ]
        elif args.profile == "starzplay-live":
            matched = []
            for ch in targets:
                source_name = str(ch.get("channelTitle") or ch.get("channelTitleAr") or "").strip()
                item = starzplay_catalogue_match(source_name)
                if item:
                    row = dict(ch)
                    row["_starzplay_name"] = item["name"]
                    row["_starzplay_aliases"] = list(item["aliases"])
                    matched.append(row)
            targets = matched
        if args.max_channels and args.max_channels > 0:
            targets = targets[: args.max_channels]

        stats["profile"] = args.profile
        stats["selected_channels"] = len(targets)

        seen_cids = set()
        prepared = []
        for source_index, ch in enumerate(targets):
            title_en = str(ch.get("channelTitle") or "").strip()
            title_ar = str(ch.get("channelTitleAr") or "").strip()
            sid = str(ch.get("channelID") or "").strip()
            name = str(ch.get("_starzplay_name") or title_en or title_ar).strip()
            if not sid:
                stats["source_metadata_issues"].append({
                    "source_index": source_index,
                    "name": name,
                    "reason": "missing_channelID",
                    "retained": False,
                })
                continue
            if not name:
                # A valid STC channelID must never disappear just because the
                # current catalogue omitted both display-title fields.
                name = f"STC TV {sid}"
                stats["source_metadata_issues"].append({
                    "source_index": source_index,
                    "channelID": sid,
                    "reason": "missing_title_placeholder_used",
                    "retained": True,
                })
            if args.profile == "starzplay-live":
                item = starzplay_catalogue_match(name) or {
                    "name": name,
                    "aliases": tuple(ch.get("_starzplay_aliases") or (norm(name),)),
                }
                cid = starzplay_catalogue_id(item, index)
            else:
                cid = xml_id(name, index, sid)
            if cid in seen_cids:
                # Two STC source entries may legitimately share the same
                # display name/canonical alias (regional or package variants).
                # Never drop the second source ID: keep the canonical ID for
                # the first occurrence and give subsequent variants a stable
                # source-ID-based local identifier.
                seed = norm(name) + "|" + sid
                cid = "stctv." + hashlib.sha1(seed.encode()).hexdigest()[:12]
                bump = 1
                while cid in seen_cids:
                    cid = "stctv." + hashlib.sha1((seed + f"|{bump}").encode()).hexdigest()[:12]
                    bump += 1
            seen_cids.add(cid)
            normalized_name = norm(name)
            if any(tok in normalized_name for tok in ARABIC_NATIVE_TOKENS):
                prof = "arabic_native"
            elif any(tok in normalized_name for tok in HYBRID_TOKENS):
                prof = "hybrid"
            else:
                prof = "arabic_native"
            prepared.append((ch, name, sid, cid, prof))

            c = ET.SubElement(root, "channel", {"id": cid})
            ET.SubElement(c, "display-name", {"lang": "en"}).text = name
            ar_name = str(ch.get("channelTitleAr") or "").strip()
            if ar_name:
                ET.SubElement(c, "display-name", {"lang": "ar"}).text = ar_name
            ET.SubElement(c, "url", {"system": "stctv-id"}).text = sid
            stats["channels"] += 1
            if prof == "hybrid":
                stats["hybrid_channels"].append(cid)

        if args.profile == "starzplay-live":
            matched_names = {norm(name) for _, name, _, _, _ in prepared}
            added_catalogue_only = 0
            for item in STARZPLAY_LIVE_CATALOGUE:
                if norm(item["name"]) in matched_names:
                    continue
                cid = starzplay_catalogue_id(item, index)
                if cid in seen_cids:
                    continue
                seen_cids.add(cid)
                c = ET.SubElement(root, "channel", {"id": cid})
                ET.SubElement(c, "display-name", {"lang": "en"}).text = item["name"]
                ET.SubElement(c, "url", {"system": "starzplay-catalogue"}).text = item["name"]
                stats["channels"] += 1
                stats["zero_epg"].append({
                    "id": cid,
                    "name": item["name"],
                    "reason": "not_available_in_stctv_schedule_fallback",
                })
                added_catalogue_only += 1
            stats["official_catalogue_channels"] = len(STARZPLAY_LIVE_CATALOGUE)
            stats["matched_fallback_channels"] = len(prepared)
            stats["catalogue_only_channels"] = added_catalogue_only

        stats["catalogue_only"] = bool(args.catalogue_only)
        if args.catalogue_only:
            prepared = []

        # The public schedule API supports a full-day batch response containing
        # all channelIds. This reduces a 144-channel/48h refresh from hundreds
        # of HTTP calls to roughly one call per UTC day.
        schedule_by_id = {}
        batch_errors = []
        if prepared:
            for di, date in enumerate(days):
                try:
                    data2 = get_json(
                        sess,
                        f"{SCHEDULE_BASE}/{date}/3",
                        {"apikey": SCHEDULE_KEY, "productKey": "stc-tv"},
                        args.timeout,
                    )
                    stats["requests"] += 1
                    if isinstance(data2, list):
                        for block in data2:
                            if not isinstance(block, dict):
                                continue
                            bid = str(block.get("channelId") or "").strip()
                            listings = block.get("listings") or []
                            if bid and isinstance(listings, list):
                                schedule_by_id.setdefault(bid, []).extend(listings)
                except Exception as exc:
                    batch_errors.append({"date": date, "error": str(exc)[:240]})
                if di + 1 < len(days):
                    time.sleep(max(0, args.delay))
        stats["schedule_mode"] = "batch_per_day"
        stats["schedule_days"] = days
        stats["schedule_channel_blocks"] = len(schedule_by_id)
        stats["batch_errors"] = batch_errors

        for ci, (ch, name, sid, cid, prof) in enumerate(prepared):
            allrows = list(schedule_by_id.get(sid, []))

            seen = set()
            keep = []
            for li in allrows:
                st = int(li.get("startTime") or 0)
                et = int(li.get("endTime") or 0)
                if not st or not et:
                    continue
                sdt = datetime.fromtimestamp(st / 1000, tz=timezone.utc)
                edt = datetime.fromtimestamp(et / 1000, tz=timezone.utc)
                if sdt >= end or edt <= now - timedelta(hours=1):
                    continue
                key = (st, et, str(li.get("listingId") or li.get("title") or ""))
                if key in seen:
                    continue
                seen.add(key)
                keep.append(li)

            if keep:
                stats["channels_with_epg"] += 1
                for li in sorted(keep, key=lambda x: int(x.get("startTime") or 0)):
                    lt = li.get("localizedTitle") or {}
                    ld = li.get("localizedDescription") or {}
                    en_title = str(li.get("title") or "").strip()
                    ar_title = str(lt.get("ar") or "").strip() if isinstance(lt, dict) else ""
                    ar_desc = str(ld.get("ar") or "").strip() if isinstance(ld, dict) else ""
                    if prof == "hybrid":
                        title = en_title or ar_title
                        lang = "en" if en_title else "ar"
                    else:
                        title = ar_title or en_title
                        lang = "ar" if ar_title else "en"
                    if not title:
                        continue
                    p = ET.SubElement(
                        root,
                        "programme",
                        {
                            "channel": cid,
                            "start": fmt(int(li["startTime"])),
                            "stop": fmt(int(li["endTime"])),
                        },
                    )
                    ET.SubElement(p, "title", {"lang": lang}).text = title
                    if ar_desc:
                        ET.SubElement(p, "desc", {"lang": "ar"}).text = ar_desc
                    stats["programmes"] += 1
                    if cid not in stats["samples"]:
                        stats["samples"][cid] = {
                            "name": name,
                            "profile": prof,
                            "title": title,
                            "desc": ar_desc[:220],
                        }
            else:
                stats["zero_epg"].append({"id": cid, "name": name})

            # No per-channel network delay is needed in batch mode.

    except Exception as e:
        stats["error"] = str(e)

    ET.indent(root, space="  ")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "samples"}, ensure_ascii=False))
    if args.catalogue_only:
        return 0 if stats["channels"] else 11
    return 0 if stats["channels"] and stats["programmes"] else 11


if __name__ == "__main__":
    raise SystemExit(main())
