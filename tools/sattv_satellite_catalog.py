#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

SATELLITES = {
    "1": ("nilesat-7w", "Eutelsat/Nilesat 7/8W"),
    "2": ("hotbird-13e", "HOTBIRD 13E"),
}


def norm_name(s: str) -> str:
    s = (s or "").casefold()
    s = re.sub(r"&amp;", "and", s)
    s = re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", s)
    return s


def stable_id(sat_id: str, name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", ".", name).strip(".")[:48] or "channel"
    digest = hashlib.sha1((sat_id + "|" + name).encode("utf-8")).hexdigest()[:8]
    return f"SatTV.{sat_id}.{slug}.{digest}"


def clone_channel(node: ET.Element, sat_id: str) -> ET.Element:
    out = ET.Element("channel")
    for key in ("site", "site_id", "lang", "xmltv_id"):
        val = node.get(key)
        if val is not None:
            out.set(key, val)
    name = (node.text or "").strip()
    if not (out.get("xmltv_id") or "").strip():
        out.set("xmltv_id", stable_id(sat_id, name))
    out.text = name
    return out


def write_catalog(nodes: list[ET.Element], path: Path) -> None:
    root = ET.Element("channels")
    for node in nodes:
        root.append(node)
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    src = ET.parse(args.input).getroot()
    grouped: dict[str, dict[str, ET.Element]] = {sid: {} for sid in SATELLITES}
    dupes = {sid: 0 for sid in SATELLITES}
    invalid = 0

    for node in src.findall("channel"):
        site_id = (node.get("site_id") or "").strip()
        parts = site_id.split("#", 2)
        if len(parts) != 3 or parts[0] not in SATELLITES:
            continue
        sat_id = parts[0]
        name = (node.text or parts[2] or "").strip()
        if not name:
            invalid += 1
            continue
        copied = clone_channel(node, sat_id)
        cid = (copied.get("xmltv_id") or "").strip()
        key = cid.casefold() if cid else norm_name(name)
        if key in grouped[sat_id]:
            dupes[sat_id] += 1
            continue
        grouped[sat_id][key] = copied

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "source": "sat.tv", "input": str(args.input), "invalid": invalid, "satellites": {}}

    for sat_id, (basename, label) in SATELLITES.items():
        nodes = sorted(grouped[sat_id].values(), key=lambda n: ((n.text or "").casefold(), n.get("site_id") or ""))
        if len(nodes) < 10:
            raise SystemExit(f"Abnormally small {label} catalogue: {len(nodes)} channels")
        write_catalog(nodes, out_dir / f"{basename}.channels.xml")
        channels = []
        for n in nodes:
            channels.append({
                "xmltv_id": n.get("xmltv_id") or "",
                "name": (n.text or "").strip(),
                "site": n.get("site") or "sat.tv",
                "site_id": n.get("site_id") or "",
                "lang": n.get("lang") or "ar",
            })
        sat_manifest = {
            "satellite_id": sat_id,
            "satellite": label,
            "unique_channels": len(channels),
            "duplicate_rows_skipped": dupes[sat_id],
            "channels": channels,
        }
        (out_dir / f"{basename}.catalog.json").write_text(
            json.dumps(sat_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest["satellites"][basename] = {
            "satellite_id": sat_id,
            "label": label,
            "channels": len(channels),
            "duplicate_rows_skipped": dupes[sat_id],
        }
        print(f"{label}: {len(channels)} unique channels ({dupes[sat_id]} duplicates skipped)")

    (out_dir / "catalog-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
