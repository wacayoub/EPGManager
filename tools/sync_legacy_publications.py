"""Historical recovery utility for verified bundles; no active workflow uses it.

EPGManager owns publications. EPG-Scrapers is retired; this tool is kept only
to recover historical checkouts, and never scrapes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

try:
    from tools.verify_unified_repository import verify
except ModuleNotFoundError:
    from verify_unified_repository import verify


def synchronize(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('source and compatibility checkout must be separate')
    verify(source)  # Validate all 14 bundles before copying any production path.
    names=json.loads((source/'config/canonical-channel-policy.json').read_text(encoding='utf-8'))['default_source_order']
    derived=source/'feeds/mena.json'
    if derived.is_file():
        meta=json.loads(derived.read_text(encoding='utf-8'))
        data=(source/'feeds/mena.xml.gz').read_bytes()
        if meta.get('sha256')==hashlib.sha256(data).hexdigest() and meta.get('size_bytes')==len(data):
            names=names+['mena']
    copied=[]
    for name in names:
        for ext in ('xml.gz','json','txt'):
            copied.append(Path('feeds')/(name+'.'+ext))
        copied.extend(p.relative_to(source) for p in (source/'reports').glob(name+'-*.json'))
        copied.extend(p.relative_to(source) for p in (source/'reports').glob(name+'-*.md'))
    if 'mena' in names:
        copied.extend(Path('reports')/name for name in ('canonical-source-routing.json','canonical-source-routing.md'))
    for relative in sorted(set(copied)):
        original=source/relative
        if not original.is_file():
            raise ValueError('missing verified publication: '+str(relative))
    changed=0
    for relative in sorted(set(copied)):
        original, target=source/relative,destination/relative
        if target.is_file() and target.read_bytes()==original.read_bytes():
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,target)
        changed+=1
    print('LEGACY_MIRROR_OK changed_files='+str(changed)+'; provider timestamps and bytes preserved')
    return changed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    synchronize(args.source,args.destination)
