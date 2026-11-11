# -*- coding: utf-8 -*-
"""Persistent low-impact XMLTV quality profiles used to arbitrate source conflicts.

The golden SAT mapper is not modified here.  This module only measures the
actual programme payload of a candidate XMLTV source/channel so an already
valid mapping can prefer the guide that contains useful events instead of a
header-only / placeholder feed.

Performance policy for Vu+ receivers:
- one small JSON file per source (no giant global quality JSON);
- no network access from this module;
- remote profiles are produced by the existing on-demand Smart Mapping worker;
- local EPGManager XML files may be profiled from disk and are cached by mtime;
- Import All only reads cached metrics and never downloads a guide for scoring.
"""
from __future__ import print_function

import calendar
import json
import os
import re
import time
from datetime import datetime
from xml.etree import ElementTree as ET

from .logger import get_logger

log = get_logger(__name__)
QUALITY_DIR = "/etc/epgmanager_epg/.source_quality"
VERSION = 2
_ARABIC_RE = re.compile(u"[\u0600-\u06ff]")
_SPACE_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^a-z0-9\u0600-\u06ff]+", re.I)
_MEM = {}


def _sid(item_or_id):
    if isinstance(item_or_id, dict):
        return str(item_or_id.get("source_id") or item_or_id.get("id") or item_or_id.get("manager_id") or "").strip().lower()
    return str(item_or_id or "").strip().lower()


def _slug(value):
    value = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").lower()).strip("_")
    return value or "unknown"


def path_for(item_or_id):
    return os.path.join(QUALITY_DIR, _slug(_sid(item_or_id)) + ".json")


def _sig(path):
    try:
        st = os.stat(path)
        return [int(st.st_mtime), int(st.st_size)]
    except Exception:
        return []


def _load_path(path):
    sig = _sig(path)
    cached = _MEM.get(path)
    if cached and cached[0] == sig:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _MEM[path] = (sig, data)
    return data


def load(item_or_id):
    return _load_path(path_for(item_or_id))


def has(item_or_id):
    data = load(item_or_id)
    return bool(data.get("channels") or data.get("overall"))


def save(item_or_id, profile):
    sid = _sid(item_or_id)
    if not sid or not isinstance(profile, dict):
        return False
    path = path_for(sid)
    try:
        if not os.path.isdir(QUALITY_DIR):
            os.makedirs(QUALITY_DIR)
        payload = dict(profile)
        payload["version"] = VERSION
        payload["source_id"] = sid
        payload.setdefault("checked", int(time.time()))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        _MEM[path] = (_sig(path), payload)
        return True
    except Exception:
        log.exception("Could not save source quality profile %s", sid)
        return False


def _localname(tag):
    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _text(elem):
    try:
        return _SPACE_RE.sub(" ", "".join(elem.itertext()).strip())
    except Exception:
        return ""


def _norm(value):
    value = str(value or "").lower()
    value = _NONWORD_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _xmltv_epoch(raw):
    raw = str(raw or "").strip()
    digits = re.sub(r"[^0-9]", "", raw[:14])
    if len(digits) < 12:
        return 0
    fmt = "%Y%m%d%H%M%S" if len(digits) >= 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(digits[:14] if len(digits) >= 14 else digits[:12], fmt)
        return int(time.mktime(dt.timetuple()))
    except Exception:
        return 0


_TZ_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def _xmltv_epoch_precise(raw):
    """UTC epoch for preview timing, including XMLTV timezone offsets.

    Quality scoring keeps its long-standing parser above to avoid changing
    historical quality metrics in a UI-only release.  NOW/NEXT preview needs
    accurate wall-clock timing, so it uses this stricter parser.
    """
    if not raw:
        return 0
    parts = str(raw).strip().split()
    stamp = str(parts[0] or "")
    if len(stamp) < 12:
        return 0
    stamp = stamp[:14].ljust(14, "0")
    try:
        epoch = calendar.timegm((int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]),
                                 int(stamp[8:10]), int(stamp[10:12]), int(stamp[12:14]),
                                 0, 0, 0))
    except Exception:
        return 0
    if len(parts) > 1:
        tz = str(parts[1] or "").strip()
        if tz.upper() == "Z":
            return int(epoch)
        match = _TZ_RE.match(tz)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            offset = (int(match.group(2)) * 60 + int(match.group(3))) * 60
            epoch -= sign * offset
    return int(epoch)


def new_collector(channel_names=None):
    return {
        "channel_names": dict(channel_names or {}),
        "programs": 0,
        "desc": 0,
        "arabic": 0,
        "title_arabic": 0,
        "desc_arabic": 0,
        "titles": {},
        "channels": {},
        "first": 0,
        "last": 0,
        "future_programmes": 0,
        "future_last": 0,
        # Capture time is fixed for the whole sample so programme ordering does
        # not change the NOW/NEXT decision while a remote stream is parsed.
        "preview_now": int(time.time()),
    }


