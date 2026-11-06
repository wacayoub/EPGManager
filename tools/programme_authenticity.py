"""Recognize explicit generated schedule rows, not merely repeated real shows."""
import re

VIEWER_CATEGORY = "Viewer-reported recurring schedule"
VIEWER_SOURCE = "viewer-reported-arryadia-hourly-grand-format"
SMART_CATEGORY = "Smart estimated programme"
BOTOLA_CATEGORY = "Estimated Botola broadcast"
SMART_SOURCE = "arryadia-smart-estimate"
BOTOLA_SOURCE = "arryadia-botola-estimate"
AUTO_CATEGORY = "Automatic fallback schedule"
AUTO_SOURCE = "morocco-auto-fallback"


def viewer_reported_schedule(node):
    return any((x.text or '').strip() == VIEWER_CATEGORY for x in node.findall('category'))


def smart_estimated_schedule(node):
    return any((x.text or '').strip() in (SMART_CATEGORY,BOTOLA_CATEGORY) for x in node.findall('category'))


def automatic_fallback_schedule(node):
    return any((x.text or '').strip() == AUTO_CATEGORY for x in node.findall('category'))


def estimated_schedule(node):
    return viewer_reported_schedule(node) or smart_estimated_schedule(node) or automatic_fallback_schedule(node)


def schedule_source(node):
    categories={(x.text or '').strip() for x in node.findall('category')}
    if AUTO_CATEGORY in categories:return AUTO_SOURCE
    if BOTOLA_CATEGORY in categories:return BOTOLA_SOURCE
    if SMART_CATEGORY in categories:return SMART_SOURCE
    return VIEWER_SOURCE if VIEWER_CATEGORY in categories else 'old'


def generated_placeholder(title):
    title = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    return title in {
        "arryadia programme", "programme arryadia",
        "برامج قناة السابعة aflam",
    }


UNAVAILABLE_DESCRIPTIONS = (
    "دليل البرامج غير متوفر", "جدول البرامج غير متوفر",
    "program guide not available", "epg not available",
    "program information unavailable", "programme information unavailable",
)


def normalize_label(value):
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def placeholder_title(title, channel_names=()):
    """Reject explicit filler and long exact channel labels, not real repeats."""
    normalized = normalize_label(title)
    folded = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    return (generated_placeholder(title)
            or folded == "program guide"
            or any(phrase in folded for phrase in
                   ("program guide not available", "epg not available", "دليل البرامج غير متوفر"))
            or (len(normalized) >= 7 and normalized in
                {normalize_label(name) for name in channel_names}))


def clean_programme(node, channel_names=()):
    """Return a copy with genuine programme data, or None for a filler row.

    A genuine bilingual title wins over a placeholder in another language.
    Missing descriptions stay missing; repeated real programmes stay intact.
    """
    from xml.etree import ElementTree as ET
    titles = [(t.text or "").strip() for t in node.findall("title") if (t.text or "").strip()]
    if not titles or all(placeholder_title(t, channel_names) for t in titles):
        return None
    result = ET.fromstring(ET.tostring(node, encoding="utf-8"))
    for title in result.findall("title"):
        if placeholder_title(title.text, channel_names):
            result.remove(title)
    for desc in result.findall("desc"):
        if any(phrase in (desc.text or "").casefold() for phrase in UNAVAILABLE_DESCRIPTIONS):
            result.remove(desc)
    return result


def strict_arabic_channel(cid):
    return str(cid).casefold() == "nationalgeographicabudhabi.ae@sd"


def arabic_title(title):
    return bool(re.search(r"[\u0600-\u06ff]", title or ""))
