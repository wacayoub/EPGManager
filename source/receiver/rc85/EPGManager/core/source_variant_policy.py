# -*- coding: utf-8 -*-
"""Mutually-exclusive language variants for provider feeds.

Some OpenEPG country shards publish the same XMLTV channel IDs twice with
parallel programme languages (AR/EN). Importing both is unsafe because the last
feed imported can overwrite the same service/channel IDs. This module keeps
those variants mutually exclusive while preserving an explicit user choice.

It is intentionally UI/import policy only; it does not touch the frozen SAT/SRP
mapping engines.
"""
from __future__ import print_function

# (preferred Arabic shard, parallel English shard)
PAIRS = (
    ("ext_openepg_egypt_1", "ext_openepg_egypt_2"),
    ("ext_openepg_saudi_arabia_1", "ext_openepg_saudi_arabia_2"),
    ("ext_openepg_saudi_arabia_3", "ext_openepg_saudi_arabia_4"),
    ("ext_openepg_saudi_arabia_5", "ext_openepg_saudi_arabia_6"),
    ("ext_openepg_qatar_4", "ext_openepg_qatar_5"),
)

_AR_TO_EN = dict(PAIRS)
_EN_TO_AR = dict((en, ar) for ar, en in PAIRS)


def arabic_counterpart(source_id):
    return _EN_TO_AR.get(str(source_id or ""), "")


def english_counterpart(source_id):
    return _AR_TO_EN.get(str(source_id or ""), "")


def is_arabic_variant(source_id):
    return str(source_id or "") in _AR_TO_EN


def is_english_variant(source_id):
    return str(source_id or "") in _EN_TO_AR


def sibling(source_id):
    sid = str(source_id or "")
    return _AR_TO_EN.get(sid) or _EN_TO_AR.get(sid) or ""


def normalize_selected(source_ids, explicit_choice=None):
    """Return a stable source list with only one shard from each language pair.

    Existing configurations that accidentally contain both variants migrate to
    Arabic by default. During an explicit UI toggle, the newly selected shard
    wins so a user can still intentionally choose English.
    """
    raw = []
    seen = set()
    for sid in source_ids or []:
        sid = str(sid or "")
        if sid and sid not in seen:
            seen.add(sid); raw.append(sid)

    explicit = str(explicit_choice or "")
    chosen = set(raw)
    for ar, en in PAIRS:
        if ar not in chosen or en not in chosen:
            continue
        keep = explicit if explicit in (ar, en) else ar
        drop = en if keep == ar else ar
        chosen.discard(drop)

    return [sid for sid in raw if sid in chosen]


def preferred_variant_rank(source_id, prefer_arabic=True):
    """Lower is better for otherwise equivalent Smart Mapping candidates."""
    sid = str(source_id or "")
    if not prefer_arabic:
        if is_english_variant(sid): return 0
        if is_arabic_variant(sid): return 1
        return 1
    if is_arabic_variant(sid): return 0
    if is_english_variant(sid): return 4
    return 1
