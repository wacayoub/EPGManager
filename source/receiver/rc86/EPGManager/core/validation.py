# -*- coding: utf-8 -*-
"""
core/validation.py

Validates a generated XMLTV file BEFORE it is allowed to replace the
previous good file. This is what section 17/18 of the spec calls the
"generate temp -> validate -> atomic rename, else keep last good EPG" flow,
which NONE of the six original scripts implemented (they all wrote straight
to the final path, so a zero-program scrape or a parser crash could clobber
a working EPG file).
"""

from xml.etree import ElementTree as ET

from .logger import get_logger

log = get_logger(__name__)


class ValidationResult(object):
    def __init__(self, ok, program_count=0, channel_count=0, errors=None):
        self.ok = ok
        self.program_count = program_count
        self.channel_count = channel_count
        self.errors = errors or []

    def __bool__(self):
        return self.ok

    __nonzero__ = __bool__  # Python 2 compat, harmless on 3

    def __repr__(self):
        return "<ValidationResult ok=%s programs=%d channels=%d errors=%s>" % (
            self.ok, self.program_count, self.channel_count, self.errors)


def validate_xmltv_string(xml_text, min_programs=1):
    """Parse and sanity-check an XMLTV document held in memory (used right
    before writing it to a temp file)."""
    errors = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return ValidationResult(False, errors=["XML parse error: %s" % e])

    if root.tag != "tv":
        errors.append("Root element is <%s>, expected <tv>" % root.tag)

    channels = root.findall("channel")
    programmes = root.findall("programme")

    if not channels:
        errors.append("No <channel> elements found")

    for p in programmes:
        if "start" not in p.attrib:
            errors.append("A <programme> is missing the 'start' attribute")
            break
        if "channel" not in p.attrib:
            errors.append("A <programme> is missing the 'channel' attribute")
            break

    if len(programmes) < min_programs:
        errors.append(
            "Only %d programme(s) found, minimum required is %d - refusing "
            "to treat this as a valid update" % (len(programmes), min_programs)
        )

    ok = not errors
    return ValidationResult(ok, len(programmes), len(channels), errors)


def validate_xmltv_file(path, min_programs=1):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return ValidationResult(False, errors=["Could not read file: %s" % e])
    return validate_xmltv_string(text, min_programs=min_programs)
