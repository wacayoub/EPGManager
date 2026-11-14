# -*- coding: utf-8 -*-
"""Tiny OpenEPG channel-ID catalogue reader (beta31).

The .xml.txt endpoints are treated as metadata catalogues only.  We never save
remote XMLTV programme payloads locally and cap the catalogue body to 768 KiB.
The parser is deliberately tolerant because OpenEPG has changed the human
readable list format over time.
"""
from __future__ import print_function

import html
import re
import time
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

from .downloader import DEFAULT_HEADERS

MAX_CATALOG_BYTES = 768 * 1024


def _catalog_generated_at(payload):
    """Parse a leading YYYYMMDDHHMM[/SS] catalogue timestamp when present.

    EPGShare .txt catalogues place their generation stamp at the beginning of
    the small metadata file.  OpenEPG formats are intentionally left alone
    unless the same unambiguous leading timestamp exists.
    """
    if isinstance(payload, bytes):
        head = payload[:512].decode("ascii", "ignore")
    else:
        head = str(payload or "")[:512]
    m = re.search(r"(?<!\d)(20\d{10}(?:\d{2})?)(?!\d)", head)
    if not m:
        return 0
    raw = m.group(1)
    try:
        fmt = "%Y%m%d%H%M%S" if len(raw) >= 14 else "%Y%m%d%H%M"
        dt = datetime.strptime(raw[:14] if len(raw) >= 14 else raw[:12], fmt)
        return int(time.mktime(dt.timetuple()))
    except Exception:
        return 0

_COUNTRY_SUFFIXES = set((
    "sa", "ae", "eg", "qa", "ma", "mena", "arab", "ar", "en", "fr"
))
_QUALITY_SUFFIXES = set(("hd", "fhd", "uhd", "4k", "sd", "hdr", "hevc", "h265"))


def _clean_text(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:tr|p|div|li|td|th)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def label_from_channel_id(channel_id):
    """Derive a safe display-name candidate from a dotted XMLTV ID."""
    cid = str(channel_id or "").strip()
    if not cid:
        return ""
    # Dotted OpenEPG/EPGShare IDs normally encode human name + country suffix.
    parts = [p for p in re.split(r"[._]+", cid) if p]
    while parts and parts[-1].lower() in _COUNTRY_SUFFIXES:
        parts.pop()
    while parts and parts[-1].lower() in _QUALITY_SUFFIXES:
        parts.pop()
    if not parts:
        return cid
    return " ".join(parts)


def _looks_id(value):
    text = str(value or "").strip()
    if not text or len(text) > 180 or " " in text and "." not in text:
        return False
    return bool(re.search(r"[A-Za-z0-9]", text) and ("." in text or "_" in text or "-" in text))


def _add(out, seen, cid, label=""):
    cid = str(cid or "").strip().strip('"\'')
    if not cid or len(cid) > 180:
        return
    key = cid.lower()
    if key in seen:
        return
    label = str(label or "").strip()
    if not label or label == cid:
        label = label_from_channel_id(cid) or cid
    # Reject obvious HTML/javascript fragments accidentally parsed as IDs.
    low = cid.lower()
    if low.startswith(("http://", "https://", "javascript:", "function(", "var ")):
        return
    seen.add(key)
    out.append({"channel_id": cid, "display_name": label})


def parse_text(payload):
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", "replace")
    else:
        text = str(payload or "")
    out, seen = [], set()

    # XML/XML-like form: <channel id="x"><display-name>Foo</display-name>...
    for m in re.finditer(r"<channel\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*>(.*?)</channel\s*>", text,
                         flags=re.I | re.S):
        cid, body = m.group(1).strip(), m.group(2)
        dm = re.search(r"<display-name\b[^>]*>(.*?)</display-name\s*>", body, flags=re.I | re.S)
        label = _clean_text(dm.group(1)) if dm else ""
        _add(out, seen, cid, label)
    if out:
        return out

    cleaned = _clean_text(text)
    for raw in cleaned.split("\n"):
        line = raw.strip(" \t|;,")
        if not line or len(line) > 500:
            continue
        if line.lower().startswith(("channel id", "channels:", "id name", "channel name")):
            continue

        # Explicit id/name forms.
        m = re.search(r"\bid\s*[:=]\s*[\"']?([^\s\"'|;,]+)[\"']?.*?\b(?:name|display(?:-name)?)\s*[:=]\s*(.+)$",
                      line, flags=re.I)
        if m:
            _add(out, seen, m.group(1), m.group(2))
            continue

        # Common table/text separators. Decide which side is the XMLTV ID.
        parts = None
        for sep in ("\t", "|", ";", " -> ", " => ", " = "):
            if sep in line:
                p = [x.strip() for x in line.split(sep, 1)]
                if len(p) == 2:
                    parts = p
                    break
        if parts:
            a, b = parts
            if _looks_id(a):
                _add(out, seen, a, b)
                continue
            if _looks_id(b):
                _add(out, seen, b, a)
                continue

        # "Friendly Name - dotted.channel.id" and inverse.
        m = re.match(r"^(.+?)\s+-\s+([^\s]+)$", line)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if _looks_id(b):
                _add(out, seen, b, a)
                continue
            if _looks_id(a):
                _add(out, seen, a, b)
                continue

        # Bare dotted/underscored ID.
        if _looks_id(line) and " " not in line:
            _add(out, seen, line, "")

    return out


def fetch(item, timeout=5, connect_timeout=4.0):
    if requests is None:
        raise RuntimeError("python3-requests is unavailable")
    url = str((item or {}).get("catalog_url") or "").strip()
    if not url:
        raise RuntimeError("No lightweight channel catalogue URL")
    headers = dict(DEFAULT_HEADERS)
    headers["Accept-Encoding"] = "gzip, deflate"
    response = requests.get(url, headers=headers, stream=True, timeout=(float(connect_timeout), float(timeout)), verify=True)
    try:
        if response.status_code != 200:
            raise RuntimeError("HTTP %d" % response.status_code)
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_CATALOG_BYTES:
                raise RuntimeError("Channel catalogue exceeds %d KiB" % (MAX_CATALOG_BYTES // 1024))
            chunks.append(chunk)
        payload = b"".join(chunks)
        rows = parse_text(payload)
        if not rows:
            raise RuntimeError("No channel IDs parsed from lightweight catalogue")
        generated = _catalog_generated_at(payload)
        return rows, {"url": url, "scanned_bytes": total, "catalog_only": True,
                      "catalog_kind": str((item or {}).get("catalog_kind") or "id-list"),
                      "catalog_generated_at": int(generated or 0)}
    finally:
        try:
            response.close()
        except Exception:
            pass
