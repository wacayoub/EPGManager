"""Offline structural checks for the unified production engine and feeds."""
from pathlib import Path
import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import yaml


def verify(root='.'):
    root=Path(root)
    sources=json.loads((root/'config/canonical-channel-policy.json').read_text(encoding='utf-8'))['default_source_order']
    assert len(sources)==len(set(sources))==14, 'fourteen unique source names required'
    published={p.name for p in (root/'feeds').glob('*.xml.gz')}
    assert published=={name+'.xml.gz' for name in sources}|{'mena.xml.gz'}, 'unexpected diagnostic or retired publication in active feeds'
    schedules=[]
    for path in (root/'.github/workflows').glob('*.yml'):
        text=path.read_text(encoding='utf-8')
        doc=yaml.load(text,Loader=yaml.BaseLoader)
        for ref in re.findall(r'\./\.github/workflows/[\w.-]+\.yml',text):
            assert (root/ref[2:]).is_file(), 'missing reusable workflow: '+ref
        for ref in re.findall(r'\b(?:tools|tests)/[\w./-]+\.py',text):
            assert (root/ref).is_file(), 'missing workflow script: '+ref
        events=doc.get('on') or {}
        if 'schedule' in events:
            schedules.append(path.name)
            for row in events['schedule']:
                assert row.get('timezone')=='Africa/Casablanca', 'source schedules must use Morocco local time'
    expected={'refresh-'+name+'.yml' for name in sources}|{'daily-epg-chain.yml','epg-watchdog.yml'}
    assert set(schedules)==expected, 'duplicate or missing automatic publication schedule'
    policy=json.loads((root/'config/source-schedules.json').read_text(encoding='utf-8'))
    assert set(policy['sources'])==set(sources), 'schedule policy must cover all sources'
    for name in sources:
        row=policy['sources'][name]
        hour,minute=map(int,row['time'].split(':'))
        doc=yaml.load((root/f'.github/workflows/refresh-{name}.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        assert doc['on']['schedule']==[{'cron':f'{minute} {hour} * * *','timezone':row['timezone']}], name+': schedule drift'
    chain=yaml.load((root/'.github/workflows/daily-epg-chain.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
    hour,minute=map(int,policy['postprocess']['time'].split(':'))
    assert chain['on']['schedule']==[{'cron':f'{minute} {hour} * * *','timezone':policy['postprocess']['timezone']}], 'postprocess schedule drift'
    for name in sources:
        raw=(root/f'feeds/{name}.xml.gz').read_bytes()
        meta=json.loads((root/f'feeds/{name}.json').read_text(encoding='utf-8'))
        assert meta['sha256']==hashlib.sha256(raw).hexdigest(), name+': SHA mismatch'
        assert meta['size_bytes']==len(raw), name+': gzip size mismatch'
        tv=ET.fromstring(gzip.decompress(raw))
        assert tv.tag=='tv', name+': not XMLTV'
        ids=[c.get('id') for c in tv.findall('channel')]
        assert all(ids) and len(ids)==len(set(ids)), name+': duplicate or blank IDs'
        assert meta['channels']==len(ids), name+': catalogue count mismatch'
        assert meta['programmes']==len(tv.findall('programme')), name+': programme count mismatch'
        index=[row.split('|',1)[0] for row in (root/f'feeds/{name}.txt').read_text(encoding='utf-8').splitlines() if row.strip()]
        assert len(index)==len(set(index)) and set(index)==set(ids), name+': ID index mismatch'
        assert all(p.get('channel') in set(ids) for p in tv.findall('programme')), name+': orphan programme'
    print('UNIFIED_REPOSITORY_OK sources=14 schedules=16; gzip, XMLTV, manifests, IDs and workflow dependencies verified')


if __name__=='__main__':
    verify()
