#!/usr/bin/env python3
import json,re
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup

URL='https://www.sport24.rest/channels/bein/1'
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36','Accept-Language':'ar,en;q=0.8'})
r=S.get(URL,timeout=25); r.raise_for_status(); html=r.text
soup=BeautifulSoup(html,'html.parser')
base='https://www.sport24.rest'

def uniq(xs,limit=200):
    out=[]
    for x in xs:
        if x and x not in out: out.append(x)
        if len(out)>=limit: break
    return out

scripts=uniq([urljoin(URL,s.get('src')) for s in soup.find_all('script') if s.get('src')],50)
links=uniq([urljoin(URL,a.get('href')) for a in soup.find_all('a') if a.get('href')],100)
pat=re.compile(r'''(?i)(?:https?:\\?/\\?/[^\s"'<>]+|/[^\s"'<>]*(?:api|ajax|graphql|schedule|program|channel|epg)[^\s"'<>]*)''')
inline=uniq([m.group(0).replace('\\/','/') for m in pat.finditer(html)],200)
time_snips=uniq([html[max(0,m.start()-100):m.end()+180].replace('\n',' ') for m in re.finditer(r'(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?!\d)',html)],80)
js_hits=[]
for src in scripts[:20]:
    if urlparse(src).netloc not in ('www.sport24.rest','sport24.rest'): continue
    try:
        rr=S.get(src,timeout=20); txt=rr.text
    except Exception: continue
    for m in pat.finditer(txt):
        v=m.group(0).replace('\\/','/')
        if v not in js_hits: js_hits.append(v)
        if len(js_hits)>=300: break
    if len(js_hits)>=300: break

report={'page':URL,'status':r.status_code,'html_bytes':len(r.content),'scripts':scripts,'inline_candidates':inline,'js_candidates':js_hits,'time_snippets':time_snips,'links_sample':links[:40]}
open('sport24-probe.json','w',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'scripts':len(scripts),'inline_candidates':len(inline),'js_candidates':len(js_hits),'time_snippets':len(time_snips)}))
