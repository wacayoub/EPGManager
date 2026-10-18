"""Standalone RC80 installation checks. Importing this module performs no I/O.

The supervised package embeds a private baseline/candidate manifest in each
hook. Check mode is read-only. Backup mode requires a package-specific consent
value and never modifies the installed plugin, settings, mappings or EPG.
No Enigma2 module, opkg, scheduler, updater or importer is executed here.
"""
import ast
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import time

PLUGIN = Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
CONFIG = Path('/etc/enigma2')
BACKUPS = Path('/etc/epgmanager_epg/backups')
INFO_DIRS = (Path('/var/lib/opkg/info'), Path('/usr/lib/opkg/info'))
MAX_FILE = 5 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_file(root, rel):
    parts = PurePosixPath(rel).parts
    if (not parts or str(PurePosixPath(rel)) != rel or rel.startswith('/') or
            '\\' in rel or any(p in ('..', '.', '__pycache__') for p in parts)):
        raise ValueError('unsafe relative path')
    root = Path(root)
    p = root / rel
    if (root.is_symlink() or any(parent.is_symlink() for parent in p.parents) or
            p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE):
        raise ValueError('unsafe file or parent')
    return p


def verify_tree(root, expected, module_count):
    """Read exact installed files and reject drift without changing anything."""
    root = Path(root)
    if not isinstance(expected, dict) or len(expected) != module_count + 75:
        raise ValueError('unexpected manifest size')
    paths = {p.relative_to(root).as_posix() for p in root.rglob('*.py')}
    if paths != {p for p in expected if p.endswith('.py')} or len(paths) != module_count:
        raise ValueError('installed module inventory drift')
    total = 0
    for rel, row in expected.items():
        p = safe_file(root, rel)
        raw = p.read_bytes()
        total += len(raw)
        if total > MAX_TOTAL or digest(raw) != row['sha256'] or len(raw) != row['bytes']:
            raise ValueError('installed content drift: ' + rel)
        if stat.S_IMODE(p.stat().st_mode) != row['mode']:
            raise ValueError('installed mode drift: ' + rel)
        if rel.endswith('.py'):
            ast.parse(raw, filename=rel)
    return total


def passive_hook(raw):
    """Fail closed: only comments, blank lines and a single exit 0 are accepted."""
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        return False
    commands = [line.strip() for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    return commands in ([], ['exit 0'])


def check_old_removal_hooks(info_dirs=INFO_DIRS):
    for hook in ('prerm', 'postrm'):
        for folder in info_dirs:
            p = Path(folder) / ('enigma2-plugin-extensions-epgmanager.' + hook)
            if p.is_symlink():
                raise ValueError('symlinked installed removal hook')
            if p.exists():
                if not p.is_file() or p.stat().st_size > 200000 or not passive_hook(p.read_bytes()):
                    raise ValueError('installed removal hook requires review: ' + hook)
    return True


def check_before(root, manifest, info_dirs=INFO_DIRS):
    check_old_removal_hooks(info_dirs)
    return verify_tree(root, manifest['before'], 138)


def make_backup(root, config, destination, manifest):
    """Write one private, verified recovery archive, leaving original files intact."""
    root, config, destination = Path(root), Path(config), Path(destination)
    amount = verify_tree(root, manifest['before'], 138)
    if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise ValueError('unsafe backup directory')
    probe = destination
    while not probe.exists():
        probe = probe.parent
    if shutil.disk_usage(probe).free < max(32 * 1024 * 1024, amount * 3):
        raise ValueError('insufficient verified backup space')
    content = {}
    for rel, row in manifest['before'].items():
        content['EPGManager/' + rel] = (safe_file(root, rel).read_bytes(), row['mode'])
    for pattern in ('epgmanager*.json', 'epgmanager.conf'):
        for path in config.glob(pattern):
            safe = safe_file(config, path.name)
            content['config/' + path.name] = (safe.read_bytes(), 0o600)
    settings = config / 'settings'
    if settings.exists():
        raw = safe_file(config, 'settings').read_bytes()
        own = b''.join(line for line in raw.splitlines(keepends=True)
                       if line.startswith(b'config.plugins.epgmanager.'))
        content['config/epgmanager-settings.conf'] = (own, 0o600)
    if sum(len(raw) for raw, mode in content.values()) > MAX_TOTAL:
        raise ValueError('backup exceeds size budget')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    target = destination / ('rc79-before-rc80-%d-%d.tar.gz' % (time.time_ns(), os.getpid()))
    with target.open('xb') as sink:
        os.chmod(target, 0o600)
        with tarfile.open(fileobj=sink, mode='w:gz', format=tarfile.USTAR_FORMAT) as tar:
            for name, (raw, mode) in sorted(content.items()):
                item = tarfile.TarInfo(name)
                item.size, item.mode = len(raw), mode
                item.uid = item.gid = 0
                tar.addfile(item, io.BytesIO(raw))
        sink.flush()
        os.fsync(sink.fileno())
    with tarfile.open(target, 'r:gz') as tar:
        seen = set()
        for item in tar:
            if not item.isfile() or item.name not in content or item.name in seen:
                raise ValueError('backup topology verification failed')
            expected, mode = content[item.name]
            if item.mode != mode or tar.extractfile(item).read() != expected:
                raise ValueError('backup verification failed')
            seen.add(item.name)
        if seen != set(content):
            raise ValueError('incomplete backup')
    # A changing application resource must stop the upgrade before unpacking.
    verify_tree(root, manifest['before'], 138)
    return digest(target.read_bytes())


def main(mode, manifest):
    if mode == 'check':
        check_before(PLUGIN, manifest)
        print('RC80 read-only preflight passed; installation not performed')
    elif mode == 'backup':
        if os.environ.get('EPGMGR_RC80_CONSENT') != manifest['consent']:
            raise ValueError('explicit package-specific installation consent required')
        check_before(PLUGIN, manifest)
        backup_sha = make_backup(PLUGIN, CONFIG, BACKUPS, manifest)
        print('RC79 recovery archive verified; SHA256 ' + backup_sha)
    elif mode == 'verify':
        verify_tree(PLUGIN, manifest['after'], 140)
        print('RC80 140 modules/schema 15 verified; GUI/import still require testing')
    else:
        raise ValueError('unknown guard mode')
