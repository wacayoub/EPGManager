# -*- coding: utf-8 -*-
"""Central programme-feed + SAT/provider/language safety policy.

The matcher may ignore *technical* presentation suffixes (HD/SD/UHD/4K), but
must never collapse semantic broadcast feeds such as NAT/National, TNT,
International, Monde, MENA or language-specific editions.

Satellite position is contextual, not channel identity:
- Nilesat/Eutelsat 7/8W and Badr/Arabsat 25.5/26E default to Arabic/MENA when
  the receiver service exposes no stronger provider/language evidence.
- Hotbird/Astra/Hispasat are provider/country driven; Arabic is never selected
  merely because an Arabic catalogue also contains the same brand name.
- Explicit service language/provider country always wins over orbital defaults.
"""
from __future__ import print_function

import re
import unicodedata

MENA_COUNTRIES = set("MA DZ TN LY EG SA QA AE PS LB JO IQ KW BH OM YE SY MENA".split())
EUROPE_COUNTRIES = set("FR ES IT DE PT UK GB IE BE NL CH AT GR TR RO BG HR RS SI SK CZ DK SE NO FI PL UA HU LU MT CY AL BY BA ME MK HU RU".split())

# Native/primary guide language preferences. Multi-lingual countries remain sets.
COUNTRY_LANGS = {
    # Morocco is natively Arabic/French. On Nilesat/Badr the routing layer below
    # still gives Arabic a hard automatic preference for Moroccan/MENA services;
    # on Hotbird/Astra a Moroccan provider may legitimately use either AR or FR.
    "MA": {"ar", "fr"}, "DZ": {"ar"}, "TN": {"ar"}, "LY": {"ar"}, "EG": {"ar"},
    "SA": {"ar"}, "QA": {"ar"}, "AE": {"ar"}, "PS": {"ar"}, "LB": {"ar"},
    "JO": {"ar"}, "IQ": {"ar"}, "KW": {"ar"}, "BH": {"ar"}, "OM": {"ar"},
    "YE": {"ar"}, "SY": {"ar"}, "MENA": {"ar"},
    "FR": {"fr"}, "BE": {"fr", "nl"}, "CH": {"de", "fr", "it"},
    "IT": {"it"}, "ES": {"es"}, "PT": {"pt"}, "DE": {"de"}, "AT": {"de"},
    "UK": {"en"}, "GB": {"en"}, "IE": {"en"}, "NL": {"nl"}, "PL": {"pl"},
    "TR": {"tr"}, "GR": {"el"}, "RO": {"ro"}, "BG": {"bg"}, "HR": {"hr"},
    "RS": {"sr"}, "SI": {"sl"}, "SK": {"sk"}, "CZ": {"cs"}, "DK": {"da"},
    "SE": {"sv"}, "NO": {"no"}, "FI": {"fi"}, "UA": {"uk"}, "HU": {"hu"},
    "AL": {"sq"}, "BY": {"be", "ru"}, "RU": {"ru"}, "US": {"en"}, "CA": {"en", "fr"},
}

_PROVIDER_COUNTRY_HINTS = (
    (("snrt", "2m", "soread", "medi1", "maroc", "morocco"), "MA"),
    (("canal france", "canalsat", "tnt sat", "fransat", "bis tv", "csat"), "FR"),
    (("sky italia", "tivusat", "mediaset", "rai"), "IT"),
    (("movistar", "digital+ espana", "canal espana"), "ES"),
    (("sky deutschland", "hd+", "ard", "zdf"), "DE"),
    (("freesat", "sky uk", "bbc", "itv"), "UK"),
    (("polsat", "canal polska", "nc+"), "PL"),
    (("al ahly", "on time sport", "ontime sport", "dmc egypt"), "EG"),
    (("digiturk", "d-smart", "trt"), "TR"),
    (("mbc", "saudi"), "SA"),
    (("al jazeera", "alkass", "bein mena", "bein qatar"), "QA"),
    (("abu dhabi", "dubai media", "ad media"), "AE"),
)

# Short DVB provider acronyms can mean different broadcasters.  In particular
# ``SBA`` is seen both for Saudi Broadcasting Authority and Sharjah Broadcasting
# Authority.  Treat these labels as weak/ambiguous context instead of a native
# country lock; curated channel identity + source country must decide.
_AMBIGUOUS_PROVIDER_COUNTRY_TOKENS = set(("sba",))

