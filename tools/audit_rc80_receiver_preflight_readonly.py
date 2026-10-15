#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only rc80 readiness on the Vu+; no EPG data, passwords or settings read.

Never downloads, modifies configuration, installs IPKs, runs opkg update or
restarts Enigma2. It is intentionally NOT an installation command.
"""
from __future__ import print_function
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PLUGIN = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
BACKUP = Path("/etc/epgmanager_epg/backups")
PROTECTED = {
    "ui/channel_mapping.py": "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002",
    "core/direct_native_import.py": "43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31",
    "core/epg_cycle.py": "f49e92ae9c18919554a010af1f9fb6e5ba4b6a8358c9652b575c652227200887",
    "core/github_direct_stream.py": "52180c29f691e2764fd63d14906bcf851ed72c8447c6b07c4a2f272ec0b98686",
    "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
    "ui/settings.py": "ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa",
    "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
}


def nearest_existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def free_megabytes(path):
    base = nearest_existing(path)
    try:
        return round(float(shutil.disk_usage(str(base)).free) / (1024 * 1024), 1)
    except OSError:
        return None


def sha256(path):
    if not path.is_file() or path.is_symlink():
        return None
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def package_db():
    exe = shutil.which("opkg")
    if not exe:
        return {"found": False, "package_status": "unknown"}
    try:
        result = subprocess.run(
            [exe, "status", "enigma2-plugin-extensions-epgmanager"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            universal_newlines=True, timeout=7)
    except (OSError, subprocess.TimeoutExpired):
        return {"found": True, "package_status": "unreadable"}
    fields = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in ("Package", "Version", "Status", "Architecture"):
            fields[key.lower()] = value.strip()[:140]
    return {"found": True, "package_status": fields or "missing"}


def audit(plugin=PLUGIN, backup=BACKUP):
    plugin = Path(plugin)
    checked = {}
    for name, digest in PROTECTED.items():
        checked[name] = sha256(plugin / name) == digest
    source = (plugin / "version.py").read_text(encoding="utf-8", errors="replace") if (plugin / "version.py").is_file() else ""
    version = re.search(r"(?m)^__version__\s*=\s*['\"]([^'\"]+)", source)
    schema = re.search(r"(?m)^CONFIG_SCHEMA_VERSION\s*=\s*(\d+)", source)
    current_version = version.group(1) if version else "unknown"
    db = package_db()
    db_details = db.get("package_status")
    opkg_version = db_details.get("version") if isinstance(db_details, dict) else None
    warning = []
    if current_version != "2026.10.08-rc79.1":
        warning.append("Installed app version differs from audited source")
    if opkg_version and opkg_version != current_version:
        warning.append("opkg package version differs from Python plugin version")
    if not all(checked.values()):
        warning.append("A protected receiver module changed after the previous audit")
    plugin_mb = free_megabytes(plugin.parent)
    backup_mb = free_megabytes(backup)
    tmp_mb = free_megabytes("/tmp")
    if backup_mb is None or backup_mb < 6:
        warning.append("Too little or unknown free space for protected backups")
    if tmp_mb is None or tmp_mb < 10:
        warning.append("Too little or unknown temporary download space")
    if sys.version_info[:2] != (3, 14):
        warning.append("Receiver Python differs from targeted Python 3.14")
    if not all(shutil.which(x) for x in ("opkg", "tar", "python3")):
        warning.append("Required local package/backup utility missing")
    result = {
        "check": "EPGManager rc80 receiver dry-run preflight",
        "read_only": True, "receiver_modified": False,
        "installed_python_version": ".".join(map(str, sys.version_info[:3])),
        "installed_plugin_version": current_version,
        "installed_config_schema": int(schema.group(1)) if schema else None,
        "installed_python_modules": sum(1 for f in plugin.rglob("*.py") if f.is_file()) if plugin.exists() else 0,
        "protected_receiver_files_identical": checked,
        "free_space_mib": {"plugin_partition": plugin_mb, "backup_partition": backup_mb, "tmp": tmp_mb},
        "opkg_local": db,
        "config_files_exist_only": {
            "mappings": Path("/etc/enigma2/epgmanager_mappings.json").exists(),
            "source_preferences": Path("/etc/enigma2/epgmanager_source_preferences.json").exists(),
        },
        "preflight_warnings": warning,
        "environment_preflight_pass": not bool(warning) and current_version == "2026.10.08-rc79.1",
        "authorized_to_install": False,
        "reason": "Real GUI compatibility and automatic rollback after opkg errors are not proven.",
    }
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(audit(), indent=2, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"read_only": True, "environment_preflight_pass": False,
                          "authorized_to_install": False, "error_type": type(exc).__name__}))
        raise SystemExit(1)
