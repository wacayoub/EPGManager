#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish healthy candidate feeds independently with last-known-good protection.

A broken or incomplete source must never block publication of the other sources.
The strict 48h report remains diagnostic; this publisher applies conservative
per-source freshness/regression gates and keeps the previous feed on failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import hashlib
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET

OUT = Path("output/final")
FEEDS = Path("feeds")
REPORTS = Path("reports")
FEEDS.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)

POLICY = {
    # A SNRT outage must not freeze genuine 2M/Chada/Medi1 publications.
    # Three real programme-bearing services, full catalogue retention and
    # explicit current-health warnings replace synthetic Arryadia coverage.
    "morocco":   {"min_channels": 3,  "min_programmes": 50,  "min_future_ratio": 0.70, "min_horizon_hours": 8},
    "elcinema":  {"min_channels": 20, "min_programmes": 100, "min_future_ratio": 0.70, "min_horizon_hours": 8},
    "osn":       {"min_channels": 20, "min_programmes": 100, "min_future_ratio": 0.80, "min_horizon_hours": 8},
    # beIN's official package intentionally retains some catalogue IDs whose
    # guide is temporarily empty/short. 80% future coverage still requires a
    # large healthy feed while allowing valid Kids/partner updates to publish.
    "bein":      {"min_channels": 35, "min_programmes": 500, "min_future_ratio": 0.80, "min_horizon_hours": 8},
    "shahid":    {"min_channels": 20, "min_programmes": 100, "min_future_ratio": 0.60, "min_horizon_hours": 8},
    "rotana":    {"min_channels": 5,  "min_programmes": 40,  "min_future_ratio": 0.60, "min_horizon_hours": 8},
    # Tabie exports only IDs with genuine current/future programmes.
    # The provider may return ONLY its current date. An expired old LKG
    # must not forever block 3 valid new channels with a real near-future
    # first event; a strict future-only recovery gate below handles this.
    # Provider replay of the same date must never count as future days.
    "tabie":     {"min_channels": 3,  "min_programmes": 80,  "min_future_ratio": 0.80, "min_horizon_hours": 8},
    "alkass":    {"min_channels": 8,  "min_programmes": 40,  "min_future_ratio": 0.75, "min_horizon_hours": 24},
    "tunisiatv": {"min_channels": 2,  "min_programmes": 30,  "min_future_ratio": 1.00, "min_horizon_hours": 24},
    # Sport24 is event-driven; valid event schedules can naturally expose less
    # than eight continuous hours while still being fresher than the current LKG.
    "sport24":   {"min_channels": 2,  "min_programmes": 10,  "min_future_ratio": 0.60, "min_horizon_hours": 4},
    # Direct STARZPLAY preferred; fallback is verified STC public metadata for Sports 1-3.

    "starzplay":  {"min_channels": 3,  "min_programmes": 20, "min_future_ratio": 0.66, "min_horizon_hours": 8},
    # Dubai+ official public API: compact nine-channel DMI lineup.
    "dubaiplus": {"min_channels": 7,  "min_programmes": 20,  "min_future_ratio": 0.70, "min_horizon_hours": 4},
    # Al Jazeera official public schedule. Four IDs are retained; publication
    # accepts two active channels because the official indexed guide currently
    # exposes complete schedules for Al Jazeera and Al Jazeera 2.
    "aljazeera": {"min_channels": 2, "min_programmes": 40, "min_future_ratio": 0.95, "min_horizon_hours": 36},
    # STC production preserves the complete public catalogue. The standard
    # regression gate must protect it from a partial sports-only scrape.
    "stctv":     {"min_channels": 3, "min_programmes": 20, "min_future_ratio": 1.00, "min_horizon_hours": 8},
}
DT_RE = re.compile(r"^(\d{14}|\d{12})(?:\s*([+-]\d{4}|Z))?$")