_TECHNICAL = set("hd sd fhd uhd 4k 8k hevc h265 h264 hdr 1080p 2160p 720p backup feed channel tv".split())
_GENERAL_SEMANTIC = {
    "tnt": "tnt", "nat": "national", "national": "national",
    "inter": "international", "international": "international", "intl": "international",
    "monde": "world", "world": "world", "mena": "mena", "maghreb": "maghreb",
    "arabic": "arabic", "arabe": "arabic", "english": "english",
    "french": "french", "francais": "french", "français": "french",
}


def _fold(value):
    text = str(value or "").strip().casefold()
    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        pass
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return " ".join(text.split())


def _tokens(value):
    return _fold(value).split()


def _compact(value):
    return "".join(_tokens(value))


def satellite_family(value):
    try:
        o = float(value)
    except Exception:
        return "UNKNOWN"
    if -9.5 <= o <= -5.5:
        return "NILESAT"
    if 24.0 <= o <= 27.0:
        return "BADR"
    if 29.0 <= o <= 31.5:
        return "ARABSAT30E"
    if 12.0 <= o <= 14.0:
        return "HOTBIRD"
    if 18.5 <= o <= 20.0:
        return "ASTRA19"
    if 22.5 <= o <= 24.0:
        return "ASTRA23"
    if 27.5 <= o <= 29.0:
        return "ASTRA28"
    if -31.5 <= o <= -29.0:
        return "HISPASAT30W"
    return "OTHER"


def satellite_zone(value):
    fam = satellite_family(value)
    if fam in ("NILESAT", "BADR", "ARABSAT30E"):
        return "MENA"
    if fam in ("ASTRA19", "ASTRA23", "ASTRA28", "HISPASAT30W"):
        return "EUROPE"
    if fam == "HOTBIRD":
        return "MIXED"
    return ""


def _provider_hint_match(text, hint):
    """Conservative provider-label matcher.

    Short acronyms are matched as whole tokens only; longer multi-word provider
    labels may match as a phrase.  Ambiguous acronyms never establish country.
    """
    text = _fold(text)
    hint = _fold(hint)
    if not text or not hint or hint in _AMBIGUOUS_PROVIDER_COUNTRY_TOKENS:
        return False
    if len(hint) <= 3 and " " not in hint:
        return bool(re.search(r"(?:^|\s)%s(?:$|\s)" % re.escape(hint), text))
    return bool(re.search(r"(?:^|\s)%s(?:$|\s)" % re.escape(hint), text))


def provider_countries(provider):
    text = _fold(provider)
    out = set()
    if not text:
        return out
    for hints, code in _PROVIDER_COUNTRY_HINTS:
        if any(_provider_hint_match(text, h) for h in hints):
            out.add(code)
    return out


def _explicit_language_from_text(value):
    text = _fold(value)
    raw = str(value or "")
    patterns = (
        (r"(?:^|\s)(?:ar|ara|arabic|arabe)(?:\s|$)", "ar"),
        (r"(?:^|\s)(?:en|eng|english)(?:\s|$)", "en"),
        (r"(?:^|\s)(?:fr|fra|french|francais)(?:\s|$)", "fr"),
        (r"(?:^|\s)(?:it|ita|italian|italiano)(?:\s|$)", "it"),
        (r"(?:^|\s)(?:es|esp|spanish|espanol)(?:\s|$)", "es"),
        (r"(?:^|\s)(?:de|ger|german|deutsch)(?:\s|$)", "de"),
        (r"(?:^|\s)(?:pt|por|portuguese)(?:\s|$)", "pt"),
        (r"(?:^|\s)(?:tr|tur|turkish)(?:\s|$)", "tr"),
        (r"(?:^|\s)(?:pl|pol|polish)(?:\s|$)", "pl"),
    )
    for pat, lang in patterns:
        if re.search(pat, text):
            return lang
    # Arabic-script branding is a useful language hint only when no explicit
    # Latin language marker exists.
    if re.search(r"[\u0600-\u06ff]", raw):
        return "ar"
    return ""


def service_language(service):
    service = service or {}
    explicit = str(service.get("explicit_region") or "").upper()
    if explicit == "MENA":
        return "ar"
    lang = _explicit_language_from_text(service.get("clean_name") or service.get("name") or "")
    if lang:
        return lang
    return _explicit_language_from_text(service.get("provider_name") or "")


