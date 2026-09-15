#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize receiver-facing beIN XMLTV IDs to one stable canonical namespace.

This is intentionally receiver-facing only. Programme content is preserved; only
channel IDs/display names are normalized. Known weaker duplicate identities are
removed before rename so one real service maps to one receiver XMLTV ID.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

# Current post-cleanup IDs -> definitive receiver IDs.
RENAME = {
    "beIN Drama.eg": "beIN.Drama.qa",
    "BEIN MOVIES PREMIERE.eg": "beIN.Movies.Premiere.qa",
    "BEIN MOVIES ACTION.eg": "beIN.Movies.Action.qa",
    "BEIN MOVIES DRAMA.eg": "beIN.Movies.Drama.qa",
    "BEIN MOVIES FAMILY.eg": "beIN.Movies.Family.qa",
    "BeIn Series HD 1.eg": "beIN.Series.1.qa",
    "beIN Series HD 2.eg": "beIN.Series.2.qa",
    "beIN SPORTS 1.qa": "beIN.Sports.1.qa",
    "beINSports2.qa@MENA": "beIN.Sports.2.qa",
    "beINSports3.qa@MENA": "beIN.Sports.3.qa",
    "beINSports4.qa@MENA": "beIN.Sports.4.qa",
    "beIN SPORTS 5.qa": "beIN.Sports.5.qa",
    "beINSports6.qa@MENA": "beIN.Sports.6.qa",
    "beINSports7.qa@MENA": "beIN.Sports.7.qa",
    "beINSports8.qa@MENA": "beIN.Sports.8.qa",
    "beINSports9.qa@MENA": "beIN.Sports.9.qa",
    "beIN SPORTS EN 1.qa": "beIN.Sports.EN1.qa",
    "beIN SPORTS EN 2.qa": "beIN.Sports.EN2.qa",
    "bein SPORTS FTA DIGITAL.qa": "beIN.Sports.FTA.qa",
    "beIN SPORTS.qa": "beIN.Sports.qa",
    "beIN4K.qa@SD": "beIN.4K.qa",
    "beIN SPORTS NEWS.qa": "beIN.Sports.News.qa",
    "beIN SPORTS MAX1 DIGITAL.qa": "beIN.Sports.MAX1.qa",
    "beIN SPORTS MAX 2.qa": "beIN.Sports.MAX2.qa",
    "beIN SPORTS MAX 3.qa": "beIN.Sports.MAX3.qa",
    "beIN SPORTS MAX 4.qa": "beIN.Sports.MAX4.qa",
    "beIN SPORTS MAX 5.qa": "beIN.Sports.MAX5.qa",
    "beIN SPORTS MAX 6.qa": "beIN.Sports.MAX6.qa",
    "beINSportsXtra1.qa@SD": "beIN.Sports.XTRA1.qa",
    "beINSportsXtra2.qa@SD": "beIN.Sports.XTRA2.qa",
    "beINSPORTSXTRA3.qa": "beIN.Sports.XTRA3.qa",
    "beIN SPORTS XTRA 4.qa": "beIN.Sports.XTRA4.qa",
    "beIN SPORTS XTRA 5.qa": "beIN.Sports.XTRA5.qa",
    "beIN SPORTS XTRA 6.qa": "beIN.Sports.XTRA6.qa",
    "beIN SPORTS XTRA 7.qa": "beIN.Sports.XTRA7.qa",
    "beIN SPORTS XTRA 8.qa": "beIN.Sports.XTRA8.qa",
    "beIN SPORTS XTRA 9.qa": "beIN.Sports.XTRA9.qa",
}

# Same real service, weaker/incomplete timeline than the selected canonical source.
DROP = {
    "beINSports5.qa@MENA",   # 5 events / 12.2h vs beIN SPORTS 5.qa 17 / 34.4h
    "beINSeries2.qa@SD",     # 29 events / 24h vs beIN Series HD 2.eg 41 / 34.5h
}

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


def disp(c: ET.Element) -> str:
    n = c.find("display-name")
    return ((n.text if n is not None else "") or c.get("id") or "").strip()


