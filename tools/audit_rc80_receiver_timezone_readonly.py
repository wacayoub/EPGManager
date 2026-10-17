#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager RC80: non-destructive receiver clock preflight; no configuration,
mapping, EPG, logs, network or installer activity. NOT a hardware RC80 test."""
import ast
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT=Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
EPGIMPORT=Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGImport')
SAMPLES=[
    ('2026-06-01T12:00:00',3600),
    ('2026-09-19T12:00:00',3600),
    ('2026-09-20T12:00:00',0),
    ('2026-10-17T06:00:00',0),
    ('2027-01-15T06:00:00',0),
]
def observe(root=ROOT,epgimport=EPGIMPORT):
    out=dict(audit='EPGManager RC80 timezone/receiver baseline READ_ONLY',
      read_only=True, receiver_modified=False, opkg_executed=False,
      network_accessed=False, authorized_to_install_rc80=False,
      authorized_to_publish_rc80=False, hardware_certified=False,
      functional_gui_import_scheduler='NOT_TESTED',
      opkg_rollback='NOT_TESTED_WAIVED_BY_OWNER',
      python_runtime='.'.join(str(i) for i in sys.version_info[:3]))
    try:
        z=ZoneInfo('Africa/Casablanca')
        rows=[]
        for local,expected in SAMPLES:
            offset=datetime.fromisoformat(local).replace(tzinfo=z).utcoffset()
            seconds=None if offset is None else int(offset.total_seconds())
            rows.append(dict(local=local,observed_offset_seconds=seconds,
                             expected_offset_seconds=expected,correct=seconds==expected))
        out['casablanca_time_offsets']=rows
        out['casablanca_timezone_pass']=all(row['correct'] for row in rows)
    except (ZoneInfoNotFoundError,OSError,ValueError) as exc:
        out['casablanca_timezone_pass']=False
        out['timezone_error_type']=type(exc).__name__
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        out['installed_plugin_status']='MISSING'
    else:
        try:
            files=list(root.rglob('*.py'))
            bad=0
            for f in files:
                if f.is_symlink() or not f.is_file() or f.stat().st_size>3*1024*1024:
                    bad+=1
                    continue
                try: ast.parse(f.read_text(encoding='utf-8'))
                except (SyntaxError,UnicodeError):bad+=1
            v=root/'version.py'
            if v.is_symlink() or not v.is_file():raise ValueError('invalid version source')
            tree=ast.parse(v.read_text(encoding='utf-8'))
            fields={}
            for n in tree.body:
                if isinstance(n,ast.Assign):
                    for target in n.targets:
                        if isinstance(target,ast.Name) and target.id in ('__version__','CONFIG_SCHEMA_VERSION'):
                            fields[target.id]=ast.literal_eval(n.value)
            out.update(installed_version=fields.get('__version__'),installed_schema=fields.get('CONFIG_SCHEMA_VERSION'),
                       installed_modules=len(files),syntax_errors=bad)
            out['installed_plugin_status']=('RC79_STATIC_PASS' if
                fields.get('__version__')=='2026.10.08-rc79.1' and
                fields.get('CONFIG_SCHEMA_VERSION')==15 and len(files)==138 and bad==0
                else 'UNEXPECTED_INSTALLED_PLUGIN')
        except (OSError,UnicodeError,ValueError,TypeError,SyntaxError) as exc:
            out['installed_plugin_status']='INVALID_SOURCE'
            out['source_error_type']=type(exc).__name__
    out['epgimport_present']=Path(epgimport).is_dir()
    out['status']=('NO_GO_TIMEZONE_DATA' if not out['casablanca_timezone_pass']
       else 'NO_GO_RECEIVER_BASELINE' if out['installed_plugin_status']!='RC79_STATIC_PASS'
       else 'NO_GO_EPGIMPORT' if not out['epgimport_present']
       else 'RC79_BASELINE_AND_CASABLANCA_GMT_PASS_NOT_RC80_CERTIFIED')
    return out

if __name__=='__main__':
    r=observe()
    print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    sys.exit(0 if r['status']=='RC79_BASELINE_AND_CASABLANCA_GMT_PASS_NOT_RC80_CERTIFIED' else 2)
