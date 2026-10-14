#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RC80 stage 2: read-only receiver observation, NOT runtime validation.

Never import an Enigma2 module; never read user settings, mappings, tokens,
EPG XML, log contents, process arguments or environment; never install or write.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

PLUGIN = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
EXTENSIONS = Path("/usr/lib/enigma2/python/Plugins/Extensions")
EPGIMPORT_CONFIG = Path("/etc/epgimport")
PROC = Path("/proc")
RESCUE_SHA = "117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69"
ORIGINAL_SHA = "d71bb50962b2ab113f4e7eaa7a28af038e9193a5a2a39a6fca9dc9187d0d4818"
REQUIRED = (
    "plugin.py", "version.py", "ui/main.py", "ui/channel_mapping.py",
    "ui/settings.py", "ui/theme.py", "core/epg_cycle.py",
    "core/direct_native_import.py", "core/github_direct_stream.py",
    "core/github_match_health.py", "core/native_epgimport_bridge.py",
    "core/source_readiness.py", "core/source_preferences.py",
)
MARKERS = {
    "plugin.py": {"scheduler": "refresh_native_import_scheduler", "autostart": "WHERE_AUTOSTART"},
    "ui/channel_mapping.py": {
        "mapping_batch": "def _auto_batch_match(",
        "mapping_manual": "def open_manual_source_override(",
        "cross_source_suggestions": "def _refresh_cross_source_suggestions(",
    },
    "ui/main.py": {"diagnostics": "def open_diagnostics(", "native_import": "def open_native_import("},
    "core/epg_cycle.py": {
        "github_direct_check": "github_direct_stream.check_selected_sources(selected)",
        "non_destructive_import": "clear_before_import=False,",
    },
    "core/direct_native_import.py": {"saved_auto_owner": "def _merge_persisted_auto_overrides("},
}


def examine_plugin(plugin):
    plugin = Path(plugin)
    if not plugin.is_dir() or plugin.is_symlink():
        return {"present": False, "modules": 0, "syntax_errors": ["PLUGIN_MISSING"],
                "missing_required": list(REQUIRED), "markers": {}}
    modules = sorted(plugin.rglob("*.py"))
    errors, markers = [], {}
    for path in modules:
        rel = path.relative_to(plugin).as_posix()
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("unsafe file")
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=rel)
            if rel in MARKERS:
                markers[rel] = {key: token in source for key, token in MARKERS[rel].items()}
        except (OSError, ValueError, UnicodeError, SyntaxError):
            errors.append(rel + ":UNREADABLE_OR_BAD_SYNTAX")
    version, schema = "unknown", None
    try:
        source = (plugin / "version.py").read_text(encoding="utf-8")
        version_match = re.search(r'(?m)^__version__\s*=\s*["\x27]([^"\x27]+)', source)
        schema_match = re.search(r'(?m)^CONFIG_SCHEMA_VERSION\s*=\s*(\d+)', source)
        if version_match:
            version = version_match.group(1)
        if schema_match:
            schema = int(schema_match.group(1))
    except (OSError, UnicodeError):
        pass
    return {
        "present": True, "modules": len(modules), "version": version, "schema": schema,
        "syntax_errors": errors[:25], "missing_required": [x for x in REQUIRED if not (plugin / x).is_file()],
        "markers": markers,
    }


def find_gui(proc):
    """Only read process comm names; never inspect argv, environ or sockets."""
    proc = Path(proc)
    if not proc.is_dir():
        return {"observed": False, "running": None, "count": 0}
    count = 0
    try:
        for item in proc.iterdir():
            if not item.name.isdigit():
                continue
            try:
                command = (item / "comm").read_text(encoding="ascii", errors="replace")[:64].strip()
            except OSError:
                continue
            if command in ("enigma2", "enigma2.sh"):
                count += 1
    except OSError:
        return {"observed": False, "running": None, "count": 0}
    return {"observed": True, "running": count > 0, "count": count}


def rescue_check(ipk):
    # SHA verification is identity only, NEVER successful opkg recovery.
    if not ipk:
        return {"provided": False, "known_identity": False, "opkg_recovery_certified": False}
    path = Path(ipk)
    if not path.is_file() or path.is_symlink() or not 50 <= path.stat().st_size <= 30 * 1024 * 1024:
        return {"provided": True, "known_identity": False, "opkg_recovery_certified": False}
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    return {"provided": True, "known_identity": value in (RESCUE_SHA, ORIGINAL_SHA),
            "identified_as": "rescue_snapshot" if value == RESCUE_SHA else
                "original_candidate" if value == ORIGINAL_SHA else "unknown",
            "opkg_recovery_certified": False}


def audit(plugin=PLUGIN, extensions=EXTENSIONS, epgimport=EPGIMPORT_CONFIG,
          proc=PROC, rollback_ipk=None):
    code = examine_plugin(plugin)
    gui = find_gui(proc)
    native = {
        "epgimport_plugin_present": (Path(extensions) / "EPGImport").is_dir(),
        "epgimport_config_directory_present": Path(epgimport).is_dir(),
    }
    rescue = rescue_check(rollback_ipk)
    flags = []
    if code["modules"] != 138: flags.append("MODULE_COUNT_NOT_138")
    if code.get("syntax_errors"): flags.append("MODULE_SYNTAX_OR_IO_ERROR")
    if code.get("missing_required"): flags.append("MISSING_REQUIRED_MODULES")
    if code.get("version") != "2026.10.08-rc79.1" or code.get("schema") != 15:
        flags.append("UNEXPECTED_VERSION_OR_SCHEMA")
    for filename, tests in code.get("markers", {}).items():
        if not all(tests.values()): flags.append("STATIC_FEATURE_MISSING:" + filename)
    if not gui.get("running"): flags.append("ENIGMA2_PROCESS_NOT_CONFIRMED")
    if not native["epgimport_plugin_present"]: flags.append("NATIVE_EPGIMPORT_NOT_CONFIRMED")
    if not rescue["known_identity"]: flags.append("ROLLBACK_IPK_NOT_VERIFIED")
    return {
        "audit": "EPGManager rc80 stage2 receiver observation",
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True, "receiver_modified": False,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "code": code, "gui_process": gui, "native_import_prerequisites": native,
        "rollback_package": rescue, "observations": flags,
        "gui_functionally_tested": False, "epg_import_executed": False,
        "opkg_rollback_tested": False, "authorized_to_install": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", default=str(PLUGIN))
    parser.add_argument("--extensions-dir", default=str(EXTENSIONS))
    parser.add_argument("--epgimport-dir", default=str(EPGIMPORT_CONFIG))
    parser.add_argument("--proc-dir", default=str(PROC))
    parser.add_argument("--rollback-ipk", help="Optional local existing rc79.1 rescue IPK; checksum only")
    a = parser.parse_args()
    try:
        result = audit(a.plugin_dir, a.extensions_dir, a.epgimport_dir, a.proc_dir, a.rollback_ipk)
    except (OSError, ValueError) as exc:
        result = {"audit": "EPGManager rc80 stage2 receiver observation", "read_only": True,
                  "authorized_to_install": False, "receiver_modified": False,
                  "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("code", {}).get("present") else 1


if __name__ == "__main__":
    sys.exit(main())
