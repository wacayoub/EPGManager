# -*- coding: utf-8 -*-
"""EPGManager: read-only direct GitHub XMLTV freshness checks.

The Native EPGImport engine consumes HTTPS *.xml.gz; this module only reads
the corresponding EPG-Scrapers health report, never a receiver-side XML copy.
"""
from __future__ import print_function
import json
import time
from datetime import datetime
from urllib.request import Request, urlopen

PREFIX = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/feeds/"
AUDITS = "https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/reports/"


def _source_key(item):
    url = str((item or {}).get("url") or "")
    if not (url.startswith(PREFIX) and url.endswith(".xml.gz")):
        return ""
    tail = url[len(PREFIX):-len(".xml.gz")]
    if not tail or not all(c.isalnum() or c == "-" for c in tail):
        return ""
    return tail


def _as_epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _check_one(item):
    sid = str((item or {}).get("id") or "")
    name = str((item or {}).get("name") or sid)
    row = {"id": sid, "name": name, "ok": False, "mode": "remote-url"}
    key = _source_key(item)
    if not key:
        row["error"] = "Not a recognized EPG-Scrapers URL"
        return row
    try:
        req = Request(AUDITS + key + "-48h-audit.json",
                      headers={"User-Agent": "EPGManager-GitHub-Direct/1.0",
                               "Cache-Control": "no-cache"})
        with urlopen(req, timeout=12) as response:
            raw = response.read(260000)
        report = json.loads(raw.decode("utf-8"))
        audit_time = _as_epoch(report.get("generated_utc"))
        now = time.time()
        if not audit_time or abs(now - audit_time) > 72 * 3600:
            row["error"] = "Outdated GitHub EPG audit"
            return row
        feeds = report.get("feeds") or []
        feed = next((x for x in feeds
                     if x.get("feed") == key + ".xml.gz"), None)
        if not feed:
            row["error"] = "Missing source audit"
            return row
        rows = feed.get("rows") or []
        counts = sum(1 for x in rows
                     if _as_epoch(x.get("latest_stop_utc")) > now + 7200)
        total = int(feed.get("channels") or len(rows) or 0)
        if counts == 0 or (total >= 5 and counts * 100 < total * 30):
            row["error"] = ("Insufficient future EPG (%s/%s channels)" %
                            (counts, total))
            return row
        row.update(ok=True, active_channels=counts, channels=total)
    except Exception as exc:
        row["error"] = "GitHub EPG audit unavailable: %s" % exc
    return row


def check_selected_sources(selected):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    direct = [x for x in (selected or []) if (x or {}).get("direct_feed")]
    output = {"checked": len(direct), "fresh": 0, "failed": 0,
              "rows": [], "paths": {}, "mode": "github-direct-xmltv"}
    if not direct:
        return output
    with ThreadPoolExecutor(max_workers=min(4, len(direct))) as pool:
        futures = {pool.submit(_check_one, item): item for item in direct}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:
                row = {"id": item.get("id"), "name": item.get("name"),
                       "ok": False, "error": str(exc)}
            output["rows"].append(row)
            output["fresh" if row["ok"] else "failed"] += 1
    return output
