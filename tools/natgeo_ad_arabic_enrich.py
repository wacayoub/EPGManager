#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe Arabic metadata enrichment for National Geographic Abu Dhabi.

The linear timeline is never replaced.  EPGShare/Babeleye remains the timing
source; this module only replaces title/description when an exact English title
has a reviewed Arabic metadata record cached from ElCinema/ADM.

Production use is offline and deterministic: ``apply_cached_metadata`` reads the
repository JSON cache and performs no network I/O.  ``refresh-cache`` is an
explicit maintenance command used by a cloud audit job to discover new titles.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

TARGET_ID = "NationalGeographicAbuDhabi.ae"
SOURCE_ID = "Nat.Geo.Abu.Dhabi.HD.ae"
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data" / "natgeo_ad_arabic_metadata.json"
DEFAULT_SOURCE_URL = "https://epgshare01.online/epgshare01/epg_ripper_AE1.xml.gz"
AR_RE = re.compile(r"[\u0600-\u06ff]")
WORK_LINK_RE = re.compile(r'href=["\'](?:/en)?/work/(\d+)/["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def _clean_text(value):
    return SPACE_RE.sub(" ", html.unescape(value or "")).strip()


def normalize_title(value):
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _arabic_ratio(value):
    chars = [c for c in (value or "") if c.isalpha()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if AR_RE.search(c)) / float(len(chars))


def load_cache(path=DEFAULT_CACHE):
    p = Path(path)
    if not p.exists():
        return {"version": 1, "target_id": TARGET_ID, "entries": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def apply_cached_metadata(programmes, target_id=TARGET_ID, cache_path=DEFAULT_CACHE, copy_element=None):
    """Return (new_programme_map, report), touching only target_id metadata.

    A cache row is accepted only if both cached title and description are Arabic.
    Timing attributes and every non-title/description element remain untouched.
    """
    cache = load_cache(cache_path)
    entries = cache.get("entries") or {}
    if copy_element is None:
        import copy
        copy_element = copy.deepcopy

    out = dict(programmes)
    source = list(programmes.get(target_id, []))
    enriched = []
    matched = 0
    title_ar = 0
    desc_ar = 0
    unmatched = Counter()

    for programme in source:
        cp = copy_element(programme)
        title_node = cp.find("title")
        current_title = _clean_text(title_node.text if title_node is not None else "")
        key = normalize_title(current_title)
        row = entries.get(key) or {}
        ar_title = _clean_text(row.get("title_ar") or "")
        ar_desc = _clean_text(row.get("description_ar") or "")

        if ar_title and ar_desc and AR_RE.search(ar_title) and AR_RE.search(ar_desc):
            if title_node is None:
                title_node = ET.SubElement(cp, "title")
            title_node.text = ar_title
            title_node.set("lang", "ar")
            desc_node = cp.find("desc")
            if desc_node is None:
                desc_node = ET.SubElement(cp, "desc")
            desc_node.text = ar_desc
            desc_node.set("lang", "ar")
            matched += 1
        else:
            unmatched[current_title or "<EMPTY>"] += 1

        final_title = _clean_text(title_node.text if title_node is not None else "")
        final_desc_node = cp.find("desc")
        final_desc = _clean_text(final_desc_node.text if final_desc_node is not None else "")
        if AR_RE.search(final_title):
            title_ar += 1
        if AR_RE.search(final_desc):
            desc_ar += 1
        enriched.append(cp)

    if source:
        out[target_id] = enriched
    total = len(source)
    report = {
        "target_id": target_id,
        "events": total,
        "metadata_matches": matched,
        "match_pct": round(100.0 * matched / total, 1) if total else 0.0,
        "title_ar_pct": round(100.0 * title_ar / total, 1) if total else 0.0,
        "desc_ar_pct": round(100.0 * desc_ar / total, 1) if total else 0.0,
        "unmatched_titles": dict(unmatched.most_common()),
        "cache_entries": len(entries),
        "timeline_replaced": False,
    }
    return out, report


def _http_get(url, timeout=20, attempts=3):
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
                "Accept-Language": "ar,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if n + 1 < attempts:
                time.sleep(1.0 + n)
    raise last


def _visible_strings(page_bytes):
    text = page_bytes.decode("utf-8", "replace")
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</(?:div|p|li|h1|h2|h3|section|article|br)>", "\n", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    return [_clean_text(x) for x in text.splitlines() if _clean_text(x)]


def _extract_h1(page_bytes):
    text = page_bytes.decode("utf-8", "replace")
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", text, flags=re.I | re.S)
    return _clean_text(TAG_RE.sub(" ", m.group(1))) if m else ""


def _extract_arabic_work_metadata(page_bytes, english_title):
    h1 = _extract_h1(page_bytes)
    if normalize_title(english_title) not in normalize_title(h1):
        return None

    # ElCinema Arabic h1 format is typically: English Title (YEAR) Arabic Title.
    tail = re.sub(re.escape(english_title), " ", h1, count=1, flags=re.I)
    tail = re.sub(r"\(\s*\d{4}\s*\)", " ", tail)
    arabic_chunks = re.findall(r"[\u0600-\u06ff][\u0600-\u06ff\s\u064b-\u065f،؛:!?؟'\-–—0-9]+", tail)
    arabic_title = max((_clean_text(x) for x in arabic_chunks), key=len, default="")
    arabic_title = arabic_title.strip(" -–—:،؛")

    strings = _visible_strings(page_bytes)
    description = ""
    for i, value in enumerate(strings):
        if "ملخص القصة" in value:
            for candidate in strings[i + 1:i + 8]:
                candidate = candidate.replace("...اقرأ المزيد", "").strip()
                if len(candidate) >= 35 and AR_RE.search(candidate) and "المزيد" != candidate:
                    description = candidate
                    break
            if description:
                break

    # Fallback: the first substantial Arabic prose after the h1 is the top synopsis.
    if not description:
        seen_h1 = False
        for candidate in strings:
            if h1 and h1 in candidate:
                seen_h1 = True
                continue
            if not seen_h1:
                continue
            cleaned = candidate.replace("...اقرأ المزيد", "").strip()
            if len(cleaned) >= 80 and AR_RE.search(cleaned) and not cleaned.startswith("تقييمك"):
                description = cleaned
                break

    if not (arabic_title and description and AR_RE.search(arabic_title) and AR_RE.search(description)):
        return None
    return arabic_title, description


def _find_elcinema_work(english_title):
    search_url = "https://elcinema.com/search/?q=" + urllib.parse.quote(english_title)
    search = _http_get(search_url)
    ids = []
    for wid in WORK_LINK_RE.findall(search.decode("utf-8", "replace")):
        if wid not in ids:
            ids.append(wid)
    wanted = normalize_title(english_title)
    for wid in ids[:8]:
        en_url = "https://elcinema.com/en/work/%s/" % wid
        try:
            en_page = _http_get(en_url, timeout=15, attempts=2)
        except Exception:
            continue
        h1 = _extract_h1(en_page)
        h1_norm = normalize_title(h1)
        # Exact title must be visible in the work h1; search rank alone is insufficient.
        if not wanted or wanted not in h1_norm:
            continue
        # Reject obvious partial-title collisions where another title precedes/follows it.
        if not (h1_norm.startswith(wanted) or (" " + wanted + " ") in (" " + h1_norm + " ")):
            continue
        ar_url = "https://elcinema.com/work/%s/" % wid
        try:
            ar_page = _http_get(ar_url, timeout=15, attempts=2)
            metadata = _extract_arabic_work_metadata(ar_page, english_title)
        except Exception:
            metadata = None
        if metadata:
            return {
                "work_id": wid,
                "source": "elcinema.com",
                "source_url": ar_url,
                "title_en": english_title,
                "title_ar": metadata[0],
                "description_ar": metadata[1],
                "match": "exact_work_title",
            }
    return None


def _load_source_title_counts(url=DEFAULT_SOURCE_URL):
    raw = _http_get(url, timeout=35, attempts=3)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    counts = Counter()
    for p in root.findall("programme"):
        if (p.get("channel") or "").strip() != SOURCE_ID:
            continue
        title = p.find("title")
        value = _clean_text(title.text if title is not None else "")
        if value:
            counts[value] += 1
    return counts


def refresh_cache(output=DEFAULT_CACHE, source_url=DEFAULT_SOURCE_URL, delay=0.20):
    counts = _load_source_title_counts(source_url)
    existing = load_cache(output)
    entries = dict(existing.get("entries") or {})
    matched_events = 0
    searched = 0
    found = 0

    print("NatGeo Abu Dhabi source titles: events=%d unique=%d" % (sum(counts.values()), len(counts)))
    for title, count in counts.most_common():
        key = normalize_title(title)
        row = entries.get(key) or {}
        if row.get("title_ar") and row.get("description_ar") and AR_RE.search(row.get("title_ar", "")) and AR_RE.search(row.get("description_ar", "")):
            matched_events += count
            print("CACHE %3d x %s -> %s" % (count, title, row.get("title_ar")))
            continue
        searched += 1
        try:
            row = _find_elcinema_work(title)
        except Exception as exc:
            print("ERROR %3d x %s | %r" % (count, title, exc))
            row = None
        if row:
            row["event_weight"] = count
            entries[key] = row
            found += 1
            matched_events += count
            print("FOUND %3d x %s -> %s | work=%s" % (count, title, row["title_ar"], row["work_id"]))
        else:
            print("MISS  %3d x %s" % (count, title))
        time.sleep(delay)

    total = sum(counts.values())
    payload = {
        "version": 1,
        "target_id": TARGET_ID,
        "timing_source": "EPGShare/Babeleye Abu Dhabi timeline (metadata only; never replaced)",
        "metadata_policy": "exact English work-title match to Arabic ElCinema work page; both Arabic title and synopsis required",
        "source_event_count": total,
        "source_unique_titles": len(counts),
        "matched_event_count": matched_events,
        "matched_event_pct": round(100.0 * matched_events / total, 1) if total else 0.0,
        "searched_titles": searched,
        "new_matches": found,
        "entries": dict(sorted(entries.items())),
    }
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("CACHE RESULT: entries=%d matched_events=%d/%d (%.1f%%) new=%d" % (
        len(entries), matched_events, total, payload["matched_event_pct"], found))
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("refresh-cache")
    r.add_argument("--output", default=str(DEFAULT_CACHE))
    r.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    r.add_argument("--delay", type=float, default=0.20)
    a = sub.add_parser("audit-cache")
    a.add_argument("--cache", default=str(DEFAULT_CACHE))
    args = ap.parse_args(argv)
    if args.command == "refresh-cache":
        refresh_cache(args.output, args.source_url, args.delay)
        return 0
    data = load_cache(args.cache)
    entries = data.get("entries") or {}
    bad=[]
    for key,row in entries.items():
        if not (AR_RE.search(row.get("title_ar", "")) and AR_RE.search(row.get("description_ar", ""))):
            bad.append(key)
    print("NatGeo Arabic cache: entries=%d bad=%d matched_event_pct=%s" % (
        len(entries), len(bad), data.get("matched_event_pct", "n/a")))
    if bad:
        print("BAD:", ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
