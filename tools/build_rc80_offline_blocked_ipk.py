#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble a NEVER-INSTALLABLE RC80 IPK on a private offline staging host.

No receiver, opkg, EPG, settings, mappings, network or updater access.
Private rc79.1 rescue and full 140-module staged tree are required.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile

import audit_rc80_package_manifest_readonly as gate
import audit_rc80_rollback_package_readonly as rollback

RESCUE_SHA256 = "117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69"
OUTPUT_NAME = "EPGManager_RC80_OFFLINE_BLOCKED_UNTESTED.ipk"
CANDIDATE_VERSION = "2026.10.15-rc80-offline-blocked"
EXPECTED_ASSETS = 75
EXPECTED_FILES = gate.EXPECTED_COUNT + EXPECTED_ASSETS
BLOCKED_PREINST = b"#!/bin/sh\n# RC80 staging only: installation forbidden\nexit 90\n"
PASSIVE_HOOK = b"#!/bin/sh\n# No cache, EPG or configuration modification\nexit 0\n"
HOOKS = ("preinst", "postinst", "prerm", "postrm")
MAX_IPK = rollback.MAX_IPK


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_rescue(path, expected_sha):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not (0 < path.stat().st_size <= MAX_IPK):
        raise ValueError("missing, symlinked or oversized private rescue")
    raw = path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha)) or _sha(raw) != expected_sha:
        raise ValueError("private rc79.1 rescue checksum mismatch")
    ar = rollback.read_ar(raw)
    controls, control_modes = rollback.read_tar(ar["control.tar.gz"])
    payload, modes = rollback.read_tar(ar["data.tar.gz"])
    # A checksum is necessary but not sufficient for a reusable recovery package.
    # Ensure the exact rescue hooks are passive and executable.
    if (set(controls) != {"control", *HOOKS} or
            any(controls.get(h) != rollback.HOOK or
                not (control_modes.get(h, 0) & 0o111) for h in HOOKS)):
        raise ValueError("private rescue maintainer hooks are not the exact passive set")
    meta = rollback.metadata(controls.get("control", b""))
    if (meta.get("package"), meta.get("version"), meta.get("architecture")) != (
        rollback.PACKAGE, rollback.VERSION, "all"
    ):
        raise ValueError("wrong rescue opkg identity")
    if len(payload) != gate.BASELINE_COUNT + EXPECTED_ASSETS or any(
        not path.startswith(rollback.PREFIX) for path in payload
    ):
        raise ValueError("rescue must contain exactly 213 application files")
    app = {name[len(rollback.PREFIX):]: data for name, data in payload.items()}
    if any(not name or name.startswith("/") or ".." in Path(name).parts or
           name.endswith((".pyc", ".pyo")) or "__pycache__" in Path(name).parts
           for name in app):
        raise ValueError("unsafe application path in rescue")
    source = {name: raw for name, raw in app.items() if name.endswith(".py")}
    assets = {name: raw for name, raw in app.items() if not name.endswith(".py")}
    if len(source) != gate.BASELINE_COUNT or len(assets) != EXPECTED_ASSETS:
        raise ValueError("wrong original source/asset inventory")
    return controls["control"], app, {p[len(rollback.PREFIX):]: m for p, m in modes.items()}


