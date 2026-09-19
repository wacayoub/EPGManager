#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the final winner-only EPG monitoring page.

Inputs:
- MENA source master CSV (contains all source candidates)
- optional Morocco Cloud channel list + XMLTV feed

Output:
- one row per winning channel only
- alphabetical channel order
- normalized source labels
- current EPG status/title/description
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def esc(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def parse_dt(value: str):
    value = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value)
    if not m:
        return None
    digits, offset = m.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        if not offset or offset == "Z":
            return datetime.strptime(digits, fmt).replace(tzinfo=timezone.utc)
        return datetime.strptime(digits + offset, fmt + "%z").astimezone(timezone.utc)
    except ValueError:
        return None


def read_xml(path: Path):
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def node_text(node: ET.Element, tag: str) -> str:
    vals = []
    for child in node.findall(tag):
        txt = (child.text or "").strip()
        if txt:
            vals.append(txt)
    return " | ".join(vals)


def source_label(site: str) -> str:
    labels = {
        "bein.com": "beIN",
        "beinsports.com": "beIN Sports",
        "osn.com": "OSN",
        "elcinema.com": "ElCinema",
        "shahid.mbc.net": "Shahid",
        "rotana.net": "Rotana",
        "roya-tv.com": "Roya",
        "aljazeera.com": "Al Jazeera",
        "artonline.tv": "ART",
        "ayn.om": "Ayn Oman",
        "Morocco Cloud": "Morocco Cloud",
        "Sport24": "Sport24",
    }
    return labels.get(site, site)


def monitor_status(row):
    return "🟢 ON" if (row.get("now_status") or "").strip() == "NOW" else "🔴 OFF"


def off_reason(row):
    state = (row.get("now_status") or "").strip()
    if state == "NOW":
        return "EPG current"
    if state == "STALE_FEED":
        return "Published feed expired"
    if state == "NOT_PUBLISHED":
        return "ID not present in final feed"
    if state == "NO_XMLTV_ID":
        return "No XMLTV ID"
    if state == "NEXT_ONLY":
        return "Future EPG exists, nothing current"
    if state == "NO_CURRENT_EVENT":
        return "Published ID but no current event"
    return state or "Unknown"


def external_feed_rows(txt_path: Path, xml_path: Path, source_name: str):
    now = datetime.now(timezone.utc)
    root = read_xml(xml_path)
    events = {}
    next_events = {}
    latest_stop = None
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        start = parse_dt(p.get("start") or "")
        stop = parse_dt(p.get("stop") or "")
        if not cid or not start:
            continue
        if stop and (latest_stop is None or stop > latest_stop):
            latest_stop = stop
        if stop and start <= now < stop:
            old = events.get(cid)
            if old is None or start > old[0]:
                events[cid] = (start, stop, p)
        elif start > now:
            old = next_events.get(cid)
            if old is None or start < old[0]:
                next_events[cid] = (start, stop, p)

    rows = []
    for line in txt_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 2:
            continue
        cid, name = parts[0], parts[1]
        current = events.get(cid)
        nxt = next_events.get(cid)
        if current:
            _start, _stop, p = current
            status = "NOW"
            title = node_text(p, "title")
            desc = node_text(p, "desc")
        elif nxt:
            status = "NEXT_ONLY"
            title = ""
            desc = ""
        elif latest_stop and latest_stop <= now:
            status = "STALE_FEED"
            title = ""
            desc = ""
        else:
            status = "NO_CURRENT_EVENT"
            title = ""
            desc = ""
        rows.append({
            "xmltv_id": cid,
            "channel_name": name,
            "source": source_name,
            "candidate_count": "1",
            "winner_source": source_name,
            "receiver_canonical_id": cid,
            "now_status": status,
            "now_title": title,
            "now_desc": desc,
            "epg_snapshot_utc": now.isoformat(),
        })
    return rows


