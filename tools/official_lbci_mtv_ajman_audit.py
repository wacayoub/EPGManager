#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse, csv, gzip, html, json, re, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

UA='EPGManager-Official-LB-ME-Audit/1.0'
TIME_RE=re.compile(r'^(?:[01]?\d|2[0-3])[:.]\d{2}(?:\s*(?:am|pm))?$',re.I)
NOISE={
 'live','watch now','read more','view more','more','share','facebook','twitter','instagram','youtube',
 'schedule','program schedule','tv schedule','جدول البرامج','مشاهدة','المزيد','التالي','يعرض الآن','يعرض الان',
 'monday','tuesday','wednesday','thursday','friday','saturday','sunday',
 'الاثنين','الثلاثاء','الأربعاء','الاربعاء','الخميس','الجمعة','السبت','الأحد','الاحد'
}

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,*/*'})
    with urllib.request.urlopen(req,timeout=30) as r:
        return r.geturl(), r.read().decode('utf-8','replace')

def clean_lines(markup):
    soup=BeautifulSoup(markup,'html.parser')
    for n in soup(['script','style','noscript','svg']): n.decompose()
    return [re.sub(r'\s+',' ',html.unescape(x)).strip() for x in soup.stripped_strings if re.sub(r'\s+',' ',html.unescape(x)).strip()]

def is_noise(s):
    low=s.casefold().strip(' :.-')
    return low in NOISE or len(s)>180 or low.startswith(('copyright','image','menu','search','follow us','تابعونا'))

def normalize_time(s):
    s=s.strip().lower().replace('.',':')
    m=re.match(r'^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$',s)
    if not m:return None
    h,mn=int(m.group(1)),int(m.group(2)); ap=m.group(3)
    if ap:
        if h==12:h=0
        if ap=='pm':h+=12
    return f'{h:02d}:{mn:02d}' if h<24 else None

def extract(markup):
    lines=clean_lines(markup); out=[]; seen=set()
    for i,line in enumerate(lines):
        if not TIME_RE.match(line): continue
        tm=normalize_time(line)
        if not tm: continue
        cand=[]
        for j in range(max(0,i-5),min(len(lines),i+8)):
            if j==i:continue
            s=lines[j]
            if TIME_RE.match(s) or is_noise(s) or re.fullmatch(r'\d+',s):continue
            cand.append((abs(j-i),0 if j>i else 1,j,s))
        if not cand:continue
        cand.sort(); title=cand[0][3]
        key=(tm,title.casefold())
        if key in seen:continue
        seen.add(key);out.append({'time':tm,'title':title})
    return out

def read_xml(path):
    data=path.read_bytes()
    if data[:2]==b'\x1f\x8b':data=gzip.decompress(data)
    return ET.fromstring(data)

def cloud_rows(data_dir,cid):
    d={}
    for pth in Path(data_dir).glob('*.xml.gz'):
        try:root=read_xml(pth)
        except Exception:continue
        for p in root.findall('programme'):
            if (p.get('channel') or '').strip()!=cid:continue
            n=p.find('title');t=((n.text or '').strip() if n is not None else '')
            k=(p.get('start',''),t.casefold())
            d.setdefault(k,{'start':p.get('start',''),'title':t,'files':set()})['files'].add(pth.name)
    rows=[]
    for x in d.values():
        rows.append({'start':x['start'],'title':x['title'],'file':','.join(sorted(x['files']))})
    return sorted(rows,key=lambda x:x['start'])

def load_ids(path):
    with Path(path).open('r',encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def pick_id(rows, needles):
    for r in rows:
        probe=((r.get('id') or '')+' '+(r.get('name') or '')).casefold()
        if all(n.casefold() in probe for n in needles):return r
    return None

def uniq_ratio(events):
    if not events:return 0
    return len(set(re.sub(r'\W+',' ',e['title'].casefold()).strip() for e in events))/len(events)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--all-id-csv',required=True);ap.add_argument('--data-dir',required=True);ap.add_argument('--json',required=True);ap.add_argument('--text',required=True);a=ap.parse_args()
    now=datetime.now(timezone.utc); day_names=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    today=day_names[now.weekday()];tomorrow=day_names[(now+timedelta(days=1)).weekday()]
    ids=load_ids(a.all_id_csv)
    specs={
      'lbci':{'urls':['https://www.lbcgroup.tv/schedule-channels/1/lbci/en'],'needles':['LBC','International']},
      'mtv_lebanon':{'urls':['https://www.mtv.com.lb/en/schedule'],'needles':['MTV','lb']},
      'ajman':{'urls':[f'https://www.ajmantv.com/schedule/{today}/',f'https://www.ajmantv.com/schedule/{tomorrow}/'],'needles':['Ajman']},
    }
    result={'schema':1,'date_utc':now.date().isoformat(),'sources':{}}
    for name,spec in specs.items():
        row=pick_id(ids,spec['needles']);cid=(row or {}).get('id','')
        merged=[];fetches=[]
        for url in spec['urls']:
            try:
                final,markup=get(url);ev=extract(markup);merged.extend(ev);fetches.append({'url':url,'final':final,'status':'OK','bytes':len(markup.encode()),'events':len(ev),'samples':ev[:8]})
            except Exception as exc:fetches.append({'url':url,'status':'ERROR','error':str(exc)[:240]})
        ded=[];seen=set()
        for e in merged:
            k=(e['time'],e['title'].casefold())
            if k not in seen:seen.add(k);ded.append(e)
        cloud=cloud_rows(a.data_dir,cid) if cid else []
        verdict='CANDIDATE_OFFICIAL' if len(ded)>=6 and uniq_ratio(ded)>=0.25 else ('REVIEW' if ded else 'NO_DATA')
        result['sources'][name]={'id':cid,'matched_row':row,'verdict':verdict,'official_events':len(ded),'unique_ratio':round(uniq_ratio(ded),3),'official_samples':ded[:15],'current_unique_events':len(cloud),'current_samples':cloud[:8],'fetches':fetches}
    Path(a.json).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['OFFICIAL LBCI / MTV / AJMAN AUDIT',f"date_utc={result['date_utc']}",'']
    for name,x in result['sources'].items():
        lines.append(f"[{x['verdict']}] {name} | id={x['id'] or 'NOT_MATCHED'} | official={x['official_events']} current={x['current_unique_events']} unique={x['unique_ratio']*100:.1f}%")
        for e in x['official_samples'][:8]:lines.append(f"    OFFICIAL {e['time']} | {e['title']}")
        for e in x['current_samples'][:5]:lines.append(f"    CLOUD {e['start']} | {e['title']}")
        for f in x['fetches']:
            if f['status']!='OK':lines.append(f"    FETCH_ERROR {f['url']} | {f.get('error','')}")
        lines.append('')
    Path(a.text).write_text('\n'.join(lines)+'\n',encoding='utf-8');print('\n'.join(lines))
if __name__=='__main__':main()
