#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2M title-quality overlay for the Morocco Cloud runner.

This module keeps the existing multi-source/timing policy from
morocco_cloud_runner_ar2, but fixes two important title-quality cases:
1. a generic primary title such as "Programme on 2M" / "برنامج على 2M" must
   never beat a specific title from Telerama, Sudinfo or TVMag for the same slot;
2. if the Arabic translation layer cannot translate a real source title, the
   real programme name must be preserved instead of being replaced by a generic
   "برنامج على 2M" label.
"""
from __future__ import annotations

import re

import morocco_cloud_runner_ar as ar1
import morocco_cloud_runner_ar2 as base


_GENERIC_EXACT = {
    "programme",
    "programmes",
    "programme 2m",
    "programme sur 2m",
    "programme de 2m",
    "programmes sur 2m",
    "programmes de 2m",
    "tout le programme sur 2m",
    "tous les programmes sur 2m",
    "برنامج",
    "برامج",
    "برنامج على 2m",
    "برامج على 2m",
    "كل البرامج على 2m",
    "جميع البرامج على 2m",
    "برنامج على 2إم",
    "برامج على 2إم",
    "كل البرامج على 2إم",
}

# Canonical spellings for current/recurrent 2M shows which were previously
# falling through to the generic label. Keep this list deliberately explicit:
# an unknown title is preserved verbatim rather than guessed.
_KNOWN_2M_TITLES = {
    "aqba lik": "عقبا ليك",
    "al akhawat attalat": "الأخوات الثلاث",
    "3ailti": "عائلتي",
    "moughamarat": "مغامرات",
    "hikayat fi al adghal": "حكايات في الأدغال",
    "bahr addalam": "بحر الظلام",
    "mama 3roussa": "ماما عروسة",
    "abtal al bihar": "أبطال البحار",
    "oueld annas": "ولد الناس",
    "addam al machrouk": "الدم المشروك",
    "tourouq al 3arifine": "طرق العارفين",
    "al islam 3amal wa soulouk": "الإسلام عمل وسلوك",
    "addine wa annass": "الدين والناس",
    "yassar would annass": "يسار: ولد الناس",
    "akhir tamane": "آخر تمان",
}


def _generic_key(value):
    text = ar1.clean(value or "")
    # Keep Arabic letters and Latin/digits, normalize punctuation/spacing.
    text = re.sub(r"[^0-9A-Za-z\u0600-\u06ff]+", " ", text).strip().casefold()
    return re.sub(r"\s+", " ", text)


def is_generic_title(value):
    key = _generic_key(value)
    if not key:
        return True
    if key in _GENERIC_EXACT:
        return True
    # Common scraper/translation variants. Restrict this to very short titles so
    # a real programme containing the word 'programme' is never downgraded.
    if len(key) <= 40:
        if ("2m" in key or "2إم" in key) and any(token in key for token in (
            "programme", "programmes", "برنامج", "برامج", "البرامج"
        )):
            return True
        if key in {"film", "movie", "فيلم", "serie", "series", "مسلسل"}:
            return True
    return False


_original_translate_title = ar1.translate_title


def translate_title_keep_real_name(value):
    """Never destroy a specific 2M programme name with a generic fallback."""
    raw = ar1.clean(value or "")
    if not raw:
        return _original_translate_title(value)

    known = _KNOWN_2M_TITLES.get(ar1.norm(raw))
    if known:
        return known

    translated = _original_translate_title(raw)
    if not is_generic_title(translated):
        return translated

    # Translation service/dictionary had no Arabic result. Preserve the exact
    # broadcaster/source title. The Arabic suffix keeps the existing receiver
    # language-quality gate satisfied while making the real name visible.
    return "%s — برنامج 2M" % raw


def merge_prefer_specific(primary, backup):
    """Merge a backup while protecting timing and preferring real programme names.

    Exact primary timing remains authoritative. A backup may replace only a
    generic primary title with a non-generic specific title for the same start
    minute. Stops and richer Arabic descriptions keep the historical behaviour.
    """
    merged = {e.start.replace(second=0, microsecond=0): e for e in primary}
    title_repairs = 0
    for e in backup:
        key = e.start.replace(second=0, microsecond=0)
        if key not in merged:
            merged[key] = e
            continue

        old = merged[key]
        if is_generic_title(old.title) and not is_generic_title(e.title):
            old.title = e.title
            old.tl = e.tl
            # Mark the winning metadata source while preserving primary timing.
            old.source = "%s-title" % (e.source or "2m-backup")
            title_repairs += 1

        if old.stop is None and e.stop is not None:
            old.stop = e.stop
        if len(ar1.clean(e.desc)) > len(ar1.clean(old.desc)) + 20 and ar1.has_arabic(e.desc):
            old.desc = e.desc
            old.dl = "ar"

    if title_repairs:
        base.runner.log("2M detailed-title repairs from backup: %d" % title_repairs)
    return sorted(merged.values(), key=lambda x: x.start)


# Install the quality fixes into the existing, otherwise unchanged runner.
ar1.translate_title = translate_title_keep_real_name
base._merge_prefer = merge_prefer_specific


def main():
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
