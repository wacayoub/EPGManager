# -*- coding: utf-8 -*-
"""Runtime prerequisite check for EPGManager universal Enigma2 packages.

This module is intentionally standalone so postinst can execute it before
Enigma2 restarts.  It never installs packages itself; opkg resolves declared
Depends/Recommends.  The check only proves that the resulting Python runtime
is usable and records diagnostics for support.
"""
from __future__ import print_function

import importlib
import json
import os
import platform
import sys
import time

HARD_MODULES = (
    "concurrent.futures",
    "datetime",
    "difflib",
    "email.utils",
    "gzip",
    "html",
    "json",
    "logging",
    "lzma",
    "ssl",
    "tempfile",
    "xml.etree.ElementTree",
    "requests",
)

OPTIONAL_MODULES = (
    "pytz",
    "zoneinfo",
    "lxml",
    "bs4",
    "cloudscraper",
    "twisted.internet",
)


def _can_import(name):
    try:
        importlib.import_module(name)
        return True, ""
    except Exception as exc:
        return False, "%s: %s" % (exc.__class__.__name__, exc)


def run():
    hard = {}
    optional = {}
    for name in HARD_MODULES:
        ok, err = _can_import(name)
        hard[name] = {"ok": ok, "error": err}
    for name in OPTIONAL_MODULES:
        ok, err = _can_import(name)
        optional[name] = {"ok": ok, "error": err}

    # Correct Morocco timezone handling requires at least one real timezone
    # backend.  pytz is recommended; zoneinfo is the modern stdlib fallback.
    timezone_ok = bool(optional.get("pytz", {}).get("ok") or
                       optional.get("zoneinfo", {}).get("ok"))

    # EPGImport is platform functionality rather than a Python-PyPI package.
    epgimport_ok = False
    epgimport_location = ""
    e2_python = "/usr/lib/enigma2/python"
    if os.path.isdir(e2_python) and e2_python not in sys.path:
        sys.path.insert(0, e2_python)
    for mod in ("Plugins.Extensions.EPGImport.plugin",
                "Plugins.SystemPlugins.EPGImport.plugin"):
        ok, _err = _can_import(mod)
        if ok:
            epgimport_ok = True
            epgimport_location = mod
            break

    missing_hard = [name for name, state in hard.items() if not state["ok"]]
    if not timezone_ok:
        missing_hard.append("timezone_backend(pytz|zoneinfo)")
    report = {
        "version": 1,
        "timestamp": int(time.time()),
        "python": sys.version.split()[0],
        "machine": platform.machine() or "unknown",
        "hard": hard,
        "optional": optional,
        "timezone_backend_ok": timezone_ok,
        "epgimport_available": epgimport_ok,
        "epgimport_module": epgimport_location,
        "missing_hard": missing_hard,
        "ok": not missing_hard,
    }
    return report


def main():
    report = run()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    try:
        root = "/etc/epgmanager_epg"
        if os.path.isdir(root) or os.access("/etc", os.W_OK):
            if not os.path.isdir(root):
                os.makedirs(root)
            path = os.path.join(root, "dependency_status.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            os.replace(tmp, path)
    except Exception:
        pass
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