def parse_dt(value: str | None):
    m = DT_RE.match((value or "").strip())
    if not m:
        return None
    digits, off = m.group(1), m.group(2)
    fmt = "%Y%m%d%H%M%S" if len(digits) == 14 else "%Y%m%d%H%M"
    x = datetime.strptime(digits, fmt)
    if not off or off == "Z":
        return x.replace(tzinfo=timezone.utc)
    sign = 1 if off[0] == "+" else -1
    mins = sign * (int(off[1:3]) * 60 + int(off[3:5]))
    return x.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)


def load_root(path: Path):
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def stats(path: Path):
    root = load_root(path)
    if root.tag != "tv":
        raise ValueError("invalid_xmltv_root")
    channel_rows = [(c.get("id") or "").strip() for c in root.findall("channel")]
    if any(not cid for cid in channel_rows) or len(channel_rows) != len(set(channel_rows)):
        raise ValueError("empty_or_duplicate_channel_id")
    channels = set(channel_rows)
    channels.discard("")
    now = datetime.now(timezone.utc)
    valid_by_channel = {cid: 0 for cid in channels}
    future_by_channel = {cid: 0 for cid in channels}
    programmes = 0
    invalid = 0
    missing_title_rows = 0
    currently_covered = set()
    latest_stop = None
    earliest_future_start = None
    seen_programmes = set()

    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        start = parse_dt(p.get("start"))
        stop = parse_dt(p.get("stop"))
        if cid not in channels or not start or not stop or stop <= start:
            invalid += 1
            continue
        # Equivalent XMLTV offsets describe the same absolute programme slot.
        identity = (cid, start, stop, tuple((t.tag, tuple(sorted(t.attrib.items())), t.text)
                                           for t in p))
        if identity in seen_programmes:
            invalid += 1
            continue
        seen_programmes.add(identity)
        programmes += 1
        if not any((t.text or "").strip() for t in p.findall("title")):
            missing_title_rows += 1
        if start <= now < stop:
            currently_covered.add(cid)
        valid_by_channel[cid] = valid_by_channel.get(cid, 0) + 1
        if stop > now:
            future_by_channel[cid] = future_by_channel.get(cid, 0) + 1
            if latest_stop is None or stop > latest_stop:
                latest_stop = stop
            if earliest_future_start is None or start < earliest_future_start:
                earliest_future_start = start

    active_channels = sum(1 for n in valid_by_channel.values() if n > 0)
    future_channels = sum(1 for n in future_by_channel.values() if n > 0)
    # Zero-EPG catalogue IDs stay visible but do not dilute source freshness.
    ratio = future_channels / active_channels if active_channels else 0.0
    horizon = ((latest_stop - now).total_seconds() / 3600.0) if latest_stop else 0.0
    return {
        "channels": len(channels),
        "active_channels": active_channels,
        "zero_epg_channels": max(0, len(channels) - active_channels),
        "programmes": programmes,
        "future_channels": future_channels,
        "future_ratio": ratio,
        "current_channels": len(currently_covered),
        "current_ratio": (len(currently_covered) / active_channels if active_channels else 0.0),
        "future_horizon_hours": max(0.0, horizon),
        "first_future_start_lead_hours": (
            round(max(0.0,(earliest_future_start-now).total_seconds()/3600.0),2)
            if earliest_future_start else None
        ),
        "missing_title_rows": missing_title_rows,
        "invalid_rows": invalid,
    }


