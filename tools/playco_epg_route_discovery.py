#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json,re,urllib.parse,urllib.request
from pathlib import Path

PAGE='https://starzplay.com/en/admn'
UA='EPGManager-Playco-Route-Discovery/1.0'
SCRIPT_RE=re.compile(r'<script[^>]+src=["\']([^"\']+)["\']',re.I)
TERMS=['epgCDN','epg.aws.playco.com','epg','schedule','guide','linear','channel','program']

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/javascript,*/*'})
    with urllib.request.urlopen(req,timeout=25) as r:return r.geturl(),r.read().decode('utf-8','replace')

def contexts(text,needle,width=700,limit=20):
    out=[];low=text.casefold();n=needle.casefold();p=0
    while len(out)<limit:
        i=low.find(n,p)
        if i<0:break
        out.append(re.sub(r'\s+',' ',text[max(0,i-width):min(len(text),i+len(n)+width)]).strip())
        p=i+len(n)
    return out

final,html=get(PAGE)
scripts=[]
for raw in SCRIPT_RE.findall(html):
    url=urllib.parse.urljoin(final,raw)
    if url not in scripts:scripts.append(url)
result={'page':final,'scripts_total':len(scripts),'hits':[]}
for idx,url in enumerate(scripts[:80]):
    try:_,text=get(url)
    except Exception as exc:continue
    terms={}
    for term in TERMS:
        h=contexts(text,term)
        if h:terms[term]=h[:8]
    # Capture literal route-ish strings containing target terms.
    literals=[]
    for m in re.finditer(r'["\']([^"\']{2,180})["\']',text):
        s=m.group(1)
        low=s.casefold()
        if any(k in low for k in ('epg','schedule','guide','linear')) and ('/' in s or 'channel' in low):
            if s not in literals:literals.append(s)
            if len(literals)>=100:break
    if terms or literals:
        result['hits'].append({'script':url,'terms':terms,'literals':literals})
Path('output/playco-epg-route-discovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['PLAYCO EPG ROUTE DISCOVERY',f"page={final} scripts={len(scripts)} hit_scripts={len(result['hits'])}",'']
for hit in result['hits']:
    lines.append('SCRIPT '+hit['script'])
    for lit in hit['literals'][:40]:lines.append('  LITERAL '+lit[:500])
    for term in ('epgCDN','epg.aws.playco.com','schedule','linear','guide'):
        for c in hit['terms'].get(term,[])[:4]:lines.append('  CONTEXT '+term+' :: '+c[:1400])
    lines.append('')
Path('output/playco-epg-route-discovery.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
