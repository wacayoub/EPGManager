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
import json
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


def sat_norm(value: str) -> str:
    s = str(value or "")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = s.casefold()
    s = re.sub(r"@(?:sd|hd|mena|arabic)\b", " ", s)
    s = re.sub(r"\.(?:ae|sa|eg|qa|iq|jo|lb|kw|bh|om|ye|dz|tn|ly|sd|sy|mr|ps|ma|uk|us|fr|net)\b", " ", s)
    s = re.sub(r"\b(?:uhd|fhd|hd|sd|digital|channel|tv)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", s)
    return " ".join(s.split())


def sat_aliases(row):
    vals = [
        row.get("channel_name") or "",
        row.get("xmltv_id") or "",
        row.get("receiver_canonical_id") or "",
    ]
    aliases = set()
    for value in vals:
        base = sat_norm(value)
        if not base:
            continue
        variants = {base}
        variants.add(re.sub(r"\b(?:middle east|mena|arabia|arabic)\b", " ", base))
        variants.add(re.sub(r"\b(?:sports?)\b", " sport ", base))
        for v in variants:
            v = " ".join(v.split())
            letters = sum(ch.isalpha() for ch in v)
            if letters >= 4 and not v.isdigit():
                aliases.add(v)
    return sorted(aliases, key=lambda x: (-len(x), x))


