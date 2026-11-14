# -*- coding: utf-8 -*-
"""On-demand XMLTV channel-ID synchronizer for EPGManager beta21.

Performance policy for small Enigma2 receivers:
- never runs at boot/session start;
- never iterates all selected feeds;
- exactly one source is synchronized only when the user opens it in Smart Mapping;
- reads only the XMLTV <channel> header and stops at the first <programme>;
- cooperatively yields between small chunks to avoid starving Enigma2;
- stores only IDs/display-names in small per-source JSON shards; no remote XMLTV is saved.
"""
from __future__ import print_function

import bz2
import gzip
import re
import time
from xml.etree import ElementTree as ET

try:
    import lzma
except ImportError:
    lzma = None
try:
    import requests
except ImportError:
    requests = None

from .downloader import DEFAULT_HEADERS
from .logger import get_logger
from . import source_quality, source_channel_cache, remote_http_cache, fast_name_key

log = get_logger(__name__)
READ_CHUNK = 8 * 1024
FAST_READ_CHUNK = 64 * 1024
MAX_DECOMPRESSED_SCAN = 4 * 1024 * 1024
QUALITY_MAX_SCAN = 8 * 1024 * 1024
QUALITY_MAX_PROGRAMMES = 1800
COOPERATIVE_SLEEP = 0.006

class SyncDeferred(Exception):
    pass


def native_import_running():
    """Best-effort guard for the standard OE-Alliance singleton."""
    try:
        from Plugins.Extensions.EPGImport import plugin as epg_plugin
        runner = getattr(epg_plugin, "epgimport", None)
        if runner is not None and hasattr(runner, "isImportRunning"):
            return bool(runner.isImportRunning())
    except Exception:
        pass
    return False


def _yield_cpu(guard_cb=None, cooperative_sleep=None):
    if native_import_running():
        raise SyncDeferred("Native EPGImport is running")
    if guard_cb is not None:
        try:
            if bool(guard_cb()):
                raise SyncDeferred("Receiver became busy")
        except SyncDeferred:
            raise
        except Exception:
            pass
    time.sleep(COOPERATIVE_SLEEP if cooperative_sleep is None else float(cooperative_sleep))


def _localname(tag):
    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _display_names(elem, channel_id):
    """Return primary display name plus every alternate XMLTV display-name.

    Providers may publish the same channel name in Arabic and Latin/English in
    separate <display-name> elements.  Smart Mapping needs all of them for
    discovery, while the first provider name remains the visible primary label.
    """
    names = []
    seen = set()
    for child in list(elem):
        if _localname(child.tag) != "display-name":
            continue
        text = "".join(child.itertext()).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            names.append(text)
    if not names:
        names = [str(channel_id or "")]
    return names[0], names[1:]


def _display_name(elem, channel_id):
    return _display_names(elem, channel_id)[0]


def _channel_row(elem, channel_id):
    primary, aliases = _display_names(elem, channel_id)
    # rc50: ElCinema and a few Arabic-first direct feeds expose Arabic
    # display-names but stable Latin XMLTV IDs. Add conservative ID-derived
    # aliases so Smart Mapping can discover MBC 2 / MBC Action / DMC Drama etc.
    # The visible provider display-name remains unchanged.
    merged = []
    seen = set()
    for value in list(aliases or []) + list(fast_name_key.xmltv_id_aliases(channel_id) or []):
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen and key not in (str(primary or "").casefold(), str(channel_id or "").casefold()):
            seen.add(key); merged.append(text)
    row = {"channel_id": str(channel_id or ""), "display_name": primary}
    if merged:
        row["aliases"] = merged[:10]
    return row


