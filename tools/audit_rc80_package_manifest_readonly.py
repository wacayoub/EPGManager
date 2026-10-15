#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RC80 strict, READ-ONLY receiver-tree assembly integrity gate.

The private 138-Python-module rc79.1 baseline is required as an independent
manifest. The public R80 preview adds exactly two reviewed core modules,
yielding 140. This audit *never* builds an IPK, installs packages, reads
receiver configuration/mappings, or relaxes rollback/hardware release gates.

A manifest is supplied OFFLINE by the owner on a private staging computer.
No manifest, full plugin tree, or private patched Python is uploaded to GitHub.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

REPO = Path(__file__).resolve().parents[1]
BASELINE_COUNT = 138
EXPECTED_COUNT = 140
CORE_ADDITIONS = (
    "core/verified_epg_match.py",
    "core/verified_epg_refresh.py",
)
CHANGED_PUBLIC_UI = "ui/channel_mapping.py"
PRIVATE_REVIEWABLE = frozenset((
    "core/system_check.py",
    "core/github_match_health.py",
    "core/github_direct_sync.py",
    "version.py",
))
BASELINE_VERSION = "2026.10.08-rc79.1"

# Immutable Git blob identities of the three reviewed public RC80 sources.
# Comparing staging against a local checkout alone would accept coordinated
# tampering of both staging and checkout. These identifiers are branch-independent.
PINNED_PUBLIC_GIT_BLOBS = {
    "ui/channel_mapping.py": "464d219d0d951fad5105ed643bf0cfdffd7ffc26",
    "core/verified_epg_match.py": "03d5b779ec639ed7e8f82f7205cf34af4e4af56d",
    "core/verified_epg_refresh.py": "ce9c57773c739977782d22c373993a81e3a68361",
}


def _git_blob_sha(raw):
    header = b"blob " + str(len(raw)).encode("ascii") + b"\x00"
    return hashlib.sha1(header + raw).hexdigest()



def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_module_path(path):
    if not isinstance(path, str) or not path.endswith(".py") or "\\" in path:
        return False
    parts = PurePosixPath(path).parts
    return (bool(parts) and all(part not in ("..", ".", "") for part in parts)
            and not path.startswith("/") and not path.startswith("./")
            and path == str(PurePosixPath(path))
            and "__pycache__" not in parts)


def _manifest(manifest):
    if (not isinstance(manifest, dict) or
            manifest.get("receiver_version") != BASELINE_VERSION or
            manifest.get("config_schema") != 15):
        raise ValueError("missing verified private rc79.1 schema/version provenance")
    files = manifest.get("files")
    if not isinstance(files, dict) or len(files) != BASELINE_COUNT:
        raise ValueError("exactly 138 original receiver Python files must be verified")
    if any(not valid_module_path(path) or path in CORE_ADDITIONS for path in files):
        raise ValueError("invalid original receiver module paths")
    for path, record in files.items():
        if (not isinstance(record, dict) or
                not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256") or "")) or
                not isinstance(record.get("bytes"), int) or
                record["bytes"] < 0):
            raise ValueError("invalid original module SHA256/length: " + path)
    if CHANGED_PUBLIC_UI not in files or "version.py" not in files:
        raise ValueError("critical receiver baseline modules absent")
    return files