def evaluate(key: str, candidate: Path, previous: Path):
    p = POLICY[key]
    cur = stats(candidate)
    reasons = []

    if cur["active_channels"] < p["min_channels"]:
        reasons.append(f"active_channels<{p['min_channels']}")
    if cur["programmes"] < p["min_programmes"]:
        reasons.append(f"programmes<{p['min_programmes']}")
    if cur["future_ratio"] < p["min_future_ratio"]:
        reasons.append(f"future_ratio<{p['min_future_ratio']:.2f}")
    min_horizon = float(p.get("min_horizon_hours", 8))
    if cur["future_horizon_hours"] < min_horizon:
        reasons.append(f"future_horizon<{min_horizon:g}h")
    if cur["invalid_rows"] > 0:
        reasons.append(f"invalid_rows={cur['invalid_rows']}")
    if cur["missing_title_rows"] > 0:
        reasons.append(f"missing_title_rows={cur['missing_title_rows']}")

    old = None
    recovery_short_horizon = False
    if previous.exists() and previous.stat().st_size:
        try:
            old = stats(previous)
            # Reject catastrophic regressions while allowing legitimate lineup changes.
            # One deliberate migration is allowed for beIN: the old feed contained
            # package/third-party/AFC IDs; production is now scoped to ~43 owned
            # beIN MENA services. Strong beIN minimums above still protect quality.
            planned_bein_scope_rebase = (
                key == "bein"
                and old["channels"] >= 60
                and 35 <= cur["channels"] <= 50
                and cur["active_channels"] >= 35
                and cur["future_ratio"] >= 0.90
            )
            # Tabie/QMC sometimes returns the current Qatar day for requested
            # future dates. A new 8-24h *real* guide is safer than retaining a
            # fully expired archive, but MUST be labelled as partial coverage.
            # Never apply if the old feed has any future events, or the
            # candidate lacks a verified near-term programme.
            # Tabie can serve three working IDs while the previous feed
            # contains nine EMPTY/EXPIRED IDs. Publication must preserve
            # ONLY actual valid programme-bearing IDs, without allowing
            # arbitrary empty or distant fake schedules through.
            # A current_ratio of zero at 00:30 GMT is not necessarily a
            # source failure if the first authentic programme starts soon.
            tabie_genuine_ids = {
                "QatarTelevision.qa@SD", "QatarTelevision2.qa@SD",
                "QatarQuranTV.qa@SD", "QatarBusinessChannel.qa@SD",
                "AlkassOne.qa@SD", "AlRayyanTV.qa@SD",
                "AlRayyanAlQadeem.qa@SD", "TabieNow.qa", "TabieKids.qa"
            }
            candidate_tabie_ids = (
                {c.get("id") for c in load_root(candidate).findall("channel")}
                if key == "tabie" else set()
            )
            recovery_short_horizon = (
                key == "tabie"
                and old["future_channels"] == 0
                and 3 <= cur["channels"] <= 9
                and cur["active_channels"] == cur["channels"]
                and cur["future_channels"] == cur["channels"]
                and cur["future_ratio"] >= 0.99
                and cur["future_horizon_hours"] >= 8
                and cur["programmes"] >= p["min_programmes"]
                and cur["first_future_start_lead_hours"] is not None
                and cur["first_future_start_lead_hours"] <= 6.0
                and candidate_tabie_ids.issubset(tabie_genuine_ids)
                and len(candidate_tabie_ids) == cur["channels"]
                and cur["missing_title_rows"] == 0
                and cur["invalid_rows"] == 0
            )
            # Do not replace a still-useful Tabie guide by a much shorter
            # current-day replay. Recovery exemption is for expired LKG only.
            if (key == "tabie" and old["future_channels"] > 0 and
                    cur["future_horizon_hours"] + 4 < old["future_horizon_hours"] and
                    cur["future_horizon_hours"] < old["future_horizon_hours"] * 0.65):
                reasons.append("future_horizon_regression>35%")
            planned_scope_rebase = planned_bein_scope_rebase or recovery_short_horizon
            if (
                not planned_scope_rebase
                and old["channels"]
                and cur["channels"] < max(p["min_channels"], int(old["channels"] * 0.60))
            ):
                reasons.append("channel_regression>40%")
            if old["programmes"] and not planned_scope_rebase:
                # Compare like-for-like windows. A 48h candidate must not be
                # rejected merely because the previous LKG still contains
                # several days of older/future programmes.
                old_h=max(float(old.get("future_horizon_hours") or 0.0),1.0)
                cur_h=max(float(cur.get("future_horizon_hours") or 0.0),1.0)
                comparable_old=old["programmes"]*min(1.0,cur_h/old_h)
                if cur["programmes"] < max(p["min_programmes"], int(comparable_old * 0.35)):
                    reasons.append("programme_regression>65%")
        except Exception as exc:
            old = {"warning": f"previous unreadable: {exc}"}

    if recovery_short_horizon:
        reasons = [r for r in reasons if not r.startswith("future_horizon<")]
        cur["partial_recovery_from_expired_lkg"] = True
        cur["hours_of_recovered_guide"] = round(cur["future_horizon_hours"], 1)
    # A source that is current only at the end of its 48h horizon is not
    # usable for Smart Mapping now. Restrict this gate to programme-rich
    # linear sources; live-news rolling schedules are a separate policy.
    # ElCinema has genuine multi-hour holes in its official guide.
    # A fixed threshold at the morning cron slot could freeze publication
    # forever. Refuse only a *large regression* versus the current LKG;
    # continuous/48h coverage problems remain explicitly reported by audits.
    if (key == "elcinema" and old and
            old.get("current_channels", 0) >= 10 and
            old.get("current_ratio", 0.0) - cur["current_ratio"] > 0.25):
        reasons.append("current_coverage_regression>25pp")
    minimum_now = {"tabie": 0.60, "shahid": 0.60}
    floor = minimum_now.get(key)
    if floor is not None and cur["current_ratio"] < floor and not recovery_short_horizon:
        reasons.append(f"current_ratio<{floor:.2f}")
    return cur, old, reasons


