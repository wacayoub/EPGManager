#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path

BASE='https://sbauae.faulio.com/api/v1/programgrid'
UA='EPGManager-Sharjah-Window-Probe/1.0'

def get(url,headers=None):
    h={'User-Agent':UA,'Accept':'application/json'};h.update(headers or {})
    req=urllib.request.Request(url,headers=h)
    try:
        with urllib.request.urlopen(req,timeout=25) as r:return json.loads(r.read().decode('utf-8','replace'))
    except Exception as exc:return {'_error':str(exc)}

def counts(data):
    rows=[]
    for ch in data.get('channels',[]) if isinstance(data,dict) else []:
        g=ch.get('grid');rows.append({'id':ch.get('id'),'title':ch.get('title'),'grid_type':type(g).__name__,'grid_len':len(g) if isinstance(g,(list,dict)) else None})
    return {'ts_start':data.get('ts_start') if isinstance(data,dict) else None,'ts_end':data.get('ts_end') if isinstance(data,dict) else None,'channels':rows,'error':data.get('_error') if isinstance(data,dict) else None}

now=datetime.now(timezone.utc);d0=datetime(now.year,now.month,now.day,tzinfo=timezone.utc)
variants=[]
for offset in (-2,-1,0,1,2,7):
    a=d0+timedelta(days=offset);b=a+timedelta(days=1);ts1=int(a.timestamp());ts2=int(b.timestamp());ds=a.strftime('%Y-%m-%d')
    tests=[
      ('ts',{'ts_start':ts1,'ts_end':ts2},{}),
      ('start_end',{'start':ts1,'end':ts2},{}),
      ('date',{'date':ds},{}),
      ('ts_ar',{'ts_start':ts1,'ts_end':ts2},{'Accept-Language':'ar'}),
      ('ts_locale',{'ts_start':ts1,'ts_end':ts2,'locale':'ar'},{}),
    ]
    for mode,q,h in tests:
        url=BASE+'?'+urllib.parse.urlencode(q);variants.append({'offset':offset,'date':ds,'mode':mode,'url':url,'result':counts(get(url,h))})
Path('output/sharjah-window-probe.json').write_text(json.dumps(variants,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['SHARJAH WINDOW PROBE','']
for x in variants:
    c=x['result']['channels'];nonempty=[r for r in c if (r.get('grid_len') or 0)>0]
    lines.append(f"{x['date']} {x['mode']} | api_ts={x['result']['ts_start']}->{x['result']['ts_end']} | channels={len(c)} nonempty={len(nonempty)} | "+', '.join(f"{r['title']}={r['grid_len']}" for r in nonempty))
    if x['result'].get('error'):lines.append('  ERROR '+x['result']['error'])
Path('output/sharjah-window-probe.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
