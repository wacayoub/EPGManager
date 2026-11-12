# -*- coding: utf-8 -*-
"""Small persistent metadata cache for EPG source language/freshness.

Arabic is the implicit/default display language for MENA sources.  The UI only
shows a suffix for non-Arabic dominant guides (currently EN / FR), as requested.
The audit never changes mappings; it is informational/source-selection metadata.
"""
from __future__ import print_function
import json
import os
import re
import time

from .json_atomic import lock_for, mutate_json, write_json

DEFAULT_PATH = "/etc/epgmanager_epg/source_metadata.json"
_ARABIC_RE = re.compile(u"[\u0600-\u06ff]")
_TAG_RE = re.compile(br"<(?:title|desc)\b([^>]*)>(.*?)</(?:title|desc)>", re.I | re.S)
_LANG_RE = re.compile(br"\blang\s*=\s*['\"]?([a-zA-Z_-]{2,12})", re.I)
_TAG_STRIP_RE = re.compile(br"<[^>]+>")
_FR_WORDS = set("le la les de des du une un et est dans avec pour sur aux au que qui ce cette ces mais plus pas vous votre nous notre".split())
_EN_WORDS = set("the and of to in for with is on this that from your you our are as at by an a".split())
_MEM = {}
_MEM_SIG = {}

def _meta_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None



def _load(path=DEFAULT_PATH):
    with lock_for(path):
        sig = _meta_sig(path)
        if path in _MEM and _MEM_SIG.get(path) == sig:
            return _MEM[path]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data = data if isinstance(data, dict) else {}
        except Exception:
            data = {}
        _MEM[path] = data
        _MEM_SIG[path] = sig
        return data


def _save(data, path=DEFAULT_PATH):
    try:
        with lock_for(path):
            write_json(path, data)
            _MEM[path] = data
            _MEM_SIG[path] = _meta_sig(path)
    except Exception:
        pass



def _put_row(sid, row, path=DEFAULT_PATH):
    def _mut(data):
        if not isinstance(data, dict): data = {}
        data[str(sid)] = dict(row or {})
        return data
    try:
        with lock_for(path):
            data = mutate_json(path, _mut, {})
            _MEM[path] = data
            _MEM_SIG[path] = _meta_sig(path)
    except Exception:
        pass
    return row

def _signature(path):
    try:
        st = os.stat(path)
        return [int(st.st_mtime), int(st.st_size)]
    except Exception:
        return []


def _norm_lang(raw):
    raw = str(raw or "").lower().replace("_", "-")
    if raw.startswith("ar"): return "ar"
    if raw.startswith("en"): return "en"
    if raw.startswith("fr"): return "fr"
    return ""


def _detect_from_payload(payload, limit=500):
    counts = {"ar": 0, "en": 0, "fr": 0, "unknown": 0}
    seen = 0
    for match in _TAG_RE.finditer(payload or b""):
        attrs, body = match.group(1), match.group(2)
        lm = _LANG_RE.search(attrs or b"")
        lang = _norm_lang((lm.group(1).decode("ascii", "ignore") if lm else ""))
        text = _TAG_STRIP_RE.sub(b" ", body or b"").decode("utf-8", "ignore").strip()
        if not text:
            continue
        if lang:
            counts[lang] += 1
        elif _ARABIC_RE.search(text):
            counts["ar"] += 1
        else:
            words = re.findall(r"[A-Za-zÀ-ÿ']+", text.lower())
            fr = sum(1 for w in words if w in _FR_WORDS)
            en = sum(1 for w in words if w in _EN_WORDS)
            if fr >= 2 and fr > en * 1.35:
                counts["fr"] += 1
            elif en >= 2 and en > fr * 1.35:
                counts["en"] += 1
            else:
                counts["unknown"] += 1
        seen += 1
        if seen >= limit:
            break
    ranked = sorted(((v, k) for k, v in counts.items() if k != "unknown"), reverse=True)
    if not ranked or ranked[0][0] <= 0:
        return "", 0, counts
    value, lang = ranked[0]
    total = max(1, sum(counts.values()))
    confidence = int(round(100.0 * value / total))
    # Avoid labeling a mixed/uncertain file EN/FR from weak evidence.
    if lang in ("en", "fr") and confidence < 58:
        return "", confidence, counts
    return lang, confidence, counts


def audit_source(source, path, payload=None, metadata_path=DEFAULT_PATH, force=False):
    sid = str((source or {}).get("id") or "")
    if not sid or not path:
        return {}
    explicit = _norm_lang((source or {}).get("language"))
    data = _load(metadata_path)
    sig = _signature(path)
    current = data.get(sid) or {}
    if explicit:
        row = {"language": explicit, "confidence": 100, "source": "catalogue", "sig": sig, "checked": int(time.time())}
        _put_row(sid, row, metadata_path); return row
    if (not force) and current.get("sig") == sig and current.get("language"):
        return current
    if payload is None:
        try:
            # Vu+ low-memory rule: language detection needs only a representative
            # programme sample, never the whole XMLTV file.
            with open(path, "rb") as fh:
                payload = fh.read(8 * 1024 * 1024)
        except Exception:
            return current
    lang, confidence, counts = _detect_from_payload(payload)
    row = {"language": lang, "confidence": confidence, "source": "xml-audit", "counts": counts,
           "sig": sig, "checked": int(time.time())}
    _put_row(sid, row, metadata_path); return row


def get(source, epg_dir=None, metadata_path=DEFAULT_PATH):
    explicit = _norm_lang((source or {}).get("language"))
    if explicit:
        return {"language": explicit, "confidence": 100, "source": "catalogue"}
    sid = str((source or {}).get("id") or "")
    return (_load(metadata_path).get(sid) or {}) if sid else {}


def suffix(source, epg_dir=None):
    """Return only non-Arabic suffixes. Arabic/unknown stay visually clean."""
    lang = _norm_lang((get(source, epg_dir) or {}).get("language"))
    if lang == "en": return "EN"
    if lang == "fr": return "FR"
    return ""


def ensure_recovered(metadata_path=DEFAULT_PATH, preferences=None):
    """Recreate source_metadata.json if it was deleted/corrupted.

    Selection is NOT stored here (it lives in epgmanager_source_preferences.json).
    Recovery therefore never changes Smart Sources selections.  We rebuild only
    metadata for currently selected feeds, using catalogue language where known.
    Unknown-language feeds are left as unknown until their normal lightweight
    audit runs; no network access and no full XML parsing happens here.
    """
    current = _load(metadata_path)
    if current and os.path.isfile(metadata_path):
        return {"ok": True, "created": False, "sources": len(current)}
    try:
        from . import source_catalog
        from .source_preferences import SourcePreferences
        prefs = preferences or SourcePreferences()
        by_id = source_catalog.by_catalogue_id()
        data = {}
        now = int(time.time())
        for sid in prefs.selected_catalogue_ids():
            item = by_id.get(sid) or {}
            lang = _norm_lang(item.get("language"))
            if not lang:
                continue
            data[str(sid)] = {"language": lang, "confidence": 100,
                              "source": "catalogue-recovery", "sig": [], "checked": now}
        _save(data, metadata_path)
        return {"ok": True, "created": True, "sources": len(data)}
    except Exception:
        # At minimum restore a valid JSON object so later source audits can fill it.
        try:
            _save({}, metadata_path)
        except Exception:
            pass
        return {"ok": False, "created": True, "sources": 0}
