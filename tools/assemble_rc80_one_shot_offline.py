#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager RC80: one-pass private, offline, PERMANENTLY NON-INSTALLABLE build.

Requires a locally checked-out, reviewed EPGManager repository with the pinned
RC80 overlay sources and the owner's SHA-pinned rescue IPK. Never downloads,
executes opkg, imports Enigma2, reads mappings or touches a receiver.

Usage (staging workstation, NOT receiver):
  python3 rc80_one_shot_offline.py --repo /path/EPGManager \
    --rescue /private/EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk \
    --output-dir /private/output --build-blocked

Without --build-blocked runs checks only; output-dir need not be provided.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from tempfile import TemporaryDirectory

RESCUE_SHA = '117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69'
VERSION = '2026.10.15-rc80-offline-blocked'
BLOBS = {
    'ui/channel_mapping.py': '26165496d9c530058dbdbc858c34794c0a4fb1a2',
    'core/verified_epg_match.py': '6cc0e755b8f2c15065402f560ed8705358801872',
    'core/verified_epg_refresh.py': 'fae7c10b0f3e87aa8217fef3a0ba7ac11c38248d',
}
ROOT = 'usr/lib/enigma2/python/Plugins/Extensions/EPGManager/'


def fail(message):
    raise ValueError(message)


def blob_sha(buf):
    return hashlib.sha1(b'blob ' + str(len(buf)).encode('ascii') + b'\0' + buf).hexdigest()


def require_safe_root(p, kind):
    p = Path(p).absolute()
    resolved = p.resolve()
    for value in (str(p), str(resolved)):
        if value.startswith(('/usr/lib/enigma2/', '/etc/enigma2/')):
            fail(kind + ': receiver path forbidden')
    if p.is_symlink() or not p.is_dir():
        fail(kind + ': missing or symlinked directory')
    return p


def verified_overlay(repo):
    overlay = {}
    for rel, sha in BLOBS.items():
        p = repo / 'source/overlay' / rel
        if not p.is_file() or p.is_symlink():
            fail('missing or symlinked reviewed overlay: ' + rel)
        raw = p.read_bytes()
        if blob_sha(raw) != sha:
            fail('overlay SHA-1 Git blob mismatch: ' + rel)
        try:
            ast.parse(raw.decode('utf-8'), filename=rel)
        except (SyntaxError, UnicodeError) as exc:
            fail('overlay not syntactically valid: ' + rel + ' ' + str(exc))
        overlay[rel] = raw
    return overlay


