# -*- coding: utf-8 -*-
"""Safe cross-language aliases for parallel provider catalogues.

Some OpenEPG feeds publish the same XMLTV channel IDs in parallel language
shards (Arabic/English).  Smart Mapping should be able to find the Arabic row
when the receiver service uses the English/Latin name, and vice versa, without
importing both programme feeds.

This module never pairs rows by position.  An alias is accepted only when the
parallel shard has the exact same XMLTV channel ID.  That makes it safe for the
known AR/EN pairs while keeping the user's selected/imported language policy
unchanged.
"""
from __future__ import print_function

from . import source_variant_policy
from .channel_identity_resolver import label_from_id


def _unique(values):
    out = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def sibling_aliases(source_id, row, sibling_rows):
    """Return safe names from the parallel AR/EN row with the same XMLTV ID."""
    sid = str(source_id or "")
    sibling = source_variant_policy.sibling(sid)
    cid = str((row or {}).get("channel_id") or "").strip()
    if not sibling or not cid:
        return []
    target = cid.casefold()
    match = None
    if isinstance(sibling_rows, dict):
        match = sibling_rows.get(target)
    else:
        for other in sibling_rows or []:
            if str((other or {}).get("channel_id") or "").strip().casefold() == target:
                match = other or {}
                break
    if not match:
        return []

    current = {
        str((row or {}).get("display_name") or "").strip().casefold(),
        cid.casefold(),
    }
    aliases = _unique([
        (match or {}).get("display_name"),
        label_from_id((match or {}).get("channel_id")),
        (match or {}).get("channel_id"),
    ])
    return [value for value in aliases if value.casefold() not in current]
