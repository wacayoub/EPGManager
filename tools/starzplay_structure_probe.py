#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-rate STARZPLAY public page structure probe.

Fetches one public Live page and reports only JSON structure, candidate public
metadata URLs and script sources. No login, tokens, playback URLs, DRM or auth
bypass.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140 Safari/537.36"
)
HINT = re.compile(r"(api|epg|schedule|listing|program|programme|channel|live|content|guide)", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
SENSITIVE = re.compile(r"(token|authorization|bearer|license|widevine|drm|password|secret|payment)", re.I)


def walk(obj: Any, keys: Counter[str], candidates: list[str], paths: list[str], path: str = "$") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            keys[ks] += 1
            p = f"{path}.{ks}"
            if HINT.search(ks) and len(paths) < 300:
                paths.append(p)
            if isinstance(v, str):
                for u in URL_RE.findall(v.replace("\\/", "/")):
                    if HINT.search(u) and not SENSITIVE.search(u) and u not in candidates and len(candidates) < 200:
                        candidates.append(u)
            if not SENSITIVE.search(ks):
                walk(v, keys, candidates, paths, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            walk(v, keys, candidates, paths, f"{path}[{i}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=("ar", "en"), default="ar")
    ap.add_argument("--output", required=True)
    ap.add_argument("--timeout", type=int, default=25)
    args = ap.parse_args()

    q = urlencode({"selectcountry": "MA", "selectcity": "Casablanca"})
    url = f"https://www.starzplay.com/{args.lang}/live?{q}"
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en;q=0.8" if args.lang == "ar" else "en,ar;q=0.8",
        "Referer": "https://www.starzplay.com/",
    }
    r = requests.get(url, headers=h, timeout=args.timeout, allow_redirects=True)
    page = r.text
    soup = BeautifulSoup(page, "html.parser")

    scripts = []
    parsed = []
    keys: Counter[str] = Counter()
    candidates: list[str] = []
    key_paths: list[str] = []

    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            scripts.append(urljoin(r.url, str(src)))
        text = tag.string or tag.get_text("", strip=False)
        if not text:
            continue
        typ = (tag.get("type") or "").casefold()
        sid = str(tag.get("id") or "")
        obj = None
        if "json" in typ or sid in {"__NEXT_DATA__", "__NUXT_DATA__"}:
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
        if obj is not None:
            parsed.append({
                "id": sid,
                "type": typ,
                "root_type": type(obj).__name__,
                "chars": len(text),
            })
            walk(obj, keys, candidates, key_paths)

    # Candidate public metadata URLs found directly in HTML, excluding sensitive-looking URLs.
    for u in URL_RE.findall(page.replace("\\/", "/")):
        if HINT.search(u) and not SENSITIVE.search(u) and u not in candidates and len(candidates) < 200:
            candidates.append(u)

    report = {
        "requested_url": url,
        "final_url": r.url,
        "http_status": r.status_code,
        "html_bytes": len(r.content),
        "script_count": len(soup.find_all("script")),
        "external_script_count": len(scripts),
        "external_scripts": scripts[:80],
        "json_scripts": parsed,
        "top_keys": keys.most_common(150),
        "hint_key_paths": key_paths[:300],
        "candidate_public_metadata_urls": candidates[:200],
        "policy": "one public page request; structure only; sensitive/token/DRM URLs excluded",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "http_status": report["http_status"],
        "html_bytes": report["html_bytes"],
        "json_scripts": len(parsed),
        "candidate_urls": len(candidates),
        "hint_paths": len(key_paths),
    }, ensure_ascii=False))
    return 0 if r.status_code == 200 else 10


if __name__ == "__main__":
    raise SystemExit(main())
