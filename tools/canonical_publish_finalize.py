#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build one coherent canonical receiver publication before it reaches mena-data.

This is the only production boundary for namespace changes.  It runs the
reviewed cleanup/normalizers, rebuilds the legacy MENA/Premium aggregates from
the canonical combined feed, refreshes every byte-derived manifest field and
emits the raw/source-ID -> canonical-ID bridge used by the next LKG selection.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET

import bein_receiver_id_normalize as bein
import canonical_receiver_cleanup as cleanup


AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
PREMIUM_PREFIXES = ("beIN.", "OSN.")
PROVIDER_STEMS = {
    "alkass": "provider-alkass",
    "bein": "provider-bein",
    "osn": "provider-osn",
    "mbc": "provider-mbc",
    "rotana": "provider-rotana",
    "adm": "provider-adm",
    "dmi": "provider-dmi",
    "art": "provider-art",
    "ssc": "provider-ssc",
    "starz": "provider-starz",
}
BEIN_DROP_BRIDGE = {
    "beINSports5.qa@MENA": "beIN.Sports.5.qa",
    "beINSeries2.qa@SD": "beIN.Series.2.qa",
}


def read_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def clone(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def display_name(channel: ET.Element) -> str:
    for node in channel.findall("display-name"):
        value = (node.text or "").strip()
        if value:
            return value
    return (channel.get("id") or "").strip()


def title(programme: ET.Element) -> str:
    node = programme.find("title")
    return ((node.text or "").strip() if node is not None else "")


def parse_dt(value: str):
    match = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", (value or "").strip())
    if not match:
        return None
    digits, offset = match.groups()
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits, fmt)
        if offset == "Z" or not offset:
            return dt.replace(tzinfo=timezone.utc)
        return datetime.strptime(digits + offset, fmt + "%z").astimezone(timezone.utc)
    except ValueError:
        return None


def language_of(value: str) -> str:
    ar = len(AR_RE.findall(value or ""))
    latin = len(LATIN_RE.findall(value or ""))
    if ar >= 2 and ar >= latin * 0.5:
        return "ar"
    if latin >= 2:
        return "en"
    return "other"


def canonical_root(root: ET.Element, ids=None, generator=None) -> ET.Element:
    allowed = set(ids) if ids is not None else None
    channels = {}
    programmes = defaultdict(list)
    for channel in root.findall("channel"):
        cid = (channel.get("id") or "").strip()
        if cid and (allowed is None or cid in allowed):
            if cid in channels:
                raise RuntimeError("duplicate channel ID: %s" % cid)
            channels[cid] = channel
    for programme in root.findall("programme"):
        cid = (programme.get("channel") or "").strip()
        if cid in channels:
            programmes[cid].append(programme)

    attrs = dict(root.attrib)
    if generator:
        attrs["generator-info-name"] = generator
    out = ET.Element("tv", attrs)
    order = sorted(channels, key=lambda value: (value.casefold(), value))
    for cid in order:
        out.append(clone(channels[cid]))
    seen = set()
    for cid in order:
        rows = sorted(
            programmes.get(cid, []),
            key=lambda p: (
                (p.get("start") or "").strip(),
                (p.get("stop") or "").strip(),
                title(p).casefold(),
            ),
        )
        for programme in rows:
            key = (cid, (programme.get("start") or "").strip(), (programme.get("stop") or "").strip())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            cp = clone(programme)
            cp.set("channel", cid)
            out.append(cp)
    return out


