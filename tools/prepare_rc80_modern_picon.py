"""Prepare a guarded icon-only RC80 upgrade offline; no receiver access."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import struct

import audit_rc80_rollback_package_readonly as archive
import build_rc80_offline_blocked_ipk as sealed
import prepare_rc80_supervised_ipk as supervised

BASE_SHA = '99514e7c92ca6333931f469d03577beda5f5b73c890e3beca960adfc9d41cdbd'
REPO = Path(__file__).resolve().parents[1]
ICON = REPO / 'assets/branding/epgmanager-modern.png'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def add_icons(raw):
    """Only add explicit icon keywords to the two main menu descriptors."""
    source = raw.decode('utf-8')
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'Plugins')
    candidates = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'PluginDescriptor':
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if not isinstance(keywords.get('name'), ast.Constant) or keywords['name'].value != 'EPGManager':
            continue
        where = keywords.get('where')
        if not isinstance(where, ast.Attribute) or where.attr not in ('WHERE_PLUGINMENU', 'WHERE_EXTENSIONSMENU'):
            raise ValueError('unexpected main descriptor')
        if 'icon' in keywords:
            raise ValueError('main icon already declared; baseline drift')
        candidates.append(node)
    if len(candidates) != 2:
        raise ValueError('expected two main descriptors')
    lines = source.splitlines(keepends=True)
    for node in sorted(candidates, key=lambda n: n.end_lineno, reverse=True):
        closing = lines[node.end_lineno-1]
        indent = closing[:len(closing)-len(closing.lstrip())] + '    '
        lines.insert(node.end_lineno-1, indent+'icon="plugin.png",\n')
    result = ''.join(lines).encode('utf-8')
    changed = ast.parse(result)
    # Removing exactly the two added keywords must reproduce the entire AST.
    new_fn = next(n for n in changed.body if isinstance(n, ast.FunctionDef) and n.name == 'Plugins')
    removed = 0
    for node in ast.walk(new_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'PluginDescriptor':
            names = {k.arg:k.value for k in node.keywords}
            if isinstance(names.get('name'), ast.Constant) and names['name'].value == 'EPGManager':
                node.keywords = [k for k in node.keywords if k.arg != 'icon']
                removed += 1
    if removed != 2 or ast.dump(changed) != ast.dump(tree):
        raise ValueError('non-icon Python changes')
    return result


def validate_png(raw):
    if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise ValueError('PNG required')
    width, height, depth, colour = struct.unpack('>IIBB', raw[16:26])
    if (width, height, depth, colour) != (100,40,8,6) or len(raw) > 20000:
        raise ValueError('expected compact native 100x40 RGBA8 picon')


def build(original, output):
    if Path('/proc/stb/info/model').exists():
        raise ValueError('workstation only')
    output = Path(output).absolute()
    if output.is_symlink() or not output.is_dir() or output.resolve().is_relative_to(REPO.resolve()):
        raise ValueError('existing private output directory required')
    raw = Path(original).read_bytes()
    if sha(raw) != BASE_SHA:
        raise ValueError('qualified final baseline mismatch')
    ar = archive.read_ar(raw)
    controls, cmodes = archive.read_tar(ar['control.tar.gz'])
    payload, pmodes = archive.read_tar(ar['data.tar.gz'])
    if len(payload) != 217 or sum(n.endswith('.py') for n in payload) != 142:
        raise ValueError('application inventory drift')
    def inventory():
        return {n[len(archive.PREFIX):]:{'sha256':sha(b),'bytes':len(b),'mode':pmodes[n]} for n,b in sorted(payload.items())}
    before = inventory()
    icon = ICON.read_bytes()
    validate_png(icon)
    payload[archive.PREFIX+'plugin.png'] = icon
    payload[archive.PREFIX+'plugin.py'] = add_icons(payload[archive.PREFIX+'plugin.py'])
    after = inventory()
    changed = [n for n in before if before[n] != after[n]]
    if changed != ['plugin.png','plugin.py']:
        raise ValueError('unexpected application changes')
    hook = controls['preinst'].decode('utf-8')
    marker = "python3 - <<'RC80_PYTHON'\n"
    source = hook[hook.index(marker)+len(marker):hook.rindex('\nRC80_PYTHON')]
    call = ast.parse(source).body[-1]
    if not isinstance(call, ast.Expr) or not isinstance(call.value, ast.Call) or call.value.func.id != 'main' or ast.literal_eval(call.value.args[0]) != 'backup':
        raise ValueError('guard entrypoint drift')
    guard = ''.join(source.splitlines(keepends=True)[:call.lineno-1])
    if guard.count("manifest['before'], 142") != 3 or "os.environ.get('EPGMGR_RC80_CONSENT') != manifest['consent']" not in guard:
        raise ValueError('baseline or consent protection drift')
    manifest = {'version':'2026.10.20-rc80','schema':15,'before':before,'after':after,
                'consent':'install-rc80-modern-picon-'+sha(json.dumps(after,sort_keys=True).encode())[:16]}
    controls['preinst'] = supervised.hook_source(guard,manifest,'backup')
    controls['postinst'] = supervised.hook_source(guard,manifest,'verify')
    for name in ('prerm','postrm'):
        if controls[name] != sealed.PASSIVE_HOOK:
            raise ValueError('nonpassive removal hook')
    def pack():
        return sealed._ar((('debian-binary',b'2.0\n'),('control.tar.gz',sealed._tar_gzip(controls,cmodes)),('data.tar.gz',sealed._tar_gzip(payload,pmodes))))
    result = pack()
    check = archive.read_ar(result)
    if result != pack() or archive.read_tar(check['data.tar.gz']) != (payload,pmodes) or archive.read_tar(check['control.tar.gz']) != (controls,cmodes):
        raise ValueError('archive determinism/roundtrip failure')
    name = 'enigma2-plugin-extensions-epgmanager_2026.10.20-rc80_all.ipk'
    with (output/name).open('xb') as f:
        f.write(result)
    (output/'private-install-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    report = {'version':'2026.10.20-rc80','schema':15,'filename':name,'bytes':len(result),'sha256':sha(result),
              'changed_application_files':changed,'unchanged_application_files':215,'python_modules':142,'resources':75,
              'icon_pixels':[100,40],'icon_bytes':len(icon),'icon_sha256':sha(icon),'registration_ast_only_icon_keywords':True,
              'native_import_scheduler_mapping_sources_unchanged':True,'deterministic_roundtrip_verified':True,'receiver_changed':False}
    (output/'package-summary.private.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',required=True)
    p.add_argument('--output',required=True)
    args = p.parse_args()
    print(json.dumps(build(args.original,args.output),indent=2))
