# -*- coding: utf-8 -*-
"""Transient Arabic programme overlay for selected OpenEPG sports feeds.

RC15 policy
-----------
OpenEPG remains the remote authority.  Nothing from the provider is persisted
under /etc as a second XMLTV source.  When Qatar 1 is actually selected as the
owner of one or more beIN Sports receiver services, Import Source/All may fetch
that tiny remote feed once, format only the mapped beIN Sports programme text
with the exact same deterministic formatter used by EPGManager's native beIN Sports source, hand a temporary /tmp XML
file to Native EPGImport, and remove it as soon as the import finishes.

The mapping engine never sees this translated file.  Channel IDs and ServiceRefs
remain exactly the provider's original IDs, so translation cannot alter mapping.
"""
from __future__ import print_function

import gzip
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

try:
    import requests
except Exception:
    requests = None

from .downloader import DEFAULT_HEADERS
from .logger import get_logger
from ..translators.arabic import contains_arabic
from ..translators.sports import ENTITY_MAP, get_league_arabic, translate_sports_title, translate_sports_text

log = get_logger(__name__)

SUPPORTED_SOURCE_IDS = set(("ext_openepg_qatar_1",))
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
CONNECT_TIMEOUT = 4
READ_TIMEOUT = 10

# Qatar 1 does not always expose the provider LIVE flag in its XMLTV titles.
# RC19 therefore adds a deliberately conservative schedule heuristic for full
# football matches.  It never marks highlights/replays/mini-matches as live.
_NON_LIVE_MARKERS_RE = re.compile(
    r"\b(?:mini\s+match(?:es)?|highlights?|extended\s+highlights?|replay|repeat|"
    r"rerun|re-air|encore|classic|archive|review|recap|best\s+of|pre[- ]match|"
    r"post[- ]match|live\s+studio)\b",
    flags=re.I,
)
_MATCH_RE = re.compile(r"\s+(?:vs\.?|v\.?)\s+|\s*ضد\s*", flags=re.I)
_MONTHS_AR = {
    "january": "يناير", "february": "فبراير", "march": "مارس",
    "april": "أبريل", "may": "مايو", "june": "يونيو",
    "july": "يوليوز", "august": "غشت", "september": "سبتمبر",
    "october": "أكتوبر", "november": "نوفمبر", "december": "ديسمبر",
}
_SMALL_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12",
}

_LIVE_PREFIX_RE = re.compile(r"^\s*(?:Live\b(?!\s+Studio\b)|مباشر\b)\s*(?:[:\-–—]\s*)?", flags=re.I)
_LATIN_RE = re.compile(r"[A-Za-z]")
# Qatar 1 descriptions must be Arabic-only.  These additions cover current EPL
# fixture names that are not part of the older shared sports dictionary.
_ARABIC_ENTITY_EXTRAS = {
    "Nottingham Forest": "نوتنغهام فورست",
    "Sunderland": "سندرلاند",
    "Sunderland AFC": "سندرلاند",
    "Derby County": "ديربي كاونتي",
    "Ipswich Town": "إيبسويتش تاون",
    "Brighton & Hove Albion": "برايتون أند هوف ألبيون",
    "Brighton and Hove Albion": "برايتون أند هوف ألبيون",
    "Brighton": "برايتون",
    "Leeds United": "ليدز يونايتد",
    "Spurs": "توتنهام",
}


def _localname(tag):
    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _norm(value):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(value or "").casefold()).split())


def _is_bein_sports_name(value):
    text = _norm(value)
    return bool("bein" in text and ("sport" in text or "sports" in text or "4k" in text))


def _mapped_bein_targets(channel_items, registry_index):
    """Return {xmltv_id_lower: receiver_service_name} for mapped beIN services.

    The receiver name is carried only as presentation context (not mapping input),
    mainly to distinguish beIN SPORTS NEWS from numbered sports channels so the
    exact native beIN description policy can be reused.
    """
    by_ref = (registry_index or ({}, {}))[0] or {}
    out = {}
    for cid, refs in (channel_items or {}).items():
        key = str(cid or "").casefold()
        if not key:
            continue
        for ref in refs or []:
            raw = str(ref or "").strip()
            if not raw:
                continue
            service = by_ref.get(raw)
            if service is None:
                try:
                    from . import channel_registry
                    service = by_ref.get(channel_registry.canonical_service_ref(raw) or raw)
                except Exception:
                    service = None
            if not service:
                continue
            name = str((service or {}).get("name") or (service or {}).get("clean_name") or "").strip()
            if _is_bein_sports_name(name):
                out[key] = name
                break
    return out