def source_countries(source):
    return set(str(x or "").upper() for x in ((source or {}).get("countries") or []) if x)


def source_languages(source):
    source = source or {}
    langs = set()
    raw = str(source.get("language") or "").lower().replace("_", "-")
    if raw:
        langs.add(raw.split("-", 1)[0])
    # rc44: validated direct catalogues such as OSN/Shahid contain both Arabic
    # and English channel feeds.  Keep source-level language as a default, but
    # honour an explicit multi-language declaration instead of collapsing the
    # whole provider to one language.
    for value in (source.get("languages") or []):
        value = str(value or "").lower().replace("_", "-").strip()
        if value:
            langs.add(value.split("-", 1)[0])
    if not langs:
        for code in source_countries(source):
            langs.update(COUNTRY_LANGS.get(code, set()))
    return langs


def expected_languages(service):
    service = service or {}
    explicit = service_language(service)
    if explicit:
        return {explicit}, "explicit service language"
    countries = set()
    region = str(service.get("explicit_region") or "").upper()
    if region and region != "MENA":
        countries.add(region)
    # A curated channel identity is stronger than an ambiguous/platform DVB
    # provider label.  This is important for cases such as Sharjah HD where
    # provider ``SBA`` is not uniquely Saudi.
    try:
        identity = identity_countries(service.get("clean_name") or service.get("name") or "")
    except Exception:
        identity = set()
    if len(identity) == 1:
        countries.update(identity)
    countries.update(provider_countries(service.get("provider_name") or ""))
    langs = set()
    for code in countries:
        langs.update(COUNTRY_LANGS.get(code, set()))
    if langs:
        return langs, "DVB provider/country"
    fam = satellite_family(service.get("orbital"))
    if fam in ("NILESAT", "BADR", "ARABSAT30E"):
        return {"ar"}, "%s Arabic default" % fam
    return set(), "no language default"


_ID_COUNTRY_DECORATIONS = set("sa ae eg qa ma ps tn dz iq lb jo kw bh om ye sy".split())

def _semantic_identity_text(value):
    """Remove only a trailing country decoration from XMLTV IDs.

    Examples: AlAoulaInter.ma -> AlAoulaInter, 2MMonde.ma -> 2MMonde.
    AR/EN/FR/MENA are never removed because they may define the programme feed.
    """
    raw = str(value or "").strip()
    if not raw:
        return raw
    m = re.search(r"(?i)[._-]([a-z]{2,4})$", raw)
    if m and m.group(1).lower() in _ID_COUNTRY_DECORATIONS:
        raw = raw[:m.start()]
    return raw