def normalize_file(path: Path):
    root = read_root(path)
    channels = root.findall("channel")
    programmes = root.findall("programme")
    present = {(c.get("id") or "").strip() for c in channels}
    relevant = bool(present & (set(RENAME) | DROP))
    if not relevant:
        return None

    removed = sorted(present & DROP, key=str.casefold)
    rename_used = {k: v for k, v in RENAME.items() if k in present and k not in DROP}

    # Reject collisions before touching the file.
    targets = list(rename_used.values())
    if len(targets) != len(set(targets)):
        raise RuntimeError(f"rename target collision in {path.name}")
    existing_unrenamed = present - set(rename_used) - DROP
    collisions = existing_unrenamed & set(targets)
    if collisions:
        raise RuntimeError(f"existing canonical collision in {path.name}: {sorted(collisions)}")

    out = ET.Element("tv", dict(root.attrib))
    kept_ids = set()
    channel_by_old = {}
    for c in channels:
        old = (c.get("id") or "").strip()
        channel_by_old[old] = c
        if old in DROP:
            continue
        new = rename_used.get(old, old)
        c.set("id", new)
        if new in DISPLAY:
            names = c.findall("display-name")
            if names:
                names[0].text = DISPLAY[new]
            else:
                ET.SubElement(c, "display-name").text = DISPLAY[new]
        kept_ids.add(new)
        out.append(c)

    kept_programmes = 0
    for p in programmes:
        old = (p.get("channel") or "").strip()
        if old in DROP:
            continue
        new = rename_used.get(old, old)
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
        rows = []
        for c in out.findall("channel"):
            cid = (c.get("id") or "").strip()
            rows.append((cid, disp(c)))
        rows.sort(key=lambda x: x[0].casefold())
        txt.write_text("".join(f"{cid}|{name}\n" for cid, name in rows), encoding="utf-8")

    return {
        "file": path.name,
        "renamed": rename_used,
        "removed": removed,
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
            row["channels"] = r["channels"]
            row["programmes"] = r["programmes"]
            row["size_bytes"] = r["size_bytes"]
            row["sha256"] = r["sha256"]
            if stem == "provider-bein":
                row["receiver_id_namespace"] = "beIN.*.qa"
                row["receiver_policy"] = "definitive normalized canonical IDs; no legacy aliases"
                row["canonical_ids"] = sorted(RENAME.values(), key=str.casefold)
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
    for p in sorted(base.glob("*.xml.gz")):
        r = normalize_file(p)
        if r:
            reports.append(r)
    update_metadata(base, reports)

    # Hard validation of definitive provider-bein identity set.
    provider = base / "provider-bein.xml.gz"
    root = read_root(provider)
    ids = [(c.get("id") or "").strip() for c in root.findall("channel")]
    expected = set(RENAME.values())
    if set(ids) != expected:
        missing = sorted(expected - set(ids))
        extra = sorted(set(ids) - expected)
        raise SystemExit(f"provider-bein canonical set mismatch: missing={missing} extra={extra}")
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate canonical beIN IDs")
    event_ids = {(p.get("channel") or "").strip() for p in root.findall("programme")}
    if not event_ids.issubset(set(ids)):
        raise SystemExit("orphan beIN programme channel references")

    report = {
        "schema": 1,
        "policy": "definitive receiver beIN namespace; one service one ID; weaker duplicate feeds removed",
        "provider_channels": len(ids),
        "provider_programmes": len(root.findall("programme")),
        "canonical_ids": ids,
        "dropped_duplicates": sorted(DROP),
        "files": reports,
    }
    (base / "bein-receiver-id-normalization.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "BEIN RECEIVER ID NORMALIZATION\n",
        f"channels={len(ids)} programmes={report['provider_programmes']}\n",
        "policy=one real service -> one stable canonical receiver ID\n\n",
    ]
    for cid in ids:
        lines.append(cid + "\n")
    lines.append("\nDROPPED DUPLICATE IDENTITIES\n")
    for cid in sorted(DROP):
        lines.append(cid + "\n")
    (base / "bein-receiver-id-normalization.txt").write_text("".join(lines), encoding="utf-8")
    print(f"PASS beIN normalized channels={len(ids)} programmes={report['provider_programmes']}")


if __name__ == "__main__":
    main()
