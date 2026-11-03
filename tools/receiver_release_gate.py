#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hard pre-publication gate for canonical MENA receiver artifacts.

The gate validates receiver structure, provider membership, language policy,
manifest/hash coherence, real coverage, alias safety and Vu+ payload size.

Policy alignment notes:
- ADM keeps the long-standing receiver IDs restored by provider_namespace_polish.py.
- beIN Gourmet is an optional receiver service, like MAX/XTRA event services.
- beIN descriptions are optional metadata. When a description is present, Arabic
  remains the required language quality signal; missing descriptions alone do not
  block publication.
- During a namespace migration, 90-95% LKG bridge resolution is accepted only
  when the canonical receiver set is not shrinking. Below 90% remains blocking.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import xml.etree.ElementTree as ET


AR_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
try:
    from tools.receiver_contract import (
        BEIN_CORE, BEIN_OPTIONAL, OSN, MBC, ROTANA, ROTANA_OPTIONAL,
        DMI, DMI_OPTIONAL_REAL_EPG_ONLY, ADM, PREMIUM_INTERNATIONAL, PROVIDERS,
        MAX_VUPLUS_XML_BYTES,
    )
    from tools.source_feed_pack import parse_xmltv_dt
except ModuleNotFoundError:
    from receiver_contract import (
        BEIN_CORE, BEIN_OPTIONAL, OSN, MBC, ROTANA, ROTANA_OPTIONAL,
        DMI, DMI_OPTIONAL_REAL_EPG_ONLY, ADM, PREMIUM_INTERNATIONAL, PROVIDERS,
        MAX_VUPLUS_XML_BYTES,
    )
    from source_feed_pack import parse_xmltv_dt


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


def present_language_ratio(programmes, tag: str, wanted: str) -> float:
    """Language ratio among non-empty metadata only.

    This preserves the provider policy that missing descriptions are optional,
    without allowing present English/foreign descriptions to masquerade as Arabic.
    """
    langs = []
    for programme in programmes:
        node = programme.find(tag)
        text = ((node.text or "").strip() if node is not None else "")
        if text:
            langs.append(language(text))
    if not langs:
        return 1.0
    return sum(item == wanted for item in langs) / float(len(langs))


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


