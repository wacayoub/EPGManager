#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an official-first Arabic XMLTV channel catalogue from iptv-org/epg.

The script scans every *.channels.xml shipped by iptv-org/epg and selects
Arabic TV listings. Duplicate xmltv_id entries are resolved deterministically:
broadcaster-owned/official sites first, broad Arabic TV guides second, and
generic aggregators last. A very small per-ID override table is allowed only
for official-vs-official duplicates that passed a live 48h health comparison.
Morocco is intentionally excluded because the project already publishes a
higher-quality dedicated morocco.xml.gz feed. Radio services are excluded
because EPGManager's receiver-side mapping is TV-only.

SAT.TV is intentionally excluded from the MENA Cloud catalogue. It may remain
useful elsewhere, but MENA Cloud must not depend on it.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SITE_PRIORITY = {
    "shahid.mbc.net": 10,
    "rotana.net": 11,
    "roya-tv.com": 12,
    "aljazeera.com": 13,
    "artonline.tv": 14,
    "ayn.om": 15,
    "bein.com": 16,
    "beinsports.com": 17,
    "saudiatv.sa": 18,
    "sba.net.ae": 19,
    "dmi.gov.ae": 20,
    "osn.com": 25,
    "elcinema.com": 100,
    "epgshare01.online": 900,
}
EXCLUDED_SITES = {"sat.tv"}

# Verified 2026-09-14 by the parallel official duplicate-source health audit.
# In every row below, Shahid and OSN expose the same xmltv_id, but OSN had a
# complete 48h timetable with zero placeholder rows while Shahid had significant
# placeholder/all-day pollution. Keep this list explicit: no fuzzy override.
CHANNEL_SITE_OVERRIDES = {
    "AlHadath.sa@SD": "osn.com",
    "AlQuranAlKareemTV.sa@SD": "osn.com",
    "MBC3.ae@SD": "osn.com",
    "MBC5.ae@SD": "osn.com",
    "MBCDrama.ae@SD": "osn.com",
    "MBCIraq.iq@SD": "osn.com",
    "MBCMasr.eg@SD": "osn.com",
    "MBCMasr2.eg@SD": "osn.com",
    "MBCPlusDrama.sa@SD": "osn.com",
}

# Exact identity-name normalization. This is deliberately not fuzzy. Upstream
# uses the branding "MBC+ Drama"; the generic merge normalizer historically
# treated '+' as punctuation and collapsed it into the separate MBC Drama
# channel. Writing "Plus" preserves the real channel identity all the way into
# logical-key arbitration.
CHANNEL_NAME_OVERRIDES = {
    "MBCPlusDrama.sa@SD": "MBC Plus Drama",
}

# iptv-org's current OSN adapter exposes these exact broadcaster-owned channels
# with a valid site_id but an empty xmltv_id. Every target below is an XMLTV ID
# already published by EPGManager for that same named OSN linear service. This is
# an exact-name/site recovery table, never a fuzzy alias or invented identity.
BLANK_XMLTV_ID_OVERRIDES = {
    ("osn.com", "OSNtv One"): "OSNtv One.sa",
    ("osn.com", "OSNtv Showcase Classics"): "OSNtv Showcase Classics.sa",
    ("osn.com", "OSNtv iQIYI"): "OSNtv iQIYI.sa",
    ("osn.com", "OSNtv Movies Horror"): "OSNtv Movies Horror.sa",
    ("osn.com", "OSNtv Crime"): "OSNtv Crime.sa",
    ("osn.com", "OSNtv Documentary"): "OSNtv Documentary.sa",
    ("osn.com", "OSNtv Now"): "OSNtv Now.sa",
    ("osn.com", "OSNtv Movies Family"): "OSNtv Movies Family.sa",
    ("osn.com", "OSNtv Movies Comedy"): "OSNtv Movies Comedy.sa",
    ("osn.com", "OSNtv Pop Up"): "OSNtv Pop Up.sa",
}

MOROCCO_ID_RE = re.compile(r"\.ma(?:@|$)", re.I)
RADIO_ID_RE = re.compile(r"(?:^|[^a-z])(?:radio|fm)(?:[^a-z]|$)", re.I)
ARABIC_RADIO_WORDS = ("إذاعة", "راديو")


def site_score(site: str) -> tuple[int, str]:
    return (SITE_PRIORITY.get(site, 500), site)


