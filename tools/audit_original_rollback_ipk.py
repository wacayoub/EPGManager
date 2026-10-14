#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict READ-ONLY validator for an EPGManager rollback IPK.

The working receiver is rc79.1 with 7 protected modules and 138 Python
files. A restored code tar alone cannot repair opkg's package database.
This validator identifies whether a locally held ORIGINAL old-version IPK
has the exact prior bytecode-free application required for safe downgrade.
No download, writes, opkg install/remove/upgrade, or file extraction.
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
from pathlib import PurePosixPath, Path
import sys
import tarfile

PACKAGE = "enigma2-plugin-extensions-epgmanager"
VERSION = "2026.10.08-rc79.1"
REL = "usr/lib/enigma2/python/Plugins/Extensions/EPGManager/"
MAX_IPK = 30 * 1024 * 1024
MAX_TAR_BYTES = 100 * 1024 * 1024
PROTECTED = {
    "ui/channel_mapping.py": "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002",
    "core/direct_native_import.py": "43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31",
    "core/epg_cycle.py": "f49e92ae9c18919554a010af1f9fb6e5ba4b6a8358c9652b575c652227200887",
    "core/github_direct_stream.py": "52180c29f691e2764fd63d14906bcf851ed72c8447c6b07c4a2f272ec0b98686",
    "core/github_match_health.py": "941c89c9ace27f63a63a41486b3f012f9f96cd7ed6c0dca602bc8b3353f4571c",
    "ui/settings.py": "ba2a989b772e768aeef4932bd24abdf966a5286a2edff931d12e36f5bf4474fa",
    "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
    "version.py": "652b7e5b0ea4899dd44e671a62fa948e9771c56a29425de3d355851687e0cea4",
}


