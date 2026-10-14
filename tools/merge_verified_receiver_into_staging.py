#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge *verified working receiver code* into a non-installable rc78 staging tree.

Requires an explicit local --receiver-archive; NEVER fetches or publishes
receiver code, configuration, passwords, EPG XMLTV or user mappings.
This must run only AFTER assemble_rc78_staging.py. It does not call opkg.
"""
from __future__ import print_function
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

VERSION = "2026.10.14-rc80-beta1"
OLD = "2026.10.08-rc79.1"
REL = Path("usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
RECEIVER_SHA256 = {
    "ui/channel_mapping.py": "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002",
    "core/direct_native_import.py": "43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31",
    "core/epg_cycle.py": "f49e92ae9c18919554a010af1f9fb6e5ba4b6a8358c9652b575c652227200887",
    "core/github_direct_stream.py": "52180c29f691e2764fd63d14906bcf851ed72c8447c6b07c4a2f272ec0b98686",
    "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
    "ui/settings.py": "ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa",
    "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
    "version.py": "652b7e5b0ea4899dd44e671a62fa948e9771c56a29425de3d355851687e0cea4",
}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def unpack_verified_receiver(archive_path, known=RECEIVER_SHA256):
    data = Path(archive_path).read_bytes()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Receiver export archive exceeds size cap")
    required = {"EPGManager/" + x for x in known}
    required.add("EPGManager/MANIFEST.json")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) != len(required) or {m.name for m in members} != required:
            raise ValueError("Unexpected archive members (requires exactly 8 code files + manifest)")
        if any(not m.isfile() or m.issym() or m.islnk() for m in members):
            raise ValueError("Unexpected symlink or non-file member")
        manifest = json.load(tar.extractfile("EPGManager/MANIFEST.json"))
        received = {}
        for name, expected in known.items():
            raw = tar.extractfile("EPGManager/" + name).read()
            if len(raw) > 3 * 1024 * 1024 or sha(raw) != expected:
                raise ValueError("Receiver code mismatch: " + name)
            info = manifest.get("files", {}).get(name, {})
            if info.get("sha256") != expected or info.get("bytes") != len(raw):
                raise ValueError("Export manifest mismatch: " + name)
            ast.parse(raw.decode("utf-8"), filename=name)
            received[name] = raw
    version = received["version.py"].decode("utf-8")
    if '__version__ = "' + OLD + '"' not in version or "CONFIG_SCHEMA_VERSION = 15" not in version:
        raise ValueError("Receiver runtime version/schema is not the audited rc79.1")
    return received

def overlay_working_receiver(unpacked, archive_path, expected=RECEIVER_SHA256):
    root = Path(unpacked).resolve()
    plugin = root / REL
    # Operate on an extracted staging package, never on the actual installation.
    if str(root).startswith("/usr/lib/enigma2/") or str(root).startswith("/etc/enigma2/"):
        raise ValueError("Live receiver installation path is forbidden")
    if not plugin.is_dir():
        raise ValueError("Staging package directory missing")
    version_file = plugin / "version.py"
    current = version_file.read_text(encoding="utf-8")
    if '__version__ = "2026.10.14-rc79-beta1"' not in current or "CONFIG_SCHEMA_VERSION = 15" not in current:
        raise ValueError("First run assemble_rc78_staging.py to prepare the verified baseline")
    installed_before = len(list(plugin.rglob("*.py")))
    if installed_before != 136:
        raise ValueError("Expected 136 modules in rc78 staging, got %s" % installed_before)
    received = unpack_verified_receiver(archive_path, known=expected)
    mapping = received["ui/channel_mapping.py"].decode("utf-8")
    native = received["core/direct_native_import.py"].decode("utf-8")
    cycle = received["core/epg_cycle.py"].decode("utf-8")
    for marker in ("def review_next_suggestion(", "def review_next_conflict(", "github_match_health.health("):
        if marker not in mapping:
            raise ValueError("Working receiver mapping feature missing: " + marker)
    for marker in ("def _merge_persisted_auto_overrides(", "persisted_auto_keys = set()"):
        if marker not in native:
            raise ValueError("Working AUTO owner fix missing: " + marker)
    for marker in ("github_direct_stream.check_selected_sources(selected)", "clear_before_import=False,"):
        if marker not in cycle:
            raise ValueError("GitHub Direct / safe import runtime integration missing")
    if "def open_diagnostics(" not in (plugin / "ui/main.py").read_text(encoding="utf-8"):
        raise ValueError("New dashboard not assembled")
    if "Reinstall locked" not in (plugin / "online_update.py").read_text(encoding="utf-8"):
        raise ValueError("Safe updater not assembled")
    for rel, raw in received.items():
        if rel == "version.py":
            continue
        path = plugin / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    receiver_version = received["version.py"].decode("utf-8")
    updated_version = receiver_version.replace('__version__ = "' + OLD + '"', '__version__ = "' + VERSION + '"')
    updated_version = re.sub(r'(?m)^__build__\s*=.*$', '__build__ = "receiver-parity-staging-not-published"', updated_version)
    updated_version = re.sub(r'(?m)^RELEASE_CHANNEL\s*=.*$', 'RELEASE_CHANNEL = "beta"', updated_version)
    version_file.write_text(updated_version, encoding="utf-8")
    count = len(list(plugin.rglob("*.py")))
    if count != 138:
        raise ValueError("Runtime module count mismatch after receiver merge: " + str(count))
    for name, digest in expected.items():
        if name != "version.py" and sha((plugin / name).read_bytes()) != digest:
            raise ValueError("Working receiver fix was lost: " + name)
    for path in plugin.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        "status": "REVIEW ONLY - DO NOT INSTALL",
        "receiver_version": OLD, "staging_version": VERSION,
        "python_modules": count, "schema_version": 15,
        "protected_receiver_modules_exact": 7,
        "new_gui_and_updater_retained": True,
        "epg_source_code_modified": False,
        "receiver_hotfixes_preserved": True,
        "safe_to_install": False,
        "hardware_verified": False,
        "automatic_rollback_verified": False,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unpacked", required=True)
    ap.add_argument("--receiver-archive", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    result = overlay_working_receiver(args.unpacked, args.receiver_archive)
    Path(args.report).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
