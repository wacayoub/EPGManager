#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize every non-beIN receiver-facing XMLTV ID into one stable namespace.

Rules:
- provider shards get stable provider namespaces;
- country shards get ISO country suffixes;
- mena-other gets .mena;
- display names and programme content are never changed;
- exact proven same-identity + same-timeline clones are collapsed conservatively;
- the resulting old->new map is applied to every published aggregate XMLTV file.

beIN is intentionally excluded: tools/bein_receiver_id_normalize.py owns that
namespace and runs immediately before this tool.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

PROVIDER = {
    "provider-alkass": ("AlKass", "qa"),
    "provider-osn": ("OSN", "ae"),
    "provider-mbc": ("MBC", "mena"),
    "provider-rotana": ("Rotana", "mena"),
    "provider-adm": ("ADM", "ae"),
    "provider-dmi": ("DMI", "ae"),
    "provider-art": ("ART", "mena"),
    "provider-ssc": ("SSC", "sa"),
    "provider-starz": ("STARZ", "mena"),
}
COUNTRIES = {
    "mena-eg": "eg", "mena-sa": "sa", "mena-ae": "ae", "mena-qa": "qa",
    "mena-kw": "kw", "mena-bh": "bh", "mena-om": "om", "mena-jo": "jo",
    "mena-lb": "lb", "mena-iq": "iq", "mena-ps": "ps", "mena-ye": "ye",
    "mena-dz": "dz", "mena-tn": "tn", "mena-ly": "ly", "mena-sd": "sd",
    "mena-sy": "sy", "mena-mr": "mr",
}
EXCLUSIVE = list(PROVIDER) + list(COUNTRIES) + ["mena-other"]
GEO = "ae|sa|eg|qa|iq|jo|lb|kw|bh|om|ye|dz|tn|ly|sd|sy|mr|ps|uk|us|net|ir|il|fr|de|nl|mena"

ARABIC_NAME_EQUIV = {
    "إم بي سي": "MBC", "او اس ان": "OSN", "أو إس إن": "OSN",
    "روتانا": "Rotana", "دي إم سي": "DMC", "سي بي سي": "CBC",
    "صدى البلد": "Sada El Balad", "نايل دراما": "Nile Drama",
    "رؤيا": "Roya", "الرشيد": "Al Rasheed", "السومرية": "Al Sumaria",
    "عمان": "Oman", "تن": "TeN", "الجديد": "Al Jadeed",
    "إمارات": "Emirates", "الإمارات": "Emirates", "اﻹمارات": "Emirates",
}


def read_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def write_root(path: Path, root: ET.Element) -> tuple[int, str]:
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    gz = gzip.compress(xml, compresslevel=9, mtime=0)
    path.write_bytes(gz)
    return len(gz), hashlib.sha256(gz).hexdigest()


def dname(c: ET.Element) -> str:
    for n in c.findall("display-name"):
        t = (n.text or "").strip()
        if t:
            return t
    return (c.get("id") or "").strip()


def remove_geo_suffixes(s: str) -> str:
    old = None
    while old != s:
        old = s
        s = re.sub(rf"(?:[._\s-]+)(?:{GEO})(?:@(?:SD|HD|MENA|Arabic))?$", "", s, flags=re.I)
        s = re.sub(r"@(?:SD|HD|MENA|Arabic)$", "", s, flags=re.I)
    return s


def clean_label(value: str) -> str:
    s = unicodedata.normalize("NFKC", value or "").strip()
    s = re.sub(r"^(?:en|ar):\s*", "", s, flags=re.I)
    s = remove_geo_suffixes(s)
    s = s.replace("+", " Plus ")
    s = re.sub(r"\b(?:UHD|FHD|HD|SD|DIGITAL|MONO)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ._-")
    return s


def ascii_words(value: str) -> list[str]:
    x = clean_label(value)
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode("ascii")
    # Split CamelCase and acronym->word boundaries so legacy IDs remain readable.
    x = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", x)
    x = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", x)
    x = re.sub(r"[^A-Za-z0-9]+", " ", x)
    return [w for w in x.split() if w]


def old_id_label(cid: str) -> str:
    return clean_label(cid)


def descriptive(words: list[str]) -> bool:
    letters = sum(sum(ch.isalpha() for ch in w) for w in words)
    return letters >= 2 and any(any(ch.isalpha() for ch in w) for w in words)


