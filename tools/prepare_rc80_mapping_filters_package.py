"""Build a guarded Mapping-filter IPK from the exact installed readable UI package offline."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import prepare_rc80_mapping_filters as fix
import audit_rc80_rollback_package_readonly as archive
import build_rc80_offline_blocked_ipk as sealed
import prepare_rc80_supervised_ipk as supervised

ORIGINAL_IPK_SHA = 'bafee7f5d8911449a44ef3578fa2da4c74e55e8870e049cc6ee4c5259758ef64'
REPO = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def updated_guard(source):
    tree = ast.parse(source)
    call = tree.body[-1]
    if not (isinstance(call, ast.Expr) and isinstance(call.value, ast.Call) and
            isinstance(call.value.func, ast.Name) and call.value.func.id == 'main' and
            ast.literal_eval(call.value.args[0]) == 'backup'):
        raise ValueError('original preinst entrypoint drift')
    guard = ''.join(source.splitlines(keepends=True)[:call.lineno-1])
    old = "manifest['before'], 142"
    if guard.count(old) != 3:
        raise ValueError('baseline verification count drift')
    if "verify_tree(PLUGIN, manifest['after'], 142)" not in guard:
        raise ValueError('installed module count drift')
    if "os.environ.get('EPGMGR_RC80_CONSENT') != manifest['consent']" not in guard:
        raise ValueError('installation consent guard missing')
    ast.parse(guard)
    return guard


def build(original, output):
    if Path('/proc/stb/info/model').exists():
        raise ValueError('workstation only')
    output = Path(output).absolute()
    if (output.is_symlink() or any(p.is_symlink() for p in output.parents) or
            not output.is_dir() or output.resolve().is_relative_to(REPO.resolve())):
        raise ValueError('existing private output directory required')
    path = Path(original)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > archive.MAX_IPK:
        raise ValueError('unsafe original IPK')
    raw = path.read_bytes()
    if sha(raw) != ORIGINAL_IPK_SHA:
        raise ValueError('installed final IPK identity mismatch')
    ar = archive.read_ar(raw)
    controls, cmodes = archive.read_tar(ar['control.tar.gz'])
    payload, pmodes = archive.read_tar(ar['data.tar.gz'])
    if len(payload) != 217 or sum(name.endswith('.py') for name in payload) != 142:
        raise ValueError('installed final application inventory drift')
    replacements = fix.corrected({p: payload[archive.PREFIX+p] for p in fix.ORIGINAL})
    before = {name[len(archive.PREFIX):]: {'sha256': sha(value), 'bytes': len(value), 'mode': pmodes[name]}
              for name, value in sorted(payload.items())}
    for name, value in replacements.items():
        ast.parse(value)
        payload[archive.PREFIX+name] = value
        pmodes.setdefault(archive.PREFIX+name, 0o644)
    after = {name[len(archive.PREFIX):]: {'sha256': sha(value), 'bytes': len(value), 'mode': pmodes[name]}
             for name, value in sorted(payload.items())}
    manifest = {'version': '2026.10.20-rc80', 'schema': 15, 'before': before, 'after': after,
                'consent': 'install-rc80-mapping-filters-'+sha(json.dumps(after,sort_keys=True).encode())[:16]}
    hook = controls['preinst'].decode('utf-8')
    marker = "python3 - <<'RC80_PYTHON'\n"
    source = hook[hook.index(marker)+len(marker):hook.rindex('\nRC80_PYTHON')]
    guard = updated_guard(source)
    controls['preinst'] = supervised.hook_source(guard, manifest, 'backup')
    controls['postinst'] = supervised.hook_source(guard, manifest, 'verify')
    if controls['prerm'] != sealed.PASSIVE_HOOK or controls['postrm'] != sealed.PASSIVE_HOOK:
        raise ValueError('nonpassive removal hook')
    def pack():
        return sealed._ar((('debian-binary', b'2.0\n'),
            ('control.tar.gz', sealed._tar_gzip(controls, cmodes)),
            ('data.tar.gz', sealed._tar_gzip(payload, pmodes))))
    if any(payload[archive.PREFIX+n] != raw for n, raw in replacements.items()):
        raise ValueError('replacement drift')
    result = pack()
    if result != pack():
        raise ValueError('package is not deterministic')
    check = archive.read_ar(result)
    if (archive.read_tar(check['control.tar.gz']) != (controls, cmodes) or
            archive.read_tar(check['data.tar.gz']) != (payload, pmodes)):
        raise ValueError('package bytes/modes roundtrip mismatch')
    target = output/'enigma2-plugin-extensions-epgmanager_2026.10.20-rc80_all.ipk'
    with target.open('xb') as stream:
        stream.write(result)
    (output/'private-install-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    report = {'version':manifest['version'], 'schema':15, 'python_modules':142,
        'resources':75, 'payload_files':217, 'bytes':len(result), 'sha256':sha(result),
        'filename':target.name, 'changed_existing_modules':list(fix.ORIGINAL),
        'added_module':None, 'unchanged_application_files':216, 'same_package_version_requires_force_reinstall':True,
        'explicit_consent_still_required':True, 'native_import_engine_unchanged':True,
        'user_settings_packaged':False, 'receiver_changed':False, 'installed':False,
        'deterministic_bytes_verified':True, 'payload_and_modes_roundtrip_verified':True}
    (output/'package-summary.private.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',required=True)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.original,args.output),indent=2))
