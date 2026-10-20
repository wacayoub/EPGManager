"""Offline final packaging of the exact receiver-qualified 141-module payload.

Only version.py branding changes. No receiver connection or installation occurs.
Private recovery/configuration contents are never included in the package.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile

import audit_rc80_rollback_package_readonly as archive
import build_rc80_offline_blocked_ipk as sealed
import install_rc80_stale_profile as identities
import prepare_rc80_supervised_ipk as supervised

VERSION = '2026.10.20-rc80'
ZIP_SHA = '40ab3b63c455a40a42517b84be9515322de97506f33d4695fe6a7dd846dc5cb9'
RECOVERY_SHA = '4daf86c9f69d2b0177b902941ceebb73233a0492d720b9039e2b2b72387cf3b1'
INSPECTION_SHA = '81cff2c29ef3c7716bb1a6d88ad6f7a5996103c17570f2261fecb9a954ece1ee'
REPO = Path(__file__).resolve().parents[1]
GUARD_SHA = 'd31283c879da27080ed6f2f921f58c30efefc339e041c7f9989ee1c44b199bd9'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def final_guard_source(source):
    if sha(source.encode('utf-8')) != GUARD_SHA:
        raise ValueError('reviewed installation guard identity drift')
    if source.count("manifest['before'], 138") != 3 or source.count("manifest['after'], 140") != 1:
        raise ValueError('reviewed installation guard drift')
    source = source.replace("manifest['before'], 138", "manifest['before'], 141")
    source = source.replace("manifest['after'], 140", "manifest['after'], 141")
    source = source.replace('rc79-before-rc80-', 'rc80-before-final-')
    source = source.replace('RC79 recovery archive verified', 'RC80 recovery archive verified')
    source = source.replace('RC80 140 modules/schema 15', 'RC80 141 modules/schema 15')
    ast.parse(source)
    return source


def final_version(raw):
    result, n = re.subn(rb'(?m)^__version__\s*=\s*[\'\"][^\'\"]+[\'\"]',
                        b'__version__ = "'+VERSION.encode()+b'"', raw)
    if n != 1:
        raise ValueError('ambiguous version marker')
    def functional_tree(value):
        tree = ast.parse(value)
        tree.body = [n for n in tree.body if not (isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id=='__version__' for t in n.targets))]
        return ast.dump(tree)
    if functional_tree(raw) != functional_tree(result):
        raise ValueError('functional change during final branding')
    schema = [n.value.value for n in ast.parse(result).body if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=='CONFIG_SCHEMA_VERSION' for t in n.targets)
              and isinstance(n.value,ast.Constant)]
    if schema != [15]:
        raise ValueError('schema drift')
    return result


def final_control(raw):
    raw = supervised.openatv_control(raw)
    raw, n = re.subn(rb'(?m)^Version: [^\n]+$', b'Version: '+VERSION.encode(), raw)
    if n != 1:
        raise ValueError('ambiguous package version')
    raw = re.sub(rb'(?m)^Description: [^\n]+$',
                 b'Description: EPGManager RC80 qualified Vu+ Zero 4K candidate', raw)
    return raw


def build(qualified_source, recovery, inspection, output):
    if Path('/proc/stb/info/model').exists():
        raise ValueError('workstation only')
    output = Path(output).absolute()
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('symlinked private output refused')
    output = output.resolve()
    if not output.is_dir() or output.is_relative_to(REPO.resolve()):
        raise ValueError('existing private output directory required')
    inputs = []
    for name, pin in ((qualified_source, ZIP_SHA), (recovery, RECOVERY_SHA), (inspection, INSPECTION_SHA)):
        path = Path(name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>archive.MAX_IPK:
            raise ValueError('unsafe private input')
        raw = path.read_bytes()
        if sha(raw)!=pin:
            raise ValueError('private qualified input drift')
        inputs.append(raw)
    with zipfile.ZipFile(qualified_source) as z:
        names=z.namelist()
        if len(names)!=217 or len(set(names))!=217 or 'candidate-manifest.json' not in names:
            raise ValueError('qualified archive inventory drift')
        files={n[len('EPGManager/'):]:z.read(n) for n in names if n.startswith('EPGManager/')}
    if len(files)!=216 or sum(n.endswith('.py') for n in files)!=141:
        raise ValueError('qualified payload inventory drift')
    if any(not n or n.startswith('/') or '..' in Path(n).parts or '\\' in n for n in files):
        raise ValueError('unsafe application path')
    recovered, recovery_modes=archive.read_tar(inputs[1])
    before={n[len('EPGManager/'):]:v for n,v in recovered.items() if n.startswith('EPGManager/')}
    modes={n[len('EPGManager/'):]:v for n,v in recovery_modes.items() if n.startswith('EPGManager/')}
    if len(before)!=215 or set(files)-set(before)!={'core/quality_profile_freshness.py'} or set(before)-set(files):
        raise ValueError('recovery application inventory drift')
    for n, raw in before.items():
        expected=identities.BEFORE.get(n)
        if expected:
            if sha(raw)!=expected:raise ValueError('recovery preimage drift')
        elif files[n]!=raw:
            raise ValueError('unreviewed qualified application change')
    for n,pin in identities.AFTER.items():
        if sha(files[n])!=pin:raise ValueError('qualified correction drift')
    modes['core/quality_profile_freshness.py']=0o644
    for n,raw in files.items():
        if n.endswith('.py'):ast.parse(raw)
    def manifest_rows(data):
        return {n:{'sha256':sha(raw),'bytes':len(raw),'mode':modes[n]} for n,raw in sorted(data.items())}
    manifest={'version':VERSION,'schema':15,'before':manifest_rows(files)}
    files['version.py']=final_version(files['version.py'])
    manifest['after']=manifest_rows(files)
    manifest['consent']='install-rc80-final-'+sha(json.dumps(manifest['after'],sort_keys=True).encode())[:16]
    original_control,_=archive.read_tar(archive.read_ar(inputs[2])['control.tar.gz'])
    guard=final_guard_source((REPO/'tools/rc80_supervised_guard.py').read_text(encoding='utf-8'))
    controls={'control':final_control(original_control['control']),
              'conffiles':''.join('/'+archive.PREFIX+n+'\n' for n in sorted(files) if not n.endswith('.py')).encode(),
              'preinst':supervised.hook_source(guard,manifest,'backup'),
              'postinst':supervised.hook_source(guard,manifest,'verify'),
              'prerm':sealed.PASSIVE_HOOK,'postrm':sealed.PASSIVE_HOOK}
    cmodes={n:0o755 if n in sealed.HOOKS else 0o644 for n in controls}
    payload={archive.PREFIX+n:v for n,v in files.items()}
    pmodes={archive.PREFIX+n:modes[n] for n in files}
    def pack():
        return sealed._ar((('debian-binary',b'2.0\n'),('control.tar.gz',sealed._tar_gzip(controls,cmodes)),
                           ('data.tar.gz',sealed._tar_gzip(payload,pmodes))))
    raw=pack()
    if raw!=pack():raise ValueError('nondeterministic package')
    parsed=archive.read_ar(raw)
    if archive.read_tar(parsed['data.tar.gz'])!=(payload,pmodes) or archive.read_tar(parsed['control.tar.gz'])!=(controls,cmodes):
        raise ValueError('package byte/mode roundtrip failed')
    target=output/('enigma2-plugin-extensions-epgmanager_'+VERSION+'_all.ipk')
    with target.open('xb') as f:f.write(raw)
    report={'version':VERSION,'schema':15,'python_modules':141,'unchanged_resources':75,'payload_files':216,
            'qualified_application_files_identical':215,'only_runtime_branding_changed':'version.py',
            'functionality_change_from_qualified_receiver':False,'sha256':sha(raw),'bytes':len(raw),
            'package_filename':target.name,'deterministic_bytes_verified':True,'payload_and_modes_roundtrip_verified':True,
            'preinst_requires_explicit_consent':True,'preinst_verifies_141_module_baseline_and_recovery_archive':True,
            'restart_or_import_in_hooks':False,'receiver_accessed':False,'package_installed':False,'package_published':False,
            'consent_value':manifest['consent']}
    (output/'RC80-final-package-manifest.private.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('qualified-source','recovery','inspection','output'):parser.add_argument('--'+name,required=True)
    print(json.dumps(build(**{k.replace('-','_'):v for k,v in vars(parser.parse_args()).items()}),indent=2))
