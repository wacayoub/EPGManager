"""Build a complete, permanently blocked RC80 inspection IPK on the PC.

Uses the observed installed source/asset snapshots when the historical rescue
is unavailable. Does not substitute a different hash in the rescue builder.
Never runs opkg, imports plugin code, or writes to a receiver. Private inputs
and output remain outside Git. Installation always exits 90 in preinst.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile

import apply_rc80_private_functional_fixes as fixes
import audit_rc80_package_manifest_readonly as gate
import audit_rc80_rollback_package_readonly as archive
import build_rc80_offline_blocked_ipk as sealed
import reconcile_rc80_private_mapping as mapping

VERSION = '2026.10.18-rc80-candidate'
OUTPUT = 'EPGManager_RC80_COMPLETE_OFFLINE_BLOCKED.ipk'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private_json(path, expected_sha=None):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 40 * 1024 * 1024:
        raise ValueError('unsafe private snapshot')
    raw = p.read_bytes()
    if expected_sha is not None and sha(raw) != expected_sha:
        raise ValueError('snapshot SHA256 mismatch')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid private snapshot')
    return value


def application_path(rel):
    # Public gate's .py validator already implements strict POSIX topology.
    return isinstance(rel, str) and gate.valid_module_path(rel + '.py')


def assets_from_snapshot(snapshot, inventory):
    rows = inventory.get('assets')
    recorded = snapshot.get('assets')
    if (snapshot.get('receiver_modified') is not False or
            snapshot.get('source_hashes_rechecked') != 138 or
            not isinstance(rows, list) or len(rows) != 75 or
            not isinstance(recorded, dict) or len(recorded) != 75):
        raise ValueError('expected 75 observed assets and 138 rechecked modules')
    result, modes = {}, {}
    for row in rows:
        rel = row.get('path') if isinstance(row, dict) else None
        if (not application_path(rel) or rel.endswith(('.py', '.pyc', '.pyo')) or
                rel in result or rel not in recorded):
            raise ValueError('unsafe or duplicate asset path')
        data = recorded[rel]
        raw = base64.b64decode(data['base64'], validate=True)
        mode = row.get('mode')
        if (type(mode) is not int or mode & ~0o777 or
                mode != data.get('mode') or len(raw) != row.get('bytes') or
                len(raw) > archive.MAX_MEMBER or sha(raw) != data.get('sha256')):
            raise ValueError('asset hash, mode or size mismatch')
        result[rel], modes[rel] = raw, mode
    if set(result) != set(recorded):
        raise ValueError('unexpected snapshot asset')
    return result, modes


def control_for_candidate(raw):
    if b'\r' in raw:
        raise ValueError('CRLF package metadata rejected')
    meta = archive.metadata(raw)
    if (meta.get('package') != archive.PACKAGE or meta.get('version') != archive.VERSION or
            meta.get('architecture') != 'all'):
        raise ValueError('unexpected installed package identity')
    output, count = re.subn(rb'(?m)^Version: [^\n]+$',
                           b'Version: ' + VERSION.encode(), raw)
    if count != 1 or len(re.findall(rb'(?im)^Package:', raw)) != 1:
        raise ValueError('ambiguous control metadata')
    output = re.sub(rb'(?m)^Description: [^\n]+$',
                    b'Description: EPGManager RC80 complete offline inspection; install blocked', output)
    return output


def pack(control, sources, assets, modes):
    if (len(sources) != 140 or len(assets) != 75 or
            set(sources) & set(assets) or set(modes) != set(sources) | set(assets)):
        raise ValueError('package must have 140 modules and 75 unchanged assets')
    app = {**sources, **assets}
    if any(not application_path(p) for p in app):
        raise ValueError('unsafe payload path')
    payload = {archive.PREFIX + p: value for p, value in app.items()}
    payload_modes = {archive.PREFIX + p: mode for p, mode in modes.items()}
    controls = {'control': control_for_candidate(control),
                # Protect any locally edited application resources in a later
                # separately approved deployment; no user config is packaged.
                'conffiles': ''.join('/' + archive.PREFIX + p + '\n'
                                     for p in sorted(assets)).encode(),
                **{h: sealed.BLOCKED_PREINST if h == 'preinst' else sealed.PASSIVE_HOOK
                   for h in sealed.HOOKS}}
    cmodes = {k: 0o755 if k in sealed.HOOKS else 0o644 for k in controls}
    raw = sealed._ar((('debian-binary', b'2.0\n'),
                      ('control.tar.gz', sealed._tar_gzip(controls, cmodes)),
                      ('data.tar.gz', sealed._tar_gzip(payload, payload_modes))))
    if len(raw) > archive.MAX_IPK:
        raise ValueError('IPK exceeds size budget')
    parsed = archive.read_ar(raw)
    unpacked, actual_modes = archive.read_tar(parsed['data.tar.gz'])
    actual_controls, actual_cmodes = archive.read_tar(parsed['control.tar.gz'])
    if (unpacked != payload or actual_modes != payload_modes or
            actual_controls != controls or actual_cmodes != cmodes):
        raise ValueError('IPK round-trip mismatch')
    return raw


def build(source_snapshot, inventory_path, asset_snapshot, inventory_sha,
          asset_sha, output_dir):
    if Path('/proc/stb/info/model').exists() or gate.REPO == Path('/usr/lib/enigma2'):
        raise ValueError('must run on workstation')
    folder = Path(output_dir).absolute()
    if (not folder.is_dir() or folder.is_symlink() or
            folder.resolve().is_relative_to(gate.REPO.resolve()) or
            str(folder.resolve()).startswith(('/usr/lib/enigma2', '/etc/enigma2'))):
        raise ValueError('output must be a private workstation directory outside Git')
    inventory = private_json(inventory_path, inventory_sha)
    observed = private_json(source_snapshot)
    asset_doc = private_json(asset_snapshot, asset_sha)
    original = {rel: base64.b64decode(value, validate=True)
                for rel, value in observed['source_files'].items()}
    records = inventory.get('sources')
    if len(original) != 138 or not isinstance(records, dict) or set(records) != set(original):
        raise ValueError('receiver source inventory mismatch')
    for rel, raw in original.items():
        if not gate.valid_module_path(rel) or sha(raw) != records[rel].get('sha256'):
            raise ValueError('receiver source hash mismatch')
    assets, asset_modes = assets_from_snapshot(asset_doc, inventory)
    sources = dict(original)
    sources.update(fixes.corrected({rel: original[rel] for rel in fixes.ORIGINAL}))
    sources['ui/channel_mapping.py'], _ = mapping.reconcile_private_ui(
        original['ui/channel_mapping.py'],
        (gate.REPO/'source/overlay/ui/channel_mapping.py').read_bytes())
    for rel in gate.CORE_ADDITIONS:
        sources[rel] = (gate.REPO/'source/overlay'/rel).read_bytes()
    sources['version.py'], count = re.subn(
        rb'(?m)^__version__\s*=\s*[\'\"][^\'\"]+[\'\"]',
        b'__version__ = "' + VERSION.encode() + b'"', sources['version.py'])
    if count != 1:
        raise ValueError('ambiguous runtime version')
    manifest = {'receiver_version': archive.VERSION, 'config_schema': 15,
                'files': {rel: {'sha256': sha(raw), 'bytes': len(raw)}
                          for rel, raw in original.items()}}
    reviewed = {rel: sha(sources[rel]) for rel in ('version.py', *fixes.ORIGINAL)}
    with tempfile.TemporaryDirectory(prefix='rc80-package-check-') as tmp:
        for rel, raw in sources.items():
            path = Path(tmp)/rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        tree_report = gate.audit(tmp, manifest, reviewed)
    modes = {**{rel: row['mode'] for rel, row in records.items()}, **asset_modes,
             **{rel: 0o644 for rel in gate.CORE_ADDITIONS}}
    control = base64.b64decode(asset_doc['package_control_base64'], validate=True)
    raw = pack(control, sources, assets, modes)
    if raw != pack(control, sources, assets, modes):
        raise ValueError('nondeterministic package')
    target = folder/OUTPUT
    if target.exists() or target.is_symlink():
        raise ValueError('refusing to overwrite private output')
    with target.open('xb') as sink:
        sink.write(raw)
    return {'status': 'COMPLETE_OFFLINE_INSPECTION_IPK_INSTALL_BLOCKED',
            'package_built': True,
            'version': VERSION, 'schema': 15, 'source_modules': 140,
            'unchanged_assets': 75, 'payload_files': 215,
            'snapshot_assets_sha256': asset_sha, 'inventory_sha256': inventory_sha,
            'candidate_sha256': sha(raw), 'candidate_bytes': len(raw),
            'candidate_filename': OUTPUT, 'deterministic_bytes_verified': True,
            'payload_hash_and_mode_roundtrip_verified': True,
            'source_gate': tree_report, 'preinst_unconditionally_exits': 90,
            'postinst_prerm_postrm_passive': True, 'resource_conffiles': 75,
            'receiver_modified': False, 'opkg_executed': False,
            'authorized_to_install': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('source-snapshot', 'inventory', 'asset-snapshot', 'inventory-sha256',
                'asset-sha256', 'output-dir'):
        parser.add_argument('--' + arg, required=True)
    a = parser.parse_args()
    print(json.dumps(build(a.source_snapshot, a.inventory, a.asset_snapshot,
                           a.inventory_sha256, a.asset_sha256, a.output_dir), indent=2))
