# -*- coding: utf-8 -*-
"""EPG Import compatible source/channel-map export.

EPG Import keeps *programme data* and *service mapping* separate:

* ``*.sources.xml`` points at an XMLTV file and at a channels mapping file.
* the channels mapping file contains repeated
  ``<channel id=\"xmltv-id\">SERVICE_REFERENCE</channel>`` entries.

That is the same contract used by Rytec.  v6.4 uses this structure for every
EPG Manager source, so Native Import can consume matched receiver services
without a receiver-wide Auto Sync stage.
"""
from __future__ import print_function

import os
from xml.etree import ElementTree as ET

from .logger import get_logger
from .utils import indent_xml, atomic_write

log = get_logger(__name__)

OUTPUT_PATH = "/etc/epgimport/epgmanager.sources.xml"


def _xml_text(root):
    indent_xml(root)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def build_direct_sources(rows, category="EPG Manager"):
    """Build a real OE-Alliance EPG Import ``*.sources.xml`` document.

    ``rows`` is an iterable of dictionaries containing at least:
      ``xml_path``, ``map_path``, ``source_id`` and optionally ``name``.

    Local paths are written as plain paths (not ``file://``), matching EPG
    Import's own local-file detection.
    """
    root = ET.Element("sources")
    sourcecat = ET.SubElement(root, "sourcecat", sourcecatname=str(category))
    count = 0
    for row in rows or []:
        row = dict(row or {})
        xml_path = str(row.get("xml_path") or "").strip()
        map_path = str(row.get("map_path") or "").strip()
        if not xml_path or not map_path:
            continue
        if not os.path.exists(xml_path) or not os.path.exists(map_path):
            continue
        source = ET.SubElement(
            sourcecat, "source", type="gen_xmltv", channels=map_path, nocheck="1")
        label = str(row.get("name") or row.get("source_id") or os.path.basename(xml_path))
        ET.SubElement(source, "description").text = "EPG Manager - %s" % label
        ET.SubElement(source, "url").text = xml_path
        count += 1
    return _xml_text(root), count


def save_direct_sources(rows, output_path=OUTPUT_PATH, category="EPG Manager"):
    text, count = build_direct_sources(rows, category=category)
    atomic_write(output_path, text)
    log.info("Wrote EPG Import source definition %s with %d source(s)", output_path, count)
    return output_path, count


# -------------------------------------------------------------------------
# Legacy exporter kept only for the old advanced/manual mapping screen.
# The v6.4 normal workflow does NOT use this nested structure.
# -------------------------------------------------------------------------
def build_sourcexml(matched_channels, description="EPG Manager (legacy mapping export)"):
    root = ET.Element("sources")
    by_file = {}
    for ch in matched_channels:
        if not ch.get("matches"):
            continue
        by_file.setdefault(ch["epg_xml_path"], []).append(ch)

    total_channels = 0
    total_refs = 0
    for xml_path, channels in sorted(by_file.items()):
        source_el = ET.SubElement(root, "source", type="gen_xmltv", nocheck="1")
        ET.SubElement(source_el, "description").text = "%s - %s" % (
            description, os.path.basename(xml_path))
        # Historical behaviour retained for compatibility with the advanced
        # screen only. Normal v6.4 uses save_direct_sources() above.
        ET.SubElement(source_el, "url").text = "file://%s" % xml_path
        channels_el = ET.SubElement(source_el, "channels")
        for ch in channels:
            total_channels += 1
            for match in ch["matches"]:
                chan_el = ET.SubElement(channels_el, "channel", id=ch["channel_id"])
                chan_el.text = match["ref"]
                total_refs += 1
    return _xml_text(root), total_channels, total_refs


def save_sourcexml(matched_channels, output_path=OUTPUT_PATH):
    xml_text, total_channels, total_refs = build_sourcexml(matched_channels)
    atomic_write(output_path, xml_text)
    log.info("Wrote legacy mapping export %s: %d channel(s), %d ref(s)",
             output_path, total_channels, total_refs)
    return output_path, total_channels, total_refs
