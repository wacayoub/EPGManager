"""Prepare a supervised test IPK offline; never installs or contacts a receiver.

The permanent inspection IPK is retained unchanged. This creates a separate
deployment candidate whose preinst requires package-specific consent, rejects
source/resource drift and unreviewed removal hooks, and verifies a private
RC79 recovery archive before unpacking. No hook restarts Enigma2 or imports EPG.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

import audit_rc80_rollback_package_readonly as archive
import build_rc80_offline_blocked_ipk as sealed
import build_rc80_snapshot_ipk as snapshot

INSPECTION_SHA = 'd693dee2289eb2f907e0dd18b9616b3ee914603085a032d12d80628a0bb5b5b7'
OUTPUT = 'EPGManager_RC80_SUPERVISED_TEST.ipk'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def openatv_control(raw):
    """OpenATV installs python3-core; the historical python3 dependency is absent."""
    updated, count = re.subn(rb'(?m)^Depends: python3(?=,|$)', b'Depends: python3-core', raw)
    if count != 1:
        raise ValueError('unexpected legacy Python dependency declaration')
    return updated


def hook_source(guard, manifest, mode):
    code = guard + '\nmain(' + repr(mode) + ', ' + repr(manifest) + ')\n'
    compile(code, '<rc80-supervised-hook>', 'exec')
    return ("#!/bin/sh\nset -eu\npython3 - <<'RC80_PYTHON'\n" + code +
            '\nRC80_PYTHON\n').encode('utf-8')


def prepare(inspection_ipk, source_snapshot, inventory_path, assets_path, output_dir):
    if Path('/proc/stb/info/model').exists():
        raise ValueError('prepare only on workstation')
    folder = Path(output_dir).absolute()
    if (folder.is_symlink() or not folder.is_dir() or
            folder.resolve().is_relative_to(snapshot.gate.REPO.resolve()) or
            str(folder.resolve()).startswith(('/usr/lib/enigma2', '/etc/enigma2'))):
        raise ValueError('private workstation output required')
    original_path = Path(inspection_ipk)
    if original_path.is_symlink() or not original_path.is_file():
        raise ValueError('inspection package unavailable')
    raw = original_path.read_bytes()
    if sha(raw) != INSPECTION_SHA:
        raise ValueError('inspection package SHA mismatch')
    parts = archive.read_ar(raw)
    controls, cmodes = archive.read_tar(parts['control.tar.gz'])
    files, modes = archive.read_tar(parts['data.tar.gz'])
    if controls.get('preinst') != sealed.BLOCKED_PREINST or len(files) != 215:
        raise ValueError('incorrect sealed inspection package')
    inventory = snapshot.private_json(inventory_path)
    observed = snapshot.private_json(source_snapshot)
    asset_doc = snapshot.private_json(assets_path)
    assets, amodes = snapshot.assets_from_snapshot(asset_doc, inventory)
    before = {}
    for rel, encoded in observed['source_files'].items():
        value = base64.b64decode(encoded, validate=True)
        row = inventory['sources'][rel]
        if sha(value) != row['sha256']:
            raise ValueError('observed source drift')
        before[rel] = {'sha256': sha(value), 'bytes': len(value), 'mode': row['mode']}
    if len(before) != 138 or set(before) != set(inventory['sources']):
        raise ValueError('original 138-module inventory mismatch')
    before.update({rel: {'sha256': sha(value), 'bytes': len(value), 'mode': amodes[rel]}
                   for rel, value in assets.items()})
    after = {}
    for name, value in files.items():
        if not name.startswith(archive.PREFIX):
            raise ValueError('foreign package payload')
        rel = name[len(archive.PREFIX):]
        if not snapshot.application_path(rel):
            raise ValueError('unsafe package path')
        after[rel] = {'sha256': sha(value), 'bytes': len(value), 'mode': modes[name]}
        if rel in assets and (value != assets[rel] or modes[name] != amodes[rel]):
            raise ValueError('changed application resource')
    manifest = {'version': snapshot.VERSION, 'schema': 15,
                'consent': 'install-rc80-' + INSPECTION_SHA[:16],
                'before': before, 'after': after}
    guard = (Path(__file__).parent/'rc80_supervised_guard.py').read_text(encoding='utf-8')
    controls['preinst'] = hook_source(guard, manifest, 'backup')
    controls['postinst'] = hook_source(guard, manifest, 'verify')
    for h in ('prerm', 'postrm'):
        controls[h] = sealed.PASSIVE_HOOK
    controls['control'] = openatv_control(controls['control']).replace(
        b'Description: EPGManager RC80 complete offline inspection; install blocked',
        b'Description: EPGManager RC80 supervised hardware test candidate')
    out = sealed._ar((('debian-binary', b'2.0\n'),
                      ('control.tar.gz', sealed._tar_gzip(controls, cmodes)),
                      ('data.tar.gz', parts['data.tar.gz'])))
    check = archive.read_ar(out)
    if check['data.tar.gz'] != parts['data.tar.gz']:
        raise ValueError('candidate application bytes changed')
    parsed_hooks, parsed_modes = archive.read_tar(check['control.tar.gz'])
    if parsed_hooks != controls or parsed_modes != cmodes:
        raise ValueError('supervised hook archive mismatch')
    # The launcher check is mandatory before opkg, since old removal scripts
    # can execute before a new package's preinst. This file is read-only.
    preflight = guard + '\nmain("check", ' + repr(manifest) + ')\n'
    compile(preflight, '<rc80-readonly-preflight>', 'exec')
    for name in (OUTPUT, 'rc80-preflight.private.py', 'rc80-install-manifest.private.json'):
        if (folder/name).exists() or (folder/name).is_symlink():
            raise ValueError('refusing to overwrite prepared artifact')
    with (folder/OUTPUT).open('xb') as sink:
        sink.write(out)
    with (folder/'rc80-preflight.private.py').open('x', encoding='utf-8', newline='\n') as sink:
        sink.write(preflight)
    with (folder/'rc80-install-manifest.private.json').open('x', encoding='utf-8') as sink:
        json.dump(manifest, sink, indent=2)
    return {'status': 'SUPERVISED_CANDIDATE_PREPARED_NOT_INSTALLED',
            'version': snapshot.VERSION, 'schema': 15,
            'candidate_sha256': sha(out), 'candidate_bytes': len(out),
            'application_payload_identical_to_tested_inspection_ipk': True,
            'source_modules': 140, 'unchanged_assets': 75,
            'python_dependency': 'python3-core',
            'consent_required': True, 'verified_backup_required': True,
            'read_only_preflight_required_before_opkg': True,
            'installed_removal_hooks_must_be_passive': True,
            'receiver_modified': False, 'receiver_installation_tested': False,
            'native_import_executed': False, 'enigma_restart_requested': False,
            'authorized_to_install': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('inspection-ipk', 'source-snapshot', 'inventory', 'assets', 'output-dir'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.inspection_ipk, args.source_snapshot, args.inventory,
                             args.assets, args.output_dir), indent=2))
