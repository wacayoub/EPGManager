#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate, clip, LKG-protect and publish split MENA XMLTV feeds.

Outputs:
- mena-arabic.xml.gz / .txt: legacy combined feed (compatibility)
- mena.xml.gz / .txt: regular MENA channels, excluding beIN/OSN premium
- premium.xml.gz / .txt: beIN/OSN premium channels only
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
WORD_RE = re.compile(r"[A-Za-z\u0600-\u06ff]")
PREMIUM_RE = re.compile(r"(?:^|[^a-z0-9])(?:be\s*in|bein|osn|osntv)(?:[^a-z0-9]|$)", re.I)


def read_xml(path: Path):
    if not path or not path.is_file() or path.stat().st_size == 0:
        return None
    data = path.read_bytes()
    if path.suffix == ".gz" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def parse_xmltv_dt(value: str):
    value = (value or "").strip()
    m = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?", value)
    if not m:
        return None
    digits, offset = m.group(1), m.group(2)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(digits, fmt)
    if offset == "Z":
        tz = timezone.utc
    elif offset:
        sign = 1 if offset[0] == "+" else -1
        hh, mm = int(offset[1:3]), int(offset[3:5])
        tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    else:
        tz = timezone.utc
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def in_window(p: ET.Element, now: datetime, end: datetime) -> bool:
    start = parse_xmltv_dt(p.get("start") or "")
    stop = parse_xmltv_dt(p.get("stop") or "")
    if start is None:
        return False
    if stop is not None and stop <= now - timedelta(minutes=5):
        return False
    if start >= end:
        return False
    if stop is None and start < now - timedelta(hours=6):
        return False
    return True


def display_name(channel: ET.Element) -> str:
    names = channel.findall("display-name")
    for n in names:
        if (n.text or "").strip():
            return (n.text or "").strip()
    return channel.get("id") or ""


def readable_name(value: str) -> bool:
    value = (value or "").strip()
    return len(value) >= 2 and bool(WORD_RE.search(value))


def id_fallback_name(cid: str) -> str:
    base = re.split(r"\.[a-z]{2,3}(?:@|$)", cid or "", maxsplit=1, flags=re.I)[0]
    base = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)
    base = re.sub(r"[_-]+", " ", base)
    return re.sub(r"\s+", " ", base).strip() or cid


def best_mapping_name(cid: str, source_meta: dict, channel: ET.Element) -> str:
    catalogue_name = (source_meta.get("name") or "").strip()
    current_name = display_name(channel)
    if readable_name(catalogue_name):
        return catalogue_name
    if readable_name(current_name):
        return current_name
    return id_fallback_name(cid)


def programme_groups(root, now, end):
    groups = defaultdict(list)
    if root is None:
        return groups
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid and in_window(p, now, end):
            groups[cid].append(p)
    return groups


def channel_map(root):
    out = {}
    if root is None:
        return out
    for c in root.findall("channel"):
        cid = (c.get("id") or "").strip()
        if cid:
            out[cid] = c
    return out