def _known_family_variant(value):
    value = _semantic_identity_text(value)
    text = _fold(value)
    compact = _compact(value)
    toks = set(text.split())
    if not text:
        return "", ""

    # Al Aoula family.  Operator policy beta140: Al Aoula Inter/International
    # uses the same EPG mapping identity as the domestic Al Aoula feed.  Keep
    # Laayoune separate because it can carry a genuinely different schedule.
    if "alaoula" in compact or "الأولى" in str(value or "") or "الاولى" in str(value or ""):
        if any(x in compact for x in ("laayoune", "laayoun", "layoune")) or "العيون" in str(value or ""):
            return "al_aoula", "laayoune"
        return "al_aoula", "base"

    # 2M broadcast services.  Operator policy beta140: 2M National / 2M Nat /
    # 2M Monde / 2M World are mapping aliases of the normal 2M EPG channel.
    # 2M MENA remains distinct unless the operator explicitly collapses it too.
    # Arabic display aliases are part of the same deterministic identity.  This
    # matters because OpenEPG can expose ``الثانية`` while the DVB service is
    # named ``2M MAROC HD``; without this bridge the feed firewall sees an
    # unknown candidate and a foreign country container can accidentally win.
    raw_value = str(value or "")
    is_2m_ar = any(x in raw_value for x in ("الثانية", "المغربية الثانية", "القناة الثانية"))
    if compact.startswith("2m") or text == "2m" or is_2m_ar:
        if "mena" in toks or "2mmena" in compact:
            return "2m", "mena"
        # National/Nat/Monde/World intentionally collapse to the base feed.
        return "2m", "base"

    # Arryadia side/TNT feeds are separate programme feeds.
    if any(x in compact for x in ("arryadia", "arriyadia", "arriadia")):
        if "tnt" in toks or "arryadiatnt" in compact or "arriyadiatnt" in compact or "arriadiatnt" in compact:
            return "arryadia", "tnt"
        for suffix in ("hd1", "hd2", "hd3"):
            if suffix in toks or compact.endswith(suffix):
                return "arryadia", suffix
        return "arryadia", "base"

    if "medi1" in compact:
        if "maghreb" in toks or "maghreb" in compact:
            return "medi1tv", "maghreb"
        if "arabic" in toks or "arabe" in toks or compact.endswith("ar"):
            return "medi1tv", "arabic"
        return "medi1tv", "base"

    if "i24news" in compact or compact.startswith("i24"):
        for token, variant in (("arabic", "arabic"), ("arabe", "arabic"), ("english", "english"),
                               ("french", "french"), ("francais", "french")):
            if token in toks or compact.endswith(token):
                return "i24news", variant
        # Explicit short language suffixes are semantic for i24NEWS.
        for suffix, variant in (("ar", "arabic"), ("en", "english"), ("fr", "french")):
            if compact.endswith("i24news" + suffix) or compact.endswith("i24" + suffix):
                return "i24news", variant
        return "i24news", "base"

    if "alkass" in compact or "alkass" in text.replace(" ", "") or "al kass" in text:
        m = re.search(r"(?:al\s*kass|alkass)\s*0*([1-8])(?:\s|$)", text)
        if not m:
            words = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8"}
            for word, num in words.items():
                if word in toks:
                    return "alkass", num
        else:
            return "alkass", m.group(1)
        return "alkass", "base"

    if "aljazeera" in compact or "al jazeera" in text:
        if "english" in toks or compact.endswith("aljazeeraenglish") or compact.endswith("aljazeeraen"):
            return "aljazeera", "english"
        if "documentary" in toks or "doc" in toks:
            return "aljazeera", "documentary"
        if "mubasher" in toks or "mobasher" in toks:
            return "aljazeera", "mubasher"
        return "aljazeera", "arabic"

    return "", ""


# National/public broadcaster families whose programme feed must stay on their
# native country for *automatic* mapping.  A foreign country's XMLTV container
# can legitimately mention an international channel, but for these curated
# families that is never preferred over the broadcaster's own country source.
# Keeping this list intentionally small avoids over-constraining global brands.
_HOME_LOCK_FAMILIES = {
    "al_aoula": "MA",
    "2m": "MA",
    "arryadia": "MA",
    "medi1tv": "MA",
}
_HOME_LOCK_PROVIDERS = {
    "SNRT": "MA",
    "2M": "MA",
    "AL AHLY": "EG",
}


def _identity_row(value):
    try:
        from . import channel_identity_resolver
        return channel_identity_resolver.resolve(value) or {}
    except Exception:
        return {}


def identity_countries(value):
    row = _identity_row(value)
    out = set(str(x or "").upper() for x in (row.get("countries") or []) if x)
    country = str(row.get("country") or "").upper().strip()
    if country:
        out.add(country)
    return out


def locked_home_country(value):
    """Return a strict native-country lock for curated broadcaster identities."""
    name = value
    if isinstance(value, dict):
        name = (value or {}).get("clean_name") or (value or {}).get("name") or ""
    family, _variant = _known_family_variant(name)
    if family in _HOME_LOCK_FAMILIES:
        return _HOME_LOCK_FAMILIES[family]
    row = _identity_row(name)
    provider = str(row.get("provider") or "").upper().strip()
    countries = identity_countries(name)
    wanted = _HOME_LOCK_PROVIDERS.get(provider, "")
    if wanted and (not countries or wanted in countries):
        return wanted
    return ""


def _general_semantic(value):
    text = _fold(value)
    toks = text.split()
    # "National Geographic" is a brand, not a NAT feed qualifier.
    if "national geographic" in text:
        return set()
    out = set()
    for tok in toks:
        if tok in _GENERAL_SEMANTIC:
            out.add(_GENERAL_SEMANTIC[tok])
    # HD1/HD2/HD3 are semantic side feeds when written as one token.
    for tok in toks:
        if tok in ("hd1", "hd2", "hd3"):
            out.add(tok)
    return out


