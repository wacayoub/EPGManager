# -*- coding: utf-8 -*-
"""Language hygiene helpers for user-facing EPG text.

The goal is not to prohibit Latin proper names.  It is to detect sentences
where a token-by-token translation left an unreadable Arabic/English/French
hybrid (for example: ``آخر الأخبار from the newsroom``).
"""
from __future__ import print_function
import re

_AR = re.compile(r"[\u0600-\u06ff]")
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*")
_LATIN_CHAR = re.compile(r"[A-Za-zÀ-ÿ]")

# Function/common words are strong evidence of untranslated sentence grammar.
_FOREIGN_GRAMMAR = set("""
the and or of to in for with from on at by is are was were be been being this
that these those a an as into about after before during between latest news
coverage match live program programme episode season presented presents
le la les de des du une un et est sont dans avec pour sur au aux ce cette ces
qui que par presente presentee émission emission journal magazine serie série
""".lower().split())


def stats(text):
    text = str(text or "")
    ar = len(_AR.findall(text))
    latin = len(_LATIN_CHAR.findall(text))
    words = _LATIN_WORD.findall(text)
    grammar = sum(1 for word in words if word.lower().strip(".'’-") in _FOREIGN_GRAMMAR)
    return {"arabic_chars": ar, "latin_chars": latin, "latin_words": len(words), "foreign_grammar_words": grammar}


def is_mixed_sentence(text, min_latin_chars=8):
    """Return True for likely broken Arabic + foreign-language prose.

    Latin names such as ``Manchester United`` inside an otherwise Arabic
    sentence are tolerated unless foreign grammar/function words remain.
    """
    st = stats(text)
    ar, lat = st["arabic_chars"], st["latin_chars"]
    if not ar or lat < int(min_latin_chars):
        return False
    if st["foreign_grammar_words"] >= 1:
        return True
    # Several Latin words with a high Latin share usually means translation
    # stopped halfway. Two-word proper names remain tolerated when Arabic is
    # clearly dominant.
    if st["latin_words"] >= 6 and lat > max(16, ar):
        return True
    return False


def is_arabic_first(text, allow_latin_words=3):
    st = stats(text)
    if st["arabic_chars"] < 3:
        return False
    if st["foreign_grammar_words"]:
        return False
    if st["latin_words"] > int(allow_latin_words) and st["latin_chars"] > st["arabic_chars"]:
        return False
    return True
