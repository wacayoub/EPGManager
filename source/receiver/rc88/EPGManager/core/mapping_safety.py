# -*- coding: utf-8 -*-
"""Targeted automatic-mapping guardrails.

The beta1 matcher remains the primary matcher because it gives the best receiver
coverage.  This module only blocks known dangerous false positives. Manual
mapping is never restricted.
"""
from __future__ import print_function

from . import channel_mapper

_COUNTRY_HINTS = {
    "MA": set(["maroc", "morocco", "marocain"]),
    "EG": set(["egypt", "egyptian", "misr", "masr"]),
    "QA": set(["qatar", "qatari"]),
    "SA": set(["saudi", "ksa"]),
    "AE": set(["uae", "emirates"]),
    "FR": set(["france", "french", "francais"]),
    "UK": set(["uk", "british"]),
}
_MENA = set(["MENA", "MA", "DZ", "TN", "EG", "SA", "QA", "AE", "PS"])

def _norm(v): return channel_mapper.normalize_name(v or "")
def _compact(v): return "".join(_norm(v).split())
def _source_id(source):
    source=source or {}; return str(source.get("source_id") or source.get("manager_id") or source.get("id") or "")
def _source_name(source):
    source=source or {}; return str(source.get("source_name") or source.get("name") or _source_id(source))
def _expected(source):
    source=source or {}; return set(str(x) for x in (source.get("countries") or source.get("expected_codes") or []))
def _hints(text):
    words=set(_norm(text).split()); out=set()
    for code,hints in _COUNTRY_HINTS.items():
        if words & hints: out.add(code)
    return out

def channel_family(name):
    n=_norm(name); compact="".join(n.split()); words=set(n.split())
    if ("bein" in compact or "beinsports" in compact) and ("sport" in n or "sports" in n): return "bein_sports"
    if "nationalgeographic" in compact or "natgeo" in compact or ("national" in words and "geographic" in words): return "national_geographic"
    if "cartoonnetwork" in compact: return "cartoon_network"
    if "discovery" in words: return "discovery"
    if "disney" in words: return "disney"
    if "fox" in words: return "fox"
    return ""

def auto_match_allowed(epg_name, service, source, score=100):
    epg_name=str(epg_name or ""); service=service or {}; source=source or {}
    service_name=str(service.get("clean_name") or service.get("name") or "")
    if not epg_name or not service_name: return False, "missing-name"
    a,b=_compact(epg_name),_compact(service_name)
    # Production false friend: Morocco Al Aoula is NOT Egypt Al Oula.
    if set([a,b]) == set(["alaoula","aloula"]): return False, "al-aoula-vs-al-oula"
    expected=_expected(source); eh,sh=_hints(epg_name),_hints(service_name)
    if eh and sh and not (eh & sh): return False, "country-name-conflict"
    if expected and sh and not (sh & expected or ("MENA" in expected and sh & _MENA)):
        return False, "service-country-conflict"
    family=channel_family(epg_name) or channel_family(service_name)
    # Do not decide the beIN provider from its label here. Some valid MENA XMLTV
    # bundles have generic source names. EPGManager's persistent Auto-Mapping
    # source allow-list is the authoritative provider/source lock.
    # Global brands are blocked only when there is an explicit contradictory country marker.
    if family in ("national_geographic","cartoon_network","discovery","disney","fox"):
        if expected and sh and not (sh & expected or ("MENA" in expected and sh & _MENA)):
            return False, "global-brand-country-conflict"
    if int(score or 0) < 82: return False, "score-too-low"
    return True, "allowed"
