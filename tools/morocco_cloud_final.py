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
from datetime import datetime
from xml.etree import ElementTree as ET

import morocco_epg as base
import morocco_cloud_runner as runner
import morocco_cloud_legacy_logic as legacy
import morocco_cloud_runner_ar2 as final2m

TZ = runner.TZ


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


def main():
    # Install receiver-proven Moroccan source behaviour before runner.main()
    # builds its parallel provider jobs.
    legacy.install()
    runner.read_previous = tolerant_previous
    return final2m.main()


if __name__ == "__main__":
    raise SystemExit(main())