def satellite_channel_name(row, sat_names=None):
    """Prefer a receiver/satellite-style Latin service name.

    Upstream source catalogues sometimes expose Arabic names or bare numeric
    slots (for example beIN rows 1, 2, 13). For monitoring we instead derive a
    stable service label from the receiver canonical/XMLTV identity.
    """
    sat_names = sat_names or {}
    probes = [
        (row.get("xmltv_id") or "").strip(),
        (row.get("receiver_canonical_id") or "").strip(),
        (row.get("channel_name") or "").strip(),
    ]
    for probe in probes:
        if probe and probe.casefold() in sat_names:
            return sat_names[probe.casefold()]

    current = (row.get("channel_name") or "").strip()
    if current and not current.isdigit() and not re.search(r"[\u0600-\u06ff]", current):
        return current

    raw = (row.get("receiver_canonical_id") or row.get("xmltv_id") or "").strip()
    raw = re.sub(r"@(?:SD|HD|MENA|Arabic)$", "", raw, flags=re.I)
    raw = re.sub(r"\.(?:ae|sa|eg|qa|iq|jo|lb|kw|bh|om|ye|dz|tn|ly|sd|sy|mr|ps|ma|uk|us|fr)$", "", raw, flags=re.I)
    raw = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    raw = raw.replace("_", " ").replace(".", " ")
    raw = re.sub(r"\s+", " ", raw).strip()

    replacements = {
        "be IN": "beIN",
        "MBC Masr": "MBC Masr",
        "Al Jazeera": "Al Jazeera",
        "Abu Dhabi": "Abu Dhabi",
        "Cartoon Network": "Cartoon Network",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    return raw or current or "Unknown"


def satellite_positions(row, sat_index):
    if not sat_index:
        return []
    channels = sat_index.get("channels") or {}
    found = set()
    for alias in sat_aliases(row):
        for pos in channels.get(alias, []):
            found.add(pos)
    order = ["26E", "25.5E", "7W", "8W"]
    return [p for p in order if p in found]


def candidate_programme_text(row):
    state = (row.get("source_now_status") or "").strip()
    title = (row.get("source_now_title") or "").strip()
    if state == "NOW" and title:
        return title
    if state == "NEXT_ONLY":
        return "Future EPG only"
    if state == "NO_CURRENT_EVENT":
        return "No current programme"
    if state == "STALE_SOURCE_FEED":
        return "Source feed expired"
    if state == "NOT_MONITORED":
        return "No direct source feed monitored"
    return state or "No current programme"


def monitor_status(row):
    return "🟢 ON" if (row.get("now_status") or "").strip() == "NOW" else "🔴 OFF"


def off_reason(row):
    state = (row.get("now_status") or "").strip()
    if state == "NOW":
        return "EPG current"
    if state == "STALE_SOURCE_FEED":
        return "Winner source feed expired"
    if state == "STALE_FEED":
        return "Published feed expired"
    if state == "STALE_RELEASE_MISSING_ID":
        return "Current source ID absent only because published release is stale"
    if state == "NOT_PUBLISHED":
        return "ID not present in current fresh final feed"
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
    ap.add_argument("--sat-index")
    ap.add_argument("--source-registry")
    ap.add_argument("--sat-names")
    ap.add_argument("--duplicates-md")
    ap.add_argument("--zero-epg-md")
    ap.add_argument("--zero-epg-report")
    args = ap.parse_args()

    with Path(args.csv).open("r", encoding="utf-8-sig", newline="") as fh:
        all_rows = list(csv.DictReader(fh))

    sat_names = {}
    if args.sat_names and Path(args.sat_names).exists():
        try:
            raw_names = json.loads(Path(args.sat_names).read_text(encoding="utf-8"))
            if isinstance(raw_names, dict):
                sat_names = {str(k).casefold(): str(v) for k, v in raw_names.items() if str(v).strip()}
        except Exception:
            sat_names = {}

    source_registry = {}
    if args.source_registry and Path(args.source_registry).exists():
        try:
            source_registry = json.loads(Path(args.source_registry).read_text(encoding="utf-8"))
        except Exception:
            source_registry = {}

    sat_index = {}
    if args.sat_index and Path(args.sat_index).exists():
        try:
            raw = Path(args.sat_index).read_text(encoding="utf-8").strip()
            if args.sat_index.endswith(".b64"):
                raw = gzip.decompress(base64.b64decode(raw)).decode("utf-8")
            sat_index = json.loads(raw)
        except Exception:
            sat_index = {}

    # Keep all provenance rows for multi-source comparison, while the main
    # monitoring table still renders only one selected winner row per channel.
    candidates_by_id = {}
    for candidate in all_rows:
        cid = (candidate.get("xmltv_id") or "").strip()
        src = (candidate.get("source") or "").strip()
        if not cid or not src:
            continue
        bucket = candidates_by_id.setdefault(cid, {})
        bucket.setdefault(src, candidate)

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
        "➡️ **[Open duplicate-ID comparison](mena-source-id-duplicates.md)**  ",
        "➡️ **[Open zero-EPG channels](mena-source-zero-epg.md)**",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Winner channels monitored | {len(rows)} |",
        f"| 🟢 ON | {counts.get('NOW', 0)} |",
        f"| 🔴 STALE SOURCE FEED | {counts.get('STALE_SOURCE_FEED', 0)} |",
        f"| 🔴 STALE FEED | {counts.get('STALE_FEED', 0)} |",
        f"| 🔴 STALE RELEASE / MISSING ID | {counts.get('STALE_RELEASE_MISSING_ID', 0)} |",
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

    direct_rows = (source_registry.get("sources") or []) if isinstance(source_registry, dict) else []
    if direct_rows:
        out += [
            "",
            "## Direct source scrape progress",
            "",
            "> **Scrap** = source catalogue fully queried. **3h EPG coverage** = channels with usable EPG in the current 3-hour test window.",
            "",
            "| Source | Scrap | 3h EPG coverage | Active / Catalogue | Programmes | Health |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for item in sorted(direct_rows, key=lambda x: str(x.get("label") or "").casefold()):
            active = int(item.get("channels", 0) or 0)
            fallback_totals = {"elcinema": 102, "osn": 60, "bein": 85, "sport24": 19}
            key = str(item.get("key") or "").strip().casefold()
            total = int(item.get("catalogue_channels", 0) or fallback_totals.get(key, 0) or 0)
            scrape_pct = float(item.get("scrape_pct", 0.0) or 0.0)
            coverage_pct = float(item.get("coverage_pct", 0.0) or 0.0)
            if not coverage_pct and total:
                coverage_pct = round(active * 100.0 / total, 1)
            # Backward-compatible registry rows from before scrape/coverage were
            # split: a healthy completed source run means the catalogue was
            # processed even if only part of it has EPG inside the 3h window.
            if not scrape_pct and item.get("healthy") and total:
                scrape_pct = 100.0
            programs = int(item.get("programmes", 0) or 0)
            health = "🟢 HEALTHY" if item.get("healthy") else "🔴 FAILED"
            out.append(
                f"| {esc(item.get('label') or item.get('key') or '')} | **{scrape_pct:.1f}%** | "
                f"**{coverage_pct:.1f}%** | {active} / {total} | {programs} | {health} |"
            )

    out += [
        "",
        "## All winner IDs — alphabetical",
        "",
        '<table width="100%">',
        "<thead><tr>",
        '<th width="90">Status</th>',
        '<th width="115">SAT</th>',
        '<th width="190">Channel</th>',
        '<th width="120">Source</th>',
        '<th width="230">XMLTV ID</th>',
        '<th width="330">Current programme</th>',
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

        candidate_rows = list((candidates_by_id.get(raw_id) or {}).values())
        if len(candidate_rows) > 1:
            winner_site = (r.get("winner_source") or r.get("source") or "").strip()
            ordered = sorted(
                candidate_rows,
                key=lambda x: (
                    0 if (x.get("source") or "").strip() == winner_site else 1,
                    source_label((x.get("source") or "").strip()).casefold(),
                ),
            )
            compare_lines = []
            for cand in ordered:
                cand_site = (cand.get("source") or "").strip()
                cand_label = source_label(cand_site)
                prefix = "✅ Suggested — " if cand_site == winner_site else ""
                compare_lines.append(
                    f"<b>{esc(prefix + cand_label)}</b> — {esc(candidate_programme_text(cand))}"
                )
            programme_html += (
                f"<details><summary>Compare {len(ordered)} sources</summary>"
                f"<small>{'<br>'.join(compare_lines)}</small></details>"
            )

        sats = satellite_positions(r, sat_index)
        sat_html = " · ".join(sats) if sats else "—"
        out.append(
            "<tr>"
            f"<td><b>{esc(monitor_status(r))}</b></td>"
            f"<td><small><b>{esc(sat_html)}</b></small></td>"
            f"<td><b>{esc(satellite_channel_name(r, sat_names))}</b></td>"
            f"<td><b>{esc(src)}</b></td>"
            f"<td>{id_html}</td>"
            f"<td>{programme_html}</td>"
            "</tr>"
        )
    out += ["</tbody>", "</table>"]

    Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")

    if args.duplicates_md:
        dup_groups = []
        for cid, by_source in candidates_by_id.items():
            candidates = list(by_source.values())
            if len(candidates) < 2:
                continue
            winner_site = next(
                ((x.get("winner_source") or "").strip() for x in candidates if (x.get("winner_source") or "").strip()),
                "",
            )
            winner_row = next(
                (x for x in candidates if (x.get("source") or "").strip() == winner_site),
                candidates[0],
            )
            dup_groups.append((satellite_channel_name(winner_row, sat_names), cid, winner_site, candidates))

        dup_groups.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
        recoverable_now = 0
        for _display_name, _cid, winner_site, candidates in dup_groups:
            winner_row = next((x for x in candidates if (x.get("source") or "").strip() == winner_site), None)
            winner_now = bool(winner_row and (winner_row.get("source_now_status") or "").strip() == "NOW")
            alt_now = any(
                (x.get("source") or "").strip() != winner_site
                and (x.get("source_now_status") or "").strip() == "NOW"
                for x in candidates
            )
            if (not winner_now) and alt_now:
                recoverable_now += 1

        dup_out = [
            "# Duplicate EPG ID Comparison",
            "",
            "⬅️ **[Back to main monitoring](mena-source-id-master.md)**  ",
            "➡️ **[Open zero-EPG channels](mena-source-zero-epg.md)**",
            "",
            "> Only channels/IDs available from **2 or more sources** are listed here.",
            "> Use this page to compare the real current programme from every candidate before changing the winner.",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Duplicate IDs | {len(dup_groups)} |",
            f"| Candidate source rows | {sum(len(x[3]) for x in dup_groups)} |",
            f"| Current winner gaps recoverable from another direct source | **{recoverable_now}** |",
            "",
        ]

        for display_name, cid, winner_site, candidates in dup_groups:
            base = next(
                (x for x in candidates if (x.get("source") or "").strip() == winner_site),
                candidates[0],
            )
            sats = satellite_positions(base, sat_index)
            sat_text = " · ".join(sats) if sats else "—"
            canonical = (base.get("receiver_canonical_id") or "").strip()

            dup_out += [
                f"## {esc(display_name)}",
                "",
                f"- **SAT:** {esc(sat_text)}",
                f"- **XMLTV ID:** `{esc(cid)}`",
                f"- **Canonical ID:** `{esc(canonical or cid)}`",
                f"- **Current suggested winner:** **{esc(source_label(winner_site))}**",
                f"- **Candidates:** {len(candidates)}",
                "",
                "| Choice | Source | Source status | Current programme | Description |",
                "|---|---|---|---|---|",
            ]

            winner_candidate = next(
                (x for x in candidates if (x.get("source") or "").strip() == winner_site),
                None,
            )
            winner_has_now = bool(
                winner_candidate and (winner_candidate.get("source_now_status") or "").strip() == "NOW"
            )
            now_alternatives = [
                x for x in candidates
                if (x.get("source") or "").strip() != winner_site
                and (x.get("source_now_status") or "").strip() == "NOW"
            ]
            recommended_site = winner_site
            if not winner_has_now and now_alternatives:
                recommended_site = sorted(
                    now_alternatives,
                    key=lambda x: source_label((x.get("source") or "").strip()).casefold()
                )[0].get("source") or ""

            ordered = sorted(
                candidates,
                key=lambda x: (
                    0 if (x.get("source") or "").strip() == recommended_site else
                    1 if (x.get("source") or "").strip() == winner_site else 2,
                    source_label((x.get("source") or "").strip()).casefold(),
                ),
            )
            for cand in ordered:
                site = (cand.get("source") or "").strip()
                if site == recommended_site and site != winner_site:
                    choice = "⭐ Recommended now"
                elif site == winner_site and site == recommended_site:
                    choice = "✅ Keep winner"
                elif site == winner_site:
                    choice = "Current winner"
                else:
                    choice = "Alternative"
                state = (cand.get("source_now_status") or "").strip() or "NOT_MONITORED"
                title = candidate_programme_text(cand)
                desc = (cand.get("source_now_desc") or "").strip() or "—"
                dup_out.append(
                    f"| {choice} | **{esc(source_label(site))}** | {esc(state)} | "
                    f"{esc(title)} | {esc(desc)} |"
                )

            dup_out += [
                "",
                "---",
                "",
            ]

        Path(args.duplicates_md).write_text("\n".join(dup_out) + "\n", encoding="utf-8")
        print(
            f"EPG_DUPLICATES_MD PASS duplicates={len(dup_groups)} "
            f"candidate_rows={sum(len(x[3]) for x in dup_groups)}"
        )

    if args.zero_epg_md:
        exact_zero = []
        zero_report_pending = False
        if args.zero_epg_report and Path(args.zero_epg_report).exists():
            try:
                report = json.loads(Path(args.zero_epg_report).read_text(encoding="utf-8"))
                exact_zero = list(report.get("rows") or [])
                zero_report_pending = bool(report.get("pending"))
            except Exception:
                exact_zero = []
                zero_report_pending = True

        by_source_id = {}
        for r in all_rows:
            raw_id = (r.get("xmltv_id") or "").strip()
            src = (r.get("source") or "").strip()
            if raw_id and src:
                by_source_id[(src, raw_id)] = r

        source_key_map = {
            "elcinema": "elcinema.com",
            "osn": "osn.com",
            "bein": "bein.com",
        }
        zero_rows = []
        for item in exact_zero:
            raw_id = (item.get("xmltv_id") or "").strip()
            src_key = (item.get("source_key") or "").strip()
            src = source_key_map.get(src_key, (item.get("source") or "").strip())
            base = by_source_id.get((src, raw_id))
            if base is None and src_key == "bein":
                base = by_source_id.get(("beinsports.com", raw_id))
            if base is None:
                base = {
                    "xmltv_id": raw_id,
                    "source": src,
                    "channel_name": (item.get("channel_name") or raw_id).strip(),
                    "receiver_canonical_id": raw_id,
                }
            row = dict(base)
            row["zero_reason"] = (item.get("reason") or "0 programmes returned by upstream after retry").strip()
            zero_rows.append(row)

        zero_rows.sort(key=lambda r: (
            satellite_channel_name(r, sat_names).casefold(),
            source_label((r.get("source") or "").strip()).casefold(),
        ))

        z = [
            "# Zero EPG Channels",
            "",
            "⬅️ **[Back to main monitoring](mena-source-id-master.md)**  ",
            "➡️ **[Open duplicate-ID comparison](mena-source-id-duplicates.md)**",
            "",
            "> These source/channel entries currently have **no usable direct EPG now**.",
            "> They are marked **SKIP** for coverage work so they do not block the healthy sources.",
            "> Keep them here for later investigation, remapping, or replacement by another source.",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Zero/empty source entries | {'PENDING' if zero_report_pending else len(zero_rows)} |",
            "",
            "| Action | SAT | Channel | Source | XMLTV ID | Canonical ID | State | Suggested next step |",
            "|---|---|---|---|---|---|---|---|",
        ]
        if zero_report_pending:
            z += [
                "| **PENDING** | — | — | — | — | — | Waiting for exact scraper report | Run direct source scrape first |",
            ]

        for r in zero_rows:
            src = source_label((r.get("source") or "").strip())
            raw_id = (r.get("xmltv_id") or "").strip()
            canonical = (r.get("receiver_canonical_id") or "").strip() or raw_id
            sats = satellite_positions(r, sat_index)
            sat_text = " · ".join(sats) if sats else "—"
            state = "0 PROGRAMMES"
            next_step = "Check alternative source / remap / provider site later"
            reason = (r.get("zero_reason") or "0 programmes returned by upstream after retry").strip()

            z.append(
                f"| **SKIP** | {esc(sat_text)} | **{esc(satellite_channel_name(r, sat_names))}** | "
                f"{esc(src)} | `{esc(raw_id)}` | `{esc(canonical)}` | {esc(state)} | "
                f"{esc(reason)} — {esc(next_step)} |"
            )

        Path(args.zero_epg_md).write_text("\n".join(z) + "\n", encoding="utf-8")
        print(f"EPG_ZERO_MD PASS rows={len(zero_rows)}")

    print(
        f"EPG_MASTER_MD PASS winners={len(rows)} on={counts.get('NOW',0)} "
        f"morocco={source_counts.get('Morocco Cloud',0)} sport24={source_counts.get('Sport24',0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
