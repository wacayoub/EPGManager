#!/usr/bin/env python3
"""Read-only discovery probe for Al Araby official daily schedules.

Does not publish feeds or alter production routing.
"""
import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path
import requests

URL = "https://www.alaraby.com/tv-guide/{day}"
TIME = re.compile(r"(?<!\\d)([01]?\\d|2[0-3]):[0-5]\\d\\s*[-–]\\s*([01]?\\d|2[0-3]):[0-5]\\d")
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--report", default="reports/alaraby-official-probe.json")
    a = p.parse_args()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 EPG-Scrapers/AlArabyProbe", "Accept-Language": "ar"})
    rows = []
    for i in range(max(1, min(a.days, 7))):
        day = (date.today() + timedelta(days=i)).isoformat()
        item = {"date": day, "url": URL.format(day=day)}
        try:
            response = session.get(item["url"], timeout=25)
            item["http_status"] = response.status_code
            response.raise_for_status()
            html = response.text
            item["time_ranges_found"] = len(TIME.findall(re.sub(r"<[^>]+>", " ", html)))
            item["contains_arabic"] = bool(re.search(r"[\\u0600-\\u06ff]", html))
            item["candidate_only"] = True
        except requests.RequestException as exc:
            item["error"] = str(exc)
        rows.append(item)
    report = {"source": "alaraby.com", "production_enabled": False, "days": rows}
    path = Path(a.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if any(x.get("time_ranges_found", 0) > 0 for x in rows) else 2
if __name__ == "__main__":
    raise SystemExit(main())