def _mapped_bein_ids(channel_items, registry_index):
    """Backward-compatible set view used by RC14 tests/callers."""
    return set(_mapped_bein_targets(channel_items, registry_index))


def _parse_xmltv_time(value):
    """Parse a normal XMLTV timestamp into aware UTC datetime."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d%H%M%S %z", "%Y%m%d%H%M %z", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _programme_window(programme):
    start = _parse_xmltv_time((programme or {}).get("start") if hasattr(programme, "get") else "")
    stop = _parse_xmltv_time((programme or {}).get("stop") if hasattr(programme, "get") else "")
    if start is None or stop is None or stop <= start:
        return None, None, 0
    return start, stop, int((stop - start).total_seconds() // 60)


def _match_teams(title):
    """Return the two fixture names from a match-like title."""
    text = _LIVE_PREFIX_RE.sub("", str(title or "").strip())
    m = re.match(r"^(.+?)\s+(?:vs\.?|v\.?)\s+(.+?)(?:\s+[-–—|]\s+.*)?$", text, flags=re.I)
    if not m:
        m = re.match(r"^(.+?)\s*ضد\s*(.+?)(?:\s+[-–—|]\s+.*)?$", text, flags=re.I)
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def _fixture_key(title):
    a, b = _match_teams(title)
    if not a or not b:
        return ""
    return "%s|%s" % (_norm(a), _norm(b))


def _fixture_first_starts(root, target_ids):
    """Index the earliest full-match occurrence of every fixture in the feed.

    Qatar/OpenEPG often repeats the exact same game later in the night while
    retaining a provider ``Live`` label.  The earliest full-length occurrence
    is the only one eligible for inferred/validated LIVE status; later copies
    are treated as re-airings even when their title forgot to say Replay.
    """
    out = {}
    for programme in root.iter():
        if _localname(programme.tag) != "programme":
            continue
        cid = str(programme.get("channel") or "").strip().casefold()
        if cid not in target_ids:
            continue
        _el, title = _first_text(programme, "title")
        if not title or not _MATCH_RE.search(title) or _NON_LIVE_MARKERS_RE.search(title):
            continue
        key = _fixture_key(title)
        if not key:
            continue
        start, stop, minutes = _programme_window(programme)
        if start is None or stop is None or minutes < 100 or minutes > 300:
            continue
        previous = out.get(key)
        if previous is None or start < previous:
            out[key] = start
    return out


def _is_first_fixture_occurrence(programme, title, fixture_first_starts):
    key = _fixture_key(title)
    if not key:
        return True, ""
    first = (fixture_first_starts or {}).get(key)
    start, _stop, _minutes = _programme_window(programme)
    if first is None or start is None:
        return True, key
    # Allow small provider schedule jitter around the same original slot.
    return bool(start <= first + timedelta(minutes=20)), key


def _validate_explicit_live(programme, title, desc="", competition_hint="", news_channel=False, fixture_first_starts=None):
    """Validate a provider LIVE prefix instead of trusting it blindly."""
    if news_channel or not title or not _MATCH_RE.search(title):
        return False, "not a match/news channel"
    evidence = " ".join((str(title or ""), str(desc or ""), str(competition_hint or "")))
    if _NON_LIVE_MARKERS_RE.search(evidence):
        return False, "excluded replay/highlight marker"
    start, stop, minutes = _programme_window(programme)
    if start is None or stop is None or minutes < 100 or minutes > 300:
        return False, "invalid full-match schedule window"
    first_ok, _key = _is_first_fixture_occurrence(programme, title, fixture_first_starts)
    if not first_ok:
        return False, "later duplicate/re-air occurrence"
    return True, "provider LIVE prefix + first full-match occurrence"


def _infer_live_status(programme, title, desc="", competition_hint="", news_channel=False, fixture_first_starts=None):
    """Infer LIVE conservatively when Qatar 1 omits the provider flag.

    RC22 fixes the false-positive class seen on overnight replays: a repeated
    fixture can only be live on its earliest full-match occurrence.  Missing
    provider flags are inferred for a match that is currently airing, or for a
    first occurrence beginning soon with a true live-broadcast-sized window.
    """
    title = str(title or "").strip()
    if news_channel or not title or not _MATCH_RE.search(title):
        return False, ""
    evidence = " ".join((title, str(desc or ""), str(competition_hint or "")))
    if _NON_LIVE_MARKERS_RE.search(evidence):
        return False, "excluded replay/highlight marker"

    start, stop, minutes = _programme_window(programme)
    if start is None or stop is None:
        return False, "missing schedule window"
    if minutes < 100 or minutes > 300:
        return False, "duration outside live-event window"

    league = get_league_arabic(evidence)
    footballish = bool(league or re.search(
        r"\b(?:football|soccer|premier\s+league|la\s*liga|ligue\s*1|serie\s*a|"
        r"bundesliga|champions\s+league|europa\s+league|conference\s+league|"
        r"world\s+cup|afc|caf)\b|الدوري|كأس|كرة القدم",
        evidence, flags=re.I,
    ))
    if not footballish:
        return False, "not a recognised football match"

    first_ok, _key = _is_first_fixture_occurrence(programme, title, fixture_first_starts)
    if not first_ok:
        return False, "later duplicate/re-air occurrence"

    now = datetime.now(timezone.utc)
    currently_airing = bool(start <= now < stop)
    if currently_airing and 100 <= minutes <= 260:
        return True, "currently airing first fixture occurrence"

    # Future inference is intentionally bounded.  A 150+ minute first slot in
    # the next 18 hours is a likely live transmission; overnight/full-match
    # copies later than the first occurrence are already rejected above.
    if now < start <= now + timedelta(hours=18) and 150 <= minutes <= 240:
        return True, "first fixture occurrence + imminent full live window"
    return False, "insufficient live confidence"

def _small_number(value):
    token = str(value or "").strip()
    return _SMALL_NUMBER_WORDS.get(token.lower(), token)


def _arabic_month_year(value):
    value = str(value or "").strip()
    m = re.fullmatch(r"([A-Za-z]+)\s+(20\d{2})", value)
    if not m:
        return value
    return "%s %s" % (_MONTHS_AR.get(m.group(1).lower(), m.group(1)), m.group(2))


def _ltr(value):
    """Legacy helper retained for title-side bidi compatibility."""
    value = str(value or "").strip()
    return ("\u200e%s\u200e" % value) if value else value


def _arabic_entity(value):
    """Return an Arabic-only entity label when it is known with confidence."""
    raw = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;|-–—")
    if not raw:
        return ""
    aliases = dict(ENTITY_MAP)
    aliases.update(_ARABIC_ENTITY_EXTRAS)
    low = raw.casefold()
    for eng, ara in sorted(aliases.items(), key=lambda pair: len(pair[0]), reverse=True):
        if low == str(eng).casefold():
            return str(ara)
    # Shared translator may know an entity even if exact alias spelling differs.
    translated = str(translate_sports_text(raw) or "").strip()
    if translated and contains_arabic(translated) and not _LATIN_RE.search(translated):
        return translated
    return ""


def _arabic_match_summary(raw_title, competition_hint=""):
    a, b = _match_teams(raw_title)
    a_ar = _arabic_entity(a)
    b_ar = _arabic_entity(b)
    league = get_league_arabic("%s %s" % (competition_hint, raw_title))
    if a_ar and b_ar:
        sentence = "مباراة بين %s و%s" % (a_ar, b_ar)
    else:
        sentence = "مباراة كرة قدم"
    if league:
        sentence += " ضمن %s" % league
    return sentence + "."


def _pure_arabic_description(candidate, raw_title="", raw_desc="", competition_hint=""):
    """Guarantee the Qatar1 description contains no Latin prose.

    We keep a detailed deterministic translation when every token is Arabic.
    If a free-form provider paragraph still contains English after known sports
    replacements, only Arabic metadata is kept and a factual Arabic match
    summary is appended.  This is preferable to a hybrid or invented sentence.
    """
    text = str(candidate or "").strip()
    if text:
        text = str(translate_sports_text(text) or text).strip()
        text = re.sub(r"\bbeIN\s+SPORTS\s+NEWS\b", "بي إن سبورتس الإخبارية", text, flags=re.I)
        text = re.sub(r"\bbeIN\s+SPORTS\b", "بي إن سبورتس", text, flags=re.I)
        text = re.sub(r"\bFIFA\b", "فيفا", text, flags=re.I)
        text = re.sub(r"\bUEFA\b", "يويفا", text, flags=re.I)
        if contains_arabic(text) and not _LATIN_RE.search(text):
            return text

    kept = []
    # Preserve useful Arabic-only metadata from the native formatter.
    for part in [x.strip() for x in re.split(r"\s*\|\s*", str(candidate or "")) if x.strip()]:
        part = str(translate_sports_text(part) or part).strip()
        part = re.sub(r"\bFIFA\b", "فيفا", part, flags=re.I)
        part = re.sub(r"\bUEFA\b", "يويفا", part, flags=re.I)
        if contains_arabic(part) and not _LATIN_RE.search(part) and part not in kept:
            kept.append(part)

    if _MATCH_RE.search(str(raw_title or "")):
        summary = _arabic_match_summary(raw_title, competition_hint=competition_hint)
        if summary and summary not in kept:
            kept.append(summary)
    elif not kept:
        league = get_league_arabic("%s %s %s" % (competition_hint, raw_title, raw_desc))
        kept.append("برنامج رياضي%s." % ((" ضمن %s" % league) if league else ""))

    out = " | ".join(kept).strip()
    # Last hard firewall: never leak an English/hybrid Qatar1 description.
    if _LATIN_RE.search(out):
        league = get_league_arabic("%s %s" % (competition_hint, raw_title))
        out = "مباراة كرة قدم%s." % ((" ضمن %s" % league) if league else "") if _MATCH_RE.search(str(raw_title or "")) else "برنامج رياضي."
    return out


def _translate_opta_football_desc(text, competition_hint=""):
    """Translate common Opta head-to-head templates into Arabic-only prose.

    Every sentence must match a known factual template.  Unknown prose returns
    an empty string so the caller can use the safe Arabic structured fallback.
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", raw) if x.strip()]
    if not sentences:
        return ""
    league = get_league_arabic("%s %s" % (competition_hint, raw)) or "الدوري الإنجليزي الممتاز"
    rendered = []

    def ent(name):
        return _arabic_entity(name)

    def n(value):
        return _small_number(value)

    for sentence in sentences:
        # Sunderland-style two-sentence H2H stat.
        m = re.match(
            r"^(?P<a>.+?) have won just one of their last (?P<n1>\d+) Premier League meetings with "
            r"(?P<b>.+?), and are winless in (?P<n2>\d+) since a (?P<score>\d+\s*-\s*\d+) victory in "
            r"(?P<when>[A-Za-z]+\s+20\d{2}) after (?P<player>.+?)[’']s goal\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            if not a or not b:
                return ""
            rendered.append(
                "لم يفز %s سوى بمباراة واحدة من آخر %s مواجهة له مع %s في %s، "
                "ولم يحقق أي فوز في آخر %s مواجهة منذ انتصاره %s في %s."
                % (a, m.group("n1"), b, league, m.group("n2"), m.group("score"), _arabic_month_year(m.group("when")))
            )
            continue

        m = re.match(
            r"^(?P<b>.+?) have only lost one of their last (?P<n>\d+) league visits to (?P<a>.+?), "
            r"winning (?P<w>[A-Za-z]+|\d+) of their last (?P<last>[A-Za-z]+|\d+), although (?:they )?did draw "
            r"this exact fixture last season (?P<score>\d+\s*-\s*\d+)\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            if not a or not b:
                return ""
            rendered.append(
                "لم يخسر %s سوى مرة واحدة في آخر %s زيارة له إلى %s في %s، وفاز في %s من آخر %s زيارات، "
                "بينما انتهت المواجهة نفسها في الموسم الماضي بالتعادل %s."
                % (b, m.group("n"), a, league, n(m.group("w")), n(m.group("last")), m.group("score"))
            )
            continue

        # Aston Villa / Nottingham Forest template.
        m = re.match(
            r"^(?P<a>.+?) have won each of their last (?P<n>[A-Za-z]+|\d+) Premier League home games against (?P<b>.+?)\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            if not a or not b:
                return ""
            rendered.append("فاز %s في آخر %s مباريات خاضها على أرضه أمام %s في %s." % (a, n(m.group("n")), b, league))
            continue

        m = re.match(
            r"^The only side they have won (?P<n>[A-Za-z]+|\d+) in a row against at home in the competition (?:are|is) (?P<b>.+?) from (?P<y1>\d{4}) to (?P<y2>\d{4})\.?$",
            sentence, flags=re.I,
        )
        if m:
            b = ent(m.group("b"))
            if not b:
                return ""
            rendered.append("والفريق الوحيد الذي تحقق أمامه سلسلة من %s انتصارات متتالية على الأرض في المسابقة هو %s بين %s و%s." % (n(m.group("n")), b, m.group("y1"), m.group("y2")))
            continue

        m = re.match(
            r"^(?P<a>.+?) have lost (?P<n>[A-Za-z]+|\d+) of their (?P<total>\d+) Premier League games against (?P<b>.+?), "
            r"only losing (?P<threshold>\d+)\+ times against (?P<sides>[A-Za-z]+|\d+) sides in the (?:competition|comp)\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            if not a or not b:
                return ""
            rendered.append("خسر %s %s من أصل %s مباراة أمام %s في %s، ولم يخسر %s مباريات أو أكثر إلا أمام %s فرق في المسابقة." % (a, n(m.group("n")), m.group("total"), b, league, m.group("threshold"), n(m.group("sides"))))
            continue

        # Tottenham / Everton template.
        m = re.match(
            r"^(?P<a>.+?) have only lost one of their last (?P<n>\d+) Premier League home games against (?P<b>.+?), "
            r"winning their last (?P<w>[A-Za-z]+|\d+) since a (?P<score>\d+\s*-\s*\d+) defeat in (?P<when>[A-Za-z]+\s+20\d{2})\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            if not a or not b:
                return ""
            rendered.append("لم يخسر %s سوى مباراة واحدة من آخر %s مباريات خاضها على أرضه أمام %s في %s، وفاز في آخر %s مباريات منذ خسارته %s في %s." % (a, m.group("n"), b, league, n(m.group("w")), m.group("score"), _arabic_month_year(m.group("when"))))
            continue

        m = re.match(
            r"^(?P<b>.+?) lost both Premier League meetings with (?P<a>.+?) last season by an aggregate score of (?P<score>\d+\s*-\s*\d+), "
            r"but have never lost (?P<n>[A-Za-z]+|\d+) in a row to (?P<a2>.+?) without scoring in their league history\.?$",
            sentence, flags=re.I,
        )
        if m:
            a, b = ent(m.group("a")), ent(m.group("b"))
            a2 = ent(m.group("a2")) or a
            if not a or not b:
                return ""
            rendered.append("خسر %s مباراتي %s أمام %s في الموسم الماضي بنتيجة إجمالية %s، لكنه لم يسبق أن خسر %s مباريات متتالية أمام %s من دون تسجيل في تاريخه بالدوري." % (b, league, a, m.group("score"), n(m.group("n")), a2))
            continue

        # All sentences must be understood before claiming a full translation.
        return ""
    return " ".join(rendered).strip()

def _download(url):
    if requests is None:
        raise RuntimeError("python3-requests unavailable")
    headers = dict(DEFAULT_HEADERS)
    headers["Accept-Encoding"] = "gzip, deflate"
    resp = requests.get(str(url), headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True)
    try:
        resp.raise_for_status()
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_COMPRESSED_BYTES:
                raise RuntimeError("OpenEPG Qatar 1 download exceeded %d MB" % (MAX_COMPRESSED_BYTES // 1048576))
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    if len(payload) > MAX_XML_BYTES:
        raise RuntimeError("OpenEPG Qatar 1 XML exceeded %d MB" % (MAX_XML_BYTES // 1048576))
    return payload


def _first_text(parent, tag_name):
    for child in list(parent or []):
        if _localname(child.tag) == tag_name:
            text = str(child.text or "").strip()
            if text:
                return child, text
    return None, ""


def _translate_title(text):
    text = str(text or "").strip()
    if not text or contains_arabic(text):
        return text
    out = str(translate_sports_title(text) or "").strip()
    # Accept hybrid team names + Arabic competition metadata.  If the dictionary
    # learned nothing, keep the provider text rather than pretending it is Arabic.
    return out if out and (contains_arabic(out) or out != text) else text


def _translate_desc(text):
    text = str(text or "").strip()
    if not text or contains_arabic(text):
        return text
    out = str(translate_sports_text(text) or "").strip()
    # Long free-form English prose is intentionally not machine-guessed.  The
    # deterministic translator may translate known sports structure; otherwise
    # the accurate English description is safer than a fabricated Arabic one.
    return out if out and contains_arabic(out) else text


def build_transient_overlay(source_id, source_name, url, channel_items, registry_index,
                            temp_dir="/tmp"):
    """Return (effective_url, meta).

    On any problem the original remote URL is returned.  Translation is therefore
    an optional presentation layer, never an import/mapping dependency.
    """
    sid = str(source_id or "")
    meta = {
        "enabled": False, "translated_programmes": 0, "translated_titles": 0,
        "translated_descriptions": 0, "target_channels": 0, "temp_path": "",
        "fallback": False, "reason": "", "inferred_live_programmes": 0,
        "suppressed_false_live_programmes": 0,
    }
    if sid not in SUPPORTED_SOURCE_IDS or not url:
        return url, meta

    target_targets = _mapped_bein_targets(channel_items, registry_index)
    target_ids = set(target_targets)
    if not target_ids:
        meta["reason"] = "no mapped beIN Sports receiver target"
        return url, meta

    try:
        payload = _download(url)
        root = ET.fromstring(payload)

        # Provider labels are presentation context only.  Mapping ownership was
        # already resolved before this overlay is called and is never changed here.
        provider_names = {}
        for elem in list(root):
            if _localname(elem.tag) != "channel":
                continue
            cid = str(elem.get("id") or "").strip()
            if not cid:
                continue
            names = []
            for child in list(elem):
                if _localname(child.tag) == "display-name":
                    value = str(child.text or "").strip()
                    if value:
                        names.append(value)
            provider_names[cid.casefold()] = " / ".join(names)
        meta["target_channels"] = len(target_ids)
        fixture_first_starts = _fixture_first_starts(root, target_ids)

        # RC15: reuse the native beIN Sports formatter itself, rather than a
        # second token-by-token translation path.  This gives Qatar 1 the same
        # Arabic title policy, mixed-language guard, structured match fallback
        # and NEWS descriptions as sources/bein_sports.py.
        from ..sources.bein_sports import BeinSportsSource

        translated_programmes = translated_titles = translated_desc = 0
        for programme in root.iter():
            if _localname(programme.tag) != "programme":
                continue
            cid = str(programme.get("channel") or "").strip().casefold()
            if cid not in target_ids:
                continue

            title_el, old_title = _first_text(programme, "title")
            desc_el, old_desc = _first_text(programme, "desc")
            if title_el is None or not old_title:
                continue

            categories = []
            for child in list(programme):
                if _localname(child.tag) == "category":
                    value = str(child.text or "").strip()
                    if value:
                        categories.append(value)
            competition_hint = " | ".join(categories)
            receiver_name = target_targets.get(cid, "")
            provider_name = provider_names.get(cid, "")
            channel_context = "%s %s" % (receiver_name, provider_name)
            news_channel = bool("news" in _norm(channel_context))
            had_explicit_live = bool(_LIVE_PREFIX_RE.match(old_title))
            explicit_live = False
            explicit_reason = ""
            if had_explicit_live:
                explicit_live, explicit_reason = _validate_explicit_live(
                    programme, old_title, old_desc, competition_hint=competition_hint,
                    news_channel=news_channel, fixture_first_starts=fixture_first_starts,
                )
                if not explicit_live:
                    meta["suppressed_false_live_programmes"] += 1
                    log.debug("Qatar 1 suppressed false LIVE for %s: %s (%s)", cid, old_title, explicit_reason)
            inferred_live, live_reason = _infer_live_status(
                programme, old_title, old_desc, competition_hint=competition_hint,
                news_channel=news_channel, fixture_first_starts=fixture_first_starts,
            )
            is_live = bool(explicit_live or inferred_live)
            if inferred_live and not explicit_live:
                meta["inferred_live_programmes"] += 1
                log.debug("Qatar 1 inferred LIVE for %s: %s (%s)", cid, old_title, live_reason)

            # When a replay incorrectly carries a provider Live prefix, strip it
            # before passing the title to the native formatter; otherwise that
            # formatter would faithfully re-add Live even with is_live=False.
            format_title = old_title if (not had_explicit_live or explicit_live) else _LIVE_PREFIX_RE.sub("", old_title).strip()

            try:
                new_title, new_desc = BeinSportsSource._format_epg_text(
                    format_title, old_desc, competition_hint=competition_hint,
                    is_live=is_live, news_channel=news_channel,
                )
                # For common Opta head-to-head paragraphs, RC19 can provide a
                # full deterministic Arabic translation rather than only a
                # short structured summary. Unknown prose remains safe/factual.
                opta_desc = _translate_opta_football_desc(old_desc, competition_hint=competition_hint)
                if opta_desc:
                    # Keep useful formatter metadata (season, round, leg, etc.)
                    # but replace the short generic match sentence with the full
                    # deterministic Opta translation.
                    kept = []
                    for part in [x.strip() for x in str(new_desc or "").split(" | ") if x.strip()]:
                        if re.match(r"^(?:مباراة|ملخص مختصر لمباراة)\s+", part):
                            continue
                        kept.append(part)
                    kept.append(opta_desc)
                    new_desc = " | ".join(kept)
                new_desc = _pure_arabic_description(
                    new_desc, raw_title=old_title, raw_desc=old_desc,
                    competition_hint=competition_hint,
                )
            except Exception as exc:
                log.debug("Qatar 1 native beIN formatter fallback for %s: %s", cid, exc)
                new_title = _translate_title(format_title)
                new_desc = _translate_desc(old_desc) if old_desc else ""
                new_desc = _pure_arabic_description(
                    new_desc, raw_title=old_title, raw_desc=old_desc,
                    competition_hint=competition_hint,
                )

            changed = False
            new_title = str(new_title or "").strip()
            if new_title and new_title != old_title and contains_arabic(new_title):
                title_el.text = new_title
                title_el.set("lang", "ar")
                translated_titles += 1
                changed = True

            new_desc = str(new_desc or "").strip()
            # RC22 Qatar 1 policy: description is Arabic-only.  Remove every
            # alternate provider <desc> so Enigma2 cannot fall back to an
            # English language element depending on image language settings.
            if new_desc:
                if desc_el is None:
                    desc_el = ET.SubElement(programme, "desc")
                for child in list(programme):
                    if _localname(child.tag) == "desc" and child is not desc_el:
                        try:
                            programme.remove(child)
                        except Exception:
                            pass
                if str(desc_el.text or "").strip() != new_desc or str(desc_el.get("lang") or "") != "ar":
                    desc_el.text = new_desc
                    desc_el.set("lang", "ar")
                    translated_desc += 1
                    changed = True

            if changed:
                translated_programmes += 1

        # If nothing was translated, keep the original direct URL and create no
        # local copy.  This keeps the normal external-source fast path untouched.
        if translated_programmes <= 0:
            meta["reason"] = "no translatable beIN Sports programme text"
            return url, meta

        if not os.path.isdir(temp_dir):
            os.makedirs(temp_dir)
        fd, path = tempfile.mkstemp(prefix="epgmanager-qatar1-ar-", suffix=".xml", dir=temp_dir)
        os.close(fd)
        tree = ET.ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        meta.update({
            "enabled": True,
            "translated_programmes": int(translated_programmes),
            "translated_titles": int(translated_titles),
            "translated_descriptions": int(translated_desc),
            "temp_path": path,
            "reason": "native beIN Arabic formatter overlay + replay-safe LIVE + Arabic-only description",
        })
        log.info("OpenEPG Qatar 1 Arabic overlay: %d programme(s), %d title(s), %d description(s), %d inferred LIVE, %d false LIVE suppressed, %d beIN channel(s)",
                 translated_programmes, translated_titles, translated_desc,
                 int(meta.get("inferred_live_programmes") or 0),
                 int(meta.get("suppressed_false_live_programmes") or 0), len(target_ids))
        return path, meta
    except Exception as exc:
        meta["fallback"] = True
        meta["reason"] = str(exc)
        log.warning("OpenEPG Qatar 1 Arabic overlay unavailable; using original English remote feed: %s", exc)
        return url, meta


def cleanup(paths):
    for path in list(paths or []):
        path = str(path or "")
        if not path or not path.startswith("/tmp/epgmanager-qatar1-ar-"):
            continue
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except Exception:
            pass
