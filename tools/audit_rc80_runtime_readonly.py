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
# New dashboard methods are *expected absent* from the working RC79.1.
# Do not mistake an uninstalled RC80 feature for a regression in RC79.1.
RC80_ONLY_MARKERS = {"ui/main.py": ("diagnostics",)}
LEGACY_EXPECTED_VERSION = "2026.10.08-rc79.1"
ROLLBACK_SCAN_DIRS = ("/tmp", "/var/volatile/tmp", "/media/hdd", "/media/usb")
ROLLBACK_NAMES = (
    "EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk",
    "EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk",
)


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


def scan_known_rollback(directories=ROLLBACK_SCAN_DIRS):
    """Check only specifically named local rc79.1 IPKs; never traverse folders.

    An IPK's known hash establishes identity, not operable rollback with opkg.
    Do not print arbitrary unknown filenames, device configuration or secrets.
    """
    examined = 0
    verified = []
    for directory in directories:
        folder = Path(directory)
        if not folder.is_dir() or folder.is_symlink():
            continue
        for basename in ROLLBACK_NAMES:
            candidate = folder / basename
            if not candidate.is_file() or candidate.is_symlink():
                continue
            examined += 1
            report = rescue_check(candidate)
            if report.get("known_identity"):
                verified.append({"path": str(candidate),
                                 "identified_as": report.get("identified_as"),
                                 "opkg_recovery_certified": False})
    return {
        "scan_performed": True,
        "scan_scope": "four fixed directories, two known rc79.1 basenames, no recursion",
        "candidate_count": examined, "recognized_candidates": verified,
        "known_identity_found": bool(verified),
        "opkg_recovery_certified": False,
    }


def audit(plugin=PLUGIN, extensions=EXTENSIONS, epgimport=EPGIMPORT_CONFIG,
          proc=PROC, rollback_ipk=None, scan_local_rollback=False,
          rollback_directories=ROLLBACK_SCAN_DIRS):
    code = examine_plugin(plugin)
    gui = find_gui(proc)
    native = {
        "epgimport_plugin_present": (Path(extensions) / "EPGImport").is_dir(),
        "epgimport_config_directory_present": Path(epgimport).is_dir(),
    }
    rescue = rescue_check(rollback_ipk)
    scan = scan_known_rollback(rollback_directories) if scan_local_rollback else {
        "scan_performed": False, "known_identity_found": False,
        "opkg_recovery_certified": False,
    }
    flags = []
    expected_rc80_additions = []
    if code["modules"] != 138: flags.append("MODULE_COUNT_NOT_138")
    if code.get("syntax_errors"): flags.append("MODULE_SYNTAX_OR_IO_ERROR")
    if code.get("missing_required"): flags.append("MISSING_REQUIRED_MODULES")
    if code.get("version") != "2026.10.08-rc79.1" or code.get("schema") != 15:
        flags.append("UNEXPECTED_VERSION_OR_SCHEMA")
    for filename, tests in code.get("markers", {}).items():
        for marker, present in tests.items():
            if present:
                continue
            if (code.get("version") == LEGACY_EXPECTED_VERSION and
                    marker in RC80_ONLY_MARKERS.get(filename, ())):
                expected_rc80_additions.append(filename + ":" + marker)
            else:
                flags.append("STATIC_FEATURE_MISSING:" + filename + ":" + marker)
    if not gui.get("running"): flags.append("ENIGMA2_PROCESS_NOT_CONFIRMED")
    if not native["epgimport_plugin_present"]: flags.append("NATIVE_EPGIMPORT_NOT_CONFIRMED")
    if not rescue["known_identity"] and not scan["known_identity_found"]:
        flags.append("ROLLBACK_IPK_NOT_VERIFIED")
    return {
        "audit": "EPGManager rc80 stage2 receiver observation",
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True, "receiver_modified": False,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "code": code, "gui_process": gui, "native_import_prerequisites": native,
        "rollback_package": rescue, "rollback_scan": scan, "observations": flags,
        "expected_rc80_features_not_in_rc79": expected_rc80_additions,
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
    parser.add_argument("--scan-local-rollback", action="store_true",
                        help="SHA-check two known rc79.1 IPK names in four fixed locations, read-only")
    a = parser.parse_args()
    try:
        result = audit(a.plugin_dir, a.extensions_dir, a.epgimport_dir, a.proc_dir,
                       a.rollback_ipk, a.scan_local_rollback)
    except (OSError, ValueError) as exc:
        result = {"audit": "EPGManager rc80 stage2 receiver observation", "read_only": True,
                  "authorized_to_install": False, "receiver_modified": False,
                  "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("code", {}).get("present") else 1


if __name__ == "__main__":
    sys.exit(main())