def audit(staged_plugin, manifest, reviewed_deltas=None, public_root=None):
    """Reject any missing, unexpected or unauthorized source-file change."""
    root = Path(staged_plugin).absolute()
    # Refuse direct and symlink-via-parent receiver paths before accessing
    # anything from disk. Both this tool and its output are staging-only.
    for candidate in (root, root.resolve()):
        if str(candidate).startswith(("/usr/lib/enigma2/", "/etc/enigma2/")):
            raise ValueError("live receiver directory is not an offline staging tree")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("staged plugin tree missing/unsafe")
    files = _manifest(manifest)
    reviewed = reviewed_deltas or {}
    if (not isinstance(reviewed, dict) or
            any(key not in PRIVATE_REVIEWABLE for key in reviewed)):
        raise ValueError("unapproved private modification path")
    if any(not re.fullmatch(r"[a-f0-9]{64}", str(value or "")) for value in reviewed.values()):
        raise ValueError("private delta must be bound to an explicit SHA256")
    public = Path(public_root) if public_root is not None else REPO
    all_py = sorted(root.rglob("*.py"))
    if len(all_py) != EXPECTED_COUNT:
        raise ValueError("RC80 requires audited 138 baseline + 2 public bridge modules")
    actual = {}
    for file in all_py:
        if file.is_symlink() or not file.is_file():
            raise ValueError("symlinked/non-file module forbidden")
        rel = file.relative_to(root).as_posix()
        if not valid_module_path(rel):
            raise ValueError("illegal staged module name")
        actual[rel] = file.read_bytes()
    if set(actual) != set(files) | set(CORE_ADDITIONS):
        raise ValueError("source inventory does not match 138 original paths + 2 RC80 modules")

    # UI and new core additions are exact bytes from the reviewed public PR.
    public_expected = {
        CHANGED_PUBLIC_UI: public / "source/overlay/ui/channel_mapping.py",
        CORE_ADDITIONS[0]: public / "source/overlay/core/verified_epg_match.py",
        CORE_ADDITIONS[1]: public / "source/overlay/core/verified_epg_refresh.py",
    }
    for rel, path in public_expected.items():
        if path.is_symlink() or not path.is_file() or actual[rel] != path.read_bytes():
            raise ValueError("unverified new public overlay: " + rel)
        if _git_blob_sha(actual[rel]) != PINNED_PUBLIC_GIT_BLOBS[rel]:
            raise ValueError("public source differs from audited Git blob: " + rel)

    changes = {}
    for rel, record in files.items():
        raw = actual[rel]
        if digest(raw) == record["sha256"] and len(raw) == record["bytes"]:
            if rel in reviewed:
                raise ValueError("claimed private modification did not occur: " + rel)
            continue
        if rel == CHANGED_PUBLIC_UI:
            changes[rel] = "public-review-exact"
        elif rel in reviewed and digest(raw) == reviewed[rel]:
            changes[rel] = "private-reviewed-hash"
        else:
            raise ValueError("unapproved receiver runtime modification: " + rel)

    # Only the frozen 138 runtime file list plus exactly two reviewed modules
    # is acceptable. Parse all 140 even if a public module was syntactically OK.
    for rel, raw in actual.items():
        try:
            ast.parse(raw.decode("utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as err:
            raise ValueError("invalid Python source: " + rel) from err

    version_tree = ast.parse(actual["version.py"].decode("utf-8"))
    assignments = {}
    for stmt in version_tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            assignments.setdefault(stmt.targets[0].id, []).append(stmt.value)
    try:
        versions = assignments["__version__"]
        schemas = assignments["CONFIG_SCHEMA_VERSION"]
        if (len(versions) != 1 or len(schemas) != 1 or
                not isinstance(versions[0], ast.Constant) or
                not isinstance(versions[0].value, str) or
                "rc80" not in versions[0].value or
                not isinstance(schemas[0], ast.Constant) or
                type(schemas[0].value) is not int or schemas[0].value != 15):
            raise ValueError("version or schema changed")
    except (KeyError, ValueError):
        raise ValueError("invalid rc80 runtime version/config schema")
    # Structural evidence only. The private full set and actual Enigma2
    # GUI/EPGImport/opkg rollback must still pass before release.
    return {
        "audit": "EPGManager RC80 package source inventory (read-only)",
        "status": "SOURCE_TREE_STRUCTURALLY_VERIFIED_NOT_INSTALL_APPROVED",
        "baseline_python_modules": BASELINE_COUNT,
        "added_public_modules": list(CORE_ADDITIONS),
        "result_python_modules": len(actual),
        "original_file_paths_checked": BASELINE_COUNT,
        "approved_changed_paths": dict(sorted(changes.items())),
        "unapproved_modifications": 0,
        "schema": 15,
        "receiver_config_read": False,
        "receiver_mapping_read": False,
        "package_built": False,
        "hardware_tested": False,
        "opkg_rollback_tested": False,
        "authorized_to_install": False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged-plugin", required=True)
    ap.add_argument("--private-manifest", required=True)
    ap.add_argument("--reviewed-deltas", help="Private reviewed change SHA dictionary, kept local")
    args = ap.parse_args()
    manifest = json.loads(Path(args.private_manifest).read_text(encoding="utf-8"))
    reviewed = (json.loads(Path(args.reviewed_deltas).read_text(encoding="utf-8"))
                if args.reviewed_deltas else {})
    print(json.dumps(audit(args.staged_plugin, manifest, reviewed),
                     ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
