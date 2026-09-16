# -*- coding: utf-8 -*-
"""Unified EPG source catalogue.

Merges:
- EPG Manager local generators
- EPG Manager's bundled OpenEPG / EPGShare / Rytec catalogue
- any source definitions installed by EPG-Importer on the receiver

EPG-Importer source files are discovered dynamically rather than copied, so
new providers installed later appear automatically in EPG Manager.
"""
from __future__ import print_function
import glob
import hashlib
import os
import re
import xml.etree.ElementTree as ET

from . import external_sources

LOCAL_SOURCES = [
    {"id": "local_medi1tv", "manager_id": "medi1tv", "name": "Medi1 TV", "group": "Local EPG Manager", "kind": "local"},
    {"id": "local_chada_2m", "manager_id": "chada_2m", "name": "2M / Chada", "group": "Local EPG Manager", "kind": "local"},
    {"id": "local_snrt", "manager_id": "snrt", "name": "SNRT", "group": "Local EPG Manager", "kind": "local"},
    {"id": "local_bein_sports", "manager_id": "bein_sports", "name": "beIN Sports", "group": "Local EPG Manager", "kind": "local"},
    {"id": "local_almajd", "manager_id": "almajd", "name": "Almajd", "group": "Local EPG Manager", "kind": "local"},
    {"id": "local_arryadia", "manager_id": "arryadia", "name": "Arryadia", "group": "Local EPG Manager", "kind": "local"},
]

EPGIMPORT_GLOBS = (
    "/etc/epgimport/*.sources.xml",
    "/etc/epgimport/*sources*.xml",
    "/usr/lib/enigma2/python/Plugins/Extensions/EPGImport/*.sources.xml",
    "/usr/lib/enigma2/python/Plugins/SystemPlugins/EPGImport/*.sources.xml",
)


def _slug(value):
    text = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return text or hashlib.sha1((value or "source").encode("utf-8")).hexdigest()[:12]


def _text(node, tag):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag.lower() and child.text:
            return child.text.strip()
    return ""


def discover_epgimport_sources():
    """Read every installed EPG-Importer source definition we can find.

    Parsing is deliberately tolerant because OE-Alliance images ship several
    generations of the source XML schema.  Only description/url are required.
    Dynamic provider URLs (containing format placeholders) are still listed,
    but are marked dynamic and not downloaded by the generic downloader.
    """
    files = []
    for pattern in EPGIMPORT_GLOBS:
        files.extend(glob.glob(pattern))
    out, seen = [], set()
    for path in sorted(set(files)):
        try:
            root = ET.parse(path).getroot()
        except Exception:
            continue
        for src in root.iter():
            if src.tag.split("}")[-1].lower() != "source":
                continue
            desc = _text(src, "description") or src.get("description") or "EPG-Importer source"
            urls = []
            for child in list(src):
                tag = child.tag.split("}")[-1].lower()
                if tag == "url" and child.text and child.text.strip():
                    urls.append(child.text.strip())
            if not urls:
                raw = src.get("url")
                if raw:
                    urls = [raw.strip()]
            if not urls:
                continue
            channels = src.get("channels") or ""
            category = "EPG-Importer"
            parent_name = os.path.basename(path).replace(".sources.xml", "")
            for url in urls:
                key = (desc.lower(), url)
                if key in seen:
                    continue
                seen.add(key)
                source_id = "epgimport_%s" % _slug(desc + "_" + url)
                dynamic = any(token in url for token in ("%", "{", "}"))
                # Keep EPG-Importer providers, but reject obvious non-EPG endpoints
                # accidentally present in provider definitions. Local file:// XMLTV
                # entries are valid and handled without HTTP by external_sources.
                low = url.lower().split("?", 1)[0]
                is_local_file = low.startswith("file://") or low.startswith("/")
                is_epg_like = is_local_file or dynamic or any(low.endswith(ext) for ext in (".xml", ".xml.gz", ".gz", ".xz", ".xml.xz"))
                if not is_epg_like:
                    continue
                out.append({
                    "id": source_id,
                    "name": desc,
                    "url": url,
                    "group": "%s / %s" % (category, parent_name) if parent_name else category,
                    "kind": "epgimport",
                    "channels": channels,
                    "definition": path,
                    "dynamic": dynamic,
                })
    return out


def bundled_external_sources():
    out = []
    for src in external_sources.SOURCES:
        item = dict(src)
        item["kind"] = "external"
        item["group"] = "Online / %s" % item.get("region", "Other")
        item["dynamic"] = False
        out.append(item)
    return out


def all_sources():
    """Return a de-duplicated unified source list, local first."""
    merged = list(LOCAL_SOURCES) + discover_epgimport_sources() + bundled_external_sources()
    result, seen = [], set()
    for item in merged:
        # De-dupe identical online URLs while preserving installed EPG-Importer
        # naming first, then the bundled fallback catalogue.
        key = item.get("url") or item.get("id")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
