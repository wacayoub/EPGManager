#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hard pre-publication gate for canonical MENA receiver artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
MAX_VUPLUS_XML_BYTES = 12 * 1024 * 1024

BEIN_CORE = {
    "beIN.4K.qa", "beIN.Drama.qa", "beIN.Movies.Action.qa", "beIN.Movies.Drama.qa",
    "beIN.Movies.Family.qa", "beIN.Movies.Premiere.qa", "beIN.Series.1.qa",
    "beIN.Series.2.qa", "beIN.Sports.FTA.qa", "beIN.Sports.News.qa", "beIN.Sports.qa",
} | {"beIN.Sports.%d.qa" % n for n in range(1, 10)} \
  | {"beIN.Sports.EN%d.qa" % n for n in range(1, 3)}
BEIN_EVENTS = {"beIN.Sports.MAX%d.qa" % n for n in range(1, 7)} \
  | {"beIN.Sports.XTRA%d.qa" % n for n in range(1, 10)}

OSN = {
    "OSN.Comedy.ae", "OSN.Kids.ae", "OSN.Mezze.ae", "OSN.Movies.Action.ae",
    "OSN.Movies.Hollywood.ae", "OSN.Movies.Premiere.ae", "OSN.Showcase.ae",
    "OSN.Crime.ae", "OSN.Documentary.ae", "OSN.iQIYI.ae", "OSN.Movies.Comedy.ae",
    "OSN.Movies.Family.ae", "OSN.Movies.Horror.ae", "OSN.Now.ae", "OSN.One.ae",
    "OSN.Pop.Up.ae", "OSN.Showcase.Classics.ae", "OSN.Yahala.ae",
    "OSN.Yahala.Aflam.ae", "OSN.Yahala.Bil.Arabi.ae",
}
MBC = {
    "MBC.Alarabiya.mena", "MBC.Al.Hadath.mena", "MBC.1.mena", "MBC.2.mena",
    "MBC.3.mena", "MBC.4.mena", "MBC.5.mena", "MBC.Action.mena",
    "MBC.Bollywood.mena", "MBC.Drama.mena", "MBC.Iraq.mena", "MBC.Masr.mena",
    "MBC.Masr2.mena", "MBC.Masr.Drama.mena", "MBC.MAX.mena", "MBC.Persia.mena",
    "MBC.Plus.Drama.mena",
}
ROTANA = {
    "Rotana.Plus.mena", "Rotana.Aflam.Plus.mena", "Rotana.Kids.mena",
    "Rotana.M.Plus.mena", "Rotana.Music.mena", "Rotana.Cinema.Egypt.mena",
    "Rotana.Cinema.KSA.mena", "Rotana.Classic.mena", "Rotana.Comedy.mena",
    "Rotana.Drama.mena", "Rotana.Khalijia.mena",
}
ROTANA_OPTIONAL = {"Rotana.Clip.mena"}
DMI = {
    "DMI.Dubai.One.ae", "DMI.Dubai.Sports.2.ae", "DMI.Dubai.Sports.ae",
    "DMI.Dubai.Zaman.ae", "DMI.Dubai.Racing.1.ae", "DMI.Dubai.Racing.2.ae",
    "DMI.Dubai.TV.ae", "DMI.Noor.Dubai.TV.ae", "DMI.Sama.Dubai.ae",
}
ADM = {
    "ADM.Al.Emarat.TV.ae", "ADM.Abu.Dhabi.Sports.1.ae", "ADM.Abu.Dhabi.Sports.2.ae",
    "ADM.Abu.Dhabi.TV.ae", "ADM.AD.Sports.Extra.ae", "ADM.AD.Sports.Premium.1.ae",
    "ADM.AD.Sports.Premium.2.ae", "ADM.Majid.ae",
    "ADM.National.Geographic.Abu.Dhabi.ae", "ADM.Yas.TV.ae", "ADM.YAS.TV.Extra.ae",
}
PREMIUM_INTERNATIONAL = {
    "AnimalPlanetEurope.uk@SD",
    "DiscoveryChannelMiddleEastAfrica.us@SD",
    "InvestigationDiscovery.uk@SD",
    "HistoryMiddleEast.us@SD",
    "History2MiddleEast.us@SD",
    "TLCArabia.us@SD",
    "CartoonNetworkMENA.uk@SD",
    "NickelodeonArabia.ae@SD",
    "NickJrArabia.ae@SD",
    "NicktoonsArabia.ae@SD",
    "CartoonNetworkArabic.ae@SD",
}
PROVIDERS = {
    "provider-bein": ("beIN.", BEIN_CORE, BEIN_EVENTS),
    "provider-osn": ("OSN.", OSN, set()),
    "provider-mbc": ("MBC.", MBC, set()),
    "provider-rotana": ("Rotana.", ROTANA, ROTANA_OPTIONAL),
    "provider-dmi": ("DMI.", DMI, set()),
    "provider-adm": ("ADM.", ADM, set()),
}


