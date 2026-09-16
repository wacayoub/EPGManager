#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize receiver-facing beIN XMLTV IDs to one stable canonical namespace.

Known raw/legacy aliases for the same real service are grouped under one
canonical receiver ID. Unsupported legacy foreign-feed IDs are quarantined
before the strict canonical provider gate so they cannot poison the MENA feed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

ALIASES = {
    "beIN.Drama.qa": {"beIN.Drama.qa", "beIN Drama.eg", "beINDrama1.qa@SD"},
    "beIN.Movies.Premiere.qa": {"beIN.Movies.Premiere.qa", "BEIN MOVIES PREMIERE.eg", "beINMovies1Premiere.qa@SD", "MOVIES1 PREMIERE DIGITAL.qa"},
    "beIN.Movies.Action.qa": {"beIN.Movies.Action.qa", "BEIN MOVIES ACTION.eg", "beINMovies2Action.qa@SD", "MOVIES2 ACTION DIGITAL.qa"},
    "beIN.Movies.Drama.qa": {"beIN.Movies.Drama.qa", "BEIN MOVIES DRAMA.eg", "beINMovies3Drama.qa@SD", "MOVIES3 DRAMA DIGITAL.qa"},
    "beIN.Movies.Family.qa": {"beIN.Movies.Family.qa", "BEIN MOVIES FAMILY.eg", "beINMovies4Family.qa@SD", "MOVIES4 FAMILY DIGITAL.qa"},
    "beIN.Series.1.qa": {"beIN.Series.1.qa", "BeIn Series HD 1.eg", "beINSeries1.qa@SD", "SERIES1 DIGITAL.qa"},
    "beIN.Series.2.qa": {"beIN.Series.2.qa", "beIN Series HD 2.eg", "beINSeries2.qa@SD", "SERIES2 DIGITAL.qa"},
    "beIN.Sports.1.qa": {"beIN.Sports.1.qa", "beIN SPORTS 1.qa", "beINSports1.qa@MENA"},
    "beIN.Sports.2.qa": {"beIN.Sports.2.qa", "beINSports2.qa@MENA"},
    "beIN.Sports.3.qa": {"beIN.Sports.3.qa", "beINSports3.qa@MENA"},
    "beIN.Sports.4.qa": {"beIN.Sports.4.qa", "beINSports4.qa@MENA"},
    "beIN.Sports.5.qa": {"beIN.Sports.5.qa", "beIN SPORTS 5.qa", "beINSports5.qa@MENA"},
    "beIN.Sports.6.qa": {"beIN.Sports.6.qa", "beIN SPORTS 6.qa", "beINSports6.qa@MENA"},
    "beIN.Sports.7.qa": {"beIN.Sports.7.qa", "beINSports7.qa@MENA"},
    "beIN.Sports.8.qa": {"beIN.Sports.8.qa", "beIN SPORTS 8.qa", "beINSports8.qa@MENA"},
    "beIN.Sports.9.qa": {"beIN.Sports.9.qa", "beINSPORTS9.qa", "beINSports9.qa@MENA"},
    "beIN.Sports.EN1.qa": {"beIN.Sports.EN1.qa", "beIN SPORTS EN 1.qa"},
    "beIN.Sports.EN2.qa": {"beIN.Sports.EN2.qa", "beIN SPORTS EN 2.qa"},
    "beIN.Sports.FTA.qa": {"beIN.Sports.FTA.qa", "bein SPORTS FTA DIGITAL.qa"},
    "beIN.Sports.qa": {"beIN.Sports.qa", "beIN SPORTS.qa"},
    "beIN.4K.qa": {"beIN.4K.qa", "beIN4K.qa@SD", "4k DIGITAL.qa"},
    "beIN.Sports.News.qa": {"beIN.Sports.News.qa", "beIN SPORTS NEWS.qa", "NEWS DIGITAL.qa"},
    "beIN.Sports.MAX1.qa": {"beIN.Sports.MAX1.qa", "beIN SPORTS MAX1 DIGITAL.qa", "beIN SPORTS MAX 1.qa", "beINSportsMax1.qa@MENA"},
    "beIN.Sports.MAX2.qa": {"beIN.Sports.MAX2.qa", "beIN SPORTS MAX 2.qa", "beINSportsMax2.qa@MENA"},
    "beIN.Sports.MAX3.qa": {"beIN.Sports.MAX3.qa", "beIN SPORTS MAX 3.qa", "beINSportsMax3.qa@MENA"},
    "beIN.Sports.MAX4.qa": {"beIN.Sports.MAX4.qa", "beIN SPORTS MAX 4.qa", "beINSportsMax4.qa@MENA"},
    "beIN.Sports.MAX5.qa": {"beIN.Sports.MAX5.qa", "beIN SPORTS MAX 5.qa", "beINSportsMax5.qa@MENA", "NEW_beIN-SPORTS-MAX-05_AR.bein"},
    "beIN.Sports.MAX6.qa": {"beIN.Sports.MAX6.qa", "beIN SPORTS MAX 6.qa", "beINSportsMax6.qa@MENA", "NEW_beIN-SPORTS-MAX-06_AR.bein"},
    "beIN.Sports.XTRA1.qa": {"beIN.Sports.XTRA1.qa", "beINSportsXtra1.qa@SD"},
    "beIN.Sports.XTRA2.qa": {"beIN.Sports.XTRA2.qa", "beINSportsXtra2.qa@SD"},
    "beIN.Sports.XTRA3.qa": {"beIN.Sports.XTRA3.qa", "beINSPORTSXTRA3.qa"},
    "beIN.Sports.XTRA4.qa": {"beIN.Sports.XTRA4.qa", "beIN SPORTS XTRA 4.qa"},
    "beIN.Sports.XTRA5.qa": {"beIN.Sports.XTRA5.qa", "beIN SPORTS XTRA 5.qa"},
    "beIN.Sports.XTRA6.qa": {"beIN.Sports.XTRA6.qa", "beIN SPORTS XTRA 6.qa"},
    "beIN.Sports.XTRA7.qa": {"beIN.Sports.XTRA7.qa", "beIN SPORTS XTRA 7.qa"},
    "beIN.Sports.XTRA8.qa": {"beIN.Sports.XTRA8.qa", "beIN SPORTS XTRA 8.qa"},
    "beIN.Sports.XTRA9.qa": {"beIN.Sports.XTRA9.qa", "beIN SPORTS XTRA 9.qa"},
    "beIN.Gourmet.qa": {"beIN.Gourmet.qa", "beINGourmet.qa@SD"},
}

