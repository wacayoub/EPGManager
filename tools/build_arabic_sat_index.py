#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an Arabic-satellite channel availability index from LyngSat.

Positions monitored:
- 26E: Badr 7 + Badr 8
- 7W: Nilesat 201 + Nilesat 301 + Eutelsat 7 West A
- 8W: Eutelsat 8 West B
- 25.8E: Es'hail 2

The output is a compact JSON containing normalized text tokens per orbital
position. The renderer then matches every winner channel against all positions.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

PAGES = {
    "26E": [
        "https://www.lyngsat.com/Badr-7.html",
        "https://www.lyngsat.com/Badr-8.html",
    ],
    "7W": [
        "https://www.lyngsat.com/Nilesat-201.html",
        "https://www.lyngsat.com/Nilesat-301.html",
        "https://www.lyngsat.com/Eutelsat-7-West-A.html",
    ],
    "8W": [
        "https://www.lyngsat.com/Eutelsat-8-West-B.html",
    ],
    "25.8E": [
        "https://www.lyngsat.com/Eshail-2.html",
    ],
}

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def normalize(value: str) -> str:
    s = html.unescape(value or "")
    s = TAG_RE.sub(" ", s)
    s = s.replace("\xa0", " ")
    s = s.casefold()
    s = re.sub(r"\b(?:uhd|fhd|hd|sd|digital|channel|tv)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", s)
    return " ".join(s.split())


def fetch(url: str) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 EPGManager/1.0",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urlopen(req, timeout=30) as r:
        raw = r.read()
    return raw.decode("utf-8", "ignore")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    data = {"schema": 1, "source": "LyngSat", "positions": {}}
    failures = []
    for pos, urls in PAGES.items():
        combined = []
        ok_urls = []
        for url in urls:
            try:
                body = fetch(url)
                # Keep both normalized whole-page text and individual TD text.
                text = normalize(body)
                if text:
                    combined.append(text)
                    ok_urls.append(url)
            except Exception as exc:
                failures.append({"position": pos, "url": url, "error": str(exc)[:180]})
            time.sleep(0.4)
        data["positions"][pos] = {
            "urls": urls,
            "ok_urls": ok_urls,
            "text": " ".join(combined),
        }

    data["failures"] = failures
    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("SAT_INDEX positions=%d failures=%d" % (len(data["positions"]), len(failures)))
    for pos, row in data["positions"].items():
        print("%s pages=%d/%d chars=%d" % (pos, len(row["ok_urls"]), len(row["urls"]), len(row["text"])))
    # Do not fail the entire monitoring page for one unavailable satellite page.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
