# -*- coding: utf-8 -*-
"""Ultra-light shared channel-name key for paint/index hot paths.

The key intentionally preserves Arabic and strips only presentation noise.
It is used when the receiver registry is built and when the compact Smart
index is built, so Reception/Satellite switching can test suggestion presence
with one dictionary lookup and no normalization work in the GUI thread.
"""
from __future__ import print_function

import re
import unicodedata

_NOISE = set((
    "hd", "sd", "fhd", "uhd", "4k", "hdr", "tv", "television", "channel",
    "backup", "feed", "test", "hevc", "h265", "h264", "1080p", "2160p",
))
_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_EQUIV_WORDS = {
    "arabic": "arabi", "arabe": "arabi", "kurdish": "kurdi", "kurde": "kurdi",
    "sports": "sport", "national": "nat", "international": "inter",
    "francais": "french", "english": "en", "french": "fr",
    "osntv": "osn",
}


def fold(value):
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        pass
    text = text.replace("&", " and ").replace("+", " plus ")
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize(value):
    words = []
    for raw in fold(value).split():
        if raw in _NOISE:
            continue
        word = _WORD_NUMBERS.get(raw, raw)
        word = _EQUIV_WORDS.get(word, word)
        if word:
            words.append(word)
    return " ".join(words)


def compact(value):
    return normalize(value).replace(" ", "")


def xmltv_id_aliases(channel_id):
    """Return conservative Latin search aliases derived from an XMLTV ID.

    Direct Arab feeds such as ElCinema often publish Arabic display-name text
    while keeping a stable Latin XMLTV ID (for example ``MBC2.ae@SD``).  The
    Smart Mapping exact index must be able to discover the receiver label
    ``MBC 2 HD`` without translating programme metadata or inventing channel
    ownership.  Only the provider-neutral ID basename is used; the country/feed
    suffix after the first dot/@ is never folded into the alias.
    """
    raw = str(channel_id or "").strip()
    if not raw or not re.search(r"[A-Za-z]", raw):
        return []
    base = raw.split("@", 1)[0].split(".", 1)[0].strip()
    if not base:
        return []
    aliases = []
    seen = set()
    def add(value):
        value = " ".join(str(value or "").split()).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key); aliases.append(value)
    add(base)
    # CamelCase/acronym/number boundaries: MBCAction -> MBC Action,
    # MBC2 -> MBC 2, AbuDhabiTV -> Abu Dhabi TV.
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)
    spaced = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", spaced)
    spaced = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", spaced)
    # Preserve common MENA brand spelling after generic CamelCase splitting.
    spaced = re.sub(r"\bbe\s+IN\b", "beIN", spaced, flags=re.I)
    spaced = re.sub(r"\bi\s+Film\b", "iFilm", spaced, flags=re.I)
    add(spaced)
    return aliases[:3]