def prepare(repo_path, rescue_path, output_dir=None, build=False):
    # Fail closed on Enigma2/STB hardware, even for check-only mode.
    if Path('/proc/stb/info/model').exists() or Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager').exists():
        fail('receiver detected; offline assembly strictly forbidden on set-top box')
    repo = require_safe_root(repo_path, 'repository')
    toolroot = repo / 'tools'
    sys.path.insert(0, str(toolroot))
    import build_rc80_offline_blocked_ipk as builder
    import apply_rc80_private_functional_fixes as functional
    import audit_rc80_package_manifest_readonly as gate
    import audit_rc80_rollback_package_readonly as rollback
    import reconcile_rc80_private_mapping as private_mapping
    if gate.REPO.resolve() != repo.resolve():
        fail('builder imported from a different repository')
    if (builder.RESCUE_SHA256 != RESCUE_SHA or
        builder.CANDIDATE_VERSION != VERSION):
        fail('unreviewed builder constants')
    if (gate.PINNED_PUBLIC_GIT_BLOBS != BLOBS or
        tuple(gate.CORE_ADDITIONS) != ('core/verified_epg_match.py', 'core/verified_epg_refresh.py')):
        fail('public pins or module inventory changed')
    if (builder.BLOCKED_PREINST != b'#!/bin/sh\n# RC80 staging only: installation forbidden\nexit 90\n' or
        builder.EXPECTED_FILES != 215 or builder.EXPECTED_ASSETS != 75):
        fail('builder lost mandatory permanent install block')

    overlays = verified_overlay(repo)
    rescue = Path(rescue_path).absolute()
    if rescue.is_symlink() or not rescue.is_file():
        fail('private rescue missing/symlinked')
    # Let existing audited builder validate the exact original SHA, four passive
    # opkg hooks, archive topology and 138+75 payload in one place.
    control, original, modes = builder._read_rescue(rescue, RESCUE_SHA)
    pylist = sorted(n for n in original if n.endswith('.py'))
    if len(pylist) != 138 or len(original) != 213:
        fail('incorrect baseline count')
    baseline = {
        'receiver_version': gate.BASELINE_VERSION,
        'config_schema': 15,
        'files': {n: {'sha256': hashlib.sha256(original[n]).hexdigest(),
                      'bytes': len(original[n])} for n in pylist}
    }
    if any(n in original for n in gate.CORE_ADDITIONS):
        fail('baseline contains R80 additions')
    # Only four approved preview callbacks are merged into the real, working
    # receiver GUI. Its existing Auto Mapping writers/skin are retained.
    # Synthetic CI fixtures continue to use the public GUI overlay.
    original_ui_digest = hashlib.sha256(original['ui/channel_mapping.py']).hexdigest()
    if original_ui_digest == private_mapping.BASELINE_UI_SHA256:
        overlays['ui/channel_mapping.py'], preview_methods = (
            private_mapping.reconcile_private_ui(
                original['ui/channel_mapping.py'], overlays['ui/channel_mapping.py']
            )
        )
        if set(preview_methods) != {
            '_cross_source_suggestion_candidates', '_refresh_cross_source_suggestions',
            '_queue_github_best_match_health', '_poll_github_best_match_health'
        }:
            fail('unreviewed private mapping method reconciliation')
    elif RESCUE_SHA == '117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69':
        fail('unexpected mapping source for pinned private rescue')
    if build:
        if output_dir is None:
            fail('--build-blocked requires --output-dir')
        destination = require_safe_root(output_dir, 'private output directory')
        if destination.resolve().is_relative_to(repo.resolve()):
            fail('candidate must never be placed inside public repository')
    else:
        destination = None

    with TemporaryDirectory(prefix='rc80-sealed-offline-') as tmp:
        staged = Path(tmp) / 'EPGManager'
        for rel, data in original.items():
            f = staged / rel
            f.parent.mkdir(exist_ok=True, parents=True)
            f.write_bytes(data)
            f.chmod(modes[rel])
        for rel, data in overlays.items():
            f = staged / rel
            f.parent.mkdir(exist_ok=True, parents=True)
            f.write_bytes(data)
            if rel in gate.CORE_ADDITIONS:
                f.chmod(0o644)
            else:
                f.chmod(modes[rel])
        version = staged / 'version.py'
        old = version.read_text(encoding='utf-8')
        new, substitutions = re.subn(r'(?m)^(__version__\s*=\s*)[\'\"][^\'\"]+[\'\"]\s*$',
            r'\g<1>"' + VERSION + '"', old)
        if substitutions != 1:
            fail('ambiguous original version marker')
        tree = ast.parse(new, filename='version.py')
        schemas = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'CONFIG_SCHEMA_VERSION' for t in node.targets)
                   and isinstance(node.value, ast.Constant)]
        if schemas != [15]:
            fail('schema mutation refused')
        version.write_text(new, encoding='utf-8')
        version.chmod(modes['version.py'])
        reviewed = {'version.py': hashlib.sha256(version.read_bytes()).hexdigest()}
        # Strict SHA-preimage fixes live only in temporary private staging.
        # Their complete source files are never uploaded to GitHub.
        reviewed.update(functional.apply_to_staged(staged, original))
        candidate = (destination / builder.OUTPUT_NAME) if build else None
        report = builder.prepare(rescue, staged, baseline, reviewed,
                                 output=candidate, build=build)
        if report['source_modules'] != 140 or report['unchanged_other_assets'] != 75:
            fail('incorrect final contents')
        if report['authorized_to_install'] or not report['preinst_unconditionally_blocks_install']:
            fail('candidate incorrectly installable')
        report['receiver_accessed'] = False
        report['rescue_sha256'] = RESCUE_SHA
        report['reviewed_public_blobs'] = BLOBS
        report['warnings'] = ['NOT AN INSTALLABLE R80',
                              'Hardware opkg rollback waived by owner; not tested (not PASS)',
                              'Enigma2 GUI, EPG native import and 24h scheduler not certified']
        return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--rescue', required=True)
    p.add_argument('--output-dir')
    p.add_argument('--build-blocked', action='store_true')
    args = p.parse_args()
    try:
        print(json.dumps(prepare(args.repo,args.rescue,args.output_dir,args.build_blocked),
                         ensure_ascii=False, indent=2, sort_keys=True))
    except (OSError, ValueError, SyntaxError, ImportError, RuntimeError) as exc:
        print(json.dumps({'status':'NO_GO','error':str(exc),'authorized_to_install':False},
                         ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())