def _semantic_base(value):
    words = []
    for tok in _tokens(value):
        if tok in _TECHNICAL or tok in _GENERAL_SEMANTIC or tok in ("hd1", "hd2", "hd3"):
            continue
        words.append(tok)
    return "".join(words)


def feed_compatible(service, candidate_name="", display_name=""):
    """Hard semantic programme-feed gate.

    Returns (allowed, reason). Unknown identities pass only when no semantic
    NAT/TNT/etc qualifier proves a feed mismatch. This function never maps.
    """
    service_name = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    candidate = str(display_name or candidate_name or "")
    if not service_name or not candidate:
        return False, "missing programme-feed identity"

    sf, sv = _known_family_variant(service_name)
    cf, cv = _known_family_variant(candidate)
    if sf and cf and sf == cf:
        if sv == cv:
            return True, "same %s feed" % sf
        return False, "%s programme-feed mismatch (%s != %s)" % (sf, sv or "unknown", cv or "unknown")
    if sf and cf and sf != cf:
        return False, "broadcaster family mismatch (%s != %s)" % (sf, cf)

    # Curated alias identity outranks generic word heuristics.  The alias table
    # intentionally records historic/branding equivalents such as BBC World
    # News -> BBC News, Sharjah HD -> Sharjah TV and Latin/Arabic spellings.
    # Strict broadcaster families above are still evaluated first, so true
    # National/International/TNT programme-feed editions remain protected.
    try:
        sr = _identity_row(service_name)
        cr = _identity_row(candidate)
        sid = str(sr.get("id") or "").strip()
        cid = str(cr.get("id") or "").strip()
        if sid and cid and sid == cid:
            return True, "same curated programme-feed identity"
    except Exception:
        pass

    # Generic NAT/TNT/International/Monde guard. Only compare when the remaining
    # base identity is the same, so ordinary brands containing common words are
    # not affected.
    sb, cb = _semantic_base(service_name), _semantic_base(candidate)
    if sb and cb and sb == cb:
        sm, cm = _general_semantic(service_name), _general_semantic(candidate)
        if sm != cm and (sm or cm):
            return False, "programme-feed qualifier mismatch (%s != %s)" % (
                ",".join(sorted(sm)) or "base", ",".join(sorted(cm)) or "base")
    return True, "no semantic feed conflict"


# Native-Arabic coverage policy.  After beta140, operator-approved aliases
# (2M National/Nat/Monde -> 2M and Al Aoula Inter -> Al Aoula) are already the
# same feed. Other semantic editions such as Arryadia TNT or 2M MENA remain
# distinct; an Arabic service may fall back to another edition only
# from the broadcaster's native country / dedicated local Arabic source.  This
# increases coverage without allowing foreign-country containers (Belgium/UAE/
# Egypt-for-Morocco, etc.) to win merely by name similarity.
_LOCAL_ARABIC_FALLBACK_FAMILIES = set(("2m", "al_aoula", "arryadia", "medi1tv"))
_LOCAL_SOURCE_MARKERS = {
    "2m": ("2m", "chada"),
    "al_aoula": ("snrt", "al aoula", "alaoula"),
    "arryadia": ("snrt", "arryadia", "arriyadia"),
    "medi1tv": ("medi1", "medi 1"),
}


def _same_family_presentation_safe(service_name, candidate):
    """Keep number/language/+1/4K boundaries even for a local feed fallback."""
    try:
        from . import deterministic_matcher
        la = deterministic_matcher.language_signature(service_name)
        lb = deterministic_matcher.language_signature(candidate)
        if (la or lb) and la != lb:
            return False
        na = deterministic_matcher.numeric_signature(service_name)
        nb = deterministic_matcher.numeric_signature(candidate)
        if na != nb:
            return False
        qa = deterministic_matcher.quality_signature(service_name)
        qb = deterministic_matcher.quality_signature(candidate)
        if bool(qa.get("4k")) != bool(qb.get("4k")):
            return False
        if bool(qa.get("plus1")) != bool(qb.get("plus1")):
            return False
        if bool(qa.get("plus")) != bool(qb.get("plus")):
            return False
    except Exception:
        return False
    return True


