#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted MBC schedule-gap repair using Shahid as a donor only.

This module does NOT replace the selected primary source. It is deliberately
limited to audited receiver IDs where the selected primary occasionally
publishes a short/incomplete rolling 48-hour window:

- AlHadath.sa@SD: OSN remains primary; Shahid may fill uncovered time only.
- MBC3.ae@SD: OSN remains primary; Shahid may fill uncovered time only.
- MBCMasrDrama.sa@SD: ElCinema remains primary; Shahid may fill uncovered time only.

Only real Arabic Shahid events with non-empty Arabic descriptions are eligible.
Known placeholder rows are rejected, existing primary timestamps are never
modified, and any donor event that overlaps a primary/already-accepted event is
rejected. This preserves the one-primary-timeline architecture while providing
a narrow, evidence-backed gap fallback.

Important: a rolling 48-hour receiver window normally spans three UTC calendar
dates when the workflow runs after midnight. Donor fetches therefore cover
every UTC date touched by [now, now + window_hours], then trim events back to the
exact rolling window. Fetching only today + tomorrow can cap usable donor
coverage below 30 hours late in the day even when Shahid has a healthy guide.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TARGETS = {
    "AlHadath.sa@SD": {
        "site_id": "387288",
        "primary": "osn.com",
        "min_coverage_h": 30.0,
    },
    # Exact Shahid Arabic service; donor-only and aligned with the 28h MBC gate.
    "MBC3.ae@SD": {
        "site_id": "409385",
        "primary": "osn.com",
        "min_coverage_h": 28.0,
    },
    "MBCMasrDrama.sa@SD": {
        "site_id": "49923122575716",
        "primary": "elcinema.com",
        "min_coverage_h": 30.0,
    },
}