def identity_key(value: str) -> str:
    """Conservative exact display-identity key used only as an LKG fallback."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"\b(?:uhd|fhd|hd|sd|digital|mono|tv|channel)\b", " ", value)
    value = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def load_receiver_aliases(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    mapping = payload.get("mapping") if isinstance(payload, dict) else None
    if not isinstance(mapping, dict):
        return {}
    return {
        str(source).strip(): str(canonical).strip()
        for source, canonical in mapping.items()
        if str(source).strip() and str(canonical).strip()
    }


def bridge_previous_ids(current_ids, cand_channels, source_by_id, prev_channels, aliases):
    """Resolve current raw/catalogue IDs to the prior canonical receiver IDs.

    Exact source->canonical aliases are authoritative.  A display-name fallback
    is accepted only when that normalized name is unique on both sides; ambiguous
    identities are deliberately left unresolved rather than borrowing EPG from
    the wrong service.
    """
    prior_by_name = defaultdict(list)
    for cid, channel in prev_channels.items():
        key = identity_key(display_name(channel))
        if key:
            prior_by_name[key].append(cid)

    current_by_name = defaultdict(list)
    current_names = {}
    for cid in current_ids:
        channel = cand_channels.get(cid)
        name = display_name(channel) if channel is not None else str((source_by_id.get(cid) or {}).get("name") or "")
        key = identity_key(name)
        current_names[cid] = key
        if key:
            current_by_name[key].append(cid)

    bridged = {}
    methods = Counter()
    for cid in sorted(current_ids, key=str.casefold):
        if cid in prev_channels:
            bridged[cid] = cid
            methods["exact_id"] += 1
            continue
        canonical = aliases.get(cid, "")
        if canonical in prev_channels:
            bridged[cid] = canonical
            methods["receiver_alias"] += 1
            continue
        key = current_names.get(cid, "")
        matches = prior_by_name.get(key, [])
        if key and len(matches) == 1 and len(current_by_name.get(key, [])) == 1:
            bridged[cid] = matches[0]
            methods["unique_display_identity"] += 1
        else:
            methods["unresolved"] += 1
    return bridged, methods


def event_key(p: ET.Element):
    return ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip())


def useful(rows) -> bool:
    starts = {(p.get("start") or "").strip() for p in rows if (p.get("start") or "").strip()}
    return len(starts) >= 2


def copy_element(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def language_of(text: str) -> str:
    text = text or ""
    ar = len(AR_RE.findall(text))
    en = len(LATIN_RE.findall(text))
    if ar >= 2 and ar >= en * 0.5:
        return "ar"
    if en >= 2:
        return "en"
    return "other"


def is_premium_identity(cid: str, mapping_name: str) -> bool:
    return bool(PREMIUM_RE.search("%s %s" % (cid or "", mapping_name or "")))


def build_feed(ids, selected_programmes, cand_channels, prev_channels, source_by_id, generator_name):
    root = ET.Element("tv", {
        "generator-info-name": generator_name,
        "generator-info-url": "https://github.com/wacayoub/EPGManager",
    })
    text_lines = []
    source_counts = Counter()
    seen_programmes = set()
    programme_count = 0
    arabic_titles = 0
    english_titles = 0
    arabic_descs = 0

    for cid in sorted(ids, key=str.casefold):
        source_meta = source_by_id.get(cid, {})
        c = cand_channels.get(cid)
        if c is None:
            c = prev_channels.get(cid)
        if c is None:
            c = ET.Element("channel", {"id": cid})
        else:
            c = copy_element(c)
        # A bridged LKG channel element carries the prior canonical ID.  The
        # pre-publication normalizer owns the final namespace, so this build
        # boundary must keep the current candidate/catalogue ID consistent with
        # the programme references it emits.
        c.set("id", cid)
        mapping_name = best_mapping_name(cid, source_meta, c)
        existing = c.findall("display-name")
        if not existing:
            ET.SubElement(c, "display-name").text = mapping_name
        elif not readable_name(display_name(c)):
            existing[0].text = mapping_name
        root.append(c)
        site = source_meta.get("site") or "merged-external"
        source_counts[site] += 1
        text_lines.append("%s|%s" % (cid, mapping_name))

    for cid in sorted(ids, key=str.casefold):
        for p in sorted(selected_programmes.get(cid, []), key=lambda x: x.get("start") or ""):
            cp = copy_element(p)
            cp.set("channel", cid)
            key = event_key(cp)
            if not key[1] or key in seen_programmes:
                continue
            seen_programmes.add(key)
            root.append(cp)
            programme_count += 1
            title = cp.find("title")
            desc = cp.find("desc")
            title_lang = language_of((title.text or "") if title is not None else "")
            desc_lang = language_of((desc.text or "") if desc is not None else "")
            if title_lang == "ar":
                arabic_titles += 1
            if title_lang == "en":
                english_titles += 1
            if desc_lang == "ar":
                arabic_descs += 1

    ET.indent(root, space="  ")
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    gz_bytes = gzip.compress(xml_bytes, compresslevel=9)
    stats = {
        "channels": len(ids),
        "programmes": programme_count,
        "arabic_title_ratio": round(arabic_titles / float(programme_count), 4) if programme_count else 0,
        "english_title_ratio": round(english_titles / float(programme_count), 4) if programme_count else 0,
        "arabic_desc_ratio": round(arabic_descs / float(programme_count), 4) if programme_count else 0,
        "source_counts": dict(sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "size_bytes": len(gz_bytes),
        "sha256": hashlib.sha256(gz_bytes).hexdigest(),
    }
    return root, gz_bytes, text_lines, stats


def write_feed(out_dir: Path, stem: str, gz_bytes: bytes, text_lines) -> None:
    (out_dir / (stem + ".xml.gz")).write_bytes(gz_bytes)
    (out_dir / (stem + ".txt")).write_text("\n".join(text_lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--catalog-manifest", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--merge-report")
    ap.add_argument("--receiver-aliases")
    ap.add_argument("--window-hours", type=int, default=48)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, args.window_hours))
    candidate_path = Path(args.candidate)
    previous_path = Path(args.previous) if args.previous else None
    catalog = json.loads(Path(args.catalog_manifest).read_text(encoding="utf-8"))
    source_by_id = {x["xmltv_id"]: x for x in catalog.get("channels", []) if x.get("xmltv_id")}

    candidate = read_xml(candidate_path)
    previous = read_xml(previous_path) if previous_path and previous_path.is_file() else None
    cand_channels, prev_channels = channel_map(candidate), channel_map(previous)
    cand_programmes = programme_groups(candidate, now, end)
    prev_programmes = programme_groups(previous, now, end)

    aliases_path = Path(args.receiver_aliases) if args.receiver_aliases else None
    receiver_aliases = load_receiver_aliases(aliases_path)
    current_ids = set(cand_programmes) | set(source_by_id)
    previous_id_by_current, bridge_methods = bridge_previous_ids(
        current_ids, cand_channels, source_by_id, prev_channels, receiver_aliases
    )
    bridged_prev_channels = {
        cid: prev_channels[prior]
        for cid, prior in previous_id_by_current.items()
        if prior in prev_channels
    }
    bridged_prev_programmes = {
        cid: prev_programmes.get(prior, [])
        for cid, prior in previous_id_by_current.items()
    }

    selected_programmes = {}
    states = Counter()
    ids = set(current_ids)
    for cid in sorted(ids, key=str.casefold):
        fresh = cand_programmes.get(cid, [])
        old = bridged_prev_programmes.get(cid, [])
        if useful(fresh):
            selected_programmes[cid] = fresh
            states["fresh"] += 1
        elif useful(old):
            selected_programmes[cid] = old
            states["lkg"] += 1
        else:
            states["no_epg"] += 1

    all_ids = set(selected_programmes)
    premium_ids = set()
    for cid in all_ids:
        source_meta = source_by_id.get(cid, {})
        c = cand_channels.get(cid)
        if c is None:
            c = bridged_prev_channels.get(cid)
        if c is None:
            c = ET.Element("channel", {"id": cid})
        mapping_name = best_mapping_name(cid, source_meta, c)
        if is_premium_identity(cid, mapping_name):
            premium_ids.add(cid)
    mena_ids = all_ids - premium_ids

    if premium_ids & mena_ids:
        raise SystemExit("Split overlap detected")
    if premium_ids | mena_ids != all_ids:
        raise SystemExit("Split coverage mismatch")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _, combined_gz, combined_txt, combined_stats = build_feed(
        all_ids, selected_programmes, cand_channels, bridged_prev_channels, source_by_id,
        "EPGManager MENA Cloud (Legacy Combined)")
    _, mena_gz, mena_txt, mena_stats = build_feed(
        mena_ids, selected_programmes, cand_channels, bridged_prev_channels, source_by_id,
        "EPGManager MENA Cloud")
    _, premium_gz, premium_txt, premium_stats = build_feed(
        premium_ids, selected_programmes, cand_channels, bridged_prev_channels, source_by_id,
        "EPGManager Premium Cloud")

    if combined_stats["channels"] < 25 or combined_stats["programmes"] < 100:
        raise SystemExit("Combined output too small: %d channels / %d programmes" %
                         (combined_stats["channels"], combined_stats["programmes"]))
    if mena_stats["channels"] < 1 or mena_stats["programmes"] < 2:
        raise SystemExit("MENA output too small: %d channels / %d programmes" %
                         (mena_stats["channels"], mena_stats["programmes"]))
    if premium_stats["channels"] < 1 or premium_stats["programmes"] < 2:
        raise SystemExit("Premium output too small: %d channels / %d programmes" %
                         (premium_stats["channels"], premium_stats["programmes"]))

    write_feed(out_dir, "mena-arabic", combined_gz, combined_txt)
    write_feed(out_dir, "mena", mena_gz, mena_txt)
    write_feed(out_dir, "premium", premium_gz, premium_txt)

    merge_report = {}
    if args.merge_report:
        p = Path(args.merge_report)
        if p.is_file():
            try:
                merge_report = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                merge_report = {}

    manifest = {
        "schema": 4,
        "generated": now.isoformat(),
        "status": "ok",
        "window_hours": args.window_hours,
        "channels": combined_stats["channels"],
        "programmes": combined_stats["programmes"],
        "catalogue_channels": catalog.get("unique_channels", 0),
        "fresh_channels": states["fresh"],
        "lkg_channels": states["lkg"],
        "no_epg_channels": states["no_epg"],
        "lkg_bridge": {
            "previous_channels": len(prev_channels),
            "current_ids": len(current_ids),
            "resolved_current_ids": len(previous_id_by_current),
            "resolved_previous_channels": len(set(previous_id_by_current.values())),
            "exact_id": bridge_methods["exact_id"],
            "receiver_alias": bridge_methods["receiver_alias"],
            "unique_display_identity": bridge_methods["unique_display_identity"],
            "unresolved": bridge_methods["unresolved"],
            "alias_file_loaded": bool(receiver_aliases),
        },
        "arabic_title_ratio": combined_stats["arabic_title_ratio"],
        "english_title_ratio": combined_stats["english_title_ratio"],
        "arabic_desc_ratio": combined_stats["arabic_desc_ratio"],
        "premium_hybrid_events": merge_report.get("premium_hybrid_events", 0),
        "remote_sources_ok": merge_report.get("remote_sources_ok"),
        "remote_sources_failed": merge_report.get("remote_sources_failed", []),
        "logical_duplicate_groups": (merge_report.get("stats") or {}).get("logical_duplicate_groups", 0),
        "source_counts": combined_stats["source_counts"],
        "size_bytes": combined_stats["size_bytes"],
        "sha256": combined_stats["sha256"],
        "split_policy": "beIN/OSN -> premium; all other MENA -> mena; legacy combined retained",
        "splits": {
            "mena": mena_stats,
            "premium": premium_stats,
            "legacy_combined": combined_stats,
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("MENA split output: regular=%d/%d premium=%d/%d combined=%d/%d / fresh=%d lkg=%d no_epg=%d / window=%dh" % (
        mena_stats["channels"], mena_stats["programmes"],
        premium_stats["channels"], premium_stats["programmes"],
        combined_stats["channels"], combined_stats["programmes"],
        states["fresh"], states["lkg"], states["no_epg"], args.window_hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