def local_arabic_feed_fallback(service, candidate_name="", display_name="", source=None, epg=None):
    """Return (allowed, reason) for a same-broadcaster local Arabic fallback.

    This is intentionally narrower than ``feed_compatible``: it only covers a
    few curated broadcaster edition families and only when the EPG source is
    native/local Arabic.  Exact edition matches always outrank this fallback.
    """
    service = service or {}; source = source or {}; epg = epg or {}
    sname = str(service.get("clean_name") or service.get("name") or "")
    cname = str(display_name or candidate_name or epg.get("display_name") or epg.get("channel_id") or "")
    sf, sv = _known_family_variant(sname)
    cf, cv = _known_family_variant(cname)
    if not sf or sf != cf or sf not in _LOCAL_ARABIC_FALLBACK_FAMILIES or sv == cv:
        return False, "not an eligible same-broadcaster edition fallback"
    if not _same_family_presentation_safe(sname, cname):
        return False, "number/language/+1/4K variant conflict"

    home = str(locked_home_country(service) or "").upper().strip()
    if not home or home not in MENA_COUNTRIES:
        return False, "receiver broadcaster has no native MENA country"
    sc = source_countries(source)
    specific = set(x for x in sc if x not in ("MENA", "INT", "INTERNATIONAL"))
    if specific and home not in specific:
        return False, "foreign source country %s for native %s broadcaster" % ("/".join(sorted(specific)), home)

    langs = source_languages(source)
    if langs and "ar" not in langs:
        return False, "local fallback requires Arabic guide language"

    text = " %s %s %s " % (str(source.get("id") or ""), str(source.get("name") or ""), str(source.get("provider") or ""))
    text = text.casefold()
    dedicated = any(marker in text for marker in _LOCAL_SOURCE_MARKERS.get(sf, ()))
    native_country = bool(home in specific)
    local_kind = str(source.get("kind") or "").lower() == "local" and (not specific or native_country)
    if not (native_country or dedicated or local_kind):
        return False, "same-family fallback is not from a native/local source"

    return True, "same broadcaster local Arabic fallback %s:%s->%s" % (sf, sv or "base", cv or "base")


def feed_compatible_with_source(service, candidate_name="", display_name="", source=None, epg=None):
    """Source-aware feed check returning ``(allowed, reason, fallback)``."""
    ok, reason = feed_compatible(service, candidate_name, display_name)
    if ok:
        return True, reason, False
    fallback, fb_reason = local_arabic_feed_fallback(
        service, candidate_name, display_name, source=source, epg=epg)
    if fallback:
        return True, fb_reason, True
    return False, reason, False


def local_fallback_preference(service, candidate_name="", display_name=""):
    """Small deterministic tie-break: exact feed > base local fallback > sibling."""
    sname = str((service or {}).get("clean_name") or (service or {}).get("name") or "")
    cname = str(display_name or candidate_name or "")
    sf, sv = _known_family_variant(sname); cf, cv = _known_family_variant(cname)
    if not sf or sf != cf:
        return 0
    if sv == cv:
        return 60
    if cv == "base":
        return 35
    if sf == "2m" and sv == "world" and cv == "national":
        return 28
    if sf == "2m" and sv == "national" and cv == "world":
        return 18
    return 12


