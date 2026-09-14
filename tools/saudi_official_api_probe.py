#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json,re,urllib.parse,urllib.request
from pathlib import Path

PAGES={
 'alekhbaria':'https://www.alekhbariya.net/',
 'saudiatv':'https://saudiatv.sba.sa/',
 'sba':'https://sba.sa/',
}
UA='EPGManager-Saudi-Official-Probe/1.0'
SCRIPT_RE=re.compile(r'<script[^>]+src=["\']([^"\']+)["\']',re.I)
TERMS=['api','schedule','program','programme','epg','channel','grid','broadcast','tv-guide','guide','alekhbariya','sba.sa']
URL_RE=re.compile(r'(?:https?:)?//[^"\'<>\\\s]+|/[A-Za-z0-9_./?&=%:+~-]{4,}')

def get(url,timeout=25):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/javascript,application/json,*/*'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            return {'status':getattr(r,'status',200),'url':r.geturl(),'ctype':r.headers.get('Content-Type',''),'body':r.read().decode('utf-8','replace')}
    except Exception as exc:
        code=getattr(exc,'code',None);body=''
        try:body=exc.read().decode('utf-8','replace')
        except Exception:pass
        return {'status':code or 'ERROR','url':url,'ctype':'','body':body,'error':str(exc)[:240]}

def contexts(text,term,width=500,limit=8):
    out=[];low=text.casefold();needle=term.casefold();p=0
    while len(out)<limit:
        i=low.find(needle,p)
        if i<0:break
        out.append(re.sub(r'\s+',' ',text[max(0,i-width):min(len(text),i+len(needle)+width)]).strip())
        p=i+len(needle)
    return out

def endpoint_literals(base,text):
    out=[]
    for raw in URL_RE.findall(text):
        low=raw.casefold()
        if not any(k in low for k in ('api','schedule','program','epg','grid','guide')):continue
        if raw.startswith('//'):raw='https:'+raw
        url=urllib.parse.urljoin(base,raw)
        if url not in out:out.append(url)
    return out[:120]

def main():
    result={'schema':1,'pages':{}}
    for name,url in PAGES.items():
        r=get(url)
        rec={'page':{'requested':url,'status':r['status'],'final':r['url'],'ctype':r['ctype'],'bytes':len(r['body'].encode()),'error':r.get('error','')},'html_endpoints':endpoint_literals(r['url'],r['body']),'scripts':[]}
        scripts=[]
        for raw in SCRIPT_RE.findall(r['body']):
            if raw.startswith('//'):raw='https:'+raw
            js=urllib.parse.urljoin(r['url'],raw)
            if js not in scripts:scripts.append(js)
        for js in scripts[:50]:
            jr=get(js,18);text=jr['body']
            terms={}
            for term in TERMS:
                h=contexts(text,term)
                if h:terms[term]=h[:5]
            eps=endpoint_literals(jr['url'],text)
            if terms or eps:
                rec['scripts'].append({'url':js,'status':jr['status'],'ctype':jr['ctype'],'bytes':len(text.encode()),'endpoints':eps,'terms':terms})
        result['pages'][name]=rec
    Path('output/saudi-official-api-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['SAUDI OFFICIAL API PROBE','']
    for name,rec in result['pages'].items():
        p=rec['page'];lines.append(f"[{name.upper()}] status={p['status']} bytes={p['bytes']} final={p['final']}")
        if p['error']:lines.append('  ERROR '+p['error'])
        for ep in rec['html_endpoints'][:20]:lines.append('  HTML_ENDPOINT '+ep)
        for s in rec['scripts'][:20]:
            lines.append(f"  SCRIPT {s['url']} status={s['status']} bytes={s['bytes']}")
            for ep in s['endpoints'][:20]:lines.append('    ENDPOINT '+ep)
            for term in ('epg','schedule','program','grid','api'):
                for c in s['terms'].get(term,[])[:2]:lines.append('    CONTEXT '+term+' :: '+c[:1000])
        lines.append('')
    Path('output/saudi-official-api-probe.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))
if __name__=='__main__':main()
