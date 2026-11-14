# -*- coding: utf-8 -*-
"""Fast direct remote Channel-ID catalogue reader for Smart Mapping.

7.0.6 policy:
- Smart Mapping never needs a downloaded/local remote XMLTV file.
- Prefer the provider's tiny ID list (catalog_url) when one exists.
- Otherwise stream only the concrete XMLTV source <channel> declaration header.
- Shared provider channels maps are mapping authorities, not per-source membership,
  and are therefore never copied into individual source caches.
- Never retain programme data and never write the remote XML/XML.GZ itself.
- Callers may persist only the compact channel_id/display_name rows.

All network work is intended for a daemon worker.  UI-thread callers must use a
background wrapper and consume the result asynchronously.
"""
from __future__ import print_function

from . import openepg_id_catalog, source_channel_sync


def fetch(item, timeout=3, max_scan=6 * 1024 * 1024):
    """Return source-specific channel rows from the provider XML URL only.

    8.2.2-rc5 policy: the configured XMLTV URL is the authority for every
    external source.  Do not substitute a receiver-side XML copy and do not use
    a provider metadata list as the primary catalogue.  First stream only the
    <channel> header; when a provider omits channel declarations, continue into
    a bounded programme sample from that *same* URL to recover channel IDs.
    """
    item = dict(item or {})
    errors = []
    try:
        rows, meta = source_channel_sync.fetch_channel_ids_only(
            item,
            timeout=max(2, int(timeout)),
            connect_timeout=1.5,
            max_scan=int(max_scan),
            cooperative_sleep=0.0,
            fast=True,
        )
        meta = dict(meta or {})
        meta["remote_direct"] = True
        meta["method"] = "xmltv-channel-header"
        meta["authority"] = "source-xml-url"
        return rows, meta
    except Exception as exc:
        errors.append("xmltv-header: %s" % exc)

    try:
        rows, meta = source_channel_sync.fetch_channels(
            item,
            timeout=max(3, int(timeout)),
            quality_scan=int(max_scan),
            max_programmes=900,
            cooperative_sleep=0.0,
        )
        meta = dict(meta or {})
        meta["remote_direct"] = True
        meta["method"] = str(meta.get("method") or "xmltv-bounded-sample")
        meta["authority"] = "source-xml-url"
        return rows, meta
    except Exception as exc:
        errors.append("xmltv-sample: %s" % exc)

    raise RuntimeError("Direct source XML read failed (%s)" % "; ".join(errors[-2:]))

