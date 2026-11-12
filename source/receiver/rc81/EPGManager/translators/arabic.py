# -*- coding: utf-8 -*-
"""
translators/arabic.py

Small, generic Arabic/French text-normalization helpers shared by the
translation system. Extracted from the text-cleanup logic scattered across
the original 2M/Chada, SNRT, and Arryadia scripts.
"""

import re
import unicodedata


def normalize_text(text):
    """Lowercase, strip accents/apostrophes, collapse whitespace - used to
    make dictionary lookups (French key -> Arabic value) accent/case
    insensitive. Ported verbatim from the original 2M/Chada Translator."""
    text = text.lower().strip()
    text = text.replace("'", "").replace("\u2019", "").replace("-", " ")
    text = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"\s+", " ", text)


def strip_diacritics(text):
    """Remove Arabic diacritics (tashkeel) for more forgiving matching."""
    if not text:
        return text
    return re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)


def contains_arabic(text):
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def contains_latin_letters(text):
    return bool(re.search(r"[a-zA-Z]", text or ""))
