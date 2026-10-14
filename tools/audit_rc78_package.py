#!/usr/bin/env python3
"""Read-only inventory of the exact published rc78 EPGManager IPK.

Does not create an IPK, extract files onto the host, modify update.json,
modify EPG source data, or install anything on an Enigma2 device.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import sys

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "usr/lib/enigma2/python/Plugins/Extensions/EPGManager/"
SNAPSHOTS = {
    "ui/channel_mapping.py": "data/release-inspect/channel_mapping.py",
    "ui/main.py": "data/release-inspect/main.py",
    "core/direct_native_import.py": "data/release-inspect/direct_native_import.py",
    "core/epg_cycle.py": "data/release-inspect/epg_cycle.py",
    "core/mapping_store.py": "data/release-inspect/mapping_store.py",
    "core/manager.py": "data/release-inspect/manager.py",
    "core/native_epgimport_bridge.py": "data/release-inspect/native_epgimport_bridge.py",
    "core/native_importer.py": "data/release-inspect/native_importer.py",
    "core/source_preferences.py": "data/release-inspect/source_preferences.py",
    "core/source_readiness.py": "data/release-inspect/source_readiness.py",
    "core/srp_channel_map.py": "data/release-inspect/srp_channel_map.py",
    "online_update.py": "data/release-inspect/online_update.py",
    "plugin.py": "data/release-inspect/plugin.py",
    "version.py": "data/release-inspect/version.py",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(member):
    name = member.name.removeprefix("./")
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts:
        raise ValueError("Unsafe tar entry: " + name)
    return name


def payload(ipk):
    names = subprocess.check_output(["ar", "t", str(ipk)], text=True).splitlines()
    items = [x for x in names if x.startswith("data.tar.")]
    if len(items) != 1:
        raise ValueError("Expected exactly one data.tar.* payload, got " + str(names))
    archive = subprocess.check_output(["ar", "p", str(ipk), items[0]])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as stream:
        result = {}
        for info in stream.getmembers():
            name = safe_name(info)
            if info.isfile() and name.startswith(PREFIX):
                result[name[len(PREFIX):]] = stream.extractfile(info).read()
        return result


def audit(ipk):
    manifest = json.loads((ROOT / "update.json").read_text(encoding="utf-8"))
    expected_sha = str(manifest.get("sha256", "")).lower()
    expected_size = int(manifest.get("size", 0))
    data = ipk.read_bytes()
    if manifest.get("version") != "2026.10.04-rc78":
        raise ValueError("Pinned baseline is no longer rc78; abort")
    if len(data) != expected_size or sha(data) != expected_sha:
        raise ValueError("Published IPK does not match update.json SHA256 / size")
    files = payload(ipk)
    if "plugin.py" not in files or "version.py" not in files:
        raise ValueError("Package lacks plugin.py or version.py")
    ver = files["version.py"].decode("utf-8", "replace")
    row = {
        "baseline": "official rc78 pinned and SHA256 verified",
        "package_name": ipk.name,
        "package_size": len(data),
        "package_sha256": sha(data),
        "plugin_python_count": len([p for p in files if p.endswith(".py")]),
        "plugin_file_count": len(files),
        "version_text": ver[:600],
        "snapshot_comparisons": {},
        "overlay_requirements": {},
    }
    for destination, snapshot in SNAPSHOTS.items():
        installed = files.get(destination)
        reference = ROOT / snapshot
        expected = reference.read_bytes() if reference.is_file() else None
        row["snapshot_comparisons"][destination] = {
            "in_rc78": installed is not None,
            "in_reference": expected is not None,
            "rc78_bytes": len(installed) if installed is not None else None,
            "reference_bytes": len(expected) if expected is not None else None,
            "identical": sha(installed) == sha(expected) if installed is not None and expected is not None else None,
        }
    for rel, fullpath in [
        ("ui/channel_mapping.py", "source/overlay/ui/channel_mapping.py"),
        ("ui/main.py", "source/overlay/ui/main.py"),
        ("online_update.py", "source/overlay/online_update.py"),
    ]:
        candidate = ROOT / fullpath
        row["overlay_requirements"][rel] = {
            "present": candidate.is_file(),
            "syntax_checked": False,
        }
        if candidate.is_file():
            import ast
            ast.parse(candidate.read_text(encoding="utf-8"), filename=fullpath)
            row["overlay_requirements"][rel]["syntax_checked"] = True
    status = {
        "baseline_verified": True,
        "receiver_parity_verified": False,
        "package_safe_to_install": False,
        "reason": "Inspection only; live Vu+ hotfixes and all module dependencies not yet assembled/verified.",
    }
    row["decision"] = status
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipk", required=True)
    parser.add_argument("--report", default="rc78-baseline-inventory.json")
    args = parser.parse_args()
    result = audit(Path(args.ipk))
    Path(args.report).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("EPGManager rc78 baseline inventory")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("DECISION: READ-ONLY / NOT INSTALLABLE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
