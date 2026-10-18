#!/usr/bin/env python3
"""Measure all public feeds on a workstation; no receiver interaction.

Prints bounded counts and durations, never programme contents. MENA readiness
is downloaded once per run. These timings cannot certify receiver latency.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import types
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source/overlay"))
from core import verified_epg_refresh as refresh


def qualify(compare_ref=None):
    now = time.time()
    started = time.monotonic()
    readiness = refresh._download("reports/mena-readiness.json", refresh.JSON_LIMIT)
    document = json.loads(readiness.decode("utf-8"))
    baseline = None
    if compare_ref:
        code = subprocess.check_output(["git", "show",
            compare_ref + ":source/overlay/core/verified_epg_refresh.py"], cwd=Path(__file__).resolve().parents[1])
        baseline = types.ModuleType("core.qualification_baseline_refresh")
        baseline.__package__ = "core"
        exec(compile(code, "baseline/verified_epg_refresh.py", "exec"), baseline.__dict__)

    def reader(path, limit):
        if path == "reports/mena-readiness.json":
            if len(readiness) > limit:
                raise ValueError("readiness too large")
            return readiness
        return refresh._download(path, limit)

    def inspect(source):
        before = time.monotonic()
        snapshot = {}
        def source_reader(path, limit):
            if path not in snapshot:
                snapshot[path] = reader(path, limit)
            if len(snapshot[path]) > limit:
                raise ValueError("snapshot exceeds budget")
            return snapshot[path]
        previous = baseline.inspect_source(source, now=now, reader=source_reader) if baseline else None
        row = refresh.inspect_source(source, now=now, reader=source_reader)
        summary = {
            "source": source, "status": row["status"],
            "seconds_on_workstation": round(time.monotonic() - before, 3),
            "sha256_verified": row["xmltv_sha256_verified"],
            "ready_for_epg_ok": row["ready_for_epg_ok"],
            "trusted_ids": len(row["trusted_current_epg_ids"]),
            "no_future_ids": len(row.get("xmltv_no_future_ids", [])),
            "mena_empty_owner_ids": len(row.get("mena_no_future_ids", [])),
            "mena_degraded_owner_ids": len(row.get("mena_degraded_ids", [])),
            "error_type": row.get("error_type"),
        }
        if previous is not None:
            summary["before_status"] = previous["status"]
            summary["before_trusted_ids"] = len(previous["trusted_current_epg_ids"])
        return summary

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(inspect, sorted(refresh.evidence.VALID_DIRECT)))
    return {
        "checked_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "receiver_modified": False, "hardware_tested": False,
        "workstation_python": sys.version.split()[0],
        "compare_ref": compare_ref,
        "seconds_on_workstation": round(time.monotonic() - started, 3),
        "readiness_downloads": 1,
        "readiness_sha256": hashlib.sha256(readiness).hexdigest(),
        "readiness_checked_utc": document.get("checked_utc"),
        "published_selected_statuses": document.get("selected_statuses"),
        "sources": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-ref", help="reviewed local Git ref for same-byte comparison")
    args = parser.parse_args()
    if Path("/proc/stb/info/model").exists():
        parser.error("run on workstation, never receiver")
    report = qualify(args.compare_ref)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
