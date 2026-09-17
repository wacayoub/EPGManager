#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final Morocco Cloud master runner.

One GitHub Actions entry point for:
- SNRT historical logic
- Arryadia historical football logic
- 2M full-day Arabic/Darija logic + French-first evening policy
- Chada enrichment/fallback
- Medi1

The Vu+ receiver still downloads only epg-data/morocco.xml.gz.
"""
from __future__ import annotations

import gzip
import re
from collections import defaultdict
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import morocco_epg as base
import morocco_cloud_runner as runner
import morocco_cloud_legacy_logic as legacy
import morocco_cloud_runner_ar2_quality as final2m
import morocco_alaoula_fallback as alaoula_fallback

TZ = runner.TZ

# Keep this entry point tied to the final 2M title-quality overlay so changes to
# the overlay are exercised by the production Morocco workflow.


def _xmltv_dt(value):
    """Parse normal XMLTV offsets and tolerate legacy '+010' style offsets."""
    raw = str(value or "").strip()
    m = re.match(r"^(\d{14})(?:\s+([+-]\d{3,4}))?", raw)
    if not m:
        raise ValueError("bad XMLTV datetime: %r" % raw)
    stamp, offset = m.groups()
    if not offset:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=TZ)
    # Legacy generated feeds occasionally wrote +010 instead of +0100.
    if len(offset) == 4:
        offset += "0"
    return datetime.strptime(stamp + " " + offset, "%Y%m%d%H%M%S %z").astimezone(TZ)


def tolerant_previous(path):
    if not path or not path.exists():
        return []
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        root = ET.fromstring(raw)
    except Exception as exc:
        runner.log("Previous feed container unreadable: %s" % exc)
        return []

    out = []
    skipped = 0
    for p in root.findall("programme"):
        try:
            start = _xmltv_dt(p.get("start"))
            stop = _xmltv_dt(p.get("stop")) if p.get("stop") else None
            t = p.find("title")
            d = p.find("desc")
            title = (t.text or "") if t is not None else ""
            desc = (d.text or "") if d is not None else title
            out.append(base.Event(
                p.get("channel") or "", start, title, desc, stop,
                (t.get("lang") if t is not None else "ar") or "ar",
                (d.get("lang") if d is not None else "ar") or "ar",
                "old",
            ))
        except Exception:
            skipped += 1
    runner.log("Previous feed recovered: %d events, skipped=%d" % (len(out), skipped))
    return out


def _snrt_cloud_cleanup(rows, days):
    """Keep historical SNRT semantics while making the cloud XMLTV timeline strict.

    SNRT pages expose archive rows in addition to the useful current horizon and
    can occasionally expose two DOM rows at exactly the same channel/start time.
    The receiver tolerated that, but an XMLTV cloud feed must never publish
    zero-duration events.  We therefore keep only the requested horizon,
    collapse same-channel/same-start duplicates to the richer row, and infer
    every stop from the next *strictly later* event.
    """
    now = datetime.now(TZ)
    window_start = now - timedelta(hours=12)
    window_end = now + timedelta(days=max(1, int(days)) + 1)
    useful = [e for e in rows if window_start <= e.start < window_end]

    exact = {}
    collapsed = 0
    for e in useful:
        key = (e.channel, e.start)
        old = exact.get(key)
        if old is None:
            exact[key] = e
            continue
        collapsed += 1
        # Same instant cannot carry two linear-TV events. Preserve whichever row
        # has the richer receiver-derived metadata/detail description.
        old_score = len(str(old.desc or "")) * 2 + len(str(old.title or ""))
        new_score = len(str(e.desc or "")) * 2 + len(str(e.title or ""))
        if new_score > old_score:
            exact[key] = e

    by_channel = defaultdict(list)
    for e in exact.values():
        by_channel[e.channel].append(e)

    clean = []
    repaired = 0
    for cid, channel_rows in by_channel.items():
        channel_rows.sort(key=lambda x: x.start)
        for i, e in enumerate(channel_rows):
            next_start = channel_rows[i + 1].start if i + 1 < len(channel_rows) else None
            if next_start and next_start > e.start:
                if e.stop != next_start:
                    repaired += 1
                e.stop = next_start
            elif not e.stop or e.stop <= e.start:
                # Preserve the receiver's broadcaster-day convention at night;
                # otherwise use a safe one-hour tail for the final visible row.
                if e.start.hour < 7:
                    stop = e.start.replace(hour=7, minute=0, second=0, microsecond=0)
                    if stop <= e.start:
                        stop += timedelta(days=1)
                else:
                    stop = e.start + timedelta(hours=1)
                e.stop = stop
                repaired += 1
            clean.append(e)

    clean.sort(key=lambda x: (x.channel, x.start, x.title.casefold()))
    runner.log(
        "SNRT cloud timeline cleanup: input=%d horizon=%d final=%d collapsed=%d stops-repaired=%d"
        % (len(rows), len(useful), len(clean), collapsed, repaired)
    )
    return clean


def _valid_horizon(rows):
    """Return receiver-useful rows using the cloud runner's own LKG horizon."""
    now = datetime.now(TZ)
    return [
        e for e in rows
        if e.start < now + timedelta(days=8)
        and (e.stop or e.start + timedelta(hours=1)) > now - timedelta(hours=12)
    ]


