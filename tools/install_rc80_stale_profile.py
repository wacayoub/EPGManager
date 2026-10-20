"""Explicitly authorized three-file repair; no cache/config writes or restart.

All downloads and installed preimages are SHA-pinned. Reuses the already tested
backup primitives from the retention installer. Importing this file is passive.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import urllib.request

COMMIT = '1d9ec09bfcf3e0866925dc6c52433e0e315c6969'
BASE = 'https://raw.githubusercontent.com/wacayoub/EPGManager/'
BEFORE = {
    'core/source_quality.py': '9de76ea39c0c1e73dabfed124c156c40ccf3055e478b69b7e7fd4d4879880290',
    'core/github_direct_sync.py': '61f93bb6486ce08df6aeecc04ef596547a8883c56056e0d7d32cf6813423bae6',
}
AFTER = {
    'core/source_quality.py': 'a65d773a54818477a93f6f0c8d92de9bb36eb64f678846aae2a72f2de454544e',
    'core/github_direct_sync.py': '14ae9f7c2486142ba60e13d63daf3f8060b7f7d1f5bd87addc6501e706e6fc6e',
    'core/quality_profile_freshness.py': '3b7009eb9d35ef9b5bcf052580856e9d21fb77c0975da43b7b93c4c4478cb348',
}
PATCH_SHA = '5af030cf8ff9708d0dde243d18be622ae2722d80868278163344600c2ea9f1f5'
VERSION_SHA = '84aeca924badd32de57e3299031ec4851d862831a1c296212491419e358ed561'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def download(path, pin):
    with urllib.request.urlopen(path, timeout=30) as response:
        raw = response.read(131073)
    if len(raw)>131072 or sha(raw)!=pin:
        raise ValueError('reviewed download identity mismatch')
    return raw


def install(consent, helper, payload, plugin, config, backups,
            before_pins=BEFORE, after_pins=AFTER, version_pin=VERSION_SHA):
    if consent != 'APPLY_STALE_PROFILE_OWNER_AUTHORIZED':
        raise ValueError('separate owner authorization required')
    plugin, config, backups = map(helper.safe, (plugin, config, backups))
    if set(payload)!=set(after_pins) or set(before_pins)!=set(after_pins)-{'core/quality_profile_freshness.py'}:
        raise ValueError('corrective inventory mismatch')
    for name, raw in payload.items():
        if sha(raw)!=after_pins[name]:
            raise ValueError('corrective bytes mismatch')
        ast.parse(raw)
    if sha(helper.read(plugin/'version.py'))!=version_pin:
        raise ValueError('receiver version drift')
    if all((plugin/n).is_file() and sha(helper.read(plugin/n))==pin for n,pin in after_pins.items()):
        return {'status':'ALREADY_INSTALLED','receiver_modified':False}
    if (plugin/'core/quality_profile_freshness.py').exists():
        raise ValueError('unexpected new-module path')
    if any(sha(helper.read(plugin/n))!=pin for n,pin in before_pins.items()):
        raise ValueError('installed preimage drift')
    if len(list(plugin.rglob('*.py')))!=140:
        raise ValueError('expected installed 140-module receiver')
    protected=helper.protected(config); content={}; previous={}
    for path in sorted(plugin.rglob('*')):
        helper.safe(path)
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc','.pyo'):
            continue
        raw=helper.read(path); rel=path.relative_to(plugin).as_posix()
        if path.suffix=='.py':ast.parse(raw)
        previous[rel]=sha(raw)
        content['EPGManager/'+rel]=(raw,stat.S_IMODE(path.stat().st_mode))
    for name in protected:
        raw=helper.read(config/name)
        if name=='settings':
            raw=b''.join(line for line in raw.splitlines(keepends=True) if line.startswith(b'config.plugins.epgmanager.'))
            name='epgmanager-settings.conf'
        content['config/'+name]=(raw,0o600)
    destination=backups/('rc80-before-stale-profile-%d.tar.gz'%time.time_ns())
    backup_sha=helper.backup_verified(content,destination)
    if helper.protected(config)!=protected or any(sha(helper.read(plugin/n))!=pin for n,pin in previous.items()):
        raise ValueError('receiver drift during verified backup')
    staged={}
    for name,raw in payload.items():
        target=helper.safe(plugin/name)
        temporary=target.with_name('.rc80-profile-%d-%s.tmp'%(time.time_ns(),target.name))
        mode=stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        with temporary.open('xb') as sink:
            sink.write(raw); sink.flush(); os.fsync(sink.fileno())
        os.chmod(temporary,mode)
        if sha(helper.read(temporary))!=after_pins[name]:raise ValueError('staged bytes mismatch')
        staged[name]=temporary
    for name in ['core/quality_profile_freshness.py',*before_pins]:
        os.replace(staged[name],plugin/name)
    if any(sha(helper.read(plugin/name))!=pin for name,pin in after_pins.items()):
        raise RuntimeError('corrective readback failed; verified backup retained')
    for name,pin in previous.items():
        if sha(helper.read(plugin/name))!=after_pins.get(name,pin):
            raise RuntimeError('post-write verification failed; verified backup retained')
    if helper.protected(config)!=protected or len(list(plugin.rglob('*.py')))!=141:
        raise RuntimeError('post-write conservation check failed; verified backup retained')
    return {'status':'STALE_PROFILE_CORRECTION_INSTALLED_VERIFIED','receiver_modified':True,
            'changed_modules':list(before_pins),'added_modules':['core/quality_profile_freshness.py'],
            'installed_python_modules':141,'protected_config_files':len(protected),
            'protected_config_hashes_unchanged':True,'backup_readback_verified':True,
            'backup_path':str(destination),'backup_sha256':backup_sha,
            'opkg_executed':False,'restart_executed':False,'import_executed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-stale-profile-owner-authorized',action='store_true')
    args=parser.parse_args()
    if not args.apply_stale_profile_owner_authorized:
        parser.error('owner authorization flag required; no receiver changes')
    raw=download(BASE+'ab08f68d4c972aa3fc10d6d9cd075530488a1a2a/tools/install_rc80_native_retention.py',
                 'c808c21817671ea9bab9a5e5e4ee0a518d8b194d9564d3d7061f8f11dd6462d8')
    namespace={'__name__':'_reviewed_backup_primitives'}
    exec(compile(raw,'<sha-verified-backup>','exec'),namespace)
    from types import SimpleNamespace
    helper=SimpleNamespace(**namespace)
    if sha(helper.read(helper.PLUGIN/'core/direct_native_import.py'))!='c8dc4d68712006f8fe86e64e698852d6dee8601667b9d880cff4dd3873d929f7':
        raise ValueError('previous retention correction is not verified')
    if all((helper.PLUGIN/n).is_file() and sha(helper.read(helper.PLUGIN/n))==pin for n,pin in AFTER.items()):
        print('RC80_STALE_PROFILE_RESULT='+json.dumps({'status':'ALREADY_INSTALLED','receiver_modified':False}),flush=True)
        return
    patch=download(BASE+COMMIT+'/tools/apply_rc80_stale_profile_fix.py',PATCH_SHA)
    namespace={'__name__':'_reviewed_stale_profile_transform'}
    exec(compile(patch,'<sha-verified-transform>','exec'),namespace)
    payload=namespace['corrected']({n:helper.read(helper.PLUGIN/n) for n in BEFORE})
    name='core/quality_profile_freshness.py'
    payload[name]=download(BASE+COMMIT+'/source/overlay/'+name,AFTER[name])
    print('RC80_STALE_PROFILE_RESULT='+json.dumps(install('APPLY_STALE_PROFILE_OWNER_AUTHORIZED',
          helper,payload,helper.PLUGIN,helper.CONFIG,helper.BACKUPS),sort_keys=True),flush=True)


if __name__=='__main__':main()
