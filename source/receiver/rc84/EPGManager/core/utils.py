# -*- coding: utf-8 -*-
"""
core/utils.py

Small shared helpers used across core/ and sources/. Kept dependency-free
(stdlib only) since this module is imported everywhere.
"""

import html
import os
import re
import tempfile


def indent_xml(elem, level=0):
    """Pretty-indent an xml.etree.ElementTree Element in place.

    Equivalent to ET.indent() (Python 3.9+) but works on any Python 3
    version, matching the existing source-timezone approach
    script. Promoted here so every source benefits from readable XMLTV
    output regardless of the box's Python version.
    """
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            indent_xml(child, level + 1)
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def clean_text(text):
    """Decode HTML entities, collapse whitespace, strip stray leading dashes.
    Shared version of the cleanup helper duplicated (slightly differently)
    across the original SNRT/Arryadia/2M scripts."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\.\.+", ".", text)
    text = re.sub(r"^-+|^-?\s*-\s*", "", text).strip()
    return text


def atomic_write(path, content_bytes_or_str, encoding="utf-8"):
    """Write `content` to `path` atomically: write to a temp file in the
    same directory, fsync, then os.replace() over the destination. This
    guarantees a reader never sees a half-written file, and a crash mid
    write never corrupts the previous good file (the rename either fully
    happens or doesn't happen at all).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    mode = "wb" if isinstance(content_bytes_or_str, bytes) else "w"
    kwargs = {} if mode == "wb" else {"encoding": encoding}

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, mode, **kwargs) as f:
            f.write(content_bytes_or_str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def backup_previous(path):
    """Copy the current file at `path` to `path + '.bak'` before it gets
    replaced, so there is always exactly one last-known-good backup on
    disk, even if a later update produces zero programs."""
    if os.path.exists(path):
        try:
            import shutil
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def truncate(text, max_len):
    if text and len(text) > max_len:
        return text[:max_len].rsplit(" ", 1)[0] + "..."
    return text
