#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager RC80 readiness: receiver read-only code health audit.

Reads ONLY installed plugin Python source and opkg status. Does not load
receiver settings, EPG files, XMLTV, channel names or mappings, and does not
create files, import Enigma2, restart GUI, fetch feeds or run opkg changes.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
KNOWN_UI_SHA = '314cbd1dee923bdbe4b1930867050f57194a45bd94970a6924f21df6a2007002'
EXPECTED_VERSION = '2026.10.08-rc79.1'

def inspect(root=ROOT):
    result = {
        'audit':'EPGManager RC80 best-match receiver readiness',
        'mode':'READ_ONLY','authorized_to_install_rc80':False,
        'configuration_read':False,'mapping_read':False,
        'xmltv_read':False,'opkg_write':False,
        'restarts':False,'hardware_gui_and_import_validated':False
    }
    if not root.is_dir():
        result.update(status='NO_GO',reason='EPGMANAGER_NOT_FOUND')
        return result
    modules = sorted(p for p in root.rglob('*.py') if p.is_file() and not p.is_symlink())
    result['python_modules'] = len(modules)

    def source(rel):
        p = root/rel
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1_000_000:
            return None
        return p.read_bytes()

    v = source('version.py')
    if v is None:
        result.update(status='NO_GO',reason='VERSION_MISSING')
        return result
    try:
        tree = ast.parse(v.decode('utf-8'),filename='version.py')
        vals = {}
        for node in tree.body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                name=node.targets[0].id
                if name in ('__version__','CONFIG_SCHEMA_VERSION'):
                    try: vals[name]=ast.literal_eval(node.value)
                    except (ValueError,TypeError): pass
        result['version']=vals.get('__version__')
        result['schema']=vals.get('CONFIG_SCHEMA_VERSION')
    except (SyntaxError,UnicodeError):
        result.update(status='NO_GO',reason='BAD_VERSION_SYNTAX')
        return result

    ui=source('ui/channel_mapping.py')
    health=source('core/github_match_health.py')
    sync=source('core/github_direct_sync.py')
    result['modules_present']={
        'smart_mapping_ui':ui is not None,
        'github_match_health':health is not None,
        'github_direct_sync':sync is not None,
        'verified_epg_match':source('core/verified_epg_match.py') is not None,
        'verified_epg_refresh':source('core/verified_epg_refresh.py') is not None
    }
    result['ui_known_baseline_sha256']=bool(ui and hashlib.sha256(ui).hexdigest()==KNOWN_UI_SHA)
    for label,buf in (('ui',ui),('health',health)):
        functions=set()
        if buf is not None:
            try:
                parsed=ast.parse(buf.decode('utf-8'),filename=label)
                functions={n.name for n in ast.walk(parsed) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
            except (SyntaxError,UnicodeError): pass
        if label=='ui':
            result['dynamic_best_match_callbacks']={k:(k in functions) for k in (
                '_queue_github_best_match_health','_poll_github_best_match_health')}
        else:
            result['dynamic_quality_engine']={k:(k in functions) for k in (
                'refresh_async','is_busy','generation')}
    result['epgimport_plugin_present']=Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGImport').is_dir()
    reasons=[]
    if result['version']!=EXPECTED_VERSION: reasons.append('UNEXPECTED_RUNNING_VERSION')
    if result['schema']!=15: reasons.append('UNEXPECTED_SCHEMA')
    if len(modules)!=138: reasons.append('UNEXPECTED_PYTHON_COUNT')
    if not all(result['dynamic_best_match_callbacks'].values()): reasons.append('LIVE_BEST_MATCH_CALLBACK_MISSING')
    if not all(result['dynamic_quality_engine'].values()): reasons.append('LIVE_QUALITY_ENGINE_MISSING')
    if not result['epgimport_plugin_present']: reasons.append('EPGIMPORT_NOT_FOUND_AT_KNOWN_PATH')
    if reasons:
        result.update(status='NO_GO',reasons=reasons)
    else:
        result.update(status='BASELINE_RECEIVER_READ_ONLY_PASS',
            reason='RC79.1 preserved; RC80 build/GUI/native import still NOT validated')
    return result

if __name__=='__main__':
    print(json.dumps(inspect(),sort_keys=True,indent=2,ensure_ascii=False))