def collect_programme(collector, elem):
    cid = str(elem.get("channel") or "").strip().lower()
    if not cid:
        return
    title = ""
    desc = ""
    for child in list(elem):
        tag = _localname(child.tag)
        if tag == "title" and not title:
            title = _text(child)
        elif tag == "desc" and not desc:
            desc = _text(child)
    start = _xmltv_epoch(elem.get("start"))
    stop = _xmltv_epoch(elem.get("stop"))
    if not stop:
        stop = start
    c = collector["channels"].setdefault(cid, {
        "p": 0, "d": 0, "ar": 0, "tar": 0, "dar": 0, "titles": {}, "generic": 0,
        "first": 0, "last": 0, "long": 0,
        "future_programmes": 0, "future_last": 0,
        "now_title": "", "now_start": 0, "now_stop": 0,
        "next_title": "", "next_start": 0, "next_stop": 0,
    })
    c["p"] += 1
    collector["programs"] += 1

    # Tiny NOW/NEXT preview metadata.  This is derived while the existing
    # quality sampler is already parsing programme rows, so it adds no network
    # request and no extra XML pass.  Only two titles/timestamps per channel are
    # persisted in the small quality shard.
    pstart = _xmltv_epoch_precise(elem.get("start"))
    pstop = _xmltv_epoch_precise(elem.get("stop"))
    preview_now = int(collector.get("preview_now") or time.time())
    # rc43: keep exact per-ID future coverage in the same single XML pass.
    # ``future_last`` is later compared with the current clock dynamically, so
    # a quality shard cannot keep an ID green after its final event has expired.
    future_edge = int(pstop or pstart or 0)
    if future_edge > preview_now:
        c["future_programmes"] = int(c.get("future_programmes") or 0) + 1
        c["future_last"] = max(int(c.get("future_last") or 0), future_edge)
        collector["future_programmes"] = int(collector.get("future_programmes") or 0) + 1
        collector["future_last"] = max(int(collector.get("future_last") or 0), future_edge)
    if pstart:
        if pstop and pstart <= preview_now < pstop:
            if not c.get("now_start") or pstart >= int(c.get("now_start") or 0):
                c["now_title"] = title
                c["now_start"] = int(pstart)
                c["now_stop"] = int(pstop)
        elif pstart > preview_now:
            old_next = int(c.get("next_start") or 0)
            if not old_next or pstart < old_next:
                c["next_title"] = title
                c["next_start"] = int(pstart)
                c["next_stop"] = int(pstop or 0)
    nt = _norm(title)
    if nt:
        c["titles"][nt] = c["titles"].get(nt, 0) + 1
        collector["titles"][nt] = collector["titles"].get(nt, 0) + 1
    if desc and len(desc.strip()) >= 12:
        c["d"] += 1
        collector["desc"] += 1
    title_is_ar = bool(title and _ARABIC_RE.search(title))
    desc_is_ar = bool(desc and _ARABIC_RE.search(desc))
    if title_is_ar:
        c["tar"] += 1
        collector["title_arabic"] += 1
    if desc_is_ar:
        c["dar"] += 1
        collector["desc_arabic"] += 1
    sample_text = (title + " " + desc).strip()
    if sample_text and _ARABIC_RE.search(sample_text):
        c["ar"] += 1
        collector["arabic"] += 1
    cname = _norm((collector.get("channel_names") or {}).get(cid) or "")
    if nt and cname and (nt == cname or nt in ("programme " + cname, "program " + cname)):
        c["generic"] += 1
    if start:
        c["first"] = start if not c["first"] else min(c["first"], start)
        collector["first"] = start if not collector["first"] else min(collector["first"], start)
    if stop:
        c["last"] = max(c["last"], stop)
        collector["last"] = max(collector["last"], stop)
    if start and stop and stop > start and (stop - start) >= 150 * 60:
        c["long"] += 1