def _install_per_channel_lkg():
    """Upgrade provider-level LKG to channel-level completion.

    A provider group can have enough total events while one stable channel is
    empty (the AlAoula regression). Keep the provider decision, then recover
    only missing channel IDs from the previous receiver feed when useful LKG
    rows exist. No EPG is invented.
    """
    historical_choose = runner.choose

    def choose_channel_complete(name, fresh, old, channel_ids, minimum):
        rows, mode = historical_choose(name, fresh, old, channel_ids, minimum)
        present = {e.channel for e in _valid_horizon(rows)}
        recovered = {}
        for cid in sorted(set(channel_ids) - present):
            lkg = [e for e in _valid_horizon(old) if e.channel == cid]
            if not lkg:
                continue
            rows.extend(lkg)
            recovered[cid] = len(lkg)
        if recovered:
            mode = "%s+channel-lkg" % mode
            runner.log("%s: per-channel LKG recovered %s" % (name, recovered))
        return rows, mode

    runner.choose = choose_channel_complete


def _strict_cloud_timeline(rows):
    """Collapse same-start duplicates and guarantee stop > start on every channel."""
    exact = {}
    collapsed = 0
    for e in rows:
        if not getattr(e, "channel", "") or not getattr(e, "start", None):
            continue
        key = (e.channel, e.start)
        old = exact.get(key)
        if old is None:
            exact[key] = e
            continue
        collapsed += 1
        old_valid_stop = bool(old.stop and old.stop > old.start)
        new_valid_stop = bool(e.stop and e.stop > e.start)
        old_score = len(str(old.desc or "")) * 2 + len(str(old.title or "")) + (20 if old_valid_stop else 0)
        new_score = len(str(e.desc or "")) * 2 + len(str(e.title or "")) + (20 if new_valid_stop else 0)
        if new_score > old_score:
            exact[key] = e

    by_channel = defaultdict(list)
    for e in exact.values():
        by_channel[e.channel].append(e)

    clean = []
    repaired = 0
    for cid, channel_rows in by_channel.items():
        channel_rows.sort(key=lambda x: x.start)
        for i, e in enumerate(channel_rows):
            next_start = channel_rows[i + 1].start if i + 1 < len(channel_rows) else None
            if not e.stop or e.stop <= e.start:
                e.stop = next_start if next_start and next_start > e.start else e.start + timedelta(hours=1)
                repaired += 1
            elif next_start and e.stop > next_start:
                # Linear-TV programmes cannot overlap the next event. Trim only
                # at serialization time; programme title/description stay intact.
                e.stop = next_start
                repaired += 1
            if e.stop <= e.start:
                e.stop = e.start + timedelta(hours=1)
                repaired += 1
            clean.append(e)

    clean.sort(key=lambda x: (x.channel, x.start, x.title.casefold()))
    runner.log(
        "Global Morocco timeline cleanup: input=%d final=%d collapsed=%d stops-repaired=%d"
        % (len(rows), len(clean), collapsed, repaired)
    )
    return clean


def _install_strict_writer():
    """Refuse a receiver candidate containing any zero-EPG stable channel."""
    historical_write = runner.write_xml

    def strict_write(rows, path):
        clean = _strict_cloud_timeline(rows)
        useful_ids = {e.channel for e in _valid_horizon(clean)}
        missing = sorted(set(base.CHANNELS) - useful_ids)
        if missing:
            raise RuntimeError(
                "Refusing Morocco candidate with zero useful EPG for: %s"
                % ", ".join(missing)
            )
        return historical_write(clean, path)

    runner.write_xml = strict_write


def main():
    # The legacy /ar/node/1208 and /fr/programmes/alaoula grids can expose stale
    # cache rows. Keep the active official page as primary; if it still has no
    # useful dated programme, a guarded TeleNews fallback is injected below.
    legacy.SNRT_CHANNELS["AlAoula"] = "https://www.snrt.ma/ar/al-aoula"

    # Install receiver-proven Moroccan source behaviour before runner.main()
    # builds its parallel provider jobs.
    legacy.install()

    # Wrap the historical SNRT result only at the cloud serialization boundary:
    # title/description/news/TNT/weather logic stays untouched.
    historical_snrt = base.scrape_snrt

    def cloud_snrt(days):
        rows = historical_snrt(days)
        current_alaoula = [
            e for e in _valid_horizon(rows)
            if e.channel == "AlAoula"
        ]
        if not current_alaoula:
            runner.log("AlAoula official SNRT has zero useful events; trying guarded fallback")
            try:
                backup = alaoula_fallback.scrape(min(days, 3))
            except Exception as exc:
                runner.log("AlAoula guarded fallback failed: %s" % exc)
                backup = []
            if backup:
                rows.extend(backup)
                runner.log("AlAoula guarded fallback accepted: %d events" % len(backup))
            else:
                runner.log("AlAoula guarded fallback returned zero events")
        return _snrt_cloud_cleanup(rows, days)

    base.scrape_snrt = cloud_snrt

    # Global safety overlays: provider groups may use LKG per channel, and the
    # final receiver timeline must be strict across SNRT, Arryadia, 2M, Chada
    # and Medi1 before the candidate can be serialized.
    _install_per_channel_lkg()
    _install_strict_writer()

    runner.read_previous = tolerant_previous
    return final2m.main()


if __name__ == "__main__":
    raise SystemExit(main())
