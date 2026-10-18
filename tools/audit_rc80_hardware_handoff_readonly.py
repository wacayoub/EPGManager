#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only RC80 receiver handoff. NEVER certifies GUI/import by code symbols.

Reads only installed plugin Python modules, process names, and presence of
EPGImport. Never reads mappings, XMLTV, settings, logs, credentials, network,
or writes/restarts/installs anything. Suitable for baseline/lab observations.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

PLUGIN = Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
EXTENSIONS = Path('/usr/lib/enigma2/python/Plugins/Extensions')
PROC = Path('/proc')
BASE_VERSION = '2026.10.08-rc79.1'
BRIDGES = {
    'core/verified_epg_match.py': 'ce1ce19345d19b39e876757c3571d71d51850059',
    'core/verified_epg_refresh.py': '17a15648b5fc5a666a6de62a0599a51f2f015cea',
}
MAPPING = (
    '_auto_batch_match', '_merge_persisted_store_ownership',
    'auto_map_current_channel', 'auto_map_all', 'undo_last_mapping',
    'open_manual_source_override', '_start_auto_repair_batch',
    '_queue_manual_generated_sync', '_queue_github_best_match_health',
    '_poll_github_best_match_health', '_cross_source_suggestion_candidates',
    '_refresh_cross_source_suggestions',
)
FUNCTIONAL = (
    'gui_keys', 'github_native_epg_import', 'manual_auto_mapping_after_reboot',
    'casablanca_24h_scheduler', 'source_programme_quality',
    'epg_retention_after_reboot',
)

def _source(root, rel):
    p = root / rel
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 3 * 1024 * 1024:
        raise ValueError('unsafe_source:' + rel)
    return p.read_bytes()

def _blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()

def _version(raw):
    t = ast.parse(raw.decode('utf-8'), filename='version.py')
    vals = {}
    for n in t.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if n.targets[0].id in ('__version__', 'CONFIG_SCHEMA_VERSION'):
                vals[n.targets[0].id] = ast.literal_eval(n.value)
    return vals.get('__version__'), vals.get('CONFIG_SCHEMA_VERSION')

def _mapping(raw):
    t = ast.parse(raw.decode('utf-8'), filename='ui/channel_mapping.py')
    screens = [n for n in t.body if isinstance(n, ast.ClassDef) and n.name == 'ChannelMappingScreen']
    if len(screens) != 1:
        raise ValueError('mapping_screen_not_unique')
    found = [n.name for n in screens[0].body if isinstance(n, ast.FunctionDef)]
    if len(found) != len(set(found)):
        raise ValueError('duplicate_mapping_methods')
    return set(found)

def _enigma_running(proc):
    if not proc.is_dir():
        return None
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            if (p / 'comm').read_text(encoding='ascii', errors='replace').strip() in ('enigma2', 'enigma2.sh'):
                return True
        except OSError:
            pass
    return False

def audit(plugin=PLUGIN, extensions=EXTENSIONS, proc=PROC):
    root = Path(plugin)
    r = {
        'audit': 'EPGManager RC80 read-only hardware handoff',
        'receiver_modified': False, 'opkg_executed': False,
        'mappings_read': False, 'configuration_read': False,
        'epg_xmltv_read': False, 'network_accessed': False,
        'authorized_to_install': False, 'hardware_certified': False,
        'opkg_rollback_status': 'NOT_TESTED_WAIVED_BY_OWNER',
        'functional_evidence': {k: 'NOT_TESTED' for k in FUNCTIONAL},
        'observations': [],
    }
    if root.is_symlink() or not root.is_dir():
        r['status'] = 'NO_GO_PLUGIN_MISSING'
        return r
    files = sorted(root.rglob('*.py'))
    faults = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            ast.parse(_source(root, rel).decode('utf-8'), filename=rel)
        except (OSError, ValueError, SyntaxError, UnicodeError):
            faults.append(rel)
    try:
        version, schema = _version(_source(root, 'version.py'))
        methods = _mapping(_source(root, 'ui/channel_mapping.py'))
    except (OSError, ValueError, SyntaxError, UnicodeError, TypeError):
        r['status'] = 'NO_GO_CRITICAL_SOURCE'
        return r
    kind = ('RC79' if version == BASE_VERSION else
            'RC80' if re.fullmatch(r'\d{4}\.\d{2}\.\d{2}-rc80[-A-Za-z0-9.]*', str(version or '')) else
            'UNKNOWN')
    required = 138 if kind == 'RC79' else 140
    r.update(installed_version=version, schema=schema,
             python_modules=len(files), expected_python_modules=required,
             target_kind=kind, syntax_errors=len(faults),
             missing_mapping_methods=[m for m in MAPPING if m not in methods],
             epgimport_present=(Path(extensions) / 'EPGImport').is_dir(),
             enigma_process_running=_enigma_running(Path(proc)),
             python_runtime='.'.join(map(str, sys.version_info[:3])))
    if faults:
        r['observations'].append('BAD_SOURCE_SYNTAX_OR_UNSAFE_FILE')
    if kind == 'UNKNOWN':
        r['observations'].append('UNKNOWN_VERSION')
    if len(files) != required:
        r['observations'].append('MODULE_COUNT_CHANGED')
    if schema != 15:
        r['observations'].append('CONFIG_SCHEMA_CHANGED')
    if r['missing_mapping_methods']:
        r['observations'].append('MAPPING_API_REGRESSION')
    if not r['epgimport_present']:
        r['observations'].append('EPGIMPORT_NOT_INSTALLED')
    if r['enigma_process_running'] is False:
        r['observations'].append('ENIGMA2_NOT_RUNNING')
    if kind == 'RC80':
        for path, expected in BRIDGES.items():
            try:
                if _blob(_source(root, path)) != expected:
                    r['observations'].append('UNVERIFIED_RC80_BRIDGE:' + path)
            except (OSError, ValueError):
                r['observations'].append('MISSING_RC80_BRIDGE:' + path)
    r['status'] = ('NO_GO_STATIC_PREREQUISITES' if r['observations'] else
                   'RC79_BASELINE_STATIC_PASS' if kind == 'RC79' else
                   'RC80_STATIC_ONLY_PASS')
    return r

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plugin-root', default=str(PLUGIN))
    p.add_argument('--extensions-root', default=str(EXTENSIONS))
    p.add_argument('--proc-root', default=str(PROC))
    args = p.parse_args()
    try:
        r = audit(args.plugin_root, args.extensions_root, args.proc_root)
    except (OSError, ValueError, SyntaxError, TypeError) as exc:
        r = {'status':'NO_GO_AUDIT_ERROR','error_type':type(exc).__name__,
             'authorized_to_install':False,'receiver_modified':False,'hardware_certified':False}
    print(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if r['status'] in ('RC79_BASELINE_STATIC_PASS','RC80_STATIC_ONLY_PASS') else 2

if __name__ == '__main__':
    sys.exit(main())
