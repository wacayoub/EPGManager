#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STARZPLAY Live EPG adapter using the public STARZPLAY EPG API.

The adapter discovers the current Live TV categories dynamically, fetches every
channel in those categories for the requested country/language, deduplicates
channels by STARZPLAY identity and keeps channels even when their current EPG is
empty. Radio is excluded because EPGManager consumes TV services only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import html as html_mod
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import requests

API_BASE = "https://epg.aws.playco.com/api/v1.1"
UA = (
    "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140 Mobile Safari/537.36"
)
# Unicode-aware normalization: Arabic channel names must remain distinct.
NONWORD_RE = re.compile(r"[\W_]+", re.UNICODE)
TV_CATEGORY_BLOCKLIST = {"radio"}


def norm_name(value: str) -> str:
    text = html_mod.unescape(value or "").casefold().replace("&", " and ")
    return " ".join(NONWORD_RE.sub(" ", text).split())


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("text", "value", "title", "name", "label", "en", "ar"):
            if value.get(key) not in (None, ""):
                return scalar(value[key])
    return ""


def first(obj: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_dt(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def read_id_index(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "|" not in line:
            continue
        cid, name = line.split("|", 1)
        cid, name = cid.strip(), name.strip()
        if cid and name:
            out.setdefault(norm_name(name), cid)
    return out


NAME_ALIASES = {
    "abu dhabi sports 1": "AbuDhabiSports1.ae@SD",
    "ad sports 1": "AbuDhabiSports1.ae@SD",
    "abu dhabi sports 2": "AbuDhabiSports2.ae@SD",
    "ad sports 2": "AbuDhabiSports2.ae@SD",
    "abu dhabi tv": "AbuDhabiTV.ae@SD",
    "national geographic abu dhabi": "NationalGeographicAbuDhabi.ae@SD",
    "nat geo abu dhabi": "NationalGeographicAbuDhabi.ae@SD",
    "cartoon network arabic": "CartoonNetworkArabic.ae@SD",
    "sky news arabia": "SkyNewsArabia.ae@SD",
    "sky news arabia live": "SkyNewsArabia.ae@SD",
    "zee alwan": "ZeeAlwan.ae@SD",
    "zee aflam": "ZeeAflam.ae@SD",
    "al emarat tv": "AlEmaratTV.ae@SD",
    "spacetoon": "Spacetoon.ae@SD",
    "majid tv": "MajidTV.ae@SD",
    "saudi quran": "SaudiQuran.sa@SD",
}


SOURCE_ID_ALIASES = {
    # Stable STARZPLAY IDs verified against the current Live TV catalogue.
    "720335400126": "AbuDhabiSports1.ae@SD",
    "720335400127": "AbuDhabiSports2.ae@SD",
    "720335400128": "NationalGeographicAbuDhabi.ae@SD",
    "558369320039": "ZeeAflam.ae@SD",
    "311102504197": "StarzplaySports3.sa@HD",
}


def make_channel_id(name: str, source_id: str, index: dict[str, str]) -> str:
    """Return a collision-safe canonical ID.

    STARZPLAY's numeric Live TV ID is authoritative.  The generic MENA name
    index is deliberately NOT consulted here: localized Arabic display names
    caused unrelated channels to collapse onto existing IDs.  Only explicit,
    verified aliases are allowed to replace the STARZPLAY ID.
    """
    normalized = norm_name(name)
    stable_source = (source_id or "").strip()
    if stable_source in SOURCE_ID_ALIASES:
        return SOURCE_ID_ALIASES[stable_source]
    if normalized in NAME_ALIASES:
        return NAME_ALIASES[normalized]
    stable = re.sub(r"[^A-Za-z0-9_.-]+", "-", stable_source).strip("-")
    if stable:
        return f"starzplay.{stable}"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:14]
    return f"starzplay.{digest}"


def api_get(path: str, *, lang: str, country: str, timeout: int, params: dict[str, Any] | None = None) -> tuple[int, Any, str]:
    headers = {
        "User-Agent": UA,
        "Client-Type": "Android",
        "Accept": "application/json",
        "Accept-Language": ("ar,en;q=0.7" if lang == "ar" else "en,ar;q=0.7"),
        "x-geo-country": country,
    }
    url = f"{API_BASE}/{path.lstrip('/')}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    text = r.text
    try:
        obj = r.json()
    except Exception:
        obj = None
    return r.status_code, obj, text


def category_catalogue(lang: str, country: str, timeout: int) -> tuple[list[dict[str, Any]], list[str]]:
    status, obj, body = api_get("epg/category/config/0", lang=lang, country=country, timeout=timeout, params={"lang": lang})
    if status != 200 or not isinstance(obj, dict):
        raise RuntimeError(f"category config HTTP {status}: {body[:300]}")
    rows = [x for x in (obj.get("data") or []) if isinstance(x, dict)]
    slugs = []
    for row in rows:
        slug = scalar(row.get("slug"))
        if slug and slug not in TV_CATEGORY_BLOCKLIST and slug not in slugs:
            slugs.append(slug)
    return rows, slugs


def iter_channel_objects(obj: Any):
    if isinstance(obj, dict):
        events = obj.get("events")
        if isinstance(events, list):
            yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from iter_channel_objects(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_channel_objects(value)


def channel_identity(ch: dict[str, Any]) -> tuple[str, str]:
    name = scalar(first(ch, (
        "channelName", "channel_name", "displayName", "display_name",
        "title", "name", "label",
    )))
    source_id = scalar(first(ch, (
        "guid", "channelGuid", "channel_guid", "channelId", "channel_id",
        "contentId", "content_id", "id", "slug",
    )))
    events = ch.get("events") if isinstance(ch.get("events"), list) else []
    if events:
        e0 = events[0] if isinstance(events[0], dict) else {}
        if not source_id:
            source_id = scalar(first(e0, ("guid", "channelId", "channel_id", "slug")))
        if not name:
            name = scalar(first(e0, ("channelName", "channel_name", "networkName", "stationName")))
        if not name:
            # STARZPLAY's channel/start response can use channel title as each
            # event title for continuous live-news style channels.
            name = scalar(first(e0, ("description", "title")))
    return name, source_id


def collect_from_payload(payload: Any) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    channels: list[dict[str, str]] = []
    events: list[dict[str, Any]] = []
    seen_channel_keys: set[tuple[str, str]] = set()
    for ch in iter_channel_objects(payload):
        name, source_id = channel_identity(ch)
        key = (source_id, norm_name(name))
        if name or source_id:
            if key not in seen_channel_keys:
                channels.append({"name": name or source_id, "source_id": source_id})
                seen_channel_keys.add(key)
        for event in ch.get("events") or []:
            if not isinstance(event, dict):
                continue
            start = parse_dt(first(event, ("tsStart", "start", "startTime", "start_time", "startsAt")))
            stop = parse_dt(first(event, ("tsEnd", "stop", "end", "endTime", "end_time", "endsAt")))
            if not start:
                continue
            title = scalar(first(event, ("title", "name", "displayTitle", "eventName")))
            if not title:
                continue
            # The enclosing channel ID is authoritative. Event GUIDs identify
            # programme/stream assets and must never replace the channel ID.
            event_source_id = source_id or scalar(first(event, ("channelId", "channel_id", "guid")))
            events.append({
                "channel_name": name or scalar(first(event, ("channelName", "channel_name"))) or event_source_id,
                "source_id": event_source_id,
                "title": title,
                "description": scalar(first(event, ("description", "synopsis", "summary", "overview"))),
                "start": start,
                "stop": stop,
            })
    return channels, events


def fetch_all(lang: str, country: str, hours: int, timeout: int) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    now = datetime.now(timezone.utc)
    ts_start = int((now - timedelta(hours=2)).timestamp())
    ts_end = int((now + timedelta(hours=max(hours, 1) + 2)).timestamp())
    cat_rows, slugs = category_catalogue(lang, country, timeout)

    all_channels: list[dict[str, str]] = []
    all_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    category_stats: list[dict[str, Any]] = []

    for slug in slugs:
        page = 1
        seen_pages = 0
        cat_channels = 0
        cat_events = 0
        while page <= 20:
            params = {
                "category": slug,
                "ts_start": ts_start,
                "ts_end": ts_end,
                "lang": lang,
                "pg": "18",
                "page": page,
                "limit": 200,
            }
            status, obj, body = api_get(
                "epg/category/events/0",
                lang=lang,
                country=country,
                timeout=timeout,
                params=params,
            )
            if status != 200 or not isinstance(obj, dict):
                errors.append({"category": slug, "page": page, "status": status, "sample": body[:300]})
                break

            ch_rows, ev_rows = collect_from_payload(obj)
            all_channels.extend(ch_rows)
            all_events.extend(ev_rows)
            cat_channels += len(ch_rows)
            cat_events += len(ev_rows)
            seen_pages += 1

            data = obj.get("data")
            total = obj.get("total")
            data_len = len(data) if isinstance(data, list) else 0
            try:
                total_i = int(total)
            except Exception:
                total_i = data_len
            if not data_len or data_len < 200 or page * 200 >= total_i:
                break
            page += 1

        category_stats.append({
            "slug": slug,
            "pages": seen_pages,
            "channels_seen": cat_channels,
            "events_seen": cat_events,
        })

    return all_channels, all_events, {
        "categories": cat_rows,
        "category_slugs": slugs,
        "category_stats": category_stats,
        "errors": errors,
    }


def build_xml(channels_in: list[dict[str, str]], events_in: list[dict[str, Any]], *, lang: str, index: dict[str, str], hours: int) -> tuple[ET.Element, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    max_start = now + timedelta(hours=max(1, hours))

    channels: dict[str, dict[str, str]] = {}
    source_to_cid: dict[str, str] = {}
    name_to_cid: dict[str, str] = {}

    for row in channels_in:
        name = scalar(row.get("name"))
        source_id = scalar(row.get("source_id"))
        if not name and not source_id:
            continue
        cid = make_channel_id(name or source_id, source_id, index)
        channels.setdefault(cid, {"name": name or source_id or cid, "source_id": source_id})
        if source_id:
            source_to_cid[source_id] = cid
        if name:
            name_to_cid[norm_name(name)] = cid

    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events_in:
        start = event.get("start")
        if not isinstance(start, datetime) or start >= max_start:
            continue
        stop = event.get("stop")
        if isinstance(stop, datetime) and stop <= now - timedelta(hours=3):
            continue
        name = scalar(event.get("channel_name"))
        source_id = scalar(event.get("source_id"))
        cid = source_to_cid.get(source_id) or name_to_cid.get(norm_name(name))
        if not cid:
            cid = make_channel_id(name or source_id, source_id, index)
            channels.setdefault(cid, {"name": name or source_id or cid, "source_id": source_id})
        title = scalar(event.get("title"))
        dedup[(cid, fmt_dt(start), title)] = {**event, "cid": cid}

    by_cid: dict[str, list[dict[str, Any]]] = {}
    for event in dedup.values():
        by_cid.setdefault(event["cid"], []).append(event)
    for rows in by_cid.values():
        rows.sort(key=lambda x: x["start"])
        for i, event in enumerate(rows):
            if not isinstance(event.get("stop"), datetime) or event["stop"] <= event["start"]:
                nxt = rows[i + 1]["start"] if i + 1 < len(rows) else None
                event["stop"] = nxt if isinstance(nxt, datetime) and nxt > event["start"] else event["start"] + timedelta(minutes=60)

    root = ET.Element("tv", {
        "generator-info-name": f"EPGManager STARZPLAY API ({lang})",
        "generator-info-url": "https://www.starzplay.com/",
    })
    for cid in sorted(channels, key=str.casefold):
        row = channels[cid]
        c = ET.SubElement(root, "channel", {"id": cid})
        dn = ET.SubElement(c, "display-name", {"lang": lang})
        dn.text = row["name"]
        if row.get("source_id"):
            u = ET.SubElement(c, "url", {"system": "starzplay-id"})
            u.text = row["source_id"]

    programme_count = 0
    for cid in sorted(by_cid, key=str.casefold):
        for event in by_cid[cid]:
            p = ET.SubElement(root, "programme", {
                "channel": cid,
                "start": fmt_dt(event["start"]),
                "stop": fmt_dt(event["stop"]),
            })
            t = ET.SubElement(p, "title", {"lang": lang})
            t.text = scalar(event.get("title"))
            desc = scalar(event.get("description"))
            if desc:
                d = ET.SubElement(p, "desc", {"lang": lang})
                d.text = desc
            programme_count += 1

    zero_ids = sorted(cid for cid in channels if cid not in by_cid)
    return root, {
        "channels": len(channels),
        "programmes": programme_count,
        "zero_epg_channels": len(zero_ids),
        "zero_epg_ids": zero_ids,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=("ar", "en"), required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--id-index", default="feeds/mena.txt")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--country", default="AE", help="Single country fallback/compatibility code")
    ap.add_argument("--countries", default="", help="Comma-separated country codes to union, e.g. AE,SA,KW,QA,BH,OM")
    args = ap.parse_args()

    countries = []
    for code in (args.countries.split(",") if args.countries else [args.country]):
        code = code.strip().upper()
        if code and code not in countries:
            countries.append(code)
    if not countries:
        countries = ["AE"]

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "language": args.lang,
        "country": countries[0],
        "countries": countries,
        "catalogue_scope": "GCC union" if len(countries) > 1 else countries[0],
        "api_base": API_BASE,
        "hours": args.hours,
    }
    try:
        channel_rows: list[dict[str, str]] = []
        event_rows: list[dict[str, Any]] = []
        country_stats: list[dict[str, Any]] = []
        merged_errors: list[dict[str, Any]] = []
        for country in countries:
            try:
                c_rows, e_rows, c_meta = fetch_all(args.lang, country, args.hours, args.timeout)
                channel_rows.extend(c_rows)
                event_rows.extend(e_rows)
                merged_errors.extend([{"country": country, **e} for e in c_meta.get("errors", [])])
                country_stats.append({
                    "country": country,
                    "raw_channel_rows": len(c_rows),
                    "raw_event_rows": len(e_rows),
                    "category_slugs": c_meta.get("category_slugs", []),
                    "category_stats": c_meta.get("category_stats", []),
                    "errors": c_meta.get("errors", []),
                })
            except Exception as exc:
                merged_errors.append({"country": country, "error": str(exc)})
                country_stats.append({"country": country, "status": "FAIL", "error": str(exc)})

        if not channel_rows:
            raise RuntimeError("No STARZPLAY channels returned for requested countries: " + ",".join(countries))

        meta = {
            "country_stats": country_stats,
            "errors": merged_errors,
        }
        index = read_id_index(Path(args.id_index) if args.id_index else None)
        root, stats = build_xml(channel_rows, event_rows, lang=args.lang, index=index, hours=args.hours)
        ET.indent(root, space="  ")
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        report.update(meta)
        report.update(stats)
        report["channel_catalogue"] = [
            {
                "id": node.get("id") or "",
                "name": scalar(first({x.tag: x.text for x in node.findall("display-name")}, ("display-name",))) or (node.get("id") or ""),
                "source_id": next(((u.text or "").strip() for u in node.findall("url") if u.get("system") == "starzplay-id"), ""),
            }
            for node in root.findall("channel")
        ]
        report["raw_channel_rows"] = len(channel_rows)
        report["raw_event_rows"] = len(event_rows)
        report["status"] = "PASS" if stats["channels"] else "FAIL"
        rc = 0 if stats["channels"] else 11
    except Exception as exc:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text('<?xml version="1.0" encoding="UTF-8"?><tv></tv>\n', encoding="utf-8")
        report.update({"status": "FAIL", "error": str(exc), "channels": 0, "programmes": 0})
        rc = 10

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
