"""Recognize explicit generated schedule rows, not merely repeated real shows."""
import re


def generated_placeholder(title):
    title = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    return title in {
        "arryadia programme", "programme arryadia",
        "برامج قناة السابعة aflam",
    }


def strict_arabic_channel(cid):
    return str(cid).casefold() == "nationalgeographicabudhabi.ae@sd"


def arabic_title(title):
    return bool(re.search(r"[\u0600-\u06ff]", title or ""))