def slug(label: str, cid: str) -> str:
    words = ascii_words(label)
    if not descriptive(words):
        words = ascii_words(old_id_label(cid))
    if not descriptive(words):
        return "Channel" + hashlib.sha1(cid.encode("utf-8")).hexdigest()[:8]
    s = ".".join(words)
    return s[:80].strip(".") or ("Channel" + hashlib.sha1(cid.encode()).hexdigest()[:8])


def strip_provider_prefix(s: str, ns: str) -> str:
    parts = s.split(".")
    low = ns.casefold()
    # Handle compact legacy first token such as MBCMasrDrama / RotanaCinemaEgypt.
    if parts:
        p0 = parts[0]
        if p0.casefold().startswith(low) and p0.casefold() != low:
            rem = p0[len(ns):].strip("._-")
            parts = ([rem] if rem else []) + parts[1:]
    while parts and parts[0].casefold() in {low, low + "tv"}:
        parts.pop(0)
    if ns == "OSN" and parts and parts[0].casefold() == "osntv":
        parts.pop(0)
    if ns == "Rotana" and parts and parts[0].casefold() == "rotana":
        parts.pop(0)
    if ns == "AlKass" and parts and parts[0].casefold() in {"alkass", "alkas"}:
        parts.pop(0)
    if ns == "STARZ" and parts and parts[0].casefold() in {"starz", "starzplay"}:
        parts.pop(0)
    return ".".join(parts) or "Main"


def canonical_id(stem: str, cid: str, name: str) -> str:
    if stem in PROVIDER:
        ns, suffix = PROVIDER[stem]
        mid = strip_provider_prefix(slug(name, cid), ns)
        return f"{ns}.{mid}.{suffix}"
    if stem in COUNTRIES:
        return f"{slug(name, cid)}.{COUNTRIES[stem]}"
    return f"{slug(name, cid)}.mena"


def title(p: ET.Element) -> str:
    for n in p.findall("title"):
        t = (n.text or "").strip()
        if t:
            return t
    return ""


def identity_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").strip()
    for a, b in ARABIC_NAME_EQUIV.items():
        s = s.replace(a, b)
    s = clean_label(s).casefold()
    s = re.sub(r"\b(?:tv|channel)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", s)
    return " ".join(s.split())


def timeline(rows: list[ET.Element]) -> tuple:
    return tuple(((p.get("start") or "").strip(), (p.get("stop") or "").strip(), title(p)) for p in rows)


def generic_ratio(rows: list[ET.Element]) -> float:
    if not rows:
        return 1.0
    vals = [title(x).strip().casefold() for x in rows]
    generic = sum(1 for x in vals if not x or x in {"program", "programme", "tv program", "news", "bein sports max"})
    return generic / float(len(vals))


def load_exclusive(base: Path):
    data = {}
    for stem in EXCLUSIVE:
        path = base / f"{stem}.xml.gz"
        if not path.exists():
            continue
        root = read_root(path)
        chans = {(c.get("id") or "").strip(): c for c in root.findall("channel") if (c.get("id") or "").strip()}
        progs = defaultdict(list)
        for p in root.findall("programme"):
            cid = (p.get("channel") or "").strip()
            if cid in chans:
                progs[cid].append(p)
        data[stem] = (path, root, chans, progs)
    return data