def write_root(path: Path, root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    data = gzip.compress(xml, compresslevel=9, mtime=0)
    path.write_bytes(data)
    names = [
        ((c.get("id") or "").strip(), display_name(c))
        for c in root.findall("channel")
        if (c.get("id") or "").strip()
    ]
    path.with_suffix("").with_suffix(".txt").write_text(
        "".join("%s|%s\n" % row for row in names), encoding="utf-8"
    )
    return data


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def profile(path: Path, reference: datetime) -> dict:
    data = path.read_bytes()
    raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    root = ET.fromstring(raw)
    channels = [(c.get("id") or "").strip() for c in root.findall("channel")]
    programmes = root.findall("programme")
    event_ids = {(p.get("channel") or "").strip() for p in programmes}
    stops = defaultdict(list)
    ar_titles = en_titles = ar_descs = 0
    for programme in programmes:
        cid = (programme.get("channel") or "").strip()
        stop = parse_dt(programme.get("stop") or programme.get("start") or "")
        if stop:
            stops[cid].append(stop)
        title_node = programme.find("title")
        desc_node = programme.find("desc")
        title_lang = language_of((title_node.text or "") if title_node is not None else "")
        desc_lang = language_of((desc_node.text or "") if desc_node is not None else "")
        ar_titles += title_lang == "ar"
        en_titles += title_lang == "en"
        ar_descs += desc_lang == "ar"
    horizons = [max(0.0, (max(stops[cid]) - reference).total_seconds() / 3600.0) for cid in channels if stops.get(cid)]
    count = float(len(programmes) or 1)
    return {
        "channels": len(channels),
        "programmes": len(programmes),
        "size_bytes": len(data),
        "xml_size_bytes": len(raw),
        "sha256": hashlib.sha256(data).hexdigest(),
        "arabic_title_ratio": round(ar_titles / count, 4),
        "english_title_ratio": round(en_titles / count, 4),
        "arabic_desc_ratio": round(ar_descs / count, 4),
        "zero_programme_channels": len(set(channels) - event_ids),
        "coverage": {
            "reference_utc": reference.isoformat(),
            "median_future_hours": round(statistics.median(horizons), 3) if horizons else 0.0,
            "p10_future_hours": round(percentile(horizons, 0.10), 3),
            "minimum_future_hours": round(min(horizons), 3) if horizons else 0.0,
            "channels_below_24h": sum(value < 24.0 for value in horizons),
            "channels_without_timed_programmes": len(channels) - len(horizons),
        },
    }


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def build_aliases(base: Path, final_ids: set[str], seed_path: Path | None = None) -> dict:
    edges = {}
    for alias_path in (base / "receiver-id-aliases.json", seed_path):
        if alias_path is None:
            continue
        previous = load_json(alias_path, {})
        previous_map = previous.get("mapping") if isinstance(previous, dict) else None
        if isinstance(previous_map, dict):
            edges.update({str(k): str(v) for k, v in previous_map.items() if k and v})

    report = load_json(base / "global-receiver-id-normalization.json", {})
    for row in report.get("mapping", []):
        target = str(row.get("new_id") or "").strip()
        for key in ("origin_id", "input_id", "old_id"):
            source = str(row.get(key) or "").strip()
            if source and target:
                edges[source] = target
        if target:
            edges[target] = target

    edges.update(bein.RENAME)
    edges.update(BEIN_DROP_BRIDGE)
    edges.update(cleanup.BEIN_ALIAS_TO_CANONICAL)
    edges.update(cleanup.OSN_ALIAS_TO_CANONICAL)

    polish = load_json(base / "provider-namespace-polish.json", {})
    edges.update({str(k): str(v) for k, v in (polish.get("changes") or {}).items()})
    for cid in final_ids:
        edges[cid] = cid

    def resolve(value):
        seen = set()
        current = value
        while current in edges and edges[current] != current and current not in seen:
            seen.add(current)
            current = edges[current]
        return current

    mapping = {
        source: resolve(target)
        for source, target in edges.items()
        if source and resolve(target) in final_ids
    }
    return {
        "schema": 1,
        "policy": "source/raw XMLTV ID to final canonical receiver ID; used only for exact LKG continuity",
        "canonical_ids": sorted(final_ids, key=lambda value: (value.casefold(), value)),
        "mapping": dict(sorted(mapping.items(), key=lambda row: (row[0].casefold(), row[0]))),
    }


def run_transform(script_dir: Path, script: str, base: Path):
    subprocess.run(
        [sys.executable, str(script_dir / script), "--dir", str(base)],
        check=True,
    )


def refresh_transform_reports(base: Path, reference: datetime):
    """Make diagnostic hash tables describe the same final bytes as manifests."""
    final = {
        path.name: profile(path, reference)
        for path in sorted(base.glob("*.xml.gz"))
    }
    for name in (
        "canonical-id-reset.json",
        "global-receiver-id-normalization.json",
        "bein-receiver-id-normalization.json",
    ):
        path = base / name
        payload = load_json(path, {})
        changed = False
        for row in payload.get("files", []):
            stats = final.get(str(row.get("file") or ""))
            if not stats:
                continue
            for key in ("size_bytes", "sha256"):
                row[key] = stats[key]
            if "channels" in row:
                row["channels"] = stats["channels"]
            if "programmes" in row:
                row["programmes"] = stats["programmes"]
            if "channels_after" in row:
                row["channels_after"] = stats["channels"]
            if "programmes_after" in row:
                row["programmes_after"] = stats["programmes"]
            changed = True
        if changed:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--skip-normalizers", action="store_true")
    ap.add_argument("--alias-seed")
    args = ap.parse_args()
    base = Path(args.dir)
    script_dir = Path(__file__).resolve().parent

    # The retained pre-normalization snapshot can still contain the historical
    # receiver-facing MENA Other shard.  After MENA Other retirement, canonical
    # beIN aliases inside that old shard intentionally collide with provider-bein.
    # Drop only that historical bootstrap surface; the alias tables themselves
    # still preserve exact raw -> canonical continuity.  Normal production output
    # is never silently altered here and remains protected by receiver_release_gate.
    if base.name == "bootstrap" and base.parent.name == "previous":
        for suffix in ("xml.gz", "txt"):
            stale = base / ("mena-other." + suffix)
            if stale.exists():
                stale.unlink()
        stale_manifest = base / "shards.json"
        if stale_manifest.exists():
            payload = load_json(stale_manifest, {})
            rows = payload.get("shards") or {}
            if "mena-other" in rows:
                rows.pop("mena-other", None)
                payload["shards"] = rows
                payload["other_receiver_published"] = False
                stale_manifest.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print("Legacy bootstrap: retired historical mena-other shard before canonical replay")

    if not args.skip_normalizers:
        run_transform(script_dir, "canonical_receiver_cleanup.py", base)
        run_transform(script_dir, "bein_receiver_id_normalize.py", base)
        run_transform(script_dir, "global_receiver_id_normalize.py", base)
        run_transform(script_dir, "provider_namespace_polish.py", base)

    combined_path = base / "mena-arabic.xml.gz"
    combined = canonical_root(read_root(combined_path))
    combined_ids = {(c.get("id") or "").strip() for c in combined.findall("channel")}
    premium_ids = {cid for cid in combined_ids if cid.startswith(PREMIUM_PREFIXES)}
    mena_ids = combined_ids - premium_ids
    if not premium_ids or not mena_ids:
        raise SystemExit("canonical aggregate split unexpectedly empty")

    write_root(combined_path, combined)
    write_root(
        base / "mena.xml.gz",
        canonical_root(combined, mena_ids, "EPGManager MENA Cloud"),
    )
    write_root(
        base / "premium.xml.gz",
        canonical_root(combined, premium_ids, "EPGManager Premium Cloud"),
    )

    # Canonical ordering and deterministic gzip bytes for every exclusive shard.
    for path in sorted(base.glob("*.xml.gz")):
        if path.name in {"mena-arabic.xml.gz", "mena.xml.gz", "premium.xml.gz"}:
            continue
        write_root(path, canonical_root(read_root(path)))

    manifest_path = base / "manifest.json"
    manifest = load_json(manifest_path, {})
    try:
        reference = datetime.fromisoformat(str(manifest.get("generated") or "").replace("Z", "+00:00"))
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        reference = reference.astimezone(timezone.utc)
    except ValueError:
        reference = datetime.now(timezone.utc)

    combined_stats = profile(combined_path, reference)
    mena_stats = profile(base / "mena.xml.gz", reference)
    premium_stats = profile(base / "premium.xml.gz", reference)
    manifest.update({
        "schema": max(5, int(manifest.get("schema", 0) or 0)),
        "channels": combined_stats["channels"],
        "programmes": combined_stats["programmes"],
        "size_bytes": combined_stats["size_bytes"],
        "xml_size_bytes": combined_stats["xml_size_bytes"],
        "sha256": combined_stats["sha256"],
        "arabic_title_ratio": combined_stats["arabic_title_ratio"],
        "english_title_ratio": combined_stats["english_title_ratio"],
        "arabic_desc_ratio": combined_stats["arabic_desc_ratio"],
        "coverage": combined_stats["coverage"],
        "coverage_policy": "requested window and measured future horizon are reported separately",
        "canonical_receiver_ids": True,
        "legacy_aliases_published": False,
        "split_policy": "canonical beIN/OSN -> premium; all other canonical IDs -> mena; exact disjoint union retained",
        "splits": {
            "mena": mena_stats,
            "premium": premium_stats,
            "legacy_combined": combined_stats,
        },
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    shards_path = base / "shards.json"
    shards = load_json(shards_path, {})
    rows = shards.get("shards") or {}
    shard_sets = {}
    seen = set()
    for stem, row in rows.items():
        path = base / (stem + ".xml.gz")
        if not path.exists():
            raise SystemExit("missing shard declared in manifest: %s" % stem)
        stats = profile(path, reference)
        for key in ("channels", "programmes", "size_bytes", "xml_size_bytes", "sha256"):
            row[key] = stats[key]
        ids = {(c.get("id") or "").strip() for c in read_root(path).findall("channel")}
        if str(row.get("kind") or "") == "provider":
            row["canonical_ids"] = sorted(
                ids, key=lambda value: (value.casefold(), value)
            )
            row["catalog_preserves_legacy_ids"] = False
            row["receiver_policy"] = "canonical-only; legacy/compat aliases removed"
            row.pop("compat_aliases", None)
        overlap = seen & ids
        if overlap:
            raise SystemExit("exclusive shard overlap: %s: %s" % (stem, sorted(overlap)[:5]))
        seen.update(ids)
        shard_sets[stem] = ids

    if not seen.issubset(combined_ids):
        raise SystemExit("exclusive shards contain IDs absent from combined feed")
    excluded = combined_ids - seen
    shards.update({
        "schema": max(3, int(shards.get("schema", 0) or 0)),
        "input_channels": len(combined_ids),
        "published_channels": len(seen),
        "excluded_morocco_channels": len(excluded),
        "excluded_ids": sorted(excluded, key=lambda value: (value.casefold(), value)),
        "receiver_internal_only_channels": 0,
        "receiver_internal_only_ids": [],
        "other_channels": len(shard_sets.get("mena-other", set())),
        "provider_counts": {
            key: len(shard_sets.get(stem, set())) for key, stem in PROVIDER_STEMS.items()
        },
        "canonical_receiver_ids": True,
        "legacy_aliases_published": False,
    })
    shards_path.write_text(json.dumps(shards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    refresh_transform_reports(base, reference)

    aliases = build_aliases(
        base, combined_ids, Path(args.alias_seed) if args.alias_seed else None
    )
    (base / "receiver-id-aliases.json").write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": 1,
        "combined_channels": len(combined_ids),
        "mena_channels": len(mena_ids),
        "premium_channels": len(premium_ids),
        "exclusive_shard_channels": len(seen),
        "excluded_channels": len(excluded),
        "receiver_aliases": len(aliases["mapping"]),
        "combined_sha256": combined_stats["sha256"],
        "median_future_hours": combined_stats["coverage"]["median_future_hours"],
    }
    (base / "canonical-publish-finalize.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "CANONICAL_PUBLISH PASS combined=%d mena=%d premium=%d shards=%d aliases=%d median=%.2fh"
        % (
            len(combined_ids), len(mena_ids), len(premium_ids), len(seen),
            len(aliases["mapping"]), combined_stats["coverage"]["median_future_hours"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