def check_xml(path: Path, errors, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Coverage clock must have a timezone")
    data, raw, root, ids, programmes = ids_and_programmes(path)
    if root.tag != "tv":
        errors.append("%s: INVALID_XMLTV_ROOT" % path.name)
    id_set = set(ids)
    event_ids = {(p.get("channel") or "").strip() for p in programmes}
    if len(ids) != len(id_set):
        errors.append("%s: DUPLICATE_CHANNEL_IDS" % path.name)
    if not all(ids):
        errors.append("%s: BLANK_CHANNEL_ID" % path.name)
    if not event_ids.issubset(id_set):
        errors.append("%s: ORPHAN_PROGRAMMES=%s" % (path.name, sorted(event_ids - id_set)[:10]))
    if id_set - event_ids:
        errors.append("%s: ZERO_PROGRAMME_IDS=%s" % (path.name, sorted(id_set - event_ids)[:10]))
    slots = [
        ((p.get("channel") or "").strip(), (p.get("start") or "").strip(), (p.get("stop") or "").strip())
        for p in programmes
    ]
    if len(slots) != len(set(slots)):
        errors.append("%s: DUPLICATE_PROGRAMME_SLOTS" % path.name)
    latest = {}
    future_events = 0
    invalid_times = 0
    for programme in programmes:
        try:
            start = parse_xmltv_dt(programme.get("start"))
            stop = parse_xmltv_dt(programme.get("stop"))
        except (ValueError, OverflowError):
            start = stop = None
        if start is None or stop is None or stop <= start:
            invalid_times += 1
            continue
        cid = programme.get("channel")
        if cid in id_set and stop > now:
            future_events += 1
            latest[cid] = max(stop, latest.get(cid, stop))
    if invalid_times:
        errors.append("%s: INVALID_PROGRAMME_TIMES=%d" % (path.name, invalid_times))
    horizons = [max(0.0, (latest[cid]-now).total_seconds()/3600) if cid in latest else 0.0
                for cid in id_set]
    return {
        "ids": id_set,
        "programmes": programmes,
        "channels": len(ids),
        "programme_count": len(programmes),
        "size_bytes": len(data),
        "xml_size_bytes": len(raw),
        "sha256": hashlib.sha256(data).hexdigest(),
        "language": language_ratios(programmes),
        "future_programme_count": future_events,
        "channels_without_future_epg": sorted(id_set-set(latest)),
        "coverage": {
            "reference_utc": now.astimezone(timezone.utc).isoformat(),
            "computed_from": "final_decompressed_xmltv_programmes",
            "median_future_hours": median(horizons) if horizons else 0.0,
            "minimum_future_hours": min(horizons) if horizons else 0.0,
            "channels_without_future_epg": len(id_set-set(latest)),
        },
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
    ap.add_argument("--artifact-checks-only", action="store_true",
                    help="Allow exit 0 for offline artifact checks; never certifies a Vu+ release")
    args = ap.parse_args()
    base = Path(args.dir)
    errors = []
    warnings = []
    profiles = {}
    checked_at = datetime.now(timezone.utc)

    if (base / "mena-other.xml.gz").exists() or (base / "mena-other.txt").exists():
        errors.append("MENA_OTHER_RECEIVER_SHARD_PRESENT")

    for path in sorted(base.glob("*.xml.gz")):
        profiles[path.name[:-7]] = check_xml(path, errors, checked_at)

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

    for stem, (prefixes, required, optional) in PROVIDERS.items():
        if stem not in profiles:
            continue
        actual = profiles[stem]["ids"]
        missing = required - actual
        extra = actual - required - optional
        if missing:
            errors.append("%s: MISSING_CORE_IDS=%s" % (stem, sorted(missing)))
        if extra:
            errors.append("%s: UNREVIEWED_IDS=%s" % (stem, sorted(extra)))
        if prefixes and any(not cid.startswith(prefixes) for cid in actual):
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
        present_ar = present_language_ratio(profiles["provider-bein"]["programmes"], "desc", "ar")
        if present_ar < 0.90:
            errors.append("provider-bein: present_desc_ar %.1f%% < 90.0%%" % (present_ar * 100.0))
    if "provider-osn" in profiles:
        require_ratio("provider-osn", profiles["provider-osn"]["language"], "title_en", 0.90, errors)
        require_ratio("provider-osn", profiles["provider-osn"]["language"], "desc_ar", 0.90, errors)

    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    combined = profiles.get("mena-arabic", {})
    for key, actual in (
        ("channels", combined.get("channels")),
        ("programmes", combined.get("programme_count")),
        ("size_bytes", combined.get("size_bytes")),
        ("sha256", combined.get("sha256")),
    ):
        if manifest.get(key) != actual:
            errors.append("manifest.json: %s mismatch" % key)

    for split, stem in (("mena", "mena"), ("premium", "premium"), ("legacy_combined", "mena-arabic")):
        row = (manifest.get("splits") or {}).get(split) or {}
        actual = profiles.get(stem, {})
        for key, field in (
            ("channels", "channels"),
            ("programmes", "programme_count"),
            ("size_bytes", "size_bytes"),
            ("sha256", "sha256"),
        ):
            if row.get(key) != actual.get(field):
                errors.append("manifest.json: splits.%s.%s mismatch" % (split, key))

    coverage = combined.get("coverage") or {}
    measured_median = float(coverage.get("median_future_hours", 0.0) or 0.0)
    if measured_median < args.minimum_median_future_hours:
        errors.append("REAL_COVERAGE_MEDIAN=%.2fh < %.2fh" % (measured_median, args.minimum_median_future_hours))

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
        for key, field in (
            ("channels", "channels"),
            ("programmes", "programme_count"),
            ("size_bytes", "size_bytes"),
            ("sha256", "sha256"),
        ):
            if row.get(key) != actual.get(field):
                errors.append("shards.json: %s.%s mismatch" % (stem, key))
    if shard_manifest.get("published_channels") != len(seen):
        errors.append("shards.json: published_channels mismatch")
    if combined and shard_manifest.get("input_channels") != combined.get("channels"):
        errors.append("shards.json: input_channels mismatch")
    for stem in sorted({"mena-arabic", "mena", "premium"} | set(shard_manifest.get("shards") or {})):
        profile = profiles.get(stem)
        if profile and profile["channels_without_future_epg"]:
            errors.append("%s: NO_FUTURE_EPG_IDS=%d" % (stem, len(profile["channels_without_future_epg"])))

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
    if previous_channels:
        ratio = resolved / float(previous_channels)
        current_channels = int(combined.get("channels", 0) or 0)
        if ratio < 0.90:
            errors.append("LKG_BRIDGE_LOW=%d/%d" % (resolved, previous_channels))
        elif ratio < 0.95:
            if current_channels < previous_channels:
                errors.append(
                    "LKG_BRIDGE_MIGRATION_WITH_SHRINK=%d/%d current=%d"
                    % (resolved, previous_channels, current_channels)
                )
            else:
                warnings.append(
                    "LKG_BRIDGE_MIGRATION=%d/%d current=%d"
                    % (resolved, previous_channels, current_channels)
                )

    artifact_status = "FAIL" if errors else "PASS"
    release_blockers = ["ENIGMA2_RECEIVER_NOT_VALIDATED", "RECEIVER_REFRESH_AUTHORIZATION_NOT_ESTABLISHED"]
    if not args.artifact_checks_only:
        errors.extend(release_blockers)
    payload = {
        "schema": 2,
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "warnings": warnings,
        "artifact_checks_status": artifact_status,
        "receiver_release_blockers": release_blockers,
        "provider_channels": {stem: profiles.get(stem, {}).get("channels", 0) for stem in PROVIDERS},
        "language": {stem: profiles.get(stem, {}).get("language", {}) for stem in ("mena", *PROVIDERS)},
        "coverage": coverage,
        "manifest_declared_coverage": manifest.get("coverage") or {},
        "scope": "offline_receiver_artifact_checks",
        "receiver_release_status": "NO_GO",
        "enigma2_receiver_validated": False,
        "release_authorized": False,
        "vuplus_xml_bytes": combined.get("xml_size_bytes", 0),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["RECEIVER ARTIFACT CHECKS: %s" % payload["status"],
             "VU+ RELEASE: NO_GO — hardware validation and authorization are not established by this offline check"]
    lines.extend("- " + error for error in errors)
    lines.extend("- WARNING: " + warning for warning in warnings)
    if not errors:
        lines.append(
            "offline canonical union, manifests, provider cores, policy-aligned language, "
            "LKG continuity, current XMLTV horizons and configured payload-size budget: PASS"
        )
    if args.text:
        Path(args.text).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