RAW_TO_CANON = {raw: canon for canon, raws in ALIASES.items() for raw in raws}
RENAME = RAW_TO_CANON

# These are upstream legacy foreign-feed IDs, not receiver-facing MENA services.
# Do not alias them onto Arabic beIN Sports because that could replace a good
# MENA timeline with a French one when the legacy feed has more events.
DROP_ONLY_EXACT = {
    "beIN_SPORTS1_FRENCH_Digital_Mono_AR.bein",
}
DROP_ONLY_PATTERNS = (
    re.compile(r"^beIN[_ .-]*SPORTS\d+[_ .-]*FRENCH[_ .-].*\.bein$", re.I),
)

def is_drop_only(cid: str) -> bool:
    return cid in DROP_ONLY_EXACT or any(rx.match(cid) for rx in DROP_ONLY_PATTERNS)

OPTIONAL_EVENT_IDS = (
    {f"beIN.Sports.MAX{n}.qa" for n in range(1, 7)} |
    {f"beIN.Sports.XTRA{n}.qa" for n in range(1, 10)} |
    {"beIN.Gourmet.qa"}
)

DISPLAY = {
    "beIN.Drama.qa": "beIN Drama",
    "beIN.Movies.Premiere.qa": "beIN Movies Premiere",
    "beIN.Movies.Action.qa": "beIN Movies Action",
    "beIN.Movies.Drama.qa": "beIN Movies Drama",
    "beIN.Movies.Family.qa": "beIN Movies Family",
    "beIN.Series.1.qa": "beIN Series 1",
    "beIN.Series.2.qa": "beIN Series 2",
    "beIN.Sports.FTA.qa": "beIN Sports FTA",
    "beIN.Sports.qa": "beIN Sports",
    "beIN.4K.qa": "beIN 4K",
    "beIN.Sports.News.qa": "beIN Sports News",
    "beIN.Gourmet.qa": "beIN Gourmet",
}
for n in range(1, 10):
    DISPLAY[f"beIN.Sports.{n}.qa"] = f"beIN Sports {n}"
for n in range(1, 3):
    DISPLAY[f"beIN.Sports.EN{n}.qa"] = f"beIN Sports EN {n}"
for n in range(1, 7):
    DISPLAY[f"beIN.Sports.MAX{n}.qa"] = f"beIN Sports MAX {n}"
for n in range(1, 10):
    DISPLAY[f"beIN.Sports.XTRA{n}.qa"] = f"beIN Sports XTRA {n}"


def read_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def display_name(c: ET.Element) -> str:
    n = c.find("display-name")
    return ((n.text if n is not None else "") or c.get("id") or "").strip()


def choose_aliases(root: ET.Element):
    channels = {(c.get("id") or "").strip(): c for c in root.findall("channel")}
    counts = Counter((p.get("channel") or "").strip() for p in root.findall("programme"))
    chosen = {}
    dropped = {cid for cid in channels if is_drop_only(cid)}
    for canon, aliases in ALIASES.items():
        present = [raw for raw in aliases if raw in channels]
        if not present:
            continue
        present.sort(key=lambda raw: (-counts[raw], 0 if raw == canon else 1, raw.casefold()))
        winner = present[0]
        chosen[winner] = canon
        dropped.update(present[1:])
    return chosen, dropped