def route(service, source, epg=None):
    """Provider/satellite/language automatic-routing gate + tie-break score.

    beta121 rule order is deliberately strict:
      1) programme broadcaster/native-country lock (2M/SNRT/etc.),
      2) DVB provider country,
      3) explicit channel language,
      4) satellite-family language default,
      5) source region as a final tie-break only.

    A same-looking channel in a foreign country catalogue is therefore not an
    automatic match merely because its name is exact.
    """
    service = service or {}; source = source or {}; epg = epg or {}
    sat = satellite_family(service.get("orbital"))
    zone = satellite_zone(service.get("orbital"))
    # RC27: a satellite-scoped local source is never eligible outside its
    # declared orbital family. This is intentionally checked before names,
    # provider and language so a duplicate brand on Hotbird/Astra cannot take
    # the Nilesat schedule merely because the display name is identical.
    source_satellite = str(source.get("satellite_family") or "").upper().strip()
    if source_satellite and sat != source_satellite:
        return False, -1000, "%s-only EPG source rejects receiver satellite %s" % (source_satellite, sat or "UNKNOWN")
    sc = source_countries(source)
    sc_specific = set(x for x in sc if x not in ("MENA", "INT", "INTERNATIONAL"))
    sl = source_languages(source)
    provider_name = service.get("provider_name") or ""
    pc = provider_countries(provider_name)
    explicit_country = str(service.get("explicit_region") or "").upper()
    if explicit_country and explicit_country not in ("MENA",):
        pc.add(explicit_country)

    service_name = str(service.get("clean_name") or service.get("name") or "")
    identity_country_set = identity_countries(service_name)
    identity_country = next(iter(identity_country_set)) if len(identity_country_set) == 1 else ""
    if identity_country:
        pc.add(identity_country)
    home_country = locked_home_country(service)
    if home_country:
        pc.add(home_country)

    # If both service/candidate have deterministic identities, a candidate from
    # another native country is never safe even before source ranking.
    candidate_value = str(epg.get("display_name") or epg.get("channel_id") or "")
    candidate_countries = identity_countries(candidate_value)
    if home_country and candidate_countries and home_country not in candidate_countries:
        return False, -700, "candidate identity country conflicts with native broadcaster country"

    # Curated national/public families are source-country locked. A broad MENA
    # source remains an admissible fallback, but a concrete foreign country
    # (Egypt for 2M, Belgium for Al Aoula Inter, etc.) is rejected.
    if home_country and sc_specific and home_country not in sc_specific:
        return False, -680, "native broadcaster %s rejects foreign EPG source country %s" % (
            home_country, "/".join(sorted(sc_specific)))

    # A DVB provider that unambiguously identifies a country is authoritative on
    # every satellite, not only Hotbird/Astra. This closes the beta120 hole where
    # SNRT/MA on Nilesat could still accept Belgium because both names matched.
    provider_pc = provider_countries(provider_name)
    if provider_pc and sc_specific:
        aliases = set(provider_pc)
        if "UK" in aliases: aliases.add("GB")
        if "GB" in aliases: aliases.add("UK")
        if aliases.isdisjoint(sc_specific):
            # Curated channel identity outranks a conflicting platform/ambiguous
            # provider label.  The identity-country firewall below/PrecisionMatch
            # still rejects a truly foreign source, so this is not a fuzzy bypass.
            if not (identity_country and identity_country in sc_specific):
                return False, -620, "DVB provider country conflicts with EPG source country"

    expected, why = expected_languages(service)
    src_region = str(source.get("region") or "").upper()
    src_mena = bool(sc & MENA_COUNTRIES or "MENA" in src_region or "ARAB" in src_region)
    src_europe = bool(sc & EUROPE_COUNTRIES or "EUROPE" in src_region)

    explicit_lang = service_language(service)
    mena_service = bool((pc & MENA_COUNTRIES) or (home_country in MENA_COUNTRIES) or (zone == "MENA" and not pc))

    # Nilesat/Badr/Arabsat: Arabic is the native automatic guide language for an
    # Arabic/MENA service unless the channel itself explicitly says EN/FR/etc.
    # This does not force a French/UK provider carried on Nilesat into Arabic.
    if sat in ("NILESAT", "BADR", "ARABSAT30E") and not explicit_lang and mena_service:
        if src_europe and not src_mena and not (home_country and home_country in sc_specific):
            return False, -500, "%s Arabic/MENA service rejects European regional EPG" % sat
        if sl and "ar" not in sl and sl.intersection({"en", "fr"}):
            return False, -470, "%s Arabic/MENA service requires Arabic guide unless channel language is explicit" % sat

    if expected and sl and expected.isdisjoint(sl):
        return False, -400, "EPG language conflicts with %s (%s vs %s)" % (
            why, "/".join(sorted(expected)), "/".join(sorted(sl)))

    score = 0
    if home_country and home_country in sc_specific:
        score += 260
    if pc and sc and not pc.isdisjoint(sc):
        score += 180
    if expected and sl and not expected.isdisjoint(sl):
        score += 140
    if sat in ("NILESAT", "BADR", "ARABSAT30E"):
        if "ar" in sl or src_mena:
            score += 110
    elif sat in ("ASTRA19", "ASTRA23", "ASTRA28", "HISPASAT30W"):
        if pc and sc and not pc.isdisjoint(sc):
            score += 100
        elif src_europe:
            score += 30
    elif sat == "HOTBIRD":
        # Hotbird is mixed: no regional default, provider/country/language only.
        if pc and sc and not pc.isdisjoint(sc):
            score += 100
    return True, score, "provider/satellite/language compatible"
