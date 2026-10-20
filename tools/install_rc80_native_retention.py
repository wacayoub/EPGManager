"""Owner-approved single-module hotfix. No opkg, imports, restart or EPG writes.

CLI is pinned to the observed RC80 receiver version and source identities.
Importing this file has no effects. A verified recovery archive is mandatory.
"""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import time
import urllib.request

BEFORE = '43564fe313513deaa49b80acece8fccf9dedc0b9e3fcf210177da7b5f2207c31'
AFTER = 'c8dc4d68712006f8fe86e64e698852d6dee8601667b9d880cff4dd3873d929f7'
VERSION_SHA = '84aeca924badd32de57e3299031ec4851d862831a1c296212491419e358ed561'
PATCH_URL = 'https://raw.githubusercontent.com/wacayoub/EPGManager/907a56f318bb2b23826aa19b3899293ed8b30159/tools/apply_rc80_private_functional_fixes.py'
PATCH_SHA = 'f86c4bc4f6c742f7d48fd8bbaef5bc1c5efcf63bdaec26c498d9f3bdc51af71b'
PLUGIN = Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
CONFIG = Path('/etc/enigma2')
BACKUPS = Path('/etc/epgmanager_epg/backups')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('symlinked path rejected')
    return path


def read(path):
    path = safe(path)
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('unsafe or oversized source/config file')
    return path.read_bytes()


def protected(config):
    paths = set(config.glob('epgmanager*.json')) | set(config.glob('epgmanager.conf'))
    if (config/'settings').exists():
        paths.add(config/'settings')
    return {p.name: sha(read(p)) for p in sorted(paths)}


def backup_verified(content, destination):
    safe(destination.parent).mkdir(parents=True, exist_ok=True, mode=0o700)
    if sum(len(row[0]) for row in content.values()) > 100 * 1024 * 1024:
        raise ValueError('backup size budget exceeded')
    with destination.open('xb') as sink:
        os.chmod(destination, 0o600)
        with tarfile.open(fileobj=sink, mode='w:gz') as archive:
            for name, (raw, mode) in sorted(content.items()):
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode = len(raw), mode
                archive.addfile(entry, io.BytesIO(raw))
        sink.flush()
        os.fsync(sink.fileno())
    with tarfile.open(destination, 'r:gz') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != set(content) or len(members) != len(content):
            raise ValueError('backup inventory mismatch')
        for member in members:
            raw, mode = content[member.name]
            if not member.isfile() or member.mode != mode or archive.extractfile(member).read() != raw:
                raise ValueError('backup bytes/modes verification failed')
    return sha(destination.read_bytes())


def install(consent, transform, plugin=PLUGIN, config=CONFIG, backups=BACKUPS,
            before_sha=BEFORE, after_sha=AFTER, version_sha=VERSION_SHA):
    if consent != 'APPLY_NATIVE_RETENTION_OWNER_AUTHORIZED':
        raise ValueError('explicit owner authorization required')
    plugin, config, backups = map(safe, (plugin, config, backups))
    target = plugin/'core/direct_native_import.py'
    original = read(target)
    if sha(read(plugin/'version.py')) != version_sha:
        raise ValueError('installed RC80 version drift')
    if sha(original) == after_sha:
        return {'status': 'ALREADY_INSTALLED', 'module_sha256': after_sha,
                'restart_executed': False, 'import_executed': False}
    if sha(original) != before_sha:
        raise ValueError('installed native module drift; no write performed')
    replacement = transform(original.decode('utf-8')).encode('utf-8')
    if sha(replacement) != after_sha:
        raise ValueError('corrective payload SHA mismatch')
    ast.parse(replacement)
    modules = sorted(plugin.rglob('*.py'))
    if len(modules) != 140:
        raise ValueError('expected installed 140-module RC80')
    before_modules = {}
    content = {}
    for path in sorted(plugin.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlinked plugin member')
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        raw = read(path)
        if path.suffix == '.py':
            ast.parse(raw)
            before_modules[path.relative_to(plugin).as_posix()] = sha(raw)
        content['EPGManager/'+path.relative_to(plugin).as_posix()] = (raw, stat.S_IMODE(path.stat().st_mode))
    config_before = protected(config)
    for name in config_before:
        raw = read(config/name)
        if name == 'settings':
            raw = b''.join(line for line in raw.splitlines(keepends=True)
                           if line.startswith(b'config.plugins.epgmanager.'))
            name = 'epgmanager-settings.conf'
        content['config/'+name] = (raw, 0o600)
    destination = backups/('rc80-before-native-retention-%d.tar.gz' % time.time_ns())
    backup_sha = backup_verified(content, destination)
    # Abort if anything changed while the recovery archive was created/read back.
    if protected(config) != config_before or read(target) != original:
        raise ValueError('receiver drift during backup; patch not applied')
    for rel, expected in before_modules.items():
        if sha(read(plugin/rel)) != expected:
            raise ValueError('module drift during backup; patch not applied')
    temporary = target.with_name('.rc80-native-retention-%d.tmp' % time.time_ns())
    mode = stat.S_IMODE(target.stat().st_mode)
    with temporary.open('xb') as sink:
        sink.write(replacement)
        sink.flush()
        os.fsync(sink.fileno())
    os.chmod(temporary, mode)
    if sha(read(temporary)) != after_sha:
        raise ValueError('temporary payload verification failed')
    os.replace(temporary, target)
    if sha(read(target)) != after_sha or stat.S_IMODE(target.stat().st_mode) != mode:
        raise RuntimeError('post-write verification failed; recovery archive retained')
    config_unchanged = protected(config) == config_before
    changed = [rel for rel, expected in before_modules.items()
               if sha(read(plugin/rel)) != expected]
    if not config_unchanged or changed != ['core/direct_native_import.py']:
        raise RuntimeError('post-install conservation check failed')
    return {'status': 'NATIVE_RETENTION_INSTALLED_VERIFIED', 'changed_modules': changed,
            'module_sha256': after_sha, 'protected_config_files': len(config_before),
            'protected_config_hashes_unchanged': config_unchanged,
            'backup_path': str(destination), 'backup_sha256': backup_sha,
            'backup_bytes': destination.stat().st_size, 'backup_readback_verified': True,
            'installed_python_modules': 140, 'receiver_modified': True,
            'opkg_executed': False, 'restart_executed': False, 'import_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-native-retention-owner-authorized', action='store_true')
    args = parser.parse_args()
    if not args.apply_native_retention_owner_authorized:
        parser.error('owner authorization flag is required; no receiver write performed')
    with urllib.request.urlopen(PATCH_URL, timeout=30) as response:
        patch = response.read(128 * 1024)
    if sha(patch) != PATCH_SHA:
        raise ValueError('reviewed transformation download SHA mismatch')
    namespace = {'__name__': '_reviewed_native_retention_transform'}
    exec(compile(patch, '<sha-verified-transform>', 'exec'), namespace)
    report = install('APPLY_NATIVE_RETENTION_OWNER_AUTHORIZED', namespace['preserve_native_events'])
    print('RC80_NATIVE_RETENTION_RESULT='+json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