def _read_ar(data):
    """Parse ar(5) without depending on a system ar executable."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("Not a Unix ar/IPK archive")
    pos = 8
    files = {}
    while pos < len(data):
        if len(data) - pos < 60:
            raise ValueError("Truncated archive header")
        head = data[pos:pos+60]
        if head[58:] != b"\x60\n":
            raise ValueError("Invalid archive member magic")
        name = head[:16].decode("ascii", "strict").strip().rstrip("/")
        if "/" in name or ".." in name or not name:
            raise ValueError("Unexpected ar member")
        try:
            length = int(head[48:58].decode("ascii").strip())
        except (UnicodeError, ValueError):
            raise ValueError("Invalid archive member size")
        if length < 0 or length > MAX_TAR_BYTES or name in files:
            raise ValueError("Oversized or duplicate archive member")
        begin = pos+60
        end = begin+length
        if end > len(data):
            raise ValueError("Truncated archive member")
        files[name] = data[begin:end]
        pos = end+(length%2)
    if pos != len(data):
        raise ValueError("Invalid archive padding")
    return files


def _data_tar(files, prefix):
    choices = [(name, payload) for name,payload in files.items()
               if name.startswith(prefix)]
    if len(choices) != 1:
        raise ValueError("Expected exactly one "+prefix+"* archive")
    name, payload = choices[0]
    if not name.endswith((".gz", ".xz", ".bz2", ".tar")):
        raise ValueError("Unsupported tar compression")
    return tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")


def _safe_file_members(tar):
    records = {}
    size_total = 0
    for member in tar:
        rel = member.name.removeprefix("./")
        p = PurePosixPath(rel)
        if rel in ("", ".") and member.isdir():
            continue
        if not rel or p.is_absolute() or ".." in p.parts:
            raise ValueError("Unsafe embedded tar path")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError("Unsafe embedded tar entry")
        if not member.isfile():
            if not member.isdir():
                raise ValueError("Unexpected embedded tar entry")
            continue
        size_total += member.size
        if size_total > MAX_TAR_BYTES or member.size < 0 or rel in records:
            raise ValueError("Oversized or repeated embedded tar member")
        stream = tar.extractfile(member)
        if stream is None:
            raise ValueError("Cannot read tar member")
        records[rel] = stream.read()
    return records


def _parse_control(contents):
    fields = {}
    current = None
    for line in contents.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current = key.strip().lower()
        fields[current] = value.strip()
    return fields


def verify_old_ipk(ipk_path):
    file = Path(ipk_path)
    if not file.is_file() or file.is_symlink():
        raise ValueError("Rollback IPK does not exist or is a symlink")
    if file.stat().st_size > MAX_IPK:
        raise ValueError("Rollback IPK too large")
    raw = file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    parts = _read_ar(raw)
    if parts.get("debian-binary", b"").strip() != b"2.0":
        raise ValueError("Unsupported IPK format")
    with _data_tar(parts, "control.tar") as ctar:
        control_files = _safe_file_members(ctar)
    control_data = control_files.get("control")
    if control_data is None:
        raise ValueError("Missing rollback opkg control metadata")
    fields = _parse_control(control_data)
    if fields.get("package") != PACKAGE:
        raise ValueError("Unexpected rollback package name")
    if fields.get("version") != VERSION:
        raise ValueError("Rollback IPK version differs from installed receiver")
    if fields.get("architecture") != "all":
        raise ValueError("Unexpected rollback architecture; receiver reports all")
    with _data_tar(parts, "data.tar") as dtar:
        data_files = _safe_file_members(dtar)
    python_files = [name for name in data_files if name.startswith(REL) and name.endswith(".py")]
    matches = {}
    for relative, expected in PROTECTED.items():
        source = data_files.get(REL+relative)
        matches[relative] = bool(source is not None and hashlib.sha256(source).hexdigest() == expected)
    count_match = len(python_files) == 138
    other = [name for name in data_files if name.startswith(REL) and name.endswith((".pyc", ".pyo"))]
    valid = all(matches.values()) and count_match and not other
    return {
        "audit": "EPGManager original rollback IPK compatibility",
        "read_only": True,
        "candidate_path": str(file),
        "candidate_sha256": digest,
        "candidate_size": len(raw),
        "package": fields.get("package"),
        "version": fields.get("version"),
        "architecture": fields.get("architecture"),
        "python_modules": len(python_files),
        "python_modules_match_receiver": count_match,
        "protected_module_hashes_match": matches,
        "stale_bytecode_count": len(other),
        "rollback_package_identical_to_working_app": valid,
        "opkg_rollback_tested": False,
        "authorized_to_install_new_version": False,
        "reason": ("Original rollback IPK matches receiver's protected code; "
                   "still requires supervised opkg recovery rehearsal on a disposable test root.")
                  if valid else "Original rollback IPK is incomplete or differs from currently working receiver.",
    }


DEFAULT_SEARCH_ROOTS = (
    "/tmp",
    "/var/volatile/tmp",
    "/var/cache/opkg",
    "/home/root",
    "/media/hdd",
    "/media/usb",
    "/etc/epgmanager_epg/backups",
)


def discover(roots=DEFAULT_SEARCH_ROOTS):
    """Check only named local locations. Never crawl an arbitrary receiver disk."""
    candidates = []
    seen = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            continue
        for pattern in ("*epgmanager*.ipk", "*EPGManager*.ipk",
                        "*/enigma2-plugin-extensions-epgmanager*.ipk"):
            for file in sorted(root.glob(pattern)):
                if str(file) in seen or not file.is_file() or file.is_symlink():
                    continue
                seen.add(str(file))
                candidates.append(file)
                if len(candidates) >= 30:
                    break
            if len(candidates) >= 30:
                break
        if len(candidates) >= 30:
            break
    results = []
    for candidate in candidates:
        try:
            report = verify_old_ipk(candidate)
            results.append({"path": str(candidate),
                            "exact": report["rollback_package_identical_to_working_app"],
                            "reason": report["reason"],
                            "sha256": report["candidate_sha256"],
                            "version": report["version"]})
        except (OSError, ValueError, tarfile.TarError) as err:
            results.append({"path": str(candidate), "exact": False,
                            "reason": str(err)})
    exact = [x for x in results if x["exact"]]
    return {
        "audit": "EPGManager local rollback IPK discovery",
        "read_only": True,
        "search_scope": "fixed top-level cache/temp/backup locations only",
        "candidates_checked": len(candidates),
        "exact_rc79_rollback_candidates": len(exact),
        "candidates": results,
        "rollback_with_opkg_tested": False,
        "authorized_to_install_new_version": False,
        "reason": ("Original byte-matching rc79.1 IPK found, but supervised opkg"
                   " downgrade still untested.")
                  if exact else "No exact rollback IPK verified; rc80 install stays blocked.",
    }

def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ipk",
                      help="Path to locally-held original EPGManager rc79.1 IPK.")
    mode.add_argument("--discover", action="store_true",
                      help="Check only a short allowlist of cache/temporary directories.")
    args = parser.parse_args()
    try:
        result = discover() if args.discover else verify_old_ipk(args.ipk)
        print(json.dumps(result, indent=2, sort_keys=True))
        eligible = (result.get("exact_rc79_rollback_candidates", 0) > 0
                    if args.discover
                    else result.get("rollback_package_identical_to_working_app", False))
        return 0 if eligible else 2
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(json.dumps({"audit": "EPGManager original rollback IPK compatibility",
                          "read_only": True, "rollback_package_identical_to_working_app": False,
                          "authorized_to_install_new_version": False,
                          "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