def parse_channel_header(reader, max_scan=MAX_DECOMPRESSED_SCAN, quality_scan=QUALITY_MAX_SCAN,
                         max_programmes=QUALITY_MAX_PROGRAMMES, guard_cb=None, cooperative_sleep=None):
    """Read channel IDs plus a bounded programme-quality sample.

    The old beta21 path stopped at the first <programme>.  beta25 keeps the
    same on-demand/no-boot policy but continues for a bounded sample so source
    conflicts can prefer real, descriptive EPG over channel-name placeholders.
    """
    parser = ET.XMLPullParser(events=("start", "end"))
    rows, seen, names = [], set(), {}
    collector = source_quality.new_collector()
    scanned = 0
    programmes = 0
    saw_programme = False
    complete = False
    while True:
        _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
        chunk = reader.read(READ_CHUNK)
        if not chunk:
            complete = True
            break
        scanned += len(chunk)
        # Before programme data starts we keep the original strict header cap.
        if not saw_programme and scanned > int(max_scan):
            raise RuntimeError("XMLTV channel header exceeds %d MB" % (int(max_scan) // 1048576))
        parser.feed(chunk)
        stop = False
        for event, elem in parser.read_events():
            tag = _localname(elem.tag)
            if event == "end" and tag == "channel":
                cid = str(elem.get("id") or "").strip()
                key = cid.lower()
                if cid and key not in seen:
                    seen.add(key)
                    row = _channel_row(elem, cid)
                    rows.append(row)
                    names[key] = row.get("display_name") or cid
                elem.clear()
            elif event == "end" and tag == "programme":
                if not saw_programme:
                    saw_programme = True
                    collector["channel_names"] = dict(names)
                # Rytec and a few legacy XMLTV providers keep channel/service
                # mappings in a separate channels.xml file and therefore omit
                # <channel> declarations from the programme feed itself.  Treat
                # programme@channel as a valid catalogue ID fallback instead of
                # declaring such a source NOK.  The official Rytec SRP map is
                # still used later for actual receiver mapping.
                pcid = str(elem.get("channel") or "").strip()
                pkey = pcid.lower()
                if pcid and pkey not in seen:
                    seen.add(pkey)
                    rows.append({"channel_id": pcid, "display_name": names.get(pkey) or pcid})
                    names.setdefault(pkey, pcid)
                    collector["channel_names"] = dict(names)
                source_quality.collect_programme(collector, elem)
                programmes += 1
                elem.clear()
                if programmes >= int(max_programmes) or scanned >= int(quality_scan):
                    stop = True
                    break
        if stop:
            break
    if not rows:
        raise RuntimeError("No XMLTV channel IDs found before programme data")
    profile = source_quality.finish(collector, scanned_bytes=scanned, complete=complete)
    return rows, scanned, profile




def parse_channel_ids_only(reader, max_scan=8 * 1024 * 1024, guard_cb=None, cooperative_sleep=None,
                           read_chunk=FAST_READ_CHUNK):
    """Read the complete XMLTV channel declaration block and stop at programmes.

    beta39 uses this for OpenEPG ID harvesting.  It deliberately does *not*
    sample programme data: the task is only to build a complete channel-ID
    catalogue with very small CPU/network cost.  The XMLTV convention keeps
    <channel> declarations before the first <programme>; once programme data
    starts the catalogue is considered complete.
    """
    parser = ET.XMLPullParser(events=("start", "end"))
    rows, seen = [], set()
    scanned = 0
    saw_programme = False
    while True:
        _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
        chunk = reader.read(max(4096, int(read_chunk or FAST_READ_CHUNK)))
        if not chunk:
            break
        scanned += len(chunk)
        if scanned > int(max_scan):
            raise RuntimeError("XMLTV channel header exceeds %d MB" % (int(max_scan) // 1048576))
        parser.feed(chunk)
        for event, elem in parser.read_events():
            tag = _localname(elem.tag)
            if event == "start" and tag == "programme":
                saw_programme = True
                break
            if event == "end" and tag == "channel":
                cid = str(elem.get("id") or "").strip()
                key = cid.lower()
                if cid and key not in seen:
                    seen.add(key)
                    rows.append(_channel_row(elem, cid))
                elem.clear()
        if saw_programme:
            break
    if not rows:
        raise RuntimeError("No XMLTV <channel> declarations found")
    return rows, scanned


def parse_provider_channel_map(reader, max_scan=32 * 1024 * 1024, guard_cb=None, cooperative_sleep=None,
                               read_chunk=FAST_READ_CHUNK):
    """Parse an EPGImport ``*.channels.xml`` provider map into catalogue rows.

    Rytec/Koala publish the authoritative channel-id map separately from the
    programme XMLTV feed.  beta114 only inspected the programme feed, which can
    legitimately contain no ``<channel>`` declarations and made Rytec appear
    empty in Smart Mapping.  This parser accepts both EPGImport mapping rows
    (``<channel id=...>service-ref</channel>``) and normal XMLTV channel rows.
    Only compact IDs/names are retained; service references remain provider-owned.
    """
    parser = ET.XMLPullParser(events=("end",))
    rows, seen = [], set()
    scanned = 0
    while True:
        _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
        chunk = reader.read(max(4096, int(read_chunk or FAST_READ_CHUNK)))
        if not chunk:
            break
        scanned += len(chunk)
        if scanned > int(max_scan):
            raise RuntimeError("Provider channel map exceeds %d MB" % (int(max_scan) // 1048576))
        parser.feed(chunk)
        for event, elem in parser.read_events():
            if _localname(elem.tag) != "channel":
                continue
            cid = str(elem.get("id") or "").strip()
            key = cid.casefold()
            if cid and key not in seen:
                seen.add(key)
                rows.append(_channel_row(elem, cid))
            elem.clear()
    if not rows:
        raise RuntimeError("No <channel id=...> rows found in provider channel map")
    return rows, scanned


def fetch_provider_channel_map(item, timeout=8, max_scan=32 * 1024 * 1024,
                               guard_cb=None, cooperative_sleep=None, connect_timeout=5.0,
                               fast=False):
    """Fetch a source's explicit ``channels_urls`` map before its EPG body."""
    if requests is None:
        raise RuntimeError("python3-requests is unavailable")
    urls = [str(x) for x in ((item or {}).get("channels_urls") or []) if str(x or "").strip()]
    if not urls:
        raise RuntimeError("Source has no provider channel-map URL")
    errors = []
    for url in urls:
        response = reader = None
        try:
            _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            headers = dict(DEFAULT_HEADERS)
            headers["Accept-Encoding"] = "identity"
            response = requests.get(url, headers=headers, stream=True,
                                    timeout=(float(connect_timeout), float(timeout)), verify=True)
            if response.status_code != 200:
                raise RuntimeError("HTTP %d" % response.status_code)
            reader = _open_reader(response, url)
            rows, scanned = parse_provider_channel_map(
                reader, max_scan=max_scan, guard_cb=guard_cb,
                cooperative_sleep=cooperative_sleep,
                read_chunk=(FAST_READ_CHUNK if fast else READ_CHUNK))
            return rows, {"url": url, "scanned_bytes": int(scanned),
                          "catalog_only": True, "catalog_complete": True,
                          "method": "provider-channels-map"}
        except SyncDeferred:
            raise
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
            log.warning("Provider channel-map harvest failed for %s: %s", url, exc)
        finally:
            try:
                if reader is not None and response is not None and reader is not response.raw:
                    reader.close()
            except Exception:
                pass
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
    raise RuntimeError("Provider channel-map harvest failed (%s)" % "; ".join(errors[-2:]))


def fetch_channel_ids_only(item, timeout=8, max_scan=8 * 1024 * 1024, guard_cb=None, cooperative_sleep=None,
                           connect_timeout=5.0, fast=False):
    """Fetch only an XMLTV source's channel declaration header.

    The remote programme body is never persisted and parsing stops before the
    first programme.  Mirrors are tried sequentially.
    """
    if requests is None:
        raise RuntimeError("python3-requests is unavailable")
    urls = list((item or {}).get("urls") or [])
    if not urls and (item or {}).get("url"):
        urls = [item.get("url")]
    if not urls:
        raise RuntimeError("Source has no URL")
    errors = []
    sid = str((item or {}).get("id") or "")
    for url in urls:
        response = reader = None
        started = time.monotonic()
        try:
            _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            headers = dict(DEFAULT_HEADERS)
            headers["Accept-Encoding"] = "identity"
            # RC7: validators are safe only when the compact Channel-ID shard
            # already exists.  A 304 then reuses that tiny JSON shard; provider
            # XMLTV is never written to receiver flash.
            if sid and source_channel_cache.has(sid):
                try:
                    headers.update(remote_http_cache.conditional_headers(sid, str(url)))
                except Exception:
                    pass
            response = requests.get(str(url), headers=headers, stream=True,
                                    timeout=(float(connect_timeout), float(timeout)), verify=True)
            latency_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
            if int(response.status_code or 0) == 304 and sid:
                rows = source_channel_cache.get(sid)
                if rows:
                    try:
                        remote_http_cache.record_success(
                            sid, str(url), etag=response.headers.get("ETag") or "",
                            last_modified=response.headers.get("Last-Modified") or "",
                            status=304, latency_ms=latency_ms,
                            content_length=0, channel_count=len(rows), not_modified=True)
                    except Exception:
                        pass
                    return rows, {"url": str(url), "scanned_bytes": 0,
                                  "catalog_only": True, "catalog_complete": True,
                                  "not_modified": True, "cache_hit": True,
                                  "latency_ms": latency_ms,
                                  "method": "http-304-channel-cache"}
            if response.status_code != 200:
                raise RuntimeError("HTTP %d" % response.status_code)
            reader = _open_reader(response, url)
            rows, scanned = parse_channel_ids_only(
                reader, max_scan=max_scan, guard_cb=guard_cb, cooperative_sleep=cooperative_sleep,
                read_chunk=(FAST_READ_CHUNK if fast else READ_CHUNK))
            try:
                remote_http_cache.record_success(
                    sid, str(url), etag=response.headers.get("ETag") or "",
                    last_modified=response.headers.get("Last-Modified") or "",
                    status=int(response.status_code or 200), latency_ms=latency_ms,
                    content_length=int(response.headers.get("Content-Length") or 0),
                    channel_count=len(rows), not_modified=False)
            except Exception:
                pass
            return rows, {"url": str(url), "scanned_bytes": int(scanned),
                          "catalog_only": True, "catalog_complete": True,
                          "cache_hit": False, "latency_ms": latency_ms,
                          "method": "xmltv-channel-header"}
        except SyncDeferred:
            raise
        except Exception as exc:
            latency_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
            try:
                remote_http_cache.record_failure(sid, str(url), str(exc),
                                                 status=int(getattr(response, "status_code", 0) or 0),
                                                 latency_ms=latency_ms)
            except Exception:
                pass
            errors.append("%s: %s" % (url, exc))
            log.warning("Channel-header harvest failed for %s: %s", url, exc)
        finally:
            try:
                if reader is not None and response is not None and reader is not response.raw:
                    reader.close()
            except Exception:
                pass
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
    raise RuntimeError("Channel-header harvest failed (%s)" % "; ".join(errors[-2:]))

def _open_reader(response, url):
    lower = str(url or "").lower().split("?", 1)[0]
    raw = response.raw
    try:
        raw.decode_content = False
    except Exception:
        pass
    if lower.endswith(".gz"):
        return gzip.GzipFile(fileobj=raw, mode="rb")
    if lower.endswith((".xz", ".lzma")):
        if lzma is None:
            raise RuntimeError("Python lzma module unavailable")
        return lzma.LZMAFile(raw, mode="rb")
    if lower.endswith(".bz2"):
        return bz2.BZ2File(raw, mode="rb")
    # ZIP is intentionally not handled here: reading a whole ZIP just to get
    # channel IDs violates the low-impact policy. Such a source can still use
    # its existing local cache/manual Update Source workflow.
    if lower.endswith(".zip"):
        raise RuntimeError("ZIP catalogue sync is disabled in performance-safe mode")
    return raw



def parse_programme_previews(reader, channel_ids, max_scan=6 * 1024 * 1024,
                             max_programmes=6000, guard_cb=None,
                             cooperative_sleep=0.002, read_chunk=FAST_READ_CHUNK):
    """Collect NOW/NEXT for a tiny set of XMLTV IDs from a bounded stream.

    This is used only by Smart Mapping's background preview warmer.  It retains
    no XML, no descriptions and no unrelated programme rows.  Mapping/quality
    state is never changed by this helper.
    """
    targets = set(str(x or "").strip().lower() for x in (channel_ids or []) if str(x or "").strip())
    if not targets:
        return {}, 0, 0
    parser = ET.XMLPullParser(events=("end",))
    now_epoch = int(time.time())
    previews = dict((cid, {"now_title": "", "now_start": 0, "now_stop": 0,
                           "next_title": "", "next_start": 0, "next_stop": 0})
                    for cid in targets)
    scanned = 0
    programmes = 0

    def _title(elem):
        for child in list(elem):
            if _localname(child.tag) == "title":
                try:
                    return " ".join("".join(child.itertext()).split())
                except Exception:
                    return ""
        return ""

    while True:
        _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
        chunk = reader.read(max(4096, int(read_chunk or FAST_READ_CHUNK)))
        if not chunk:
            break
        scanned += len(chunk)
        parser.feed(chunk)
        stop_scan = False
        for _event, elem in parser.read_events():
            if _localname(elem.tag) != "programme":
                continue
            programmes += 1
            cid = str(elem.get("channel") or "").strip().lower()
            if cid in targets:
                title = _title(elem)
                start = source_quality._xmltv_epoch_precise(elem.get("start"))
                stop = source_quality._xmltv_epoch_precise(elem.get("stop"))
                row = previews[cid]
                if title and start:
                    if stop and start <= now_epoch < stop:
                        if not row.get("now_start") or start >= int(row.get("now_start") or 0):
                            row["now_title"] = title
                            row["now_start"] = int(start)
                            row["now_stop"] = int(stop)
                    elif start > now_epoch:
                        old = int(row.get("next_start") or 0)
                        if not old or start < old:
                            row["next_title"] = title
                            row["next_start"] = int(start)
                            row["next_stop"] = int(stop or 0)
            elem.clear()
            if programmes >= int(max_programmes or 6000):
                stop_scan = True
                break
        complete = True
        for row in previews.values():
            if not row.get("now_title") and not row.get("next_title"):
                complete = False
                break
        if stop_scan or complete or scanned >= int(max_scan or 0):
            break
    previews = dict((cid, row) for cid, row in previews.items()
                    if row.get("now_title") or row.get("next_title"))
    return previews, int(scanned), int(programmes)


def fetch_programme_previews(item, channel_ids, timeout=6, max_scan=6 * 1024 * 1024,
                             max_programmes=6000, guard_cb=None,
                             cooperative_sleep=0.002, connect_timeout=3.0):
    """Fetch bounded NOW/NEXT metadata for requested IDs only.

    Intended for a daemon worker after SAFE EPG MATCH is already painted.  It
    never writes provider XML to disk and never mutates mapping ownership.
    """
    if requests is None:
        raise RuntimeError("python3-requests is unavailable")
    urls = list((item or {}).get("urls") or [])
    if not urls and (item or {}).get("url"):
        urls = [item.get("url")]
    if not urls:
        raise RuntimeError("Source has no URL")
    errors = []
    for url in urls:
        response = reader = None
        try:
            _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            headers = dict(DEFAULT_HEADERS)
            headers["Accept-Encoding"] = "identity"
            response = requests.get(str(url), headers=headers, stream=True,
                                    timeout=(float(connect_timeout), float(timeout)), verify=True)
            if response.status_code != 200:
                raise RuntimeError("HTTP %d" % response.status_code)
            reader = _open_reader(response, url)
            previews, scanned, programmes = parse_programme_previews(
                reader, channel_ids, max_scan=max_scan, max_programmes=max_programmes,
                guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            return previews, {"url": str(url), "scanned_bytes": int(scanned),
                              "programmes_seen": int(programmes), "preview_only": True}
        except SyncDeferred:
            raise
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
            log.debug("Programme-preview warm failed for %s: %s", url, exc)
        finally:
            try:
                if reader is not None and response is not None and reader is not response.raw:
                    reader.close()
            except Exception:
                pass
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
    raise RuntimeError("Programme-preview warm failed (%s)" % "; ".join(errors[-2:]))


def fetch_channels(item, timeout=8, quality_scan=QUALITY_MAX_SCAN, max_programmes=QUALITY_MAX_PROGRAMMES,
                   guard_cb=None, cooperative_sleep=None):
    if requests is None:
        raise RuntimeError("python3-requests is unavailable")
    urls = list((item or {}).get("urls") or [])
    if not urls and (item or {}).get("url"):
        urls = [item.get("url")]
    if not urls:
        raise RuntimeError("Source has no URL")
    errors = []
    # One URL attempt at a time. Mirrors are fallbacks only, never parallel.
    for url in urls:
        response = reader = None
        try:
            _yield_cpu(guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            headers = dict(DEFAULT_HEADERS)
            headers["Accept-Encoding"] = "identity"
            response = requests.get(str(url), headers=headers, stream=True,
                                    timeout=(5, int(timeout)), verify=True)
            if response.status_code != 200:
                raise RuntimeError("HTTP %d" % response.status_code)
            reader = _open_reader(response, url)
            rows, scanned, quality = parse_channel_header(
                reader, quality_scan=quality_scan, max_programmes=max_programmes,
                guard_cb=guard_cb, cooperative_sleep=cooperative_sleep)
            return rows, {"url": str(url), "scanned_bytes": int(scanned), "quality": quality}
        except SyncDeferred:
            raise
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
            log.warning("On-demand channel sync failed for %s: %s", url, exc)
        finally:
            try:
                if reader is not None and response is not None and reader is not response.raw:
                    reader.close()
            except Exception:
                pass
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
    raise RuntimeError("Channel sync failed (%s)" % "; ".join(errors[-2:]))
