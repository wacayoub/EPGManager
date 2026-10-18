#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remote receiver-only hardware fingerprint sent as SSH stdin. READ ONLY.

Never receives credentials, downloads, imports Enigma2 modules, writes any
receiver file, reads config/log/EPG contents or launches a subprocess.
Output is bounded JSON with hashes and presence flags only.
Compatible with Python 3.14 on Vu+ Zero 4K / OpenATV.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import platform
import sys

PLUGIN = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
EXTENSIONS = Path("/usr/lib/enigma2/python/Plugins/Extensions")
CONFIG = Path("/etc/enigma2")
KNOWN_BASIS = "2026.10.08-rc79.1"
PROTECTED = (
    "ui/channel_mapping.py",
    "core/direct_native_import.py",
    "core/epg_cycle.py",
    "core/github_direct_stream.py",
    "core/github_match_health.py",
    "ui/settings.py",
    "ui/theme.py",
)
KNOWN_RC79_SHA256 = {
    "ui/channel_mapping.py": "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002",
    "core/direct_native_import.py": "43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31",
    "core/epg_cycle.py": "f49e92ae9c18919554a010af1f9fb6e5ba4b6a8358c9652b575c652227200887",
    "core/github_direct_stream.py": "52180c29f691e2764fd63d14906bcf851ed72c8447c6b07c4a2f272ec0b98686",
    "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
    "ui/settings.py": "ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa",
    "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
}
FEATURES = {
    "plugin.py": ("refresh_native_import_scheduler", "WHERE_AUTOSTART"),
    "ui/channel_mapping.py": ("def _auto_batch_match(", "def open_manual_source_override(",
                               "def undo_last_mapping(", "def _queue_github_best_match_health(",
                               "def _poll_github_best_match_health("),
    "core/direct_native_import.py": ("def _merge_persisted_auto_overrides(",),
    "core/epg_cycle.py": ("clear_before_import=False",),
}


def safe_file(base: Path, rel: str):
    """Permit only bounded, non-symlink .py source under installed plugin."""
    if base.is_symlink() or not base.is_dir():
        return None
    f = base / rel
    if f.is_symlink() or not f.is_file() or f.stat().st_size > 4 * 1024 * 1024:
        return None
    return f.read_bytes()


def module_version(source: bytes | None):
    if not source:
        return {"version": None, "schema": None}
    out = {"version": None, "schema": None}
    try:
        tree = ast.parse(source.decode("utf-8"))
        for statement in tree.body:
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)):
                key = statement.targets[0].id
                if key == "__version__":
                    out["version"] = str(ast.literal_eval(statement.value))
                elif key == "CONFIG_SCHEMA_VERSION":
                    out["schema"] = int(ast.literal_eval(statement.value))
    except (SyntaxError, UnicodeError, ValueError, TypeError):
        pass
    return out


def enigma_running(proc=Path("/proc")):
    """Read process *name* only, not command line, environment or arguments."""
    try:
        for directory in proc.iterdir():
            if directory.name.isdigit():
                name = directory / "comm"
                if name.is_file() and (name.read_text(errors="replace").strip() in
                                       ("enigma2", "enigma2.bin")):
                    return True
    except (OSError, PermissionError):
        return None
    return False


def observe(plugin=PLUGIN, extensions=EXTENSIONS, config=CONFIG, proc=Path("/proc")):
    plugin = Path(plugin)
    extensions = Path(extensions)
    config = Path(config)
    info = module_version(safe_file(plugin, "version.py"))
    hashes = {}
    for name in PROTECTED:
        content = safe_file(plugin, name)
        digest = hashlib.sha256(content).hexdigest() if content is not None else None
        hashes[name] = {
            "present": content is not None,
            "sha256": digest,
            "matches_known_rc79_basis": digest == KNOWN_RC79_SHA256[name],
        }
    markers = {}
    for name, strings in FEATURES.items():
        content = safe_file(plugin, name)
        text = content.decode("utf-8", errors="replace") if content is not None else ""
        markers[name] = {
            "available": content is not None,
            "markers": {symbol: symbol in text for symbol in strings},
        }
    try:
        module_count = sum(1 for path in plugin.rglob("*.py")
                           if path.is_file() and not path.is_symlink()) if plugin.is_dir() else 0
    except (OSError, PermissionError):
        module_count = None
    result = {
        "audit": "EPGManager Codex SSH receiver fingerprint",
        "mode": "READ_ONLY",
        "receiver_modified": False,
        "authorized_to_install_rc80": False,
        "receiver_gui_functionally_tested": False,
        "native_epg_import_functionally_tested": False,
        "mappings_read": False,
        "settings_read": False,
        "source_xmltv_read": False,
        "logs_read": False,
        "passwords_tokens_read": False,
        "baseline_version_reference": KNOWN_BASIS,
        "runtime_python": platform.python_version(),
        "plugin_directory_present": plugin.is_dir() and not plugin.is_symlink(),
        "installed_version": info["version"],
        "installed_schema": info["schema"],
        "installed_python_modules": module_count,
        "epgimport_extension_present": (extensions / "EPGImport").is_dir(),
        "enigma_process_detected": enigma_running(Path(proc)),
        "mappings_file_exists_only": (config / "epgmanager_mappings.json").is_file(),
        "source_preferences_file_exists_only": (
            config / "epgmanager_source_preferences.json").is_file(),
        "protected_module_hashes": hashes,
        "functional_code_markers_not_hardware_proof": markers,
    }
    result["status"] = (
        "NO_EPGMANAGER_PLUGIN" if not result["plugin_directory_present"]
        else "INSTALLED_FINGERPRINT_OBSERVED_NOT_FUNCTIONALLY_TESTED"
    )
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(observe(), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({
            "audit": "EPGManager Codex SSH receiver fingerprint",
            "mode": "READ_ONLY",
            "receiver_modified": False,
            "authorized_to_install_rc80": False,
            "status": "INCOMPLETE",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        sys.exit(2)
