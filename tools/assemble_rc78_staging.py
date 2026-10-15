#!/usr/bin/env python3
"""Assemble a NON-PUBLISHED rc78-based staging tree from the official IPK.

Execute only after verifying the exact rc78 release SHA256. Applies a small
allowlist of application modules; never copies source/overlay/plugin.py or
source/overlay/version.py (both are from rc70 and would regress rc78).
No receiver installation and no data feed changes.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_REL = "usr/lib/enigma2/python/Plugins/Extensions/EPGManager"
VERSION = "2026.10.14-rc79-beta1"
OVERLAYS = {
    "ui/channel_mapping.py": "source/overlay/ui/channel_mapping.py",
    "ui/main.py": "source/overlay/ui/main.py",
    "online_update.py": "source/overlay/online_update.py",
    "core/direct_native_import.py": "data/release-inspect/direct_native_import.py",
}
BASELINE_MUST_MATCH = {
    "ui/channel_mapping.py": "data/release-inspect/channel_mapping.py",
    "ui/main.py": "data/release-inspect/main.py",
    "core/epg_cycle.py": "data/release-inspect/epg_cycle.py",
    "plugin.py": "data/release-inspect/plugin.py",
    "version.py": "data/release-inspect/version.py",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unpacked", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    root = Path(args.unpacked)
    plugin = root / PLUGIN_REL
    if not plugin.is_dir():
        raise RuntimeError("rc78 plugin root missing")
    checks = {}
    for path, reference in BASELINE_MUST_MATCH.items():
        got = (plugin / path).read_bytes()
        ref = (ROOT / reference).read_bytes()
        checks[path] = {"rc78_snapshot_identical": digest(got) == digest(ref)}
        if got != ref:
            raise RuntimeError("Expected verified rc78 baseline differs: " + path)

    original = (plugin / "version.py").read_text(encoding="utf-8")
    if 'CONFIG_SCHEMA_VERSION = 15' not in original:
        raise RuntimeError("Refusing to downgrade configuration schema")
    if '__version__ = "2026.10.04-rc78"' not in original:
        raise RuntimeError("Unexpected base version")

    modified = {}
    for target, source in OVERLAYS.items():
        src = ROOT / source
        dest = plugin / target
        if not src.is_file() or not dest.is_file():
            raise RuntimeError("Missing runtime module for " + target)
        payload = src.read_bytes()
        ast.parse(payload.decode("utf-8"), filename=str(src))
        prev = dest.read_bytes()
        dest.write_bytes(payload)
        modified[target] = {
            "before_sha256": digest(prev),
            "after_sha256": digest(payload),
            "changed": payload != prev,
            "bytes": len(payload),
        }
    native = (plugin / "core/direct_native_import.py").read_text(encoding="utf-8")
    for symbol in ("def _merge_persisted_auto_overrides(", "persisted_auto_keys = set()"):
        if symbol not in native:
            raise RuntimeError("Missing AUTO ownership repair: " + symbol)
    mapping = (plugin / "ui/channel_mapping.py").read_text(encoding="utf-8")
    for symbol in ("def _auto_batch_match(", "def _refresh_cross_source_suggestions(",
                   "def open_manual_source_override(", "def undo_last_mapping("):
        if symbol not in mapping:
            raise RuntimeError("Full Smart Mapping is incomplete: " + symbol)
    dashboard = (plugin / "ui/main.py").read_text(encoding="utf-8")
    if 'self.open_native_import, self.open_diagnostics' not in dashboard:
        raise RuntimeError("Dashboard did not retain direct import and Diagnostics")
    updater = (plugin / "online_update.py").read_text(encoding="utf-8")
    if "opkg install --force-reinstall" in updater or "killall" in updater:
        raise RuntimeError("Unsafe updater path found")

    patched = original.replace('__version__ = "2026.10.04-rc78"', '__version__ = "' + VERSION + '"')
    patched = re.sub(r'(?m)^__build__\s*=.*$', '__build__ = "plugin-staging-not-published"', patched)
    patched = re.sub(r'(?m)^RELEASE_CHANNEL\s*=.*$', 'RELEASE_CHANNEL = "beta"', patched)
    (plugin / "version.py").write_text(patched, encoding="utf-8")
    if 'CONFIG_SCHEMA_VERSION = 15' not in patched:
        raise RuntimeError("Schema lost during version stamp")
    # ALL runtime Python must parse, not just the files in the overlay.
    all_py = list(plugin.rglob("*.py"))
    for f in all_py:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    # Never overwrite rc78's real entrypoint with the obsolete rc70 overlay.
    if (plugin / "plugin.py").read_bytes() != (ROOT / "data/release-inspect/plugin.py").read_bytes():
        raise RuntimeError("rc78 plugin entrypoint was modified by staging")

    output = {
        "base_release": "2026.10.04-rc78",
        "staging_version": VERSION,
        "schema_version": 15,
        "baseline_checks": checks,
        "modified_modules": modified,
        "retained_rc78_entrypoint": True,
        "python_modules_syntax_checked": len(all_py),
        "source_provider_files_changed": False,
        "published": False,
        "receiver_tested": False,
        "safe_to_install": False,
        "blockers": [
            "No Vu+ Zero 4K test",
            "No automatic rollback",
            "epg_cycle.py GitHub Direct receiver hotfix not yet folded in",
            "Installed receiver-specific modules not compared",
        ],
    }
    Path(args.report).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("STAGING ONLY — DO NOT INSTALL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
