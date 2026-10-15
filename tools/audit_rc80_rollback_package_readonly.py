#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strictly read-only parity inspection of two known rc79.1 recovery IPKs.

NO opkg commands, unpacking to disk, Enigma2 imports or writes. No settings,
mappings, EPG XML, process environment, credentials or logs are accessed.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile

ROOT = Path("/usr/lib/enigma2/python/Plugins/Extensions/EPGManager")
PREFIX = "usr/lib/enigma2/python/Plugins/Extensions/EPGManager/"
PACKAGE = "enigma2-plugin-extensions-epgmanager"
VERSION = "2026.10.08-rc79.1"
KNOWN = {
    "rescue_snapshot": (
        "/var/volatile/tmp/EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk",
        "117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69",
    ),
    "original_candidate": (
        "/var/volatile/tmp/EPGManager_rc79_1_AUTOMAPPING_SAFE.ipk",
        "d71bb50962b2ab113f4e7eaa7a28af038e9193a5a2a39a6fca9dc9187d0d4818",
    ),
}
HOOK = b"#!/bin/sh\n# EPGManager local rescue: no EPG/config changes\nexit 0\n"
HOOKS = ("preinst", "postinst", "prerm", "postrm")
MAX_IPK = 30 * 1024 * 1024
MAX_MEMBER = 5 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_ar(data):
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("NOT_IPK")
    out, pos = {}, 8
    while pos < len(data):
        if len(data) - pos < 60:
            raise ValueError("TRUNCATED_AR")
        head = data[pos:pos+60]
        if head[58:] != b"\x60\n":
            raise ValueError("INVALID_AR_HEADER")
        name = head[:16].decode("ascii").strip().rstrip("/")
        if not name or "/" in name or ".." in name or name in out:
            raise ValueError("UNSAFE_AR_MEMBER")
        try:
            n = int(head[48:58].decode("ascii").strip())
        except ValueError as exc:
            raise ValueError("INVALID_AR_LENGTH") from exc
        if n < 0 or n > MAX_IPK or pos + 60 + n > len(data):
            raise ValueError("AR_MEMBER_TOO_LARGE_OR_TRUNCATED")
        pos += 60
        out[name] = data[pos:pos+n]
        pos += n + n % 2
    if pos != len(data) or set(out) != {"debian-binary", "control.tar.gz", "data.tar.gz"}:
        raise ValueError("UNEXPECTED_AR_MEMBERS")
    if out["debian-binary"].strip() != b"2.0":
        raise ValueError("UNSUPPORTED_IPK_FORMAT")
    return out