def _validate_staging(staged_plugin, baseline, rescue_app, rescue_modes):
    root = Path(staged_plugin).absolute()
    if root.is_symlink() or not root.is_dir() or str(root.resolve()).startswith(
        ("/usr/lib/enigma2/", "/etc/enigma2/")
    ):
        raise ValueError("not a safe offline staging tree")
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in staged application")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("special file in staged application")
        rel = path.relative_to(root).as_posix()
        if (not rel or rel.startswith("/") or ".." in Path(rel).parts or
                "/__pycache__/" in "/" + rel or rel.endswith((".pyc", ".pyo"))):
            raise ValueError("unsafe staging path")
        if path.stat().st_size > rollback.MAX_MEMBER:
            raise ValueError("oversized staged application file")
        actual[rel] = path.read_bytes()
        if len(actual) > 1000:
            raise ValueError("oversized staged file inventory")
    if len(actual) != EXPECTED_FILES:
        raise ValueError("staging needs exactly 140 modules and 75 unchanged assets")
    expected_paths = set(rescue_app) | set(gate.CORE_ADDITIONS)
    if set(actual) != expected_paths:
        raise ValueError("unexpected/missing staged application file")
    for name, original in rescue_app.items():
        if not name.endswith(".py") and actual[name] != original:
            raise ValueError("staged non-Python asset differs from rescue: " + name)
        if stat.S_IMODE((root / name).stat().st_mode) != rescue_modes[name]:
            raise ValueError("staged mode differs from rescue: " + name)
    for name in gate.CORE_ADDITIONS:
        if stat.S_IMODE((root / name).stat().st_mode) != 0o644:
            raise ValueError("new RC80 modules require mode 0644")
    for name, record in baseline["files"].items():
        original = rescue_app.get(name)
        if original is None or len(original) != record["bytes"] or _sha(original) != record["sha256"]:
            raise ValueError("private original manifest does not match pinned rescue: " + name)
    return actual


def _replace_version(control):
    lines = control.splitlines(keepends=True)
    matched = 0
    out = []
    for line in lines:
        if re.match(rb"(?i)^Version\s*:", line):
            matched += 1
            out.append(b"Version: " + CANDIDATE_VERSION.encode("ascii") + b"\n")
        else:
            out.append(line)
    if matched != 1 or b"".join(out).count(b"\r"):
        raise ValueError("ambiguous rescue control/version")
    return b"".join(out)


def _tar_gzip(files, modes):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(files):
                raw = files[name]
                item = tarfile.TarInfo("./" + name)
                item.size = len(raw)
                item.mode = modes[name]
                item.uid = item.gid = item.mtime = 0
                item.uname = item.gname = ""
                tar.addfile(item, io.BytesIO(raw))
    return buffer.getvalue()


def _ar(members):
    out = bytearray(b"!<arch>\n")
    for name, raw in members:
        if not name.isascii() or len(name) > 15:
            raise ValueError("invalid archive member name")
        header = ("%-16s%-12d%-6d%-6d%-8s%-10d" % (
            name + "/", 0, 0, 0, "100644", len(raw)
        )).encode("ascii") + b"\x60\n"
        if len(header) != 60:
            raise ValueError("invalid ar header")
        out.extend(header)
        out.extend(raw)
        if len(raw) % 2:
            out.append(10)
    return bytes(out)


