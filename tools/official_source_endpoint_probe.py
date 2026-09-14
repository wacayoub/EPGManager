#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

TARGETS = {
    "alfa": [
        "https://alfatv.com/ar/tv-guide",
        "https://alfatv.com/en/tv-guide",
    ],
    "ajman": [
        "https://www.ajmantv.com/",
        "https://www.ajmantv.com/schedule/",
        "https://www.ajmantv.com/schedule/sunday/",
        "https://www.ajmantv.com/wp-json/",
    ],
    "sharjah": [
        "https://sba.net.ae/",
        "http://sba.net.ae/",
    ],
    "majid": [
        "https://www.admn.ae/en/brand/4197603/majid-tv",
        "https://www.adtv.ae/",
    ],
}

KEYWORDS = ("api", "graphql", "ajax", "schedule", "tv-guide", "tvguide", "program", "programme", "guide", "calendar", "epg")
URL_RE = re.compile(r"(?:https?:)?//[^\"'<>\\\s]+|/[A-Za-z0-9_./?&=%:+~-]{4,}")
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
NEXT_RE = re.compile(r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.I | re.S)
WP_RE = re.compile(r"<link[^>]+rel=[\"']https://api\.w\.org/[\"'][^>]+href=[\"']([^\"']+)", re.I)


def get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "EPGManager-Official-Source-Probe/1.0", "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "")
        return {
            "url": r.geturl(),
            "status": getattr(r, "status", 200),
            "ctype": ctype,
            "last_modified": r.headers.get("Last-Modified", ""),
            "etag": r.headers.get("ETag", ""),
            "data": data,
        }


def decode(data: bytes):
    return data.decode("utf-8", "replace")


def absolutize(base: str, value: str):
    value = unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    return urllib.parse.urljoin(base, value)


def endpoint_candidates(base: str, text: str):
    out = []
    seen = set()
    for raw in URL_RE.findall(text):
        low = raw.casefold()
        if not any(k in low for k in KEYWORDS):
            continue
        url = absolutize(base, raw)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out[:80]


def probe_url(url: str):
    try:
        r = get(url)
    except Exception as exc:
        return {"requested": url, "status": "ERROR", "error": str(exc)[:240]}
    data = r["data"]
    text = decode(data[:2_500_000])
    rec = {
        "requested": url,
        "final_url": r["url"],
        "status": r["status"],
        "content_type": r["ctype"],
        "bytes": len(data),
        "last_modified": r["last_modified"],
        "etag": r["etag"],
        "scripts": [],
        "endpoints": endpoint_candidates(r["url"], text),
        "next_data": False,
        "wordpress_api": [],
    }
    for s in SCRIPT_RE.findall(text)[:20]:
        rec["scripts"].append(absolutize(r["url"], s))
    if NEXT_RE.search(text):
        rec["next_data"] = True
    for wp in WP_RE.findall(text):
        rec["wordpress_api"].append(absolutize(r["url"], wp))
    return rec


def scan_scripts(rec):
    found = []
    for js in rec.get("scripts", [])[:10]:
        try:
            r = get(js, timeout=15)
        except Exception:
            continue
        ctype = r["ctype"].casefold()
        if "javascript" not in ctype and not js.casefold().endswith((".js", ".mjs")):
            continue
        text = decode(r["data"][:3_000_000])
        for ep in endpoint_candidates(r["url"], text):
            if ep not in found:
                found.append(ep)
    rec["script_endpoints"] = found[:120]


def special_checks():
    urls = [
        "https://www.ajmantv.com/wp-json/",
        "https://www.ajmantv.com/wp-json/wp/v2/search?search=schedule&per_page=20",
        "https://www.ajmantv.com/wp-json/wp/v2/search?search=%D8%A7%D9%84%D8%A7%D8%AD%D8%AF&per_page=20",
        "https://alfatv.com/api/",
        "https://alfatv.com/graphql",
    ]
    out = []
    for url in urls:
        try:
            r = get(url, timeout=15)
            body = decode(r["data"][:2500]).replace("\n", " ")
            out.append({"url": url, "status": r["status"], "content_type": r["ctype"], "bytes": len(r["data"]), "sample": body[:700]})
        except Exception as exc:
            out.append({"url": url, "status": "ERROR", "error": str(exc)[:220]})
    return out


def main():
    result = {"schema": 1, "targets": {}, "special_checks": []}
    for name, urls in TARGETS.items():
        rows = []
        for url in urls:
            rec = probe_url(url)
            if isinstance(rec.get("status"), int):
                scan_scripts(rec)
            rows.append(rec)
        result["targets"][name] = rows
    result["special_checks"] = special_checks()
    Path("output/official-source-endpoint-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["OFFICIAL SOURCE ENDPOINT PROBE", ""]
    for name, rows in result["targets"].items():
        lines.append("[%s]" % name.upper())
        for r in rows:
            lines.append("- %s -> %s status=%s bytes=%s ctype=%s last_modified=%s" % (
                r.get("requested"), r.get("final_url", "-"), r.get("status"), r.get("bytes", 0), r.get("content_type", ""), r.get("last_modified", "")))
            for ep in (r.get("wordpress_api") or [])[:4]:
                lines.append("    WP_API %s" % ep)
            for ep in (r.get("endpoints") or [])[:12]:
                lines.append("    HTML_ENDPOINT %s" % ep)
            for ep in (r.get("script_endpoints") or [])[:20]:
                lines.append("    JS_ENDPOINT %s" % ep)
            if r.get("error"):
                lines.append("    ERROR %s" % r["error"])
        lines.append("")
    lines.append("[SPECIAL]")
    for x in result["special_checks"]:
        lines.append("- %s status=%s bytes=%s ctype=%s" % (x["url"], x["status"], x.get("bytes", 0), x.get("content_type", "")))
        if x.get("sample"):
            lines.append("    %s" % x["sample"][:500])
        if x.get("error"):
            lines.append("    ERROR %s" % x["error"])
    Path("output/official-source-endpoint-probe.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
