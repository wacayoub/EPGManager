"""Opt-in Arryadia HD estimates: learned recurrence and published Botola fixtures."""
from collections import defaultdict
from datetime import datetime,timedelta,time as clock,timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import requests
try:
    from tools import morocco_epg as base
    from tools.programme_authenticity import SMART_SOURCE,BOTOLA_SOURCE
except ModuleNotFoundError:
    import morocco_epg as base
    from programme_authenticity import SMART_SOURCE,BOTOLA_SOURCE

CHANNEL='Arryadia_HD'


def family(title):
    n=base.norm(title)
    if n in ('flash info','موجز الأخبار','موجز الاخبار'):return 'news'
    if ('arryadia chabab' in n or 'الرياضية شباب' in n):
        if 'reportage' in n or 'روبورتاج' in n:return 'reportage'
        if 'football' in n or 'كرة القدم' in n:return 'youth-football'
    return None


def learn(observations,now):
    buckets=defaultdict(dict)
    for event in observations:
        if (event.channel!=CHANNEL or event.source not in ('arryadia-snrt','arryadia-history')
                or not event.stop or event.stop<=event.start or event.start>now):continue
        kind=family(event.title)
        if kind is None:continue
        minute=event.start.hour*60+event.start.minute
        buckets[(kind,minute//30)][event.start.date()]=event
    templates=[]
    for (kind,_),samples in buckets.items():
        if len(samples)<3:continue
        events=list(samples.values())
        minutes=sorted(e.start.hour*60+e.start.minute for e in events)
        minute=minutes[len(minutes)//2]
        if max(abs(m-minute) for m in minutes)>20:continue
        durations=sorted(int((e.stop-e.start).total_seconds()/60) for e in events)
        duration=min(120,max(10,durations[len(durations)//2]))
        example=max(events,key=lambda e:e.start)
        templates.append({'family':kind,'minute':minute,'duration_minutes':duration,
                          'title':example.title,'language':example.tl,
                          'observed_dates':sorted(d.isoformat() for d in samples)})
    return sorted(templates,key=lambda t:(t['minute'],t['family']))


def model(observations,now,path=None):
    templates=learn(observations,now)
    mode='learned_from_official_dated_rows'
    if templates and path:
        target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'schema':1,'channel':CHANNEL,'learned_utc':now.astimezone(timezone.utc).isoformat(),
                          'not_an_official_future_guide':True,'templates':templates},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    elif not templates and path and Path(path).exists():
        try:
            saved=json.loads(Path(path).read_text(encoding='utf-8'))
            if saved.get('channel')==CHANNEL and saved.get('schema')==1:
                for t in saved.get('templates',[]):
                    if (t['family'] in ('news','reportage','youth-football') and family(t['title'])==t['family']
                            and 0<=t['minute']<1440 and 10<=t['duration_minutes']<=120
                            and len(set(t['observed_dates']))>=3):templates.append(t)
                mode='persisted_model_after_provider_outage'
        except (OSError,ValueError,KeyError,TypeError):pass
    return templates,mode


class FixtureHTTP:
    """Bounded normal public requests; no repeated requests to a denied host."""
    def __init__(self):self.deadline=time.monotonic()+35;self.denied=set();self.attempts=[]
    def get(self,url,**kwargs):
        host=urlsplit(url).hostname
        if host in self.denied or time.monotonic()>=self.deadline:
            raise TimeoutError('Fixture provider unavailable within request budget')
        response=requests.get(url,timeout=min(10,max(1,self.deadline-time.monotonic())),**kwargs)
        self.attempts.append({'url':url,'http_status':response.status_code})
        if response.status_code in (403,429):self.denied.add(host)
        response.raise_for_status()
        return response


def fixtures(hours):
    client=FixtureHTTP()
    rows=base._sofascore_botola_fixtures(client,hours)
    provider='SofaScore'
    if not rows:
        rows=base._livescore_botola_fixtures(client,hours);provider='LiveScore'
    return rows,{'provider':provider,'requests':client.attempts,'fixture_count':len(rows)}


def generate(observations,verified,now,hours=48,model_path=None,fixture_rows=None):
    end=now+timedelta(hours=hours)
    templates,mode=model(observations,now,model_path)
    if fixture_rows is None:fixture_rows,provider=fixtures(hours)
    else:provider={'provider':'test-supplied fixtures','fixture_count':len(fixture_rows)}
    output=[];occupied=[e for e in verified if e.channel==CHANNEL and e.stop and e.stop>e.start]
    def overlaps(start,stop):return any(e.start<stop and e.stop>start for e in occupied)
    candidates={}
    for f in fixture_rows:
        start,stop=f.get('start'),f.get('stop')
        if (not start or not stop or not start.tzinfo or not stop.tzinfo or start>=end
                or stop<=now or stop<=start or stop-start>timedelta(hours=3)
                or not f.get('home') or not f.get('away') or f['home']==f['away']):continue
        candidates[(start,base.norm(f['home']),base.norm(f['away']))]=f
    candidates=list(candidates.values());accepted=[];conflicts=[]
    for f in sorted(candidates,key=lambda f:(f['start'],f.get('id',''))):
        if any(o is not f and o['start']<f['stop'] and o['stop']>f['start'] for o in candidates):
            conflicts.append(f.get('id'));continue
        if overlaps(f['start'],f['stop']):continue
        title=f['title']+' [Diffusion estimée]'
        desc=('تقدير بث على Arryadia HD اعتماداً على موعد المباراة المنشور؛ القناة ووقت النهاية غير مؤكدين. '+f['desc'])
        event=base.Event(CHANNEL,f['start'],title,desc,f['stop'],'fr','ar',BOTOLA_SOURCE)
        output.append(event);occupied.append(event)
        accepted.append({'fixture_id':f.get('id'),'title':f['title'],'kickoff_utc':f['start'].astimezone(timezone.utc).isoformat(),
                         'broadcast_channel_confirmed':False,'duration_minutes_estimated':round((f['stop']-f['start']).total_seconds()/60)})
    day=now.date()
    while day<=end.date():
        for t in templates:
            start=base.morocco_wall_clock(day,clock(t['minute']//60,t['minute']%60));stop=start+timedelta(minutes=t['duration_minutes'])
            if stop<=now or start>=end or overlaps(start,stop):continue
            dates=', '.join(t['observed_dates'])
            desc='تقدير برنامج متكرر من ملاحظات SNRT السابقة، وليس موعد بث رسمياً. تواريخ العينة: '+dates
            event=base.Event(CHANNEL,start,'[Estimé] '+t['title'],desc,stop,t['language'],'ar',SMART_SOURCE)
            output.append(event);occupied.append(event)
        day+=timedelta(days=1)
    return sorted(output,key=lambda e:e.start),{'channel':CHANNEL,'mode':mode,'template_count':len(templates),
        'model_observed_dates':sorted({d for t in templates for d in t['observed_dates']}),
        'fixture_provider':provider,'botola_matches_added':accepted,'simultaneous_matches_not_assigned':conflicts,
        'smart_recurring_programmes':sum(e.source==SMART_SOURCE for e in output),'official_broadcast_claim':False}