def channel_site_score(cid: str, site: str) -> tuple[int, int, str]:
    wanted = CHANNEL_SITE_OVERRIDES.get(cid)
    if wanted and site == wanted:
        return (-1, SITE_PRIORITY.get(site, 500), site)
    return (0, SITE_PRIORITY.get(site, 500), site)


def is_arabic_channel(node: ET.Element) -> bool:
    lang = (node.get("lang") or "").strip().lower()
    return lang == "ar" or lang.startswith("ar-")


def channel_key(node: ET.Element) -> str:
    return (node.get("xmltv_id") or "").strip()


def is_radio_service(node: ET.Element, cid: str) -> bool:
    name = (node.text or "").strip()
    folded_id = re.sub(r"([a-z])([A-Z])", r"\1 \2", cid)
    if RADIO_ID_RE.search(folded_id):
        return True
    lower_name = name.casefold()
    if re.search(r"(?:^|\W)(?:radio|fm)(?:\W|$)", lower_name, re.I):
        return True
    return any(word in name for word in ARABIC_RADIO_WORDS)


def copy_channel(node: ET.Element) -> ET.Element:
    out = ET.Element("channel")
    for key in ("site", "site_id", "lang", "xmltv_id"):
        value = node.get(key)
        if value is not None:
            out.set(key, value)
    out.text = (node.text or "").strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epg-root", required=True)
    ap.add_argument("--output-channels", required=True)
    ap.add_argument("--output-manifest", required=True)
    ap.add_argument("--include-morocco", action="store_true")
    args = ap.parse_args()

    root = Path(args.epg_root)
    files = sorted(root.glob("sites/**/*.channels.xml"))
    if not files:
        raise SystemExit("No iptv-org *.channels.xml files found under %s" % root)

    winners: dict[str, tuple[tuple[int, int, str], ET.Element, str, str, bool]] = {}
    seen_ar = 0
    invalid = 0
    morocco_skipped = 0
    radio_skipped = 0
    excluded_site_skipped = 0
    blank_id_recovered = 0
    parse_errors = []

    for path in files:
        try:
            tree = ET.parse(str(path))
        except Exception as exc:
            parse_errors.append({"path": str(path), "error": str(exc)[:180]})
            continue
        for node in tree.getroot().findall("channel"):
            if not is_arabic_channel(node):
                continue
            seen_ar += 1
            site = (node.get("site") or path.parent.name or "unknown").strip()
            name = (node.text or "").strip()
            cid = channel_key(node)
            recovered_blank_id = False
            if not cid:
                recovered = BLANK_XMLTV_ID_OVERRIDES.get((site, name), "")
                if recovered:
                    cid = recovered
                    recovered_blank_id = True
                    blank_id_recovered += 1
            if not cid or not node.get("site_id") or not site:
                invalid += 1
                continue
            if site in EXCLUDED_SITES:
                excluded_site_skipped += 1
                continue
            if not args.include_morocco and MOROCCO_ID_RE.search(cid):
                morocco_skipped += 1
                continue
            if is_radio_service(node, cid):
                radio_skipped += 1
                continue
            score = channel_site_score(cid, site)
            current = winners.get(cid)
            candidate_node = copy_channel(node)
            if recovered_blank_id:
                candidate_node.set("xmltv_id", cid)
            candidate = (score, candidate_node, site, str(path.relative_to(root)), recovered_blank_id)
            if current is None or score < current[0]:
                winners[cid] = candidate

    channels = ET.Element("channels")
    selected = []
    source_counts = Counter()
    applied_overrides = []
    applied_name_overrides = []
    applied_blank_id_overrides = []
    for cid in sorted(winners, key=lambda x: x.casefold()):
        score, node, site, source_file, recovered_blank_id = winners[cid]
        forced_name = CHANNEL_NAME_OVERRIDES.get(cid)
        if forced_name:
            node.text = forced_name
            applied_name_overrides.append(cid)
        channels.append(node)
        source_counts[site] += 1
        override_site = CHANNEL_SITE_OVERRIDES.get(cid, "")
        if override_site and site == override_site:
            applied_overrides.append(cid)
        if recovered_blank_id:
            applied_blank_id_overrides.append(cid)
        selected.append({
            "xmltv_id": cid,
            "name": (node.text or cid).strip(),
            "site": site,
            "site_id": node.get("site_id") or "",
            "priority": SITE_PRIORITY.get(site, 500),
            "override_site": override_site,
            "name_override": CHANNEL_NAME_OVERRIDES.get(cid, ""),
            "blank_id_recovered": recovered_blank_id,
            "source_file": source_file,
        })

    if len(selected) < 25:
        raise SystemExit("Abnormally small Arabic TV catalogue: %d channels" % len(selected))

    missing_overrides = sorted(set(CHANNEL_SITE_OVERRIDES) - set(applied_overrides), key=str.casefold)
    if missing_overrides:
        raise SystemExit("Verified source override missing from current upstream catalogue: %s" % ", ".join(missing_overrides))
    missing_name_overrides = sorted(set(CHANNEL_NAME_OVERRIDES) - set(applied_name_overrides), key=str.casefold)
    if missing_name_overrides:
        raise SystemExit("Verified name override missing from current upstream catalogue: %s" % ", ".join(missing_name_overrides))

    # Future-proof guard: the exact OSN services must resolve to osn.com even if
    # upstream later fills xmltv_id itself. That future change should stop needing
    # blank-ID recovery without turning a healthy catalogue red.
    selected_by_id = {row["xmltv_id"]: row for row in selected}
    missing_osn_identity = []
    for (site, name), cid in BLANK_XMLTV_ID_OVERRIDES.items():
        row = selected_by_id.get(cid)
        if not row or row.get("site") != site or row.get("name") != name:
            missing_osn_identity.append("%s=%s" % (name, cid))
    if missing_osn_identity:
        raise SystemExit("Verified OSN official identity recovery missing: %s" % ", ".join(missing_osn_identity))

    # Regression guard: MBC Plus Drama must remain a distinct identity string;
    # otherwise the merge layer would collapse it with MBC Drama again.
    plus_row = next((x for x in selected if x["xmltv_id"] == "MBCPlusDrama.sa@SD"), None)
    if plus_row and "plus" not in plus_row["name"].casefold():
        raise SystemExit("MBC Plus Drama identity regression: %s" % plus_row["name"])

    out_xml = Path(args.output_channels)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(channels, space="  ")
    out_xml.write_bytes(ET.tostring(channels, encoding="utf-8", xml_declaration=True))

    manifest = {
        "schema": 6,
        "strategy": "official-first-all-arabic-tv-no-sattv-with-verified-health-and-osn-id-recovery",
        "source_project": "iptv-org/epg",
        "input_channel_files": len(files),
        "arabic_rows_seen": seen_ar,
        "invalid_rows_skipped": invalid,
        "morocco_rows_skipped": morocco_skipped,
        "radio_rows_skipped": radio_skipped,
        "excluded_site_rows_skipped": excluded_site_skipped,
        "excluded_sites": sorted(EXCLUDED_SITES),
        "verified_site_overrides": dict(sorted(CHANNEL_SITE_OVERRIDES.items())),
        "applied_site_overrides": sorted(applied_overrides, key=str.casefold),
        "verified_name_overrides": dict(sorted(CHANNEL_NAME_OVERRIDES.items())),
        "applied_name_overrides": sorted(applied_name_overrides, key=str.casefold),
        "verified_blank_id_overrides": {
            "%s|%s" % key: value for key, value in sorted(BLANK_XMLTV_ID_OVERRIDES.items())
        },
        "blank_id_rows_recovered": blank_id_recovered,
        "applied_blank_id_overrides": sorted(applied_blank_id_overrides, key=str.casefold),
        "unique_channels": len(selected),
        "morocco_excluded": not args.include_morocco,
        "tv_only": True,
        "source_counts": dict(sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "parse_errors": parse_errors[:20],
        "channels": selected,
    }
    Path(args.output_manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("MENA Arabic TV catalogue: %d unique channels from %d source sites" %
          (len(selected), len(source_counts)))
    print("  skipped: Morocco=%d radio=%d excluded-site=%d" %
          (morocco_skipped, radio_skipped, excluded_site_skipped))
    print("  verified source overrides applied=%d" % len(applied_overrides))
    print("  verified name overrides applied=%d" % len(applied_name_overrides))
    print("  OSN exact blank-ID recoveries applied=%d" % len(applied_blank_id_overrides))
    for site, count in source_counts.most_common(20):
        print("  %-28s %4d" % (site, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