def read_tar(data):
    files, modes, total = {}, {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar:
                name = member.name.removeprefix("./")
                path = PurePosixPath(name)
                if name in ("", ".") and member.isdir():
                    continue
                if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise ValueError("UNSAFE_TAR_MEMBER")
                if member.isdir():
                    continue
                if not member.isfile() or name in files:
                    raise ValueError("UNEXPECTED_TAR_MEMBER")
                if member.size < 0 or member.size > MAX_MEMBER:
                    raise ValueError("TAR_MEMBER_TOO_LARGE")
                total += member.size
                if total > MAX_UNPACKED or len(files) >= 5000:
                    raise ValueError("TAR_TOO_LARGE")
                handle = tar.extractfile(member)
                if handle is None:
                    raise ValueError("UNREADABLE_TAR_MEMBER")
                body = handle.read(MAX_MEMBER + 1)
                if len(body) != member.size:
                    raise ValueError("CORRUPT_TAR_MEMBER")
                files[name] = body
                modes[name] = member.mode & 0o777
    except tarfile.TarError as exc:
        raise ValueError("INVALID_TAR") from exc
    return files, modes


def metadata(control):
    values = {}
    for line in control.decode("utf-8", "replace").splitlines():
        if line and not line[0].isspace() and ":" in line:
            name, value = line.split(":", 1)
            values[name.strip().lower()] = value.strip()
    return values


def check_one(identity, ipk, expected_digest, plugin=ROOT):
    ipk, plugin = Path(ipk), Path(plugin)
    result = {
        "identity": identity, "present": False, "recognized_sha256": False,
        "python_content_identical": False, "assets_content_identical": False,
        "mode_parity": False, "rescue_hooks_passive": False,
        "complete_rescue_content": False, "opkg_recovery_tested": False,
        "authorized_to_install_rc80": False,
    }
    if not ipk.is_file() or ipk.is_symlink():
        result["status"] = "MISSING"
        return result
    result["present"] = True
    if not plugin.is_dir() or plugin.is_symlink():
        result["status"] = "MISSING_RECEIVER_PLUGIN"
        return result
    if not 0 < ipk.stat().st_size <= MAX_IPK:
        result["status"] = "INVALID_IPK_SIZE"
        return result
    data = ipk.read_bytes()
    if sha(data) != expected_digest:
        result["status"] = "UNRECOGNIZED_CHECKSUM"
        return result
    result["recognized_sha256"] = True
    try:
        ar = read_ar(data)
        control, control_modes = read_tar(ar["control.tar.gz"])
        payload, modes = read_tar(ar["data.tar.gz"])
        fields = metadata(control.get("control", b""))
        if (fields.get("package"), fields.get("version"), fields.get("architecture")) != (
            PACKAGE, VERSION, "all"
        ):
            raise ValueError("INCORRECT_OPKG_CONTROL")
        if not payload or any(not p.startswith(PREFIX) for p in payload):
            raise ValueError("PAYLOAD_OUTSIDE_APPLICATION")
        if any(p.endswith((".pyc", ".pyo")) or "/__pycache__/" in p for p in payload):
            raise ValueError("PYTHON_BYTECODE_IN_IPK")
        app = {p[len(PREFIX):]: data for p, data in payload.items()}
        py = [p for p in app if p.endswith(".py")]
        installed = {}
        for p in plugin.rglob("*"):
            if p.is_symlink():
                raise ValueError("UNEXPECTED_RECEIVER_SYMLINK")
            if not p.is_file():
                continue
            rel = p.relative_to(plugin).as_posix()
            if "__pycache__" in p.parts or rel.endswith((".pyc", ".pyo")):
                continue
            installed[rel] = p
        python_matches = asset_matches = mode_matches = 0
        for rel, expected in app.items():
            receiver_file = installed.get(rel)
            if receiver_file is None or receiver_file.stat().st_size != len(expected):
                continue
            dig = hashlib.sha256()
            with receiver_file.open("rb") as fp:
                for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                    dig.update(chunk)
            if dig.hexdigest() != sha(expected):
                continue
            if rel.endswith(".py"):
                python_matches += 1
            else:
                asset_matches += 1
            if stat.S_IMODE(receiver_file.stat().st_mode) == modes[PREFIX + rel]:
                mode_matches += 1
        result.update({
            "package_python_modules": len(py),
            "installed_python_modules": sum(x.endswith(".py") for x in installed),
            "package_application_files": len(app),
            "receiver_only_unmatched_file_count": len(set(installed) - set(app)),
            "package_only_unmatched_file_count": len(set(app) - set(installed)),
            "matched_python_modules": python_matches,
            "matched_other_assets": asset_matches,
            "matched_file_modes": mode_matches,
            "python_content_identical": len(py) == python_matches == 138,
            "assets_content_identical": asset_matches == len(app) - len(py),
            "mode_parity": mode_matches == len(app),
            "rescue_hooks_passive": all(
                control.get(h) == HOOK and bool(control_modes.get(h, 0) & 0o111)
                for h in HOOKS
            ),
            "opkg_control_valid": True,
        })
        result["complete_rescue_content"] = (
            identity == "rescue_snapshot"
            and result["installed_python_modules"] == 138
            and result["python_content_identical"]
            and result["assets_content_identical"]
            and result["mode_parity"]
            and result["rescue_hooks_passive"]
            and not result["receiver_only_unmatched_file_count"]
            and not result["package_only_unmatched_file_count"]
        )
        result["status"] = (
            "COMPLETE_CONTENT_OPKG_UNTESTED" if result["complete_rescue_content"]
            else "INCOMPLETE_OR_UNSAFE_FOR_RECOVERY"
        )
    except (OSError, ValueError, UnicodeError) as exc:
        result["status"] = "VALIDATION_FAILED"
        result["failure_type"] = str(exc).split(":")[0][:70]
    return result


def audit(plugin=ROOT, candidates=KNOWN):
    rows = [check_one(k, *v, plugin=plugin) for k, v in candidates.items()]
    return {
        "audit": "EPGManager rc80 rollback IPK full application parity",
        "read_only": True, "receiver_modified": False, "candidates": rows,
        "rescue_content_parity_found": any(x["complete_rescue_content"] for x in rows),
        "opkg_rollback_tested": False, "enigma2_restart_tested": False,
        "authorized_to_install_rc80": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", default=str(ROOT))
    args = parser.parse_args()
    print(json.dumps(audit(plugin=args.plugin_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