def morocco_rows(txt_path: Path, xml_path: Path):
    return external_feed_rows(txt_path, xml_path, "Morocco Cloud")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--md", required=True)
    ap.add_argument("--morocco-list")
    ap.add_argument("--morocco-xml")
    ap.add_argument("--sport24-list")
    ap.add_argument("--sport24-xml")
    args = ap.parse_args()

    with Path(args.csv).open("r", encoding="utf-8-sig", newline="") as fh:
        all_rows = list(csv.DictReader(fh))

    # Only the selected winning source row for each channel survives monitoring.
    rows = [
        r for r in all_rows
        if (r.get("xmltv_id") or "").strip()
        and (r.get("source") or "").strip()
        and (r.get("source") or "").strip() == (r.get("winner_source") or "").strip()
    ]

    # Morocco is not part of the MENA Cloud feed; append its dedicated Cloud winners.
    if args.morocco_list and args.morocco_xml:
        lp, xp = Path(args.morocco_list), Path(args.morocco_xml)
        if lp.exists() and xp.exists():
            rows.extend(morocco_rows(lp, xp))

    if args.sport24_list and args.sport24_xml:
        lp, xp = Path(args.sport24_list), Path(args.sport24_xml)
        if lp.exists() and xp.exists():
            rows.extend(external_feed_rows(lp, xp, "Sport24"))

    # De-duplicate exact monitoring IDs, preferring Morocco Cloud for .ma dedicated IDs.
    dedup = {}
    for r in rows:
        key = (r.get("receiver_canonical_id") or r.get("xmltv_id") or "").strip()
        if not key:
            continue
        current = dedup.get(key)
        if current is None:
            dedup[key] = r
        elif r.get("source") == "Morocco Cloud" and current.get("source") != "Morocco Cloud":
            dedup[key] = r
    rows = list(dedup.values())

    # User-facing alphabetical order by channel name, case-insensitive.
    rows.sort(key=lambda r: ((r.get("channel_name") or "").casefold(), (r.get("xmltv_id") or "").casefold()))

    counts = {}
    for r in rows:
        s = (r.get("now_status") or "").strip() or "UNKNOWN"
        counts[s] = counts.get(s, 0) + 1
    snapshot = next(((r.get("epg_snapshot_utc") or "").strip() for r in rows if (r.get("epg_snapshot_utc") or "").strip()), "")

    source_counts = {}
    for r in rows:
        lab = source_label((r.get("winner_source") or r.get("source") or "").strip())
        source_counts[lab] = source_counts.get(lab, 0) + 1

    out = [
        "# EPG Source ID Monitoring",
        "",
        "> Winner-only monitoring page.  ",
        "> Rule: **1 real channel → 1 canonical XMLTV ID → 1 winning source**.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Winner channels monitored | {len(rows)} |",
        f"| 🟢 ON | {counts.get('NOW', 0)} |",
        f"| 🔴 STALE FEED | {counts.get('STALE_FEED', 0)} |",
        f"| 🔴 NOT PUBLISHED | {counts.get('NOT_PUBLISHED', 0)} |",
        f"| 🔴 NO CURRENT EVENT | {counts.get('NO_CURRENT_EVENT', 0)} |",
        f"| Snapshot UTC | {esc(snapshot)} |",
        "",
        "## Winners by source",
        "",
        "| Source | Winners |",
        "|---|---:|",
    ]
    for src, count in sorted(source_counts.items(), key=lambda kv: kv[0].casefold()):
        out.append(f"| {esc(src)} | {count} |")

    out += [
        "",
        "## All winner IDs — alphabetical",
        "",
        '<table width="100%">',
        "<thead><tr>",
        '<th width="90">Status</th>',
        '<th width="190">Channel</th>',
        '<th width="120">Source</th>',
        '<th width="230">XMLTV ID</th>',
        '<th width="330">Current programme</th>',
        '<th width="210">Monitoring</th>',
        "</tr></thead>",
        "<tbody>",
    ]

    for r in rows:
        src = source_label((r.get("winner_source") or r.get("source") or "").strip())
        raw_id = (r.get("xmltv_id") or "").strip()
        canonical = (r.get("receiver_canonical_id") or "").strip()
        title = (r.get("now_title") or "").strip()
        desc = (r.get("now_desc") or "").strip()
        id_html = f"<code>{esc(raw_id)}</code>"
        if canonical and canonical != raw_id:
            id_html += f"<br><small>↳ {esc(canonical)}</small>"

        if title:
            programme_html = f"<b>{esc(title)}</b>"
            if desc:
                programme_html += (
                    "<details><summary>description</summary>"
                    f"<small>{esc(desc)}</small></details>"
                )
        else:
            programme_html = "—"

        reason = off_reason(r)
        if (r.get("now_status") or "").strip() == "NOW":
            reason_html = "<b>EPG current</b>"
        else:
            reason_html = esc(reason)

        out.append(
            "<tr>"
            f"<td><b>{esc(monitor_status(r))}</b></td>"
            f"<td><b>{esc(r.get('channel_name') or '')}</b></td>"
            f"<td><b>{esc(src)}</b></td>"
            f"<td>{id_html}</td>"
            f"<td>{programme_html}</td>"
            f"<td>{reason_html}</td>"
            "</tr>"
        )
    out += ["</tbody>", "</table>"]

    Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(
        f"EPG_MASTER_MD PASS winners={len(rows)} on={counts.get('NOW',0)} "
        f"morocco={source_counts.get('Morocco Cloud',0)} sport24={source_counts.get('Sport24',0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
