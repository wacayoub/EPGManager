#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, urllib.request
from pathlib import Path

URL='https://api-prd.alekhbariya.net/ekh/ekh-rest-api/generic/epg/live'
req=urllib.request.Request(URL,headers={'User-Agent':'EPGManager-AlEkhbariya-Probe/1.0','Accept':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(); status=getattr(r,'status',200); ctype=r.headers.get('Content-Type',''); final=r.geturl()
    text=raw.decode('utf-8','replace')
    try:data=json.loads(text)
    except Exception:data={'_raw':text[:20000]}
    out={'status':status,'content_type':ctype,'final_url':final,'bytes':len(raw),'data':data}
except Exception as exc:
    out={'status':'ERROR','error':str(exc),'data':None}
Path('output/alekhbaria-official-epg-probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['AL EKHBARIYA OFFICIAL EPG PROBE',f"url={URL}",f"status={out.get('status')} bytes={out.get('bytes',0)} ctype={out.get('content_type','')}"]
if out.get('error'):lines.append('ERROR '+out['error'])
else:
    d=out.get('data')
    lines.append('TYPE '+type(d).__name__)
    if isinstance(d,dict):
        lines.append('KEYS '+', '.join(map(str,d.keys())))
        # Print compact top-level/list samples without assuming schema.
        for k,v in d.items():
            if isinstance(v,list):
                lines.append(f"LIST {k} len={len(v)}")
                for x in v[:10]: lines.append('  '+json.dumps(x,ensure_ascii=False)[:1800])
            elif isinstance(v,dict):
                lines.append(f"OBJECT {k} keys={','.join(map(str,v.keys()))}")
                lines.append('  '+json.dumps(v,ensure_ascii=False)[:2500])
            else:
                lines.append(f"SCALAR {k}={str(v)[:1000]}")
    elif isinstance(d,list):
        lines.append(f"LIST root len={len(d)}")
        for x in d[:20]:lines.append('  '+json.dumps(x,ensure_ascii=False)[:1800])
Path('output/alekhbaria-official-epg-probe.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