def normalize_file(path: Path):
    root = read_root(path)
    channels = root.findall("channel")
    programmes = root.findall("programme")
    present = {(c.get("id") or "").strip() for c in channels}
    if not (present & set(RAW_TO_CANON)) and not any(is_drop_only(cid) for cid in present):
        return None

    chosen, dropped = choose_aliases(root)
    out = ET.Element("tv", dict(root.attrib))
    kept_ids = set()
    renamed = {}

    for c in channels:
        old = (c.get("id") or "").strip()
        if old in dropped:
            continue
        new = chosen.get(old, old)
        if old != new:
            renamed[old] = new
        c.set("id", new)
        if new in DISPLAY:
            names = c.findall("display-name")
            if names:
                names[0].text = DISPLAY[new]
            else:
                ET.SubElement(c, "display-name").text = DISPLAY[new]
        if new in kept_ids:
            raise RuntimeError(f"duplicate normalized channel {new} in {path.name}")
        kept_ids.add(new)
        out.append(c)

    kept_programmes = 0
    for p in programmes:
        old = (p.get("channel") or "").strip()
        if old in dropped:
            continue
        new = chosen.get(old, old)
        if new not in kept_ids:
            continue
        p.set("channel", new)
        out.append(p)
        kept_programmes += 1

    ET.indent(out, space="  ")
    xml = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    gz = gzip.compress(xml, compresslevel=9, mtime=0)
    path.write_bytes(gz)

    txt = path.with_suffix("").with_suffix(".txt")
    if txt.exists():
        rows = sorted(((c.get("id") or "", display_name(c)) for c in out.findall("channel")), key=lambda x: x[0].casefold())
        txt.write_text("".join(f"{cid}|{name}\n" for cid, name in rows), encoding="utf-8")

    return {
        "file": path.name,
        "renamed": renamed,
        "removed": sorted(dropped, key=str.casefold),
        "channels": len(out.findall("channel")),
        "programmes": kept_programmes,
        "size_bytes": len(gz),
        "sha256": hashlib.sha256(gz).hexdigest(),
    }


def update_metadata(base: Path, reports):
    by_stem = {r["file"][:-7]: r for r in reports}
    shards = base / "shards.json"
    if shards.exists():
        data = json.loads(shards.read_text(encoding="utf-8"))
        for stem, r in by_stem.items():
            row = (data.get("shards") or {}).get(stem)
            if not row:
                continue
            for key in ("channels", "programmes", "size_bytes", "sha256"):
                row[key] = r[key]
            if stem == "provider-bein":
                row["receiver_id_namespace"] = "beIN.*.qa"
                row["receiver_policy"] = "definitive canonical IDs; richest available alias wins; unsupported foreign legacy feeds quarantined"
                row["canonical_ids"] = sorted(ALIASES, key=str.casefold)
        shards.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = base / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["bein_receiver_ids_normalized"] = True
        data["bein_receiver_namespace"] = "beIN.*.qa"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    base = Path(args.dir)
    reports = []
    for path in sorted(base.glob("*.xml.gz")):
        report = normalize_file(path)
        if report:
            reports.append(report)
    update_metadata(base, reports)

    provider = base / "provider-bein.xml.gz"
    root = read_root(provider)
    ids = [(c.get("id") or "").strip() for c in root.findall("channel")]
    allowed = set(ALIASES)
    required_core = allowed - OPTIONAL_EVENT_IDS
    missing = sorted(required_core - set(ids))
    extra = sorted(set(ids) - allowed)
    if missing or extra:
        raise SystemExit(f"provider-bein canonical set mismatch: missing_core={missing} extra={extra}")
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate canonical beIN IDs")
    event_ids = {(p.get("channel") or "").strip() for p in root.findall("programme")}
    if not event_ids.issubset(set(ids)):
        raise SystemExit("orphan beIN programme channel references")

    dropped = sorted({x for r in reports for x in r["removed"]}, key=str.casefold)
    report = {
        "schema": 3,
        "policy": "one real service -> one stable canonical receiver ID; richest available alias wins; unsupported foreign legacy feeds quarantined",
        "provider_channels": len(ids),
        "provider_programmes": len(root.findall("programme")),
        "canonical_ids": ids,
        "dropped_duplicate_aliases": dropped,
        "files": reports,
    }
    (base / "bein-receiver-id-normalization.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "BEIN RECEIVER ID NORMALIZATION\n",
        f"channels={len(ids)} programmes={report['provider_programmes']}\n",
        "policy=canonical MENA IDs; unsupported foreign legacy feeds quarantined\n\n",
    ]
    lines.extend(cid + "\n" for cid in ids)
    lines.append("\nDROPPED / QUARANTINED LEGACY IDS\n")
    lines.extend(cid + "\n" for cid in dropped)
    (base / "bein-receiver-id-normalization.txt").write_text("".join(lines), encoding="utf-8")
    print(f"PASS beIN normalized channels={len(ids)} programmes={report['provider_programmes']} quarantined={len(dropped)}")


if __name__ == "__main__":
    main()
