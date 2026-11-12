# -*- coding: utf-8 -*-
"""Single channel-identity facade for Smart Mapping beta85.

The project historically accumulated two curated identity databases.  Smart
Mapping must not let different code paths choose different identities, so this
module is the only identity API used by the manual mapping assistant.  It merges
both existing curated datasets without touching the frozen SAT/SRP mapper.
"""
from __future__ import print_function

from . import channel_identity_resolver, smartmatch_ai
import re


def _merge_aliases(out, values):
    seen = set(str(x).casefold() for x in out if x)
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)


_COUNTRY_SUFFIXES = set("ar ae at au be bg br ca ch cz de dk eg es fi fr gb gr hr hu ie il in it jp kr ma mx nl no nz pl pt qa ro rs sa se si sk tr uk us za".split())

# Historical alias packs used a few different IDs for the same real channel.
# Smart Mapping needs one canonical bucket or exact matches can vanish depending
# on which spelling was used by the receiver/provider.  This is UI/search-only.
_CANONICAL_ID_ALIASES = {
    "2m": "2m_maroc",
}

def canonicalize_identity_id(value):
    key = str(value or "").strip().casefold()
    return _CANONICAL_ID_ALIASES.get(key, key)

def search_variants(value):
    """Return conservative display-name variants used by Smart Mapping.

    XMLTV catalogues commonly append transport/country decorations (``.cz``,
    ``.sa``, ``.fr``, `` net``, ``[M]``).  These are not channel identity and
    previously made famous channels such as TRT Arabi / M6 / 2M invisible to
    exact lookup.  Only trailing, well-known decorations are removed.
    """
    raw=str(value or "").strip(); out=[]
    def add(v):
        v=re.sub(r"\s+"," ",str(v or "").strip())
        if v and v.casefold() not in {x.casefold() for x in out}: out.append(v)
    add(raw)
    v=re.sub(r"\s*\[[A-Za-z]{1,3}\]\s*$","",raw).strip(); add(v)
    v=re.sub(r"(?i)(?:[._\-\s]+)(net|network)$","",v).strip(" ._- "); add(v)
    m=re.search(r"(?i)(?:[._\-\s]+)([a-z]{2})$",v)
    if m and m.group(1).lower() in _COUNTRY_SUFFIXES:
        add(v[:m.start()].strip(" ._- "))
    return out

def resolve(value):
    """Return one merged identity row or None.

    ``srp_aliases`` is authoritative where it resolves an exact alias because it
    is shared with the deterministic SAT identity layer.  The smaller UI pack is
    then merged as additional aliases/context; it never replaces the first ID.
    """
    primary = None
    secondary = None
    for candidate in search_variants(value):
        if primary is None:
            try: primary = channel_identity_resolver.resolve(candidate)
            except Exception: primary = None
        if secondary is None:
            try: secondary = smartmatch_ai.identify(candidate)
            except Exception: secondary = None
        if primary or secondary:
            break
    if not primary and not secondary:
        return None

    base = dict(primary or secondary or {})
    if base.get("id"):
        base["id"] = canonicalize_identity_id(base.get("id"))
    aliases = []
    _merge_aliases(aliases, (primary or {}).get("aliases") or [])
    _merge_aliases(aliases, (secondary or {}).get("aliases") or [])
    _merge_aliases(aliases, search_variants(value))
    base["aliases"] = aliases

    # Normalise country metadata to one list while retaining the legacy field.
    countries = []
    for raw in ((primary or {}).get("countries") or [], (secondary or {}).get("countries") or []):
        for code in raw:
            code = str(code or "").upper().strip()
            if code and code not in countries:
                countries.append(code)
    for code in ((primary or {}).get("country"), (secondary or {}).get("country")):
        code = str(code or "").upper().strip()
        if code and code not in countries:
            countries.append(code)
    if countries:
        base["countries"] = countries
        base["country"] = countries[0]
    if not base.get("provider"):
        base["provider"] = str((secondary or {}).get("provider") or (primary or {}).get("provider") or "")
    return base


def canonical_id(value):
    row = resolve(value) or {}
    return canonicalize_identity_id(row.get("id") or "")


def aliases(value):
    row = resolve(value) or {}
    out = list(row.get("aliases") or [])
    text = str(value or "").strip()
    if text and text.casefold() not in set(str(x).casefold() for x in out):
        out.insert(0, text)
    return out


def equivalent(left, right):
    if not left or not right:
        return False
    lid = canonical_id(left)
    rid = canonical_id(right)
    if lid and rid:
        return lid == rid
    try:
        return channel_identity_resolver.equivalent(left, right)
    except Exception:
        return False
