#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    ap.add_argument('--json', required=True)
    ap.add_argument('--text', required=True)
    a = ap.parse_args()
    src=json.loads(Path(a.audit).read_text(encoding='utf-8'))
    rows={r.get('id'):r for r in src.get('channels', [])}
    groups=[]
    for g in src.get('duplicate_timeline_groups', []):
        ids=g.get('ids') or []
        members=[]
        shards=set(); countries=set()
        for cid in ids:
            r=rows.get(cid,{})
            shards.add(r.get('shard') or '')
            if r.get('feed_country'):
                countries.add(r.get('feed_country'))
            preview=[]
            for p in r.get('preview') or []:
                t=(p or {}).get('title')
                if t: preview.append(t)
            members.append({
                'id':cid,'name':r.get('name'),'shard':r.get('shard'),
                'feed_country':r.get('feed_country'),'events':r.get('events'),
                'verdict':r.get('verdict'),'score':r.get('score'),'preview':preview[:3],
            })
        compat=bool(g.get('compatibility_alias'))
        unrelated=bool(g.get('unrelated'))
        if compat:
            priority='COMPAT_ALIAS'
        elif len(ids)>=3 and (unrelated or len(shards)>=3 or len(countries)>=3):
            priority='QUARANTINE_HIGH'
        elif unrelated:
            priority='VERIFY_PAIR'
        else:
            priority='REVIEW_SIMULCAST_OR_ALIAS'
        groups.append({
            'priority':priority,'size':len(ids),'unrelated':unrelated,
            'compatibility_alias':compat,'shards':sorted(x for x in shards if x),
            'countries':sorted(countries),'members':members,
        })
    rank={'QUARANTINE_HIGH':0,'VERIFY_PAIR':1,'REVIEW_SIMULCAST_OR_ALIAS':2,'COMPAT_ALIAS':3}
    groups.sort(key=lambda g:(rank[g['priority']],-g['size'],','.join(m['id'] for m in g['members'])))
    out={'groups':len(groups),'quarantine_high':sum(g['priority']=='QUARANTINE_HIGH' for g in groups),
         'verify_pair':sum(g['priority']=='VERIFY_PAIR' for g in groups),'items':groups}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['CLONE GROUP TRIAGE','groups=%d quarantine_high=%d verify_pair=%d' % (out['groups'],out['quarantine_high'],out['verify_pair']),'']
    for i,g in enumerate(groups,1):
        lines.append('%02d. %s size=%d unrelated=%s shards=%s countries=%s' % (i,g['priority'],g['size'],g['unrelated'],','.join(g['shards']),','.join(g['countries'])))
        for m in g['members']:
            lines.append('    - [%s] %s | %s | events=%s | %s' % (m['shard'],m['id'],m['name'],m['events'],' -> '.join(m['preview'][:2])))
    Path(a.text).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines[:140]))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
