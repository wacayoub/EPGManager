# -*- coding: utf-8 -*-
"""
core/dependencies.py

Detects the runtime environment (Python version, architecture, Enigma2 image)
and the availability of third-party modules used by the various EPG sources.

This module NEVER calls `pip install` automatically. Enigma2 boxes frequently
have no pip, no compiler, and limited storage; a silent pip install can brick
an update cycle or fill up flash storage. Instead it:

  1. Reports exactly what's missing.
  2. Tells the operator which opkg/feed package (where known) provides it.
  3. Lets each source module degrade gracefully (see sources/base.py) when a
     "nice to have" dependency (e.g. cloudscraper, lxml) is absent, and only
     hard-fails when a dependency is truly required and has no fallback.

Nothing here raises on import - it is safe to import even on a system missing
every optional dependency.
"""

import importlib
import platform
import sys
import os

from .logger import get_logger

log = get_logger(__name__)

# module_name -> (opkg/feed package guess, is_hard_required, notes)
KNOWN_DEPENDENCIES = {
    "requests": (
        "python3-requests",
        True,
        "Used by every source for HTTP downloads. No stdlib fallback is "
        "attempted because urllib alone cannot cleanly cover all sources' "
        "session/retry/header needs.",
    ),
    "bs4": (
        "python3-beautifulsoup4",
        False,
        "Used by Medi1TV, Arryadia, beIN-fallback parsers. Falls "
        "back to a regex/ElementTree based mini-parser if absent (reduced "
        "accuracy but source keeps working).",
    ),
    "lxml": (
        "python3-lxml",
        False,
        "Used as the fast HTML/XML backend for SNRT, Arryadia, 2M/Chada. "
        "Falls back to Python's builtin html.parser / xml.etree if absent.",
    ),
    "pytz": (
        "python3-pytz",
        False,
        "Preferred Africa/Casablanca timezone backend. zoneinfo is accepted "
        "as an equally valid runtime fallback.",
    ),
    "zoneinfo": (
        "python3-zoneinfo",
        False,
        "Modern Python timezone backend. At least one of pytz or zoneinfo "
        "must be available for DST/Ramadan-safe Casablanca handling.",
    ),
    "cloudscraper": (
        "(not typically packaged - pip only)",
        False,
        "Only used by 2M/Chada and as an optional anti-bot fallback for "
        "SNRT/Arryadia. These sources still work with plain 'requests' "
        "for most pages; cloudscraper is only needed if Cloudflare-style "
        "JS challenges appear.",
    ),
}


def get_python_info():
    return {
        "version": sys.version.split()[0],
        "version_tuple": sys.version_info[:3],
        "implementation": platform.python_implementation(),
        "architecture": platform.machine() or "unknown",
    }


def get_image_info():
    """Best-effort detection of the Enigma2 image in use."""
    info = {"image": "unknown", "raw": None}
    for path in ("/etc/image-version", "/etc/enigma2-image-version"):
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    raw = f.read().strip()
                info["raw"] = raw
                break
            except Exception:
                pass

    # Distro id file used by OpenATV / OpenPLi / OpenViX / OpenBH etc.
    issue_path = "/etc/issue"
    label = None
    if os.path.exists(issue_path):
        try:
            with open(issue_path, "r") as f:
                label = f.read().strip()
        except Exception:
            pass

    text = " ".join(filter(None, [info["raw"], label])).lower()
    for name in ("openatv", "openpli", "openvix", "openbh", "egami", "vti"):
        if name in text:
            info["image"] = name
            break

    return info


def check_module(name):
    """Return True if `name` can be imported right now."""
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def scan():
    """Full dependency scan. Returns a structured report (dict)."""
    report = {
        "python": get_python_info(),
        "image": get_image_info(),
        "modules": {},
        "missing_hard": [],
        "missing_soft": [],
    }

    for mod_name, (pkg_hint, hard_required, notes) in KNOWN_DEPENDENCIES.items():
        available = check_module(mod_name)
        report["modules"][mod_name] = {
            "available": available,
            "package_hint": pkg_hint,
            "hard_required": hard_required,
            "notes": notes,
        }
        if not available:
            if hard_required:
                report["missing_hard"].append(mod_name)
            else:
                report["missing_soft"].append(mod_name)

    timezone_ok = bool(report["modules"].get("pytz", {}).get("available") or
                       report["modules"].get("zoneinfo", {}).get("available"))
    report["timezone_backend_ok"] = timezone_ok
    if not timezone_ok:
        report["missing_hard"].append("timezone_backend(pytz|zoneinfo)")

    if report["missing_hard"]:
        log.error(
            "Missing hard dependencies: %s. EPG Manager cannot run sources "
            "until these are installed via your image's package manager "
            "(opkg install <package>) since automatic pip install is "
            "intentionally disabled on Enigma2.",
            ", ".join(report["missing_hard"]),
        )
    if report["missing_soft"]:
        log.warning(
            "Missing optional dependencies (fallbacks will be used, reduced "
            "accuracy possible): %s",
            ", ".join(report["missing_soft"]),
        )

    return report


def has_lxml():
    return check_module("lxml")


def has_bs4():
    return check_module("bs4")


def has_cloudscraper():
    return check_module("cloudscraper")


def get_html_parser_name():
    """Best available BeautifulSoup parser backend name, for callers that
    build BeautifulSoup(html, get_html_parser_name())."""
    if has_lxml():
        return "lxml"
    return "html.parser"


if __name__ == "__main__":
    import json
    print(json.dumps(scan(), indent=2, default=str))