def read_file(path: Path):
    data = path.read_bytes()
    raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    return data, raw, ET.fromstring(raw)


def ids_and_programmes(path: Path):
    data, raw, root = read_file(path)
    ids = [(c.get("id") or "").strip() for c in root.findall("channel")]
    programmes = root.findall("programme")
    return data, raw, root, ids, programmes


def language(value: str) -> str:
    ar = len(AR_RE.findall(value or ""))
    latin = len(LATIN_RE.findall(value or ""))
    if ar >= 2 and ar >= latin * 0.5:
        return "ar"
    if latin >= 2:
        return "en"
    return "other"


def language_ratios(programmes):
    counts = Counter()
    for programme in programmes:
        title = programme.find("title")
        desc = programme.find("desc")
        counts["title_" + language((title.text or "") if title is not None else "")] += 1
        counts["desc_" + language((desc.text or "") if desc is not None else "")] += 1
    total = float(len(programmes) or 1)
    return {
        "title_ar": counts["title_ar"] / total,
        "title_en": counts["title_en"] / total,
        "desc_ar": counts["desc_ar"] / total,
    }


def programme_counter(programmes):
    def semantic(node):
        return (
            node.tag,
            tuple(sorted(node.attrib.items())),
            (node.text or "").strip(),
            tuple(semantic(child) for child in node),
        )
    return Counter(
        (
            (p.get("channel") or "").strip(),
            (p.get("start") or "").strip(),
            (p.get("stop") or "").strip(),
            semantic(p),
        )
        for p in programmes
    )