def verify_bundle(key, candidate, measured):
    """Check every sidecar before any last-known-good file is replaced."""
    raw = candidate.read_bytes()
    metadata = json.loads(candidate.with_name(key + ".json").read_text(encoding="utf-8"))
    expected = {"label": key, "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw), "channels": measured["channels"],
                "programmes": measured["programmes"]}
    if any(metadata.get(field) != value for field, value in expected.items()):
        raise ValueError("candidate_manifest_mismatch")
    generated = datetime.fromisoformat(metadata["generated_utc"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("candidate_metadata_timezone_missing")
    index = candidate.with_name(key + ".txt").read_text(encoding="utf-8").splitlines()
    ids = [row.split("|", 1)[0].strip() for row in index if row.strip()]
    declared = {c.get("id") for c in load_root(candidate).findall("channel")}
    if len(ids) != len(set(ids)) or set(ids) != declared:
        raise ValueError("candidate_id_index_mismatch")


ap = argparse.ArgumentParser()
ap.add_argument("--source", choices=sorted(POLICY), help="Publish only one source; default publishes all candidates")
ap.add_argument("--report", default="reports/publish-lkg.json")
args = ap.parse_args()

selected_sources = [args.source] if args.source else list(POLICY)

result = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
    "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    "sources": {},
}

for key in selected_sources:
    src = OUT / f"{key}.xml.gz"
    dst = FEEDS / f"{key}.xml.gz"
    row = {"candidate": str(src), "published": False}

    if not src.exists() or not src.stat().st_size:
        row["decision"] = "KEEP_LKG"
        row["reasons"] = ["candidate_missing"]
        result["sources"][key] = row
        print("KEEP LKG", key, "candidate missing")
        continue

    try:
        cur, old, reasons = evaluate(key, src, dst)
        row["candidate_stats"] = cur
        if old is not None:
            row["previous_stats"] = old
        if reasons:
            row["decision"] = "KEEP_LKG"
            row["reasons"] = reasons
            result["sources"][key] = row
            print("KEEP LKG", key, "; ".join(reasons))
            continue

        verify_bundle(key, src, cur)
        shutil.copy2(src, dst)
        for ext in ("json", "txt"):
            p = OUT / f"{key}.{ext}"
            if p.exists():
                shutil.copy2(p, FEEDS / p.name)
        row["decision"] = "PUBLISH"
        row["published"] = True
        row["reasons"] = []
        result["sources"][key] = row
        print(
            "PUBLISHED", key,
            f"channels={cur['channels']}",
            f"active={cur['active_channels']}",
            f"programmes={cur['programmes']}",
            f"future={cur['future_ratio']:.1%}",
            f"horizon={cur['future_horizon_hours']:.1f}h",
        )
    except Exception as exc:
        row["decision"] = "KEEP_LKG"
        row["reasons"] = [f"candidate_error:{exc}"]
        result["sources"][key] = row
        print("KEEP LKG", key, exc)

report_path = Path(args.report)
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
