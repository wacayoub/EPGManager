#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only EPGManager receiver compatibility fingerprint.

NO configuration reads, secret extraction, EPG/source inspection, network calls,
module imports, writes to Enigma2, subprocesses, opkg, restart, or installation.
Prints only file hashes, sizes, feature flags, and Python runtime metadata.
"""
from __future__ import print_function
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import sys

DEFAULT_PLUGIN_DIR = "/usr/lib/enigma2/python/Plugins/Extensions/EPGManager"
FILES = (
    "plugin.py", "version.py", "online_update.py",
    "ui/main.py", "ui/channel_mapping.py", "ui/settings.py", "ui/theme.py",
    "core/epg_cycle.py", "core/direct_native_import.py",
    "core/manager.py", "core/mapping_store.py", "core/native_epgimport_bridge.py",
    "core/native_importer.py", "core/source_preferences.py",
    "core/source_readiness.py", "core/srp_channel_map.py",
    "core/github_direct_stream.py", "core/github_match_health.py",
)
FEATURES = {
    "plugin.py": {
        "native_scheduler": "refresh_native_import_scheduler",
        "autostart": "WHERE_AUTOSTART",
    },
    "ui/channel_mapping.py": {
        "full_auto_batch": "def _auto_batch_match(",
        "cross_source_suggestions": "def _refresh_cross_source_suggestions(",
        "manual_override": "def open_manual_source_override(",
        "undo_last_mapping": "def undo_last_mapping(",
        "best_epg_health": "github_match_health.health(",
    },
    "ui/main.py": {
        "direct_import_screen": "def open_native_import(",
        "unified_diagnostics": "def open_diagnostics(",
    },
    "core/direct_native_import.py": {
        "auto_owner_persistence": "def _merge_persisted_auto_overrides(",
        "auto_owner_second_pass": "persisted_auto_keys = set()",
        "native_github_direct_stream": "EPG-Scrapers XMLTV: stream the HTTPS GitHub feed",
    },
    "core/epg_cycle.py": {
        "github_direct_health_check": "github_direct_stream.check_selected_sources(selected)",
        "non_destructive_import": "clear_before_import=False,",
    },
    "online_update.py": {
        "background_worker": "threading.Thread(",
        "mandatory_sha256": "Missing or invalid release SHA256",
        "no_force_reinstall": "Reinstall locked",
    },
}
CONF_NAMES = (
    "epgmanager_mappings.json",
    "epgmanager_source_preferences.json",
)


def file_info(base, rel):
    path = base / rel
    if not path.is_file() or path.is_symlink():
        return {"exists": False, "sha256": None, "bytes": None}
    content = path.read_bytes()
    return {
        "exists": True,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def audit(plugin_dir, config_dir):
    base = Path(plugin_dir)
    if not base.is_dir():
        return {
            "audit": "EPGManager receiver compatibility fingerprint",
            "ok": False,
            "error": "Plugin directory not found",
        }
    installed_files = {}
    flags = {}
    for rel in FILES:
        installed_files[rel] = file_info(base, rel)
        markers = FEATURES.get(rel, {})
        if markers and installed_files[rel]["exists"]:
            content = (base / rel).read_text(encoding="utf-8", errors="replace")
            flags[rel] = {name: token in content for name, token in markers.items()}
        elif markers:
            flags[rel] = {name: False for name in markers}
    text = (base / "version.py").read_text(encoding="utf-8", errors="replace") if (base / "version.py").is_file() else ""
    def literal(name):
        match = re.search(r"(?m)^" + re.escape(name) + r"\s*=\s*['\"]([^'\"]+)['\"]", text)
        return match.group(1) if match else None
    schema = re.search(r"(?m)^CONFIG_SCHEMA_VERSION\s*=\s*(\d+)", text)
    cfg_dir = Path(config_dir)
    config = {name: (cfg_dir / name).is_file() for name in CONF_NAMES}
    return {
        "audit": "EPGManager receiver compatibility fingerprint",
        "ok": True,
        "read_only": True,
        "collected_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python_version": platform.python_version(),
        "receiver_plugin_version": literal("__version__"),
        "receiver_plugin_build": literal("__build__"),
        "config_schema_version": int(schema.group(1)) if schema else None,
        "total_python_modules": sum(1 for x in base.rglob("*.py") if x.is_file() and not x.is_symlink()),
        "files": installed_files,
        "features": flags,
        "config_file_exists_only": config,
        "sensitive_data_included": False,
    }


def main():
    parser = argparse.ArgumentParser(description="EPGManager read-only receiver diagnostic")
    parser.add_argument("--plugin-dir", default=DEFAULT_PLUGIN_DIR)
    parser.add_argument("--config-dir", default="/etc/enigma2")
    options = parser.parse_args()
    try:
        result = audit(options.plugin_dir, options.config_dir)
    except Exception as exc:
        result = {"audit": "EPGManager receiver compatibility fingerprint", "ok": False,
                  "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