PLACEHOLDER_TEXT = {
    "tv guide is not available",
    "programme schedule unavailable",
    "program schedule unavailable",
    "schedule unavailable",
    "no information",
    "no info",
    "tba",
    "جدول البرامج غير متاح",
    "لا توجد معلومات",
    "لا يوجد برنامج",
}
AR_RE = re.compile(r"[\u0600-\u06ff]")
_XMLTV_RE = re.compile(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?")


def _parse_xmltv(value: str):
    value = (value or "").strip()
    m = _XMLTV_RE.match(value)
    if not m:
        return None
    digits, offset = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits, fmt)
    except ValueError:
        return None
    if offset == "Z":
        tz = timezone.utc
    elif offset:
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    else:
        tz = timezone.utc
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def _parse_iso(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _xmltv_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _placeholder(title: str, desc: str) -> bool:
    title_norm = re.sub(r"\s+", " ", (title or "").casefold()).strip()
    desc_norm = re.sub(r"\s+", " ", (desc or "").casefold()).strip()
    return (
        title_norm in PLACEHOLDER_TEXT
        or desc_norm in PLACEHOLDER_TEXT
        or "tv guide is not available" in title_norm
        or "جدول البرامج غير متاح" in title_norm
    )


def _programme_rows(root: ET.Element, cid: str):
    rows = []
    for programme in root.findall("programme"):
        if (programme.get("channel") or "").strip() != cid:
            continue
        start = _parse_xmltv(programme.get("start") or "")
        stop = _parse_xmltv(programme.get("stop") or "")
        if start is None or stop is None or stop <= start:
            continue
        rows.append((start, stop, programme))
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows


def _window_rows(rows, now: datetime, end: datetime):
    return [(s, e, p) for s, e, p in rows if e > now - timedelta(minutes=5) and s < end]


def _profile(rows, now: datetime, end: datetime):
    rows = _window_rows(rows, now, end)
    coverage = sum((e - s).total_seconds() for s, e, _ in rows) / 3600.0
    gaps = []
    cursor = None
    for start, stop, programme in rows:
        if cursor is not None and start > cursor:
            delta = (start - cursor).total_seconds() / 3600.0
            if delta > 2.0:
                gaps.append({
                    "from": cursor.isoformat(),
                    "to": start.isoformat(),
                    "hours": round(delta, 3),
                    "next_title": _text(programme, "title"),
                })
        if cursor is None or stop > cursor:
            cursor = stop
    return {
        "events": len(rows),
        "coverage_h": round(coverage, 3),
        "gaps_gt_2h": len(gaps),
        "gap_hours": round(sum(x["hours"] for x in gaps), 3),
        "gaps": gaps,
    }


def _needs_repair(profile, minimum: float) -> bool:
    return float(profile.get("coverage_h", 0.0)) < minimum or int(profile.get("gaps_gt_2h", 0)) > 0


def _donor_days(now: datetime, end: datetime):
    """Return every UTC calendar date touched by the rolling receiver window."""
    day = now.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    days = []
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


def _fetch_shahid(site_id: str, day, timeout: int = 15):
    params = {
        "csvChannelIds": site_id,
        "from": day.strftime("%Y-%m-%d") + "T00:00:00.000Z",
        "to": day.strftime("%Y-%m-%d") + "T23:59:59.999Z",
        "country": "SA",
        "language": "ar",
        "Accept-Language": "ar",
    }
    url = "https://api2.shahid.net/proxy/v2.1/shahid-epg-api/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "EPGManager-MENA-Cloud/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    out = []
    for schedules in payload.get("items") or []:
        if str(schedules.get("channelId")) != str(site_id):
            continue
        for item in schedules.get("items") or []:
            start = _parse_iso(item.get("actualFrom"))
            stop = _parse_iso(item.get("actualTo"))
            title = str(item.get("title") or "").strip()
            desc = str(item.get("description") or "").strip()
            if start is None or stop is None or stop <= start:
                continue
            if (stop - start) > timedelta(hours=12):
                continue
            if not title or not desc or _placeholder(title, desc):
                continue
            if not AR_RE.search(title) or not AR_RE.search(desc):
                continue
            out.append({
                "start": start,
                "stop": stop,
                "title": title,
                "desc": desc,
                "season": item.get("seasonNumber"),
                "episode": item.get("episodeNumber"),
                "genres": item.get("genres") or [],
            })
    return out


def _donor_programme(cid: str, row: dict) -> ET.Element:
    programme = ET.Element("programme", {
        "channel": cid,
        "start": _xmltv_stamp(row["start"]),
        "stop": _xmltv_stamp(row["stop"]),
    })
    title = ET.SubElement(programme, "title", {"lang": "ar"})
    title.text = row["title"]
    desc = ET.SubElement(programme, "desc", {"lang": "ar"})
    desc.text = row["desc"]
    for genre in row.get("genres") or []:
        value = str(genre or "").strip()
        if value:
            category = ET.SubElement(programme, "category", {"lang": "ar"})
            category.text = value
    return programme


def _overlaps(start: datetime, stop: datetime, rows) -> bool:
    return any(start < existing_stop and stop > existing_start for existing_start, existing_stop, _ in rows)


def repair_file(path: str | Path, window_hours: int = 48, report_path: str | Path | None = None):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        return {"status": "SKIP", "reason": "candidate missing"}

    root = ET.parse(str(path)).getroot()
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, int(window_hours)))
    donor_days = _donor_days(now, end)
    report = {
        "schema": 2,
        "mode": "targeted-shahid-gap-donor",
        "generated_utc": now.isoformat(),
        "window_hours": int(window_hours),
        "window_end_utc": end.isoformat(),
        "donor_days_utc": [day.isoformat() for day in donor_days],
        "targets": {},
    }
    changed = 0

    for cid, config in TARGETS.items():
        primary_rows = _programme_rows(root, cid)
        before = _profile(primary_rows, now, end)
        target_report = {
            "primary": config["primary"],
            "donor": "shahid.mbc.net",
            "before": before,
            "donor_days_utc": [day.isoformat() for day in donor_days],
            "donor_raw_real": 0,
            "accepted": 0,
            "rejected_overlap": 0,
            "rejected_duplicate": 0,
            "fetch_errors": [],
        }
        report["targets"][cid] = target_report

        if not _needs_repair(before, float(config["min_coverage_h"])):
            target_report["status"] = "PRIMARY_ALREADY_HEALTHY"
            target_report["after"] = before
            continue

        donor_rows = []
        seen_donor = set()
        for day in donor_days:
            try:
                fetched = _fetch_shahid(config["site_id"], day)
            except Exception as exc:
                target_report["fetch_errors"].append("%s: %s" % (day.isoformat(), str(exc)[:180]))
                continue
            for row in fetched:
                key = (row["start"], row["stop"], row["title"])
                if key in seen_donor:
                    target_report["rejected_duplicate"] += 1
                    continue
                seen_donor.add(key)
                if row["stop"] <= now - timedelta(minutes=5) or row["start"] >= end:
                    continue
                donor_rows.append(row)
        donor_rows.sort(key=lambda x: (x["start"], x["stop"], x["title"]))
        target_report["donor_raw_real"] = len(donor_rows)

        occupied = list(primary_rows)
        accepted = []
        existing_keys = {(s, e, _text(p, "title")) for s, e, p in occupied}
        for donor in donor_rows:
            key = (donor["start"], donor["stop"], donor["title"])
            if key in existing_keys:
                target_report["rejected_duplicate"] += 1
                continue
            if _overlaps(donor["start"], donor["stop"], occupied):
                target_report["rejected_overlap"] += 1
                continue
            programme = _donor_programme(cid, donor)
            root.append(programme)
            occupied.append((donor["start"], donor["stop"], programme))
            occupied.sort(key=lambda x: (x[0], x[1]))
            existing_keys.add(key)
            accepted.append(donor)

        target_report["accepted"] = len(accepted)
        target_report["accepted_preview"] = [
            {
                "start": row["start"].isoformat(),
                "stop": row["stop"].isoformat(),
                "title": row["title"],
            }
            for row in accepted[:12]
        ]
        after_rows = _programme_rows(root, cid)
        after = _profile(after_rows, now, end)
        target_report["after"] = after
        target_report["status"] = (
            "REPAIRED"
            if not _needs_repair(after, float(config["min_coverage_h"]))
            else "IMPROVED_NOT_YET_GATE_READY"
        )
        changed += len(accepted)

    report["accepted_total"] = changed
    if changed:
        ET.indent(root, space="  ")
        path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    if report_path:
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    statuses = {cid: row.get("status") for cid, row in report["targets"].items()}
    print("MBC Shahid donor repair: accepted=%d donor_days=%s statuses=%s" % (
        changed, ",".join(day.isoformat() for day in donor_days), statuses))
    for cid, row in report["targets"].items():
        print("  %s: %.1fh/%d gaps -> %.1fh/%d gaps, accepted=%d" % (
            cid,
            float(row["before"].get("coverage_h", 0.0)), int(row["before"].get("gaps_gt_2h", 0)),
            float(row["after"].get("coverage_h", 0.0)), int(row["after"].get("gaps_gt_2h", 0)),
            int(row.get("accepted", 0)),
        ))
    return report
