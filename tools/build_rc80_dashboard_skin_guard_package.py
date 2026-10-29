"""Build a guarded dashboard-only correction from the exact qualified Rytec IPK."""
import argparse
import ast
import json
from pathlib import Path

import prepare_rc80_dashboard_skin_guard as fix
from prepare_rc80_rytec_native_package import archive, sealed, supervised, sha, updated_guard

ORIGINAL_IPK_SHA = '5888e63c3f4f96e531939663014a3d72161af8b86aa81a29a567b36ca8172e23'
REPO = Path(__file__).resolve().parents[1]


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
        raise ValueError('qualified IPK identity mismatch')
    ar = archive.read_ar(raw)
    controls, cmodes = archive.read_tar(ar['control.tar.gz'])
    payload, pmodes = archive.read_tar(ar['data.tar.gz'])
    if len(payload) != 217 or sum(p.endswith('.py') for p in payload) != 142:
        raise ValueError('qualified application inventory drift')
    replacements = fix.corrected({p: payload[archive.PREFIX+p] for p in fix.ORIGINAL})
    def inventory():
        return {p[len(archive.PREFIX):]: {'sha256':sha(v),'bytes':len(v),'mode':pmodes[p]}
                for p,v in sorted(payload.items())}
    before = inventory()
    for name, value in replacements.items():
        ast.parse(value, filename=name)
        payload[archive.PREFIX+name] = value
    after = inventory()
    manifest = {'version':'2026.10.20-rc80','schema':15,'before':before,'after':after,
                'consent':'install-rc80-dashboard-skin-guard-'+sha(json.dumps(after,sort_keys=True).encode())[:16]}
    hook = controls['preinst'].decode('utf-8')
    marker = "python3 - <<'RC80_PYTHON'\n"
    source = hook[hook.index(marker)+len(marker):hook.rindex('\nRC80_PYTHON')]
    guard = updated_guard(source)
    controls['preinst'] = supervised.hook_source(guard,manifest,'backup')
    controls['postinst'] = supervised.hook_source(guard,manifest,'verify')
    if controls['prerm'] != sealed.PASSIVE_HOOK or controls['postrm'] != sealed.PASSIVE_HOOK:
        raise ValueError('nonpassive removal hook')
    def pack():
        return sealed._ar((('debian-binary',b'2.0\n'),
            ('control.tar.gz',sealed._tar_gzip(controls,cmodes)),
            ('data.tar.gz',sealed._tar_gzip(payload,pmodes))))
    result = pack()
    if result != pack():raise ValueError('nondeterministic package')
    check = archive.read_ar(result)
    if (archive.read_tar(check['control.tar.gz']) != (controls,cmodes) or
            archive.read_tar(check['data.tar.gz']) != (payload,pmodes)):
        raise ValueError('package roundtrip mismatch')
    filename = 'enigma2-plugin-extensions-epgmanager_2026.10.20-rc80_all.ipk'
    with (output/filename).open('xb') as stream:stream.write(result)
    (output/'private-install-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    report = {'filename':filename,'sha256':sha(result),'bytes':len(result),
              'modules':142,'payload_files':217,'changed_modules':list(replacements),
              'unchanged_files':216,'deterministic_bytes_and_modes_verified':True,
              'user_settings_packaged':False,'receiver_changed':False,
              'installed':False,'consent_guard_preserved':True,'verified_backup_required':True}
    (output/'package-summary.private.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',required=True)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.original,args.output),indent=2))
