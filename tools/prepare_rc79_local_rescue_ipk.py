#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect / locally reconstruct an EPGManager rc79.1 rescue IPK.

Default --check is strictly read-only. --build writes ONE new, restricted
/tmp IPK containing the *current* installed Python modules plus the existing
rc79.1 package's non-Python application assets. It does NOT modify the
installed plugin or user config and NEVER invokes opkg.

This is a locally held rescue candidate, NOT a proven opkg downgrade package.
Original maintainer scripts are NOT carried over (they may clear XMLTV data).
The local output is intentionally not published or uploaded.
"""
from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import time

from audit_original_rollback_ipk import (
    _read_ar, _data_tar, _safe_file_members, _parse_control,
    PROTECTED, PACKAGE, VERSION, REL, verify_old_ipk,
)

EXPECTED_ORIGINAL_SHA256 = "d71bb50962b2ab113f4e7eaa7a28af038e9193a5a2a39a6fca9dc9187d0d4818"
ORIGINAL = Path("/var/volatile/tmp/EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk")
RECEIVER = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
RESCUE_BASENAME = "EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk"
MAX_RECEIVER_FILE = 5 * 1024 * 1024
MAX_RECEIVER_TOTAL = 100 * 1024 * 1024
IGNORED_EXT = (".pyc", ".pyo")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_safe_tar(tar):
    files = {}
    modes = {}
    total_size = 0
    for item in tar:
        name = item.name.removeprefix("./")
        if name in ("", ".") and item.isdir():
            continue
        if name.startswith("/") or not name or ".." in Path(name).parts:
            raise ValueError("Unsafe path in original IPK")
        if item.issym() or item.islnk() or item.isdev() or item.isfifo():
            raise ValueError("Symlink or special file in original IPK")
        if item.isdir():
            continue
        if not item.isfile() or name in files:
            raise ValueError("Unsupported/duplicate member in original IPK")
        if item.size < 0 or item.size > MAX_RECEIVER_FILE:
            raise ValueError("Oversized member in original IPK")
        total_size += item.size
        if total_size > MAX_RECEIVER_TOTAL or len(files) >= 5000:
            raise ValueError("Original IPK exceeds safe extraction limits")
        raw = tar.extractfile(item).read()
        if len(raw) != item.size:
            raise ValueError("Truncated original IPK member")
        files[name] = raw
        modes[name] = item.mode & 0o777
    return files, modes


def _trusted_inputs(candidate, receiver, expected_sha):
    candidate, receiver = Path(candidate), Path(receiver)
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("Original candidate unavailable or symlinked")
    raw = candidate.read_bytes()
    if len(raw) > 30 * 1024 * 1024 or sha(raw) != expected_sha:
        raise ValueError("Original rc79.1 candidate SHA256 mismatch")
    parts = _read_ar(raw)
    if parts.get("debian-binary", b"").strip() != b"2.0":
        raise ValueError("Unsupported original IPK format")
    with _data_tar(parts, "control.tar") as tar:
        ctrls, _ = _read_safe_tar(tar)
    if "control" not in ctrls:
        raise ValueError("Original opkg control metadata is missing")
    metadata = _parse_control(ctrls["control"])
    if (metadata.get("package"), metadata.get("version"), metadata.get("architecture")) != (PACKAGE, VERSION, "all"):
        raise ValueError("Original IPK package/version/architecture mismatch")
    with _data_tar(parts, "data.tar") as tar:
        payload, perms = _read_safe_tar(tar)
    foreign = sorted(x for x in payload if not x.startswith(REL))
    if foreign:
        raise ValueError("Candidate has non-plugin package members: " + ", ".join(foreign[:6]))
    if not receiver.is_dir() or receiver.is_symlink():
        raise ValueError("Working receiver application directory missing")
    current = {}
    modes = {}
    size = 0
    for path in receiver.rglob("*.py"):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unexpected symlink in installed Python tree")
        if path.stat().st_size > MAX_RECEIVER_FILE:
            raise ValueError("Unexpectedly large installed module: " + path.name)
        rel = path.relative_to(receiver).as_posix()
        raw_file = path.read_bytes()
        size += len(raw_file)
        if size > MAX_RECEIVER_TOTAL:
            raise ValueError("Receiver application modules exceed size cap")
        current[rel] = raw_file
        modes[rel] = stat.S_IMODE(path.stat().st_mode) & 0o777
    if len(current) != 138:
        raise ValueError("Working receiver must have exactly 138 Python modules")
    for name, digest in PROTECTED.items():
        if sha(current.get(name, b"")) != digest:
            raise ValueError("Protected receiver module changed: " + name)
    # Identify non-Python files present at runtime that an older candidate
    # might not carry. NEVER silently claim full device parity in that case.
    missing_assets = []
    changed_assets = []
    changed_asset_modes = []
    installed_assets = set()
    for path in receiver.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in installed application tree")
        if not path.is_file():
            continue
        rel = path.relative_to(receiver).as_posix()
        if rel.endswith(".py") or rel.endswith(IGNORED_EXT) or "__pycache__" in path.parts:
            continue
        installed_assets.add(rel)
        packaged = payload.get(REL + rel)
        if packaged is None:
            missing_assets.append(rel)
        elif path.stat().st_size > MAX_RECEIVER_FILE or sha(path.read_bytes()) != sha(packaged):
            changed_assets.append(rel)
        elif stat.S_IMODE(path.stat().st_mode) & 0o777 != perms.get(REL + rel, 0o644):
            changed_asset_modes.append(rel)
    package_assets = {
        name[len(REL):] for name in payload
        if name.startswith(REL) and not name.endswith(".py")
        and not name.endswith(IGNORED_EXT)
    }
    package_only_assets = sorted(package_assets - installed_assets)
    return {
        "control": ctrls["control"],
        "payload": payload, "perms": perms,
        "receiver": current, "receiver_modes": modes,
        "missing_assets": sorted(missing_assets),
        "changed_assets": sorted(changed_assets),
        "changed_asset_modes": sorted(changed_asset_modes),
        "package_only_assets": package_only_assets,
        "original_sha256": sha(raw),
        "original_scripts": sorted(x for x in ctrls if x != "control"),
        "original_python": sorted(x[len(REL):] for x in payload
                                  if x.startswith(REL) and x.endswith(".py")),
    }


def _make_tar(data, perms):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(data):
            raw = data[name]
            item = tarfile.TarInfo("./" + name)
            item.size = len(raw)
            item.uid = item.gid = 0
            item.uname = item.gname = "root"
            item.mode = perms.get(name, 0o644) & 0o777
            tar.addfile(item, io.BytesIO(raw))
    return buf.getvalue()


def _make_ar(data):
    out = bytearray(b"!<arch>\n")
    for name, raw in data:
        if len(name) > 15:
            raise ValueError("Invalid ar member name")
        header = ("%-16s%-12d%-6d%-6d%-8s%-10d" %
                  (name + "/", 0, 0, 0, "100644", len(raw))).encode("ascii") + b"\x60\n"
        if len(header) != 60:
            raise ValueError("Invalid ar header")
        out.extend(header)
        out.extend(raw)
        if len(raw) % 2:
            out.append(10)
    return bytes(out)


def _package_bytes(inputs):
    data = {name: raw for name, raw in inputs["payload"].items()
            if not name.endswith(".py") and not name.endswith(IGNORED_EXT)}
    modes = {name: inputs["perms"][name] for name in data}
    for rel, raw in inputs["receiver"].items():
        name = REL + rel
        data[name] = raw
        modes[name] = inputs["receiver_modes"][rel]
    safe_hook = b"#!/bin/sh\n# EPGManager local rescue: no EPG/config changes\nexit 0\n"
    control = {"control": inputs["control"]}
    perms_control = {"control": 0o644}
    # Never carry historic preinst/prerm/postinst scripts with cache-removal
    # side effects. The resulting package still requires opkg-level testing.
    for hook in ("preinst", "postinst", "prerm", "postrm"):
        control[hook] = safe_hook
        perms_control[hook] = 0o755
    return _make_ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", _make_tar(control, perms_control)),
        ("data.tar.gz", _make_tar(data, modes)),
    ])


def evaluate(candidate=ORIGINAL, receiver=RECEIVER, expected_sha=EXPECTED_ORIGINAL_SHA256,
             build=False, output=None, _test_output_dir=None):
    inputs = _trusted_inputs(candidate, receiver, expected_sha)
    expected_paths = set(inputs["receiver"])
    old_paths = set(inputs["original_python"])
    result = {
        "audit": "EPGManager local rc79.1 rescue package preparation",
        "read_only": not build,
        "installed_python_modules": len(expected_paths),
        "original_ipk_python_modules": len(old_paths),
        "python_filenames_added_from_receiver": len(expected_paths - old_paths),
        "python_filenames_dropped_from_original": len(old_paths - expected_paths),
        "python_modules_different_or_missing": sum(
            1 for rel in expected_paths
            if inputs["payload"].get(REL + rel) != inputs["receiver"][rel]),
        "protected_modules_identical_to_oct14_snapshot": True,
        "original_ipk_sha256_verified": True,
        "original_package_hook_files": inputs["original_scripts"],
        "missing_receiver_assets_count": len(inputs["missing_assets"]),
        "missing_receiver_assets_sample": inputs["missing_assets"][:8],
        "changed_receiver_assets_count": len(inputs["changed_assets"]),
        "changed_receiver_assets_sample": inputs["changed_assets"][:8],
        "changed_receiver_asset_modes_count": len(inputs["changed_asset_modes"]),
        "changed_receiver_asset_modes_sample": inputs["changed_asset_modes"][:8],
        "extra_original_package_assets_count": len(inputs["package_only_assets"]),
        "extra_original_package_assets_sample": inputs["package_only_assets"][:8],
        "config_and_mapping_files_read": False,
        "opkg_executed": False,
        "receiver_plugin_modified": False,
        "authorized_to_install_rc80": False,
        "rescue_ipk_opkg_install_tested": False,
        "rescue_ipk_complete_receiver_parity": False,
    }
    if not build:
        result["next"] = "Read-only assessment complete; rescue packaging requires explicit --build."
        return result
    if (inputs["missing_assets"] or inputs["changed_assets"] or
            inputs["changed_asset_modes"] or inputs["package_only_assets"]):
        raise ValueError("Non-Python application assets do not match receiver; refusing unsafe rescue build")
    output = Path(output or "/tmp/" + RESCUE_BASENAME)
    allowed_dirs = (Path("/tmp").resolve(), Path("/var/volatile/tmp").resolve())
    if _test_output_dir is not None:
        allowed_dirs += (Path(_test_output_dir).resolve(),)
    if output.name != RESCUE_BASENAME or output.parent.resolve() not in allowed_dirs:
        raise ValueError("Rescue output must be the fixed filename in /tmp")
    payload = _package_bytes(inputs)
    if len(payload) > 30 * 1024 * 1024:
        raise ValueError("Prepared rescue IPK exceeds allowed size")
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        raise
    try:
        report = verify_old_ipk(output, receiver_root=receiver)
        if not report["rollback_python_tree_identical_to_working_app"]:
            raise ValueError("Built rescue IPK does not reproduce working Python modules")
    except Exception:
        output.unlink()
        raise
    result.update({
        "read_only": False,
        "rescue_ipk_created": True,
        "rescue_ipk": str(output),
        "rescue_ipk_sha256": sha(payload),
        "rescue_ipk_bytes": len(payload),
        "rescue_python_tree_identical": True,
        "rescue_ipk_opkg_install_tested": False,
        "authorized_to_install_rc80": False,
        "next": "Keep this local recovery candidate; no opkg invocation is authorized.",
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only report (default task)")
    mode.add_argument("--build", action="store_true",
                      help="Create a private rescue IPK under /tmp; never install")
    args = parser.parse_args()
    try:
        print(json.dumps(evaluate(build=args.build), indent=2, sort_keys=True))
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(json.dumps({"audit": "EPGManager local rc79.1 rescue package preparation",
                          "ok": False, "error": str(exc),
                          "authorized_to_install_rc80": False}, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