def _finish_row(row):
    p = int(row.get("p") or 0)
    d = int(row.get("d") or 0)
    ar = int(row.get("ar") or 0)
    tar = int(row.get("tar") or 0)
    dar = int(row.get("dar") or 0)
    titles = row.get("titles") or {}
    unique = len(titles)
    generic = int(row.get("generic") or 0)
    first = int(row.get("first") or 0)
    last = int(row.get("last") or 0)
    future_programmes = int(row.get("future_programmes") or 0)
    future_last = int(row.get("future_last") or 0)
    days = 0.0
    if first and last and last >= first:
        days = round(max(0.05, float(last - first) / 86400.0), 2)
    repeated = 0.0
    if p:
        most = max(titles.values()) if titles else 0
        repeated = float(most) / float(p)
    placeholder = bool(
        p >= 3 and (
            (unique <= 1 and repeated >= 0.75) or
            (generic >= max(2, int(round(p * 0.5)))) or
            (int(row.get("long") or 0) >= max(2, int(round(p * 0.7))) and unique <= 2)
        )
    )
    out = {
        "programs": p,
        "desc_ratio": round(float(d) / float(p), 3) if p else 0.0,
        "arabic_ratio": round(float(ar) / float(p), 3) if p else 0.0,
        "title_arabic_ratio": round(float(tar) / float(p), 3) if p else 0.0,
        "desc_arabic_ratio": round(float(dar) / float(p), 3) if p else 0.0,
        "unique_titles": unique,
        "repeat_ratio": round(repeated, 3),
        "generic_ratio": round(float(generic) / float(p), 3) if p else 0.0,
        "days": days,
        "placeholder": placeholder,
        "future_programmes": future_programmes,
        "future_last": future_last,
    }
    # Keep preview metadata absent from channels where the existing bounded
    # sample did not actually encounter a relevant event.  Missing preview is
    # UNKNOWN, never evidence of an empty guide.
    if row.get("now_title"):
        out["now_title"] = str(row.get("now_title") or "")
        out["now_start"] = int(row.get("now_start") or 0)
        out["now_stop"] = int(row.get("now_stop") or 0)
    if row.get("next_title"):
        out["next_title"] = str(row.get("next_title") or "")
        out["next_start"] = int(row.get("next_start") or 0)
        out["next_stop"] = int(row.get("next_stop") or 0)
    return out


def finish(collector, source_name="", scanned_bytes=0, complete=False):
    channels = {}
    for cid, row in (collector.get("channels") or {}).items():
        channels[cid] = _finish_row(row)
    # A complete full-file profile can safely mark header IDs with no programme
    # rows as zero-quality. A bounded remote sample cannot make that claim.
    if complete:
        for cid in (collector.get("channel_names") or {}).keys():
            channels.setdefault(str(cid).lower(), _finish_row({}))
    overall = _finish_row({
        "p": collector.get("programs") or 0,
        "d": collector.get("desc") or 0,
        "ar": collector.get("arabic") or 0,
        "tar": collector.get("title_arabic") or 0,
        "dar": collector.get("desc_arabic") or 0,
        "titles": collector.get("titles") or {},
        "generic": 0,
        "first": collector.get("first") or 0,
        "last": collector.get("last") or 0,
        "long": 0,
        "future_programmes": collector.get("future_programmes") or 0,
        "future_last": collector.get("future_last") or 0,
    })
    return {
        "source_name": str(source_name or ""),
        "checked": int(time.time()),
        "scanned_bytes": int(scanned_bytes or 0),
        "complete": bool(complete),
        "overall": overall,
        "channels": channels,
    }


def _channel_from_profile(data, channel_id):
    data = data or {}
    cid = str(channel_id or "").strip().lower()
    row = (data.get("channels") or {}).get(cid)
    if row is not None:
        out = dict(row or {})
        out["specific"] = True
        out["profile_checked"] = data.get("checked")
        out["profile_complete"] = bool(data.get("complete"))
        return out
    overall = data.get("overall") or {}
    if overall:
        out = dict(overall)
        out["specific"] = False
        out["profile_complete"] = bool(data.get("complete"))
        return out
    return {}


def get_channel(item_or_id, channel_id):
    return _channel_from_profile(load(item_or_id), channel_id)


def get_channel_cached(item_or_id, channel_id):
    """Hot-path quality lookup for Smart Mapping.

    The first lookup still validates/loads the tiny per-source quality JSON.
    Later candidate rows reuse the in-process profile directly, avoiding one
    ``stat()`` per EPG ID while a large provider list is painted on a Vu+.
    ``save()`` already refreshes ``_MEM``, so an in-process source update is
    visible immediately without any extra invalidation protocol.
    """
    path = path_for(item_or_id)
    cached = _MEM.get(path)
    data = cached[1] if cached else _load_path(path)
    return _channel_from_profile(data, channel_id)


