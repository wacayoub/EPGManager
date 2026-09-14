#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re, urllib.request
from bs4 import BeautifulSoup
from pathlib import Path

URL='https://www.alsumaria.tv/TV-grid'
TARGETS=['ناس وناس','Live Talk','نشرة أخبار السومرية','عشرين','من كثر حبي لك','ورود ملونة']
req=urllib.request.Request(URL,headers={'User-Agent':'EPGManager-Alsumaria-DOM/1.0'})
with urllib.request.urlopen(req,timeout=30) as r:
    html=r.read().decode('utf-8','replace')
soup=BeautifulSoup(html,'html.parser')
out=[]
for target in TARGETS:
    matches=[]
    for node in soup.find_all(string=lambda s: isinstance(s,str) and target.casefold() in s.casefold()):
        cur=node.parent
        chain=[]
        for depth in range(7):
            if cur is None: break
            text=' '.join(cur.stripped_strings)
            text=re.sub(r'\s+',' ',text).strip()
            attrs={k:v for k,v in cur.attrs.items() if k in ('class','id','data-time','data-date','data-start','data-end','datetime','href')}
            chain.append({'tag':cur.name,'attrs':attrs,'text':text[:1600]})
            cur=cur.parent
        matches.append(chain)
        if len(matches)>=4: break
    out.append({'target':target,'matches':matches})
Path('output/alsumaria-dom-probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['ALSUMARIA DOM PROBE','']
for row in out:
    lines.append('TARGET '+row['target'])
    for i,chain in enumerate(row['matches']):
        lines.append(' MATCH %d'% (i+1))
        for x in chain:
            lines.append('  <%s %s> %s'%(x['tag'],json.dumps(x['attrs'],ensure_ascii=False),x['text'][:1000]))
    lines.append('')
Path('output/alsumaria-dom-probe.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