def check_xml(path: Path, errors):
    data, raw, root, ids, programmes = ids_and_programmes(path)
    id_set = set(ids)
    event_ids = {(p.get("channel") or "").strip() for p in programmes}
    if len(ids) != len(id_set):
        errors.append("%s: DUPLICATE_CHANNEL_IDS" % path.name)
    if not event_ids.issubset(id_set):
        errors.append("%s: ORPHAN_PROGRAMMES=%s" % (path.name, sorted(event_ids - id_set)[:10]))
    if id_set - event_ids:
        errors.append("%s: ZERO_PROGRAMME_IDS=%s" % (path.name, sorted(id_set - event_ids)[:10]))
    slots = [((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip()) for p in programmes]
    if len(slots) != len(set(slots)):
        errors.append("%s: DUPLICATE_PROGRAMME_SLOTS" % path.name)
    return {
        "ids": id_set,
        "programmes": programmes,
        "channels": len(ids),
        "programme_count": len(programmes),
        "size_bytes": len(data),
        "xml_size_bytes": len(raw),
        "sha256": hashlib.sha256(data).hexdigest(),
        "language": language_ratios(programmes),
    }


def require_ratio(name, ratios, key, minimum, errors):
    value = ratios[key]
    if value < minimum:
        errors.append("%s: %s %.1f%% < %.1f%%" % (name, key, value * 100.0, minimum * 100.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--minimum-median-future-hours", type=float, default=30.0)
    ap.add_argument("--json")
    ap.add_argument("--text")
    args = ap.parse_args()
    base = Path(args.dir)
    errors = []
    profiles = {}
    if (base / "mena-other.xml.gz").exists() or (base / "mena-other.txt").exists():
        errors.append("MENA_OTHER_RECEIVER_SHARD_PRESENT")

    for path in sorted(base.glob("*.xml.gz")):
        profiles[path.name[:-7]] = check_xml(path, errors)

    required_files = {"mena-arabic", "mena", "premium", "provider-international"} | set(PROVIDERS)
    missing_files = sorted(required_files - set(profiles))
    if missing_files:
        errors.append("MISSING_FILES=%s" % missing_files)

    if not missing_files:
        combined = profiles["mena-arabic"]
        mena = profiles["mena"]
        premium = profiles["premium"]
        if mena["ids"] & premium["ids"]:
            errors.append("AGGREGATE_SPLIT_OVERLAP")
        if mena["ids"] | premium["ids"] != combined["ids"]:
            errors.append("AGGREGATE_CHANNEL_UNION_MISMATCH")
        if programme_counter(mena["programmes"]) + programme_counter(premium["programmes"]) != programme_counter(combined["programmes"]):
            errors.append("AGGREGATE_PROGRAMME_UNION_MISMATCH")
        if any(cid.startswith(("beIN.", "OSN.")) for cid in mena["ids"]):
            errors.append("MENA_CONTAINS_PREMIUM_ID")
        premium_expected = profiles["provider-bein"]["ids"] | profiles["provider-osn"]["ids"]
        if premium["ids"] != premium_expected:
            errors.append("PREMIUM_PROVIDER_MEMBERSHIP_MISMATCH")
        if combined["xml_size_bytes"] > MAX_VUPLUS_XML_BYTES:
            errors.append("VUPLUS_XML_TOO_LARGE=%d" % combined["xml_size_bytes"])

    for stem, (prefix, required, optional) in PROVIDERS.items():
        if stem not in profiles:
            continue
        actual = profiles[stem]["ids"]
        missing = required - actual
        extra = actual - required - optional
        if missing:
            errors.append("%s: MISSING_CORE_IDS=%s" % (stem, sorted(missing)))
        if extra:
            errors.append("%s: UNREVIEWED_IDS=%s" % (stem, sorted(extra)))
        if any(not cid.startswith(prefix) for cid in actual):
            errors.append("%s: NAMESPACE_MISMATCH" % stem)

    if "provider-international" in profiles:
        actual = profiles["provider-international"]["ids"]
        missing = PREMIUM_INTERNATIONAL - actual
        extra = actual - PREMIUM_INTERNATIONAL
        if missing:
            errors.append("provider-international: MISSING_CORE_IDS=%s" % sorted(missing))
        if extra:
            errors.append("provider-international: UNREVIEWED_IDS=%s" % sorted(extra))

    if "mena" in profiles:
        require_ratio("mena", profiles["mena"]["language"], "title_ar", 0.55, errors)
        require_ratio("mena", profiles["mena"]["language"], "desc_ar", 0.40, errors)
    if "provider-mbc" in profiles:
        require_ratio("provider-mbc", profiles["provider-mbc"]["language"], "title_ar", 0.75, errors)
        require_ratio("provider-mbc", profiles["provider-mbc"]["language"], "desc_ar", 0.90, errors)
    if "provider-rotana" in profiles:
        require_ratio("provider-rotana", profiles["provider-rotana"]["language"], "title_ar", 0.90, errors)
        require_ratio("provider-rotana", profiles["provider-rotana"]["language"], "desc_ar", 0.90, errors)
    if "provider-dmi" in profiles:
        require_ratio("provider-dmi", profiles["provider-dmi"]["language"], "title_ar", 0.50, errors)
        require_ratio("provider-dmi", profiles["provider-dmi"]["language"], "desc_ar", 0.40, errors)
    if "provider-adm" in profiles:
        require_ratio("provider-adm", profiles["provider-adm"]["language"], "title_ar", 0.50, errors)
        require_ratio("provider-adm", profiles["provider-adm"]["language"], "desc_ar", 0.55, errors)
    if "provider-bein" in profiles:
        require_ratio("provider-bein", profiles["provider-bein"]["language"], "desc_ar", 0.90, errors)
    if "provider-osn" in profiles:
        require_ratio("provider-osn", profiles["provider-osn"]["language"], "title_en", 0.90, errors)
        require_ratio("provider-osn", profiles["provider-osn"]["language"], "desc_ar", 0.90, errors)

    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    combined = profiles.get("mena-arabic", {})
    for key, actual in (("channels", combined.get("channels")), ("programmes", combined.get("programme_count")), ("size_bytes", combined.get("size_bytes")), ("sha256", combined.get("sha256"))):
        if manifest.get(key) != actual:
            errors.append("manifest.json: %s mismatch" % key)
    for split, stem in (("mena", "mena"), ("premium", "premium"), ("legacy_combined", "mena-arabic")):
        row = (manifest.get("splits") or {}).get(split) or {}
        actual = profiles.get(stem, {})
        for key, field in (("channels", "channels"), ("programmes", "programme_count"), ("size_bytes", "size_bytes"), ("sha256", "sha256")):
            if row.get(key) != actual.get(field):
                errors.append("manifest.json: splits.%s.%s mismatch" % (split, key))

    coverage = manifest.get("coverage") or {}
    median = float(coverage.get("median_future_hours", 0.0) or 0.0)
    if median < args.minimum_median_future_hours:
        errors.append("REAL_COVERAGE_MEDIAN=%.2fh < %.2fh" % (median, args.minimum_median_future_hours))

    shard_manifest = json.loads((base / "shards.json").read_text(encoding="utf-8"))
    seen = set()
    for stem, row in (shard_manifest.get("shards") or {}).items():
        actual = profiles.get(stem)
        if actual is None:
            errors.append("shards.json: missing %s" % stem)
            continue
        if seen & actual["ids"]:
            errors.append("shards.json: overlap at %s" % stem)
        seen.update(actual["ids"])
        for key, field in (("channels", "channels"), ("programmes", "programme_count"), ("size_bytes", "size_bytes"), ("sha256", "sha256")):
            if row.get(key) != actual.get(field):
                errors.append("shards.json: %s.%s mismatch" % (stem, key))
    if shard_manifest.get("published_channels") != len(seen):
        errors.append("shards.json: published_channels mismatch")
    if combined and shard_manifest.get("input_channels") != combined.get("channels"):
        errors.append("shards.json: input_channels mismatch")

    aliases = json.loads((base / "receiver-id-aliases.json").read_text(encoding="utf-8"))
    canonical_ids = set(aliases.get("canonical_ids") or [])
    if canonical_ids != combined.get("ids", set()):
        errors.append("receiver-id-aliases.json: canonical set mismatch")
    if any(target not in canonical_ids for target in (aliases.get("mapping") or {}).values()):
        errors.append("receiver-id-aliases.json: target outside canonical set")

    bridge = manifest.get("lkg_bridge") or {}
    previous_channels = int(bridge.get("previous_channels", 0) or 0)
    resolved = int(
        bridge.get("resolved_previous_channels", bridge.get("resolved_current_ids", 0)) or 0
    )
    if previous_channels and resolved < int(previous_channels * 0.95):
        errors.append("LKG_BRIDGE_LOW=%d/%d" % (resolved, previous_channels))

    payload = {
        "schema": 1,
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "provider_channels": {stem: profiles.get(stem, {}).get("channels", 0) for stem in PROVIDERS},
        "language": {stem: profiles.get(stem, {}).get("language", {}) for stem in ("mena", *PROVIDERS)},
        "coverage": coverage,
        "vuplus_xml_bytes": combined.get("xml_size_bytes", 0),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["RECEIVER RELEASE GATE: %s" % payload["status"]]
    lines.extend("- " + error for error in errors)
    if not errors:
        lines.append("canonical union, manifests, provider cores, Arabic policy, real coverage and Vu+ size: PASS")
    if args.text:
        Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