def previous_origins(base: Path) -> dict[str, str]:
    p = base / "global-receiver-id-normalization.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for row in data.get("mapping", []):
        new = str(row.get("new_id") or "").strip()
        # Schema 2 renamed old_id to origin_id/input_id.  Reading only the
        # removed schema-1 key made a replay treat already-canonical IDs as new
        # origins.  Collision suffixes could then move to another service on
        # every reset (Al Jazeera Arabic was the first visible casualty).
        old = str(
            row.get("origin_id") or row.get("old_id") or row.get("input_id") or ""
        ).strip()
        if new and old:
            out[new] = old
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    args = ap.parse_args()
    base = Path(args.dir)
    origins = previous_origins(base)
    shards = load_exclusive(base)

    old_to_new: dict[str, str] = {}
    owner: dict[str, tuple[str, str, str]] = {}
    collisions = []
    for stem, (_, _, chans, _) in shards.items():
        used = set()
        ordered = sorted(
            chans.items(),
            key=lambda kv: (
                origins.get(kv[0], kv[0]).casefold(), origins.get(kv[0], kv[0]),
                kv[0].casefold(), kv[0],
            ),
        )
        for cid, c in ordered:
            origin = origins.get(cid, cid)
            candidate = canonical_id(stem, origin, dname(c))
            base_candidate = candidate
            n = 2
            while candidate in used:
                head, dot, tail = base_candidate.rpartition(".")
                candidate = f"{head}.{n}.{tail}" if dot else f"{base_candidate}.{n}"
                n += 1
            used.add(candidate)
            old_to_new[cid] = candidate
            owner[cid] = (stem, dname(c), origin)
            if candidate != base_candidate:
                collisions.append({"source": stem, "input_id": cid, "origin_id": origin, "new_id": candidate})

    exact_groups = defaultdict(list)
    for stem, (_, _, chans, progs) in shards.items():
        for cid, c in chans.items():
            rows = progs.get(cid, [])
            if not rows or generic_ratio(rows) > 0.80:
                continue
            exact_groups[(identity_name(dname(c)), timeline(rows))].append((stem, cid))

    drop_ids = set()
    duplicate_groups = []
    priority = {s: i for i, s in enumerate(EXCLUSIVE)}
    for members in exact_groups.values():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda x: priority.get(x[0], 999))
        winner = members[0]
        losers = members[1:]
        if any(x[0] == winner[0] and x[0].startswith("provider-") for x in losers):
            continue
        for _, cid in losers:
            drop_ids.add(cid)
        duplicate_groups.append({"canonical_source": winner[0], "canonical_input_id": winner[1], "removed": [x[1] for x in losers]})

    reports = []
    for path in sorted(base.glob("*.xml.gz")):
        root = read_root(path)
        seen_channels = set()
        newroot = ET.Element("tv", dict(root.attrib))
        kept_order = []
        for c in root.findall("channel"):
            old = (c.get("id") or "").strip()
            if old in drop_ids:
                continue
            new = old_to_new.get(old, old)
            if new in seen_channels:
                continue
            c.set("id", new)
            seen_channels.add(new)
            kept_order.append(new)
            newroot.append(c)
        prog_seen = set()
        for p in root.findall("programme"):
            old = (p.get("channel") or "").strip()
            if old in drop_ids:
                continue
            new = old_to_new.get(old, old)
            if new not in seen_channels:
                continue
            p.set("channel", new)
            key = (new, (p.get("start") or "").strip(), (p.get("stop") or "").strip(), title(p))
            if key in prog_seen:
                continue
            prog_seen.add(key)
            newroot.append(p)
        size, sha = write_root(path, newroot)
        txt = path.with_suffix("").with_suffix(".txt")
        if txt.exists():
            names = {(c.get("id") or "").strip(): dname(c) for c in newroot.findall("channel")}
            txt.write_text("".join(f"{cid}|{names.get(cid,cid)}\n" for cid in kept_order), encoding="utf-8")
        reports.append({"file": path.name, "channels": len(seen_channels), "programmes": len(prog_seen), "size_bytes": size, "sha256": sha})

    mapping = []
    for old, new in sorted(old_to_new.items()):
        stem, name, origin = owner.get(old, ("", "", old))
        mapping.append({"source": stem, "origin_id": origin, "input_id": old, "new_id": new, "name": name})
    payload = {
        "schema": 2,
        "policy": "global stable receiver namespace; one real service -> one canonical ID; only proven exact non-generic clones auto-collapsed",
        "renamed_count": len(old_to_new),
        "dropped_duplicate_count": len(drop_ids),
        "duplicate_groups": duplicate_groups,
        "collision_repairs": collisions,
        "files": reports,
        "mapping": mapping,
    }
    (base / "global-receiver-id-normalization.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "GLOBAL RECEIVER ID NORMALIZATION",
        f"renamed={len(old_to_new)} dropped_proven_duplicates={len(drop_ids)} collisions_repaired={len(collisions)}",
        "policy=one real service -> one stable canonical receiver ID",
        "",
    ]
    for stem in EXCLUSIVE:
        rows = [x for x in mapping if x["source"] == stem]
        if not rows:
            continue
        lines.append(f"=== {stem} | {len(rows)} IDs ===")
        for r in rows:
            flag = " DROP_DUP" if r["input_id"] in drop_ids else ""
            lines.append(f"{r['origin_id']} -> {r['new_id']} | {r['name']}{flag}")
        lines.append("")
    if duplicate_groups:
        lines.append("=== PROVEN DUPLICATES REMOVED ===")
        for g in duplicate_groups:
            lines.append(f"KEEP {g['canonical_source']}::{g['canonical_input_id']} | DROP {' ; '.join(g['removed'])}")
    (base / "global-receiver-id-normalization.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"GLOBAL_ID_NORMALIZATION PASS renamed={len(old_to_new)} dropped={len(drop_ids)} collisions={len(collisions)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
