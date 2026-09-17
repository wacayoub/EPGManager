#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical receiver identities for Abu Dhabi Media (ADM).

This module is intentionally identity-only.  It does not invent schedules and it
never changes the selected upstream timeline.  It collapses proven aliases to one
stable UAE receiver id while keeping the MENA merge's existing source ranking,
Arabic-first timeline selection and integrity quarantine.

The policy is installed after ``mena_cloud_merge_safe`` so its aliases extend the
safe matcher instead of bypassing it.
"""
from __future__ import annotations

import re


CANONICAL = {
    "abu dhabi tv": ("AbuDhabiTV.ae", "Abu Dhabi TV"),
    "abu dhabi emirates": ("AbuDhabiEmirates.ae", "Al Emarat TV"),
    "abu dhabi sports 1": ("AbuDhabiSports1.ae", "Abu Dhabi Sports 1"),
    "abu dhabi sports 2": ("AbuDhabiSports2.ae", "Abu Dhabi Sports 2"),
    "abu dhabi sports premium 1": ("ADSportsPremium1.ae", "AD Sports Premium 1"),
    "abu dhabi sports premium 2": ("ADSportsPremium2.ae", "AD Sports Premium 2"),
    "ad sports extra": ("ADSportsExtra.ae", "AD Sports Extra"),
    "yas tv": ("YasTV.ae", "Yas TV"),
    "yas tv extra": ("YasTVExtra.ae", "YAS TV Extra"),
    "majid": ("Majid.ae", "Majid"),
    "national geographic abu dhabi": ("NationalGeographicAbuDhabi.ae", "National Geographic Abu Dhabi"),
    "baynounah tv": ("BaynounahTV.ae", "Baynounah TV"),
}

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _flat(value):
    value = (value or "").casefold().strip().translate(_ARABIC_DIGITS)
    value = re.sub(r"^(?:ar|ara|arabic|en|eng|english)\s*[:|_-]\s*", "", value, flags=re.I)
    value = re.sub(r"\.(?:ae|sa|qa|eg|bh|kw|om|jo|lb|iq|ps|ye|mena)(?:@[^\s]*)?$", "", value, flags=re.I)
    value = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|digital|mono)\b", " ", value, flags=re.I)
    value = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def _arabic_ad_sports(probe):
    """Return the proven ADM sports logical key for Arabic display names/IDs."""
    if not re.search(r"(?:أبوظبي|ابوظبي).*?(?:الرياضية|رياضية)", probe):
        return None
    if re.search(r"(?:إكسترا|اكسترا|إكستره|اكستره)", probe):
        return "ad sports extra"
    if "بريميوم" in probe:
        m = re.search(r"بريميوم\s*([12])\b", probe)
        if m:
            return "abu dhabi sports premium %s" % m.group(1)
    m = re.search(r"(?:الرياضية|رياضية)\s*([12])\b", probe)
    if m:
        return "abu dhabi sports %s" % m.group(1)
    return None


def adm_key(cid, name):
    """Return a stable ADM logical key, or ``None`` for unrelated services."""
    rid = _flat(cid)
    rname = _flat(name)
    probe = " %s %s " % (rid, rname)
    compact = re.sub(r"\s+", "", probe)

    # Arabic ADM sports identities must be resolved before the generic Arabic
    # "Abu Dhabi" TV rule.  Otherwise e.g. أبوظبي الرياضية إكسترا / بريميوم
    # gets folded into AbuDhabiTV and a short event schedule can win the Arabic-
    # first timeline arbitration for the entertainment channel.
    ar_sports_key = _arabic_ad_sports(probe)
    if ar_sports_key:
        return ar_sports_key

    # Event-only feeds must be identified before the ordinary sports/Yas rules.
    if ("ad sports extra" in probe or "adsportsextra" in compact
            or "abu dhabi sports extra" in probe):
        return "ad sports extra"
    # EPGShare exposes verified slot-identical AR/EN twins for YAS TV Extra.
    # The Arabic identity is explicitly grouped with the English source so the
    # normal Arabic-first event merger can retain the same timeline while
    # selecting native Arabic titles and descriptions.
    if ("yas tv extra" in probe or "yastvextra" in compact
            or "ياس تي في إكسترا" in probe or "ياس تي في اكسترا" in probe):
        return "yas tv extra"

    # Premium 1/2 are real channels used for premium sports rights.  Keep them
    # separate from the free-to-air AD Sports 1/2 feeds.
    if "premium" in probe and ("ad sports" in probe or "abu dhabi sports" in probe or "adsports" in compact):
        m = re.search(r"premium\s*([12])\b", probe)
        if not m:
            m = re.search(r"premium([12])", compact)
        if m:
            return "abu dhabi sports premium %s" % m.group(1)

    # Free-to-air Abu Dhabi Sports 1/2.  Do not collapse numbered 3/4 identities
    # here: if they reappear upstream they require a fresh audit before exposure.
    if ("abu dhabi sports" in probe or "ad sports" in probe or "abudhabi sports" in probe
            or "abudhabisports" in compact or "adsports" in compact):
        m = re.search(r"(?:abu\s*dhabi|ad)\s*sports\s*([12])\b", probe)
        if not m:
            m = re.search(r"(?:abudhabisports|adsports)([12])", compact)
        if m:
            return "abu dhabi sports %s" % m.group(1)

    if ("national geographic abu dhabi" in probe or "nat geo abu dhabi" in probe
            or "nationalgeographicabudhabi" in compact or "natgeoabudhabi" in compact):
        return "national geographic abu dhabi"

    if "baynounah" in probe or "baynounah" in compact or "بينونة" in probe:
        return "baynounah tv"

    if re.search(r"(?:^| )majid(?: kids)?(?: tv)?(?: |$)", rid) or re.search(r"(?:^| )majid(?: kids)?(?: tv)?(?: |$)", rname):
        return "majid"

    if "yas tv" in probe or "yastv" in compact or re.search(r"(?:^| )yas(?: |$)", rid):
        return "yas tv"

    # Al Emarat / Emirates TV must be checked before plain Abu Dhabi TV.
    if ("al emarat" in probe or re.search(r"(?:^| )emarat(?: tv)?(?: |$)", probe)
            or "abudhabi emirates" in probe or "abudhabiemirates" in compact
            or "الإمارات" in probe):
        return "abu dhabi emirates"

    # Plain "Abu Dhabi" source ids, Abu Dhabi HD and Abu Dhabi TV are one linear
    # entertainment service.  Arabic sports terms are explicitly excluded as a
    # final safety net even though they are normally caught above.
    if ("abu dhabi tv" in probe or "abudhabi tv" in probe or "abudhabitv" in compact
            or rid in {"abu dhabi", "abu dhabi tv"}
            or rname in {"abu dhabi", "abu dhabi tv"}
            or "أبوظبي" in probe or "ابوظبي" in probe):
        excluded = (
            "sports", "sport", "premium", "yas", "national geographic", "nat geo",
            "الرياضية", "رياضية", "بريميوم", "إكسترا", "اكسترا",
        )
        if not any(x in probe for x in excluded):
            return "abu dhabi tv"

    return None


def install(base):
    """Extend an imported ``mena_cloud_merge`` module in-place."""
    if getattr(base, "_adm_identity_policy_installed", False):
        return

    previous_logical_key = base.logical_key
    previous_choose_canonical = base.choose_canonical

    def logical_key(cid, name):
        key = adm_key(cid, name)
        return key if key else previous_logical_key(cid, name)

    def choose_canonical(candidates):
        selected = previous_choose_canonical(candidates)
        key = adm_key(selected.cid, selected.name)
        if not key:
            # A selected alias may be weakly named; inspect the whole proven group.
            keys = {adm_key(c.cid, c.name) for c in candidates}
            keys.discard(None)
            if len(keys) == 1:
                key = next(iter(keys))
        spec = CANONICAL.get(key or "")
        if not spec:
            return selected

        canonical_id, canonical_name = spec
        channel = base.copy_element(selected.channel)
        channel.set("id", canonical_id)
        names = list(channel.findall("display-name"))
        if names:
            names[0].text = canonical_name
        else:
            base.ET.SubElement(channel, "display-name").text = canonical_name

        # Preserve the exact source/timeline candidate; only receiver identity is
        # rewritten.  Candidate.__init__ recomputes the patched logical key.
        return base.Candidate(
            canonical_id,
            canonical_name,
            selected.origin,
            selected.source_name,
            selected.site,
            channel,
            selected.programmes,
        )

    base.logical_key = logical_key
    base.choose_canonical = choose_canonical
    base._adm_identity_policy_installed = True


if __name__ == "__main__":
    # Lightweight self-test, safe to run in CI without network or XML files.
    cases = {
        ("Abu Dhabi.sa", "Abu Dhabi.sa"): "abu dhabi tv",
        ("AbuDhabiTV.ae@SD", "Abu Dhabi TV HD"): "abu dhabi tv",
        ("Emarat.HD.ae", "Emarat HD"): "abu dhabi emirates",
        ("Al Emarat.sa", "Al Emarat.sa"): "abu dhabi emirates",
        ("AbuDhabiSports1.ae@SD", "AD Sports 1 HD"): "abu dhabi sports 1",
        ("AD Sports 2.sa", "AD Sports 2.sa"): "abu dhabi sports 2",
        ("AD Sports Premium 1.sa", "AD Sports Premium 1.sa"): "abu dhabi sports premium 1",
        ("en:.AD.Sports.Extra.ae", "en: AD Sports Extra"): "ad sports extra",
        ("ar:.أبوظبي.الرياضية.إكسترا.ae", "ar: أبوظبي الرياضية إكسترا"): "ad sports extra",
        ("ar:.أبوظبي.الرياضية.بريميوم.2.-.الدوري.الإيطالي.ae", "ar: أبوظبي الرياضية بريميوم 2 - الدوري الإيطالي"): "abu dhabi sports premium 2",
        ("ar:.أبوظبي.الرياضية.1.ae", "ar: أبوظبي الرياضية ١"): "abu dhabi sports 1",
        ("ar:.أبوظبي.الرياضية.2.ae", "ar: أبوظبي الرياضية ٢"): "abu dhabi sports 2",
        ("en:.YAS.TV.Extra.ae", "en: YAS TV Extra"): "yas tv extra",
        ("ar:.ياس.تي.في.إكسترا.ae", "ar: ياس تي في إكسترا"): "yas tv extra",
        ("Majid.sa", "Majid.sa"): "majid",
        ("Nat.Geo.Abu.Dhabi.HD.ae", "Nat Geo Abu Dhabi HD"): "national geographic abu dhabi",
        ("Yas.TV.HD.ae", "Yas TV HD"): "yas tv",
        ("Baynounah.TV.HD.ae", "Baynounah TV HD"): "baynounah tv",
    }
    for (cid, name), expected in cases.items():
        actual = adm_key(cid, name)
        if actual != expected:
            raise SystemExit("ADM identity self-test failed: %r -> %r != %r" % (cid, actual, expected))
    print("ADM identity self-test PASS: %d aliases" % len(cases))
