#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json,re,urllib.request
from bs4 import BeautifulSoup
from pathlib import Path

SPECS={
 'lbci':('https://www.lbcgroup.tv/schedule-channels/1/lbci/en',['04:00','08:00','11:00']),
 'ajman':('https://www.ajmantv.com/schedule/monday/',['10:00','14:00','18:00'])
}
UA='EPGManager-DOM-Probe/1.0'
out={}
for name,(url,needles) in SPECS.items():
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=30) as r:html=r.read().decode('utf-8','replace')
    soup=BeautifulSoup(html,'html.parser')
    rows=[]
    for needle in needles:
        found=[]
        for node in soup.find_all(string=lambda s:isinstance(s,str) and needle in s):
            cur=node.parent;chain=[]
            for _ in range(7):
                if cur is None:break
                txt=re.sub(r'\s+',' ',' '.join(cur.stripped_strings)).strip()
                attrs={k:v for k,v in cur.attrs.items() if k in ('class','id','href','data-time','data-start','data-end','datetime','itemprop')}
                chain.append({'tag':cur.name,'attrs':attrs,'text':txt[:1800]})
                cur=cur.parent
            found.append(chain)
            if len(found)>=5:break
        rows.append({'needle':needle,'matches':found})
    out[name]={'url':url,'bytes':len(html.encode()),'rows':rows}
Path('output/lbci-ajman-dom-probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['LBCI / AJMAN DOM PROBE','']
for name,rec in out.items():
    lines.append('['+name.upper()+'] '+rec['url'])
    for row in rec['rows']:
        lines.append(' NEEDLE '+row['needle'])
        for i,chain in enumerate(row['matches'][:3]):
            lines.append('  MATCH '+str(i+1))
            for x in chain:lines.append('   <%s %s> %s'%(x['tag'],json.dumps(x['attrs'],ensure_ascii=False),x['text'][:1200]))
    lines.append('')
Path('output/lbci-ajman-dom-probe.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
