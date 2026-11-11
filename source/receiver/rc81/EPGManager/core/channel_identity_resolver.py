# -*- coding: utf-8 -*-
"""Stable deterministic channel identity resolver used by the beta41 SRP Master.

This module resolves *identity only*.  It never invents a DVB Service Reference.
All SRPs are copied from the receiver's real SAT registry/lamedb or from an
explicit user/provider map.
"""
from __future__ import print_function

import json
import os
import re
import unicodedata

PLUGIN_ROOT = os.path.dirname(os.path.dirname(__file__))
ALIASES_PATH = os.path.join(PLUGIN_ROOT, "data", "srp_aliases.json")

_DROP_WORDS = set((
    "hd", "fhd", "sd", "hevc", "h265", "mpeg4",
    "channel", "ch", "digital", "mono",
))
# Country suffixes used as provider/channel-ID decorations.  Language/feed
# suffixes (AR/EN/FR/MENA) are deliberately NOT here: they can identify a
# different programme feed (for example i24NEWS EN/AR/FR or 2M MENA).
_COUNTRY_SUFFIXES = set(("sa", "ae", "eg", "qa", "ma", "ps", "tn", "dz", "iq", "lb", "jo", "kw", "bh", "om", "ye", "sy"))
_MEM = None
_MEM_SIG = None


def _signature(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _load():
    global _MEM, _MEM_SIG
    sig = _signature(ALIASES_PATH)
    if isinstance(_MEM, dict) and sig == _MEM_SIG:
        return _MEM
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        raw = {"identities": []}
    by_alias = {}
    by_id = {}
    for row in raw.get("identities") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        item = dict(row)
        by_id[str(item["id"])] = item
        for alias in item.get("aliases") or []:
            for key in _keys_raw(alias):
                if not key:
                    continue
                previous = by_alias.get(key, "__missing__")
                if previous == "__missing__":
                    by_alias[key] = item
                elif previous is None:
                    continue
                elif str(previous.get("id") or "") != str(item.get("id") or ""):
                    # A shared normalized alias is ambiguous.  Never let JSON
                    # ordering silently choose one channel identity.
                    by_alias[key] = None
    _MEM = {"by_alias": by_alias, "by_id": by_id}
    _MEM_SIG = sig
    return _MEM


def normalize(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    # rc49: receiver bouquets often glue the presentation token in OSNtv.
    # Treat OSNtv exactly like "OSN TV" without weakening other short brands.
    raw = re.sub(r"\bosntv\b", "osn", raw)
    # beta118: strip only a trailing *country* decoration such as .ma/.eg.
    # Do not strip AR/EN/FR/MENA even behind '_' or '.', because those tokens
    # can be programme-feed identity rather than metadata.
    decoration = "|".join(sorted((re.escape(x) for x in _COUNTRY_SUFFIXES), key=len, reverse=True))
    raw = re.sub(r"(?i)[._-](?:%s)$" % decoration, "", raw).strip()
    text = raw
    # Keep Arabic letters while removing Latin diacritics.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = text.replace("+", " plus ")
    text = re.sub(r"[._/\\|:\-]+", " ", text)
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    words = [w for w in re.sub(r"\s+", " ", text).strip().split(" ") if w]
    # Do not remove plain trailing country/language tokens here: they can be
    # programme-feed identity. Decoration-only suffixes were handled above.
    words = [w for w in words if w not in _DROP_WORDS]
    return " ".join(words).strip()


def compact(value):
    return normalize(value).replace(" ", "")


def _keys_raw(value):
    n = normalize(value)
    if not n:
        return []
    c = n.replace(" ", "")
    out = ["n:" + n]
    # Compact keys bridge harmless spacing differences (Al Wataniya/Alwataniya)
    # but short names such as "ON E" must never collapse into "ONE".
    if len(c) >= 5:
        out.append("c:" + c)
    # Presentation words often occur glued to the final number in XMLTV IDs.
    for token in ("tv", "channel"):
        alt = re.sub(r"%s(?=[0-9]+$)" % token, "", c)
        if alt and alt != c and len(alt) >= 5:
            out.append("c:" + alt)
    return out


def label_from_id(channel_id):
    raw = str(channel_id or "").strip()
    if not raw:
        return ""
    # Strip common beIN asset decorations before generic dotted-ID handling.
    text = raw
    text = re.sub(r"(?i)(?:_?digital_?mono(?:_ar|_en)?|_?mono(?:_ar|_en)?|\.bein)$", "", text)
    text = re.sub(r"(?i)^logos?[-_]*", "", text)
    text = re.sub(r"(?i)bein\.com", "bein", text)
    text = text.replace("_", " ").replace(".", " ").replace("-", " ")
    text = re.sub(r"(?i)\b([a-z]+)(\d+)\b", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = text.split()
    while parts and parts[-1].lower() in _COUNTRY_SUFFIXES:
        parts.pop()
    return " ".join(parts) or raw


def resolve(value):
    data = _load()
    for key in _keys_raw(value):
        row = data["by_alias"].get(key)
        if row:
            return dict(row)
    return None


def canonical_key(value):
    row = resolve(value)
    if row:
        return "id:" + str(row.get("id"))
    n = normalize(value)
    return "name:" + n if n else ""


def keys(value):
    out = []
    row = resolve(value)
    if row:
        out.append("id:" + str(row.get("id")))
    for key in _keys_raw(value):
        if key not in out:
            out.append(key)
    return out


def equivalent(a, b):
    if not a or not b:
        return False
    ra, rb = resolve(a), resolve(b)
    if ra and rb:
        return str(ra.get("id")) == str(rb.get("id"))
    return compact(a) == compact(b)


def identity_country(value):
    row = resolve(value) or {}
    return str(row.get("country") or "").upper()
