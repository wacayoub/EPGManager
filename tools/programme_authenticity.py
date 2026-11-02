"""Recognize explicit generated schedule rows, not merely repeated real shows."""
import re


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