def get_programme_preview_cached(item_or_id, channel_id, now_epoch=None, max_future_hours=36):
    """Return cached NOW/NEXT programme metadata for one XMLTV ID.

    This is a pure cache read.  It never performs network I/O and never parses
    XML.  Expired samples return an empty dict rather than showing stale EPG.
    """
    row = get_channel_cached(item_or_id, channel_id)
    if not row.get("specific"):
        return {}
    now_epoch = int(now_epoch or time.time())
    current_title = str(row.get("now_title") or "").strip()
    current_start = int(row.get("now_start") or 0)
    current_stop = int(row.get("now_stop") or 0)
    next_title = str(row.get("next_title") or "").strip()
    next_start = int(row.get("next_start") or 0)
    next_stop = int(row.get("next_stop") or 0)

    # A stored NEXT event may have become NOW since the profile was captured.
    if next_title and next_start and next_start <= now_epoch and (not next_stop or now_epoch < next_stop):
        return {"kind": "NOW", "title": next_title, "start": next_start, "stop": next_stop}
    if current_title and current_start and current_stop and current_start <= now_epoch < current_stop:
        return {"kind": "NOW", "title": current_title, "start": current_start, "stop": current_stop}
    horizon = now_epoch + max(1, int(max_future_hours or 36)) * 3600
    if next_title and next_start and now_epoch < next_start <= horizon:
        return {"kind": "NEXT", "title": next_title, "start": next_start, "stop": next_stop}
    return {}


def confirmed_no_programmes(item_or_id, channel_id):
    """True only when a complete profile proves this exact XMLTV ID is empty.

    Partial remote samples intentionally return False when an ID was simply not
    encountered: absence in a bounded sample is not evidence of an empty feed.
    This keeps the violet warning free of false positives.
    """
    row = get_channel_cached(item_or_id, channel_id)
    return bool(row.get("specific") and row.get("profile_complete") and
                int(row.get("programs") or 0) <= 0)


def quality_score(metrics, prefer_arabic=False):
    """Return a bounded content-quality score. Hard provider gates live elsewhere."""
    m = metrics or {}
    p = int(m.get("programs") or 0)
    if p <= 0:
        return -500
    if m.get("placeholder"):
        return -260
    score = 80
    # Real programme detail is more valuable than a matching channel header.
    score += int(min(1.0, float(m.get("desc_ratio") or 0.0)) * 180)
    # Diversity catches fake guides where the channel name is repeated every few hours.
    unique = int(m.get("unique_titles") or 0)
    score += min(120, unique * 8)
    repeat = float(m.get("repeat_ratio") or 0.0)
    if repeat >= 0.8:
        score -= 130
    elif repeat >= 0.55:
        score -= 70
    # More future depth wins, but cap it so a 14-day weak guide cannot beat a
    # rich 3-day guide on duration alone.
    score += int(min(7.0, float(m.get("days") or 0.0)) * 24)
    score += min(70, int(p * 2))
    if prefer_arabic:
        title_ar = float(m.get("title_arabic_ratio") if m.get("title_arabic_ratio") is not None else m.get("arabic_ratio") or 0.0)
        desc_ar = float(m.get("desc_arabic_ratio") or 0.0)
        if title_ar >= 0.60:
            score += 220
        elif title_ar >= 0.25:
            score += 90
        elif title_ar <= 0.05 and desc_ar >= 0.35:
            score -= 180
        elif title_ar <= 0.05:
            score -= 110
        if desc_ar >= 0.60:
            score += 50
    return max(-500, min(700, int(score)))


def _file_profile(source, xml_path):
    sid = _sid(source)
    if not sid or not xml_path or not os.path.isfile(xml_path):
        return {}
    signature = _sig(xml_path)
    old = load(sid)
    if old.get("file_sig") == signature and old.get("channels"):
        return old
    names = {}
    collector = new_collector(names)
    try:
        context = ET.iterparse(xml_path, events=("start", "end"))
        for event, elem in context:
            tag = _localname(elem.tag)
            if event == "end" and tag == "channel":
                cid = str(elem.get("id") or "").strip().lower()
                if cid:
                    label = ""
                    for child in list(elem):
                        if _localname(child.tag) == "display-name":
                            label = _text(child)
                            if label:
                                break
                    names[cid] = label or cid
                    collector.setdefault("channel_names", {})[cid] = label or cid
                elem.clear()
            elif event == "end" and tag == "programme":
                collect_programme(collector, elem)
                elem.clear()
    except Exception:
        log.exception("Quality profiling failed for %s", xml_path)
        return old
    profile = finish(collector, source_name=(source or {}).get("name") or sid,
                     scanned_bytes=(signature[1] if signature else 0), complete=True)
    profile["file_sig"] = signature
    save(sid, profile)
    return profile


def ensure_local(source, epg_dir):
    if str((source or {}).get("kind") or "").lower() != "local":
        return {}
    filename = str((source or {}).get("output_file") or "")
    if not filename:
        return {}
    return _file_profile(source, os.path.join(epg_dir, filename))


def audit_file(source, xml_path):
    """Audit a fully downloaded external XMLTV file outside Import All."""
    return _file_profile(source, xml_path)