def _runtime_version(sources):
    """Require byte-stage runtime version to equal the IPK control version."""
    try:
        module = ast.parse(sources["version.py"].decode("utf-8"), filename="version.py")
    except (KeyError, UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("invalid candidate runtime version module") from exc
    values = []
    for stmt in module.body:
        if isinstance(stmt, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__version__"
                   for target in stmt.targets):
                if not isinstance(stmt.value, ast.Constant) or not isinstance(stmt.value.value, str):
                    raise ValueError("candidate version must be a literal string")
                values.append(stmt.value.value)
    if values != [CANDIDATE_VERSION]:
        raise ValueError("runtime version.py does not match opkg control version")
    return values[0]


def _package(control, sources, rescue_app, rescue_modes):
    payload = {rollback.PREFIX + p: (sources[p] if p.endswith(".py") else raw)
               for p, raw in rescue_app.items()}
    for p in gate.CORE_ADDITIONS:
        payload[rollback.PREFIX + p] = sources[p]
    data_modes = {rollback.PREFIX + p: mode for p, mode in rescue_modes.items()}
    for p in gate.CORE_ADDITIONS:
        data_modes[rollback.PREFIX + p] = 0o644
    scripts = {"control": _replace_version(control)}
    script_modes = {"control": 0o644}
    for hook in HOOKS:
        scripts[hook] = BLOCKED_PREINST if hook == "preinst" else PASSIVE_HOOK
        script_modes[hook] = 0o755
    raw = _ar((("debian-binary", b"2.0\n"),
               ("control.tar.gz", _tar_gzip(scripts, script_modes)),
               ("data.tar.gz", _tar_gzip(payload, data_modes))))
    if len(raw) > MAX_IPK:
        raise ValueError("candidate exceeds IPK size limit")
    parts = rollback.read_ar(raw)
    hooks, _ = rollback.read_tar(parts["control.tar.gz"])
    check_files, check_modes = rollback.read_tar(parts["data.tar.gz"])
    meta = rollback.metadata(hooks["control"])
    if (meta.get("package"), meta.get("version"), meta.get("architecture")) != (
        rollback.PACKAGE, CANDIDATE_VERSION, "all"
    ) or hooks.get("preinst") != BLOCKED_PREINST or any(
        hooks.get(h) != PASSIVE_HOOK for h in HOOKS if h != "preinst"
    ) or check_files != payload or check_modes != data_modes:
        raise ValueError("reconstructed IPK verification failed")
    return raw


def prepare(rescue_ipk, staged_plugin, manifest, reviewed_deltas=None,
            output=None, build=False, expected_rescue_sha=RESCUE_SHA256):
    """Default check reads only private rescue and staging sources; build writes one IPK."""
    if isinstance(manifest, (str, Path)):
        manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if isinstance(reviewed_deltas, (str, Path)):
        reviewed_deltas = json.loads(Path(reviewed_deltas).read_text(encoding="utf-8"))
    reviewed_deltas = reviewed_deltas or {}
    control, original, modes = _read_rescue(rescue_ipk, expected_rescue_sha)
    tree_report = gate.audit(staged_plugin, manifest, reviewed_deltas)
    sources = _validate_staging(staged_plugin, manifest, original, modes)
    # Production-source parity must be checked against the actual private
    # receiver baseline, not just the public preview UI blob. Test fixtures
    # use an explicitly substituted rescue hash; this branch cannot publish.
    if expected_rescue_sha == "117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69":
        import audit_rc80_mapping_parity_readonly as mapping_parity
        expected_original_ui = "314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002"
        if _sha(original["ui/channel_mapping.py"]) != expected_original_ui:
            raise ValueError("private rc79.1 original Mapping UI SHA mismatch")
        mapping_parity.require_safe_preview_parity(
            original["ui/channel_mapping.py"], sources["ui/channel_mapping.py"]
        )
    _runtime_version(sources)
    raw = _package(control, sources, original, modes)
    report = {
        "status": "SEALED_OFFLINE_CANDIDATE_NOT_INSTALLABLE",
        "original_rescue_sha256_verified": True,
        "source_modules": gate.EXPECTED_COUNT,
        "unchanged_other_assets": EXPECTED_ASSETS,
        "files_in_candidate": EXPECTED_FILES,
        "changed_sources_reviewed": tree_report["approved_changed_paths"],
        "preinst_unconditionally_blocks_install": True,
        "candidate_sha256": _sha(raw),
        "candidate_bytes": len(raw),
        "opkg_executed": False,
        "gui_tested": False,
        "opkg_rollback_tested": False,
        "authorized_to_install": False,
        "read_only": not build,
    }
    if build:
        if not output:
            raise ValueError("explicit private output path required")
        target = Path(output).absolute()
        if target.name != OUTPUT_NAME or target.parent.is_symlink() or not target.parent.is_dir():
            raise ValueError("output must have fixed name in an existing private directory")
        resolved = target.resolve()
        if (str(resolved).startswith(("/usr/lib/enigma2/", "/etc/enigma2/")) or
                resolved.is_relative_to(gate.REPO.resolve()) or
                target.is_symlink()):
            raise ValueError("output may not be placed in a receiver or public repo tree")
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as sink:
                sink.write(raw)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        report["output_private_path"] = str(target)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only; no IPK written")
    mode.add_argument("--build", action="store_true", help="Write permanently blocked private IPK")
    parser.add_argument("--rescue-ipk", required=True)
    parser.add_argument("--staged-plugin", required=True)
    parser.add_argument("--private-manifest", required=True)
    parser.add_argument("--reviewed-deltas", help="Offline reviewed private SHA256 dictionary")
    parser.add_argument("--output", help="Private full path with fixed blocked-IPK filename")
    args = parser.parse_args()
    try:
        result = prepare(args.rescue_ipk, args.staged_plugin, args.private_manifest,
                         args.reviewed_deltas, output=args.output, build=args.build)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(json.dumps({"status": "NO_GO", "error": str(exc),
                          "authorized_to_install": False}, sort_keys=True, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
