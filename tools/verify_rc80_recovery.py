#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify an EPGManager pre-upgrade backup, and rehearse code-only restoration.

Default mode is strictly read-only. --restore performs a filesystem-only
restoration, NEVER edits opkg's package database, settings, mappings, EPG,
or other app data. Restoring actual receiver code requires explicit consent
and opkg package reconciliation afterward; do NOT use as an installer.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time

MAX_UNPACKED = 100 * 1024 * 1024
MAX_MEMBERS = 12000
DEFAULT_PLUGIN = "/usr/lib/enigma2/python/Plugins/Extensions/EPGManager"


def _checked_members(archive):
    members = archive.getmembers()
    if not 2 <= len(members) <= MAX_MEMBERS:
        raise ValueError("Unexpected number of backup members")
    total = 0
    names = set()
    for item in members:
        name = item.name.removeprefix("./")
        parts = PurePosixPath(name)
        if parts.is_absolute() or not parts.parts or parts.parts[0] != "EPGManager":
            raise ValueError("Unexpected path outside EPGManager")
        if ".." in parts.parts or not (item.isfile() or item.isdir()):
            raise ValueError("Unsafe path or backup entry")
        if name in names:
            raise ValueError("Duplicate archive entry")
        names.add(name)
        if item.isfile():
            total += item.size
            if item.size > MAX_UNPACKED or total > MAX_UNPACKED:
                raise ValueError("Backup exceeds size cap")
    for required in ("EPGManager/plugin.py", "EPGManager/version.py"):
        if required not in names:
            raise ValueError("Incomplete EPGManager backup: " + required)
    return members, total


def inspect_backup(source):
    source = Path(source)
    if not source.is_file() or source.is_symlink():
        raise ValueError("Backup archive missing or symlink")
    digest = hashlib.sha256()
    with source.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    with tarfile.open(source, "r:gz") as tar:
        members, total = _checked_members(tar)
        raw = tar.extractfile("EPGManager/version.py").read(65536).decode("utf-8", "replace")
    return {
        "archive": str(source),
        "archive_sha256": digest.hexdigest(),
        "total_members": len(members),
        "unpacked_bytes": total,
        "previous_version_module_present": "__version__" in raw,
        "backup_verified": True,
        "opkg_database_restored": False,
        "receiver_modified": False,
    }


def restore_code_only(source, plugin_dir):
    source = Path(source).resolve(strict=True)
    plugin = Path(plugin_dir).resolve(strict=False)
    if plugin.name != "EPGManager" or not plugin.parent.is_dir():
        raise ValueError("Refusing unsafe plugin restore destination")
    report = inspect_backup(source)
    staging = Path(tempfile.mkdtemp(prefix=".EPGManager-recovery-", dir=str(plugin.parent)))
    retained = None
    try:
        with tarfile.open(source, "r:gz") as tar:
            members, _total = _checked_members(tar)
            for item in members:
                rel = item.name.removeprefix("./")
                dest = staging / rel
                if item.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with dest.open("xb") as dst:
                        shutil.copyfileobj(tar.extractfile(item), dst, 1024 * 1024)
                    os.chmod(dest, item.mode & 0o777)
        restored = staging / "EPGManager"
        if not restored.joinpath("plugin.py").is_file() or not restored.joinpath("version.py").is_file():
            raise ValueError("Staged receiver backup missing entrypoint")
        if plugin.exists():
            retained = plugin.with_name(".EPGManager.failed-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid()))
            if retained.exists():
                raise ValueError("Previous failed-version save path already exists")
            plugin.rename(retained)
        try:
            restored.rename(plugin)
        except Exception:
            if retained is not None:
                retained.rename(plugin)
            raise
        report.update({
            "receiver_modified": True,
            "files_restored": True,
            "failed_version_preserved_at": str(retained) if retained else None,
            "opkg_database_restored": False,
            "requires_opkg_reconciliation": True,
        })
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    parser.add_argument("--plugin-dir", default=DEFAULT_PLUGIN)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--accept-opkg-mismatch", action="store_true")
    args = parser.parse_args()
    if args.restore and not args.accept_opkg_mismatch:
        parser.error("--restore needs --accept-opkg-mismatch: this does NOT fix opkg metadata")
    report = (restore_code_only(args.backup, args.plugin_dir)
              if args.restore else inspect_backup(args.backup))
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get("requires_opkg_reconciliation"):
        print("WARNING: opkg database may still indicate the newer package; reconcile before updates.")


if __name__ == "__main__":
    main()
