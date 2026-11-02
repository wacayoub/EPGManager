#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail closed if ElCinema's deployed runtime misreads Morocco legal GMT.

Morocco decree 2.26.530 (25 June 2026), official gazette 7521:
20 September 2026 02:00 local: move from UTC+01 to UTC+00.
Python must recognize the legal timezone. Node.js 20 may keep outdated
ICU rules (+01 after Sep 20); in that precise case only, permit the
adapter's verified explicit Etc/UTC override for post-transition dates.
This does not alter published XMLTV rows. UTC XMLTV remains canonical.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

CHECKS = (
    ("2026-09-19T20:00:00+00:00", 60),
    ("2026-10-17T20:00:00+00:00", 0),
    ("2026-11-15T20:00:00+00:00", 0),
    ("2027-04-15T20:00:00+00:00", 0),
)
CASABLANCA = ZoneInfo("Africa/Casablanca")


def check_python():
    results = []
    for instant, expected_minutes in CHECKS:
        utc = datetime.fromisoformat(instant)
        local = utc.astimezone(CASABLANCA)
        minutes = int(local.utcoffset().total_seconds() / 60)
        if minutes != expected_minutes:
            raise RuntimeError(
                "ELCINEMA_MOROCCO_STALE_TZDATA: Python date=%s offset=%s expected=%s"
                % (instant, minutes, expected_minutes)
            )
        results.append({"utc": instant, "casablanca_offset_minutes": minutes})
    return results


def check_node():
    script = r"""
const checks = JSON.parse(process.argv[1]);
for (const [date, expectedOffsetMinutes] of checks) {
  const instant = new Date(date);
  function offsetMinutes(zone) {
    const f=new Intl.DateTimeFormat('en-GB', {
      timeZone:zone, year:'numeric',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'
    });
    const parts=Object.fromEntries(f.formatToParts(instant)
      .filter(x=>x.type!=='literal').map(x=>[x.type,x.value]));
    const projected=Date.UTC(
      Number(parts.year),Number(parts.month)-1,Number(parts.day),
      Number(parts.hour),Number(parts.minute));
    return Math.round((projected-instant.getTime())/60000);
  }
  const observed=offsetMinutes('Africa/Casablanca');
  const utc=offsetMinutes('Etc/UTC');
  if (utc!==0) {
    console.error('ELCINEMA_UTC_FALLBACK_BROKEN',date,utc);
    process.exit(2);
  }
  if (observed===expectedOffsetMinutes) {
    console.log('ELCINEMA_NODE_TZ_CHECK',date,observed);
  } else if (date.slice(0,10)>='2026-09-21'
      && expectedOffsetMinutes===0 && observed===60) {
    // The JS adapter pins post-decree schedules to verified Etc/UTC.
    console.log('ELCINEMA_NODE_STALE_ICU_WORKAROUND',date,
      'native',observed,'used_legal_utc',utc);
  } else {
    console.error('ELCINEMA_MOROCCO_UNEXPECTED_TIMEZONE',date,observed,
      'expected',expectedOffsetMinutes);
    process.exit(2);
  }
}
"""
    # Run the actual injected JavaScript statement with an instrumented
    # dayjs.tz stub to prove post-Sep20 dates choose UTC regardless of ICU.
    from elcinema_midnight_patch import PIN_TZ
    script += chr(10) + chr(10).join((
        "const dayjs={tz:(time,fmt,zone)=>({time,fmt,zone})};",
        "function parseElCinema(time){",
        PIN_TZ,
        "}",
        "for(const [t,z] of [",
        " ['2026-09-19 08:00 PM','Africa/Casablanca'],",
        " ['2026-10-17 08:00 PM','Etc/UTC'],",
        " ['2027-04-15 08:00 PM','Etc/UTC']]) {",
        " const r=parseElCinema(t);",
        " if(r.zone!==z){console.error('ELCINEMA_ADAPTER_ZONE_WRONG',t,r.zone,z);process.exit(2)}",
        " console.log('ELCINEMA_REAL_ADAPTER_POLICY_CHECK',t,r.zone);",
        "}",
    )) + chr(10)
    try:
        run = subprocess.run(
            ["node", "-e", script, json.dumps(CHECKS)],
            capture_output=True, text=True, timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ELCINEMA_NODE_TZ_UNAVAILABLE") from exc
    if run.returncode != 0:
        raise RuntimeError(run.stdout + run.stderr)
    return run.stdout.strip().splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", help="Optional already-patched ElCinema JS adapter")
    args = parser.parse_args()
    if args.adapter:
        from elcinema_midnight_patch import PIN_TZ
        data = Path(args.adapter).read_text(encoding="utf-8")
        if data.count(PIN_TZ) != 1:
            raise RuntimeError("ELCINEMA_ADAPTER_NOT_PINNED_TO_CASABLANCA")
    from elcinema_midnight_patch import PIN_TZ
    if ("civilDate >= '2026-09-21'" not in PIN_TZ
            or "'Etc/UTC'" not in PIN_TZ):
        raise RuntimeError("ELCINEMA_LEGAL_UTC_OVERRIDE_NOT_PINNED")
    results = check_python()
    node_results = check_node()
    print("ELCINEMA_MOROCCO_LEGAL_TIMEZONE_PASS", json.dumps({
        "legal_timezone": "Africa/Casablanca",
        "after_2026_09_20": "UTC+00:00 permanent",
        "python_results": results,
        "node_results": node_results,
        "adapter_checked": bool(args.adapter),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
