#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-deploying RC80 receiver-cache verifier smoke on REAL public XMLTV bytes.

No receiver access, settings, mappings, installation, or local cache write.
The result distinguishes working parser/integrity from source-data quality.
Run only in CI or other non-receiver staging environment.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source/overlay"))
from core import verified_epg_refresh


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="morocco,shahid",
                        help="1-3 validated public feed names, comma-separated")
    parser.add_argument("--require-bytes-verified", action="store_true")
    args = parser.parse_args(argv)
    sources = list(dict.fromkeys(x.strip() for x in args.sources.split(",") if x.strip()))
    if not sources or len(sources) > 3 or any(x not in verified_epg_refresh.evidence.VALID_DIRECT for x in sources):
        parser.error("Supply 1-3 known direct public source names")
    now = time.time()
    results = []
    for source in sources:
        row = verified_epg_refresh.inspect_source(source, now=now)
        results.append({
            "source": source,
            "status": row["status"],
            "xmltv_sha256_verified": row["xmltv_sha256_verified"],
            "ready_for_epg_ok": row["ready_for_epg_ok"],
            "live_now": row.get("xmltv_live_now_channels"),
            "active": row.get("xmltv_active_channels"),
            "trusted_id_count": len(row["trusted_current_epg_ids"]),
            "sample_trusted_ids": row["trusted_current_epg_ids"][:5],
            "error_type": row.get("error_type"),
        })
    report = {"checked_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "read_only": True, "receiver_modified": False, "install_authorized": False,
              "results": results}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.require_bytes_verified and not any(row["xmltv_sha256_verified"] for row in results):
        print("FAIL: no public compressed XMLTV passed actual SHA verification", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
