"""Read explicit SNRT calendar dates without guessing days or simulcasts."""
from datetime import datetime,timedelta,time
import re
from bs4 import BeautifulSoup
try:
    from tools.programme_authenticity import generated_placeholder
except ModuleNotFoundError:
    from programme_authenticity import generated_placeholder

CLOCK=re.compile(r'^([01]?\d|2[0-3])\s*[hH:]\s*([0-5]\d)$')
VARIANTS={'tnt':'Arryadia_TNT','sat':'Arryadia_HD',
          'hd1':'Arryadia_HD1','hd2':'Arryadia_HD2','hd3':'Arryadia_HD3'}


def clean(text):
    return re.sub(r'\s+',' ',text or '').strip()


def service_ids(row):
    def markers(text,metadata=False):
        text=clean(text).replace('_',' ')
        matches=re.findall(r'\b(?:hd\s*-?\s*[123]|tnt|sat)\b',text,re.I)
        ids={VARIANTS[re.sub(r'[\s-]+','',tag.lower())] for tag in matches}
        arabic=re.sub(r'[\u064b-\u065f\u0640]','',text).replace('أ','ا').replace('إ','ا')
        if (re.search(r'\b(?:الارضية|الارضي|ارضي|ارضية|terrestre)\b',arabic,re.I)
                or metadata and text.casefold() in ('local','locale','terrestrial')):
            ids.add('Arryadia_TNT')
        if metadata and text.casefold() in ('arryadia', 'arryadia hd', 'hd'):
            ids.add('Arryadia_HD')
        return ids

    def declared(node):
        return markers(str(node.get('data-channel','')),True) | markers(str(node.get('data-service','')),True)

    ids=declared(row)
    if ids:return ids
    labels=' '.join(x.get_text(' ',strip=True) for x in row.select('.grille-channel,.channel-label,.service-label'))
    ids=markers(labels,True)
    if ids:return ids
    # A separately labelled service section may contain unmarked programme rows.
    for parent in row.parents:
        if parent.name in ('body','html','[document]'):break
        ids=declared(parent)
        if ids:return ids
    return markers(row.get_text(' ',strip=True)) or {'Arryadia_HD'}


def row_date(row):
    node=row
    for _ in range(6):
        if node is None or node.name in ('body','html','[document]'):break
        for key in ('data-date','data-day','data-dt','id','class'):
            raw=node.get(key,'')
            raw=' '.join(raw) if isinstance(raw,list) else str(raw)
            for token in re.findall(r'(?<!\d)(?:20\d{2}-\d{2}-\d{2}|\d{8})(?!\d)',raw):
                for fmt in ('%Y-%m-%d','%Y%m%d','%d%m%Y'):
                    try:return datetime.strptime(token,fmt).date()
                    except ValueError:pass
        node=getattr(node,'parent',None)
    return None


def infer_stops(events):
    """Set inferred ends from the next start for each service."""
    result=sorted(events,key=lambda e:(e['channel'],e['start'],e['title']))
    next_starts={}
    stops={}
    for event in reversed(result):
        channel,start=event['channel'],event['start']
        key=(channel,start)
        if key not in stops:
            following=next_starts.get(channel)
            stops[key]=following if following and following-start<=timedelta(hours=6) else start+timedelta(hours=1)
            next_starts[channel]=start
        event['stop']=stops[key]
    return result


def parse_grid(html,wall_clock):
    soup=BeautifulSoup(html,'lxml')
    rows=soup.select('.grille-line,.grille-item,article[data-date],.programme[data-date],.program[data-date]')
    events={};dates=set();undated=invalid=placeholders=0
    for row in rows:
        day=row_date(row)
        if day is None:undated+=1;continue
        dates.add(day.isoformat())
        clock=row.select_one('.grille-time,.when,time,.schedule-time,[data-time]')
        label=clean(clock.get('data-time') or clock.get_text(' ',strip=True)) if clock else ''
        match=CLOCK.fullmatch(label)
        heading=row.select_one('h2,h3,h4,.program-title,.programme-title,.title')
        title=clean(heading.get_text(' ',strip=True)) if heading else ''
        if not match or not title:invalid+=1;continue
        if generated_placeholder(title) or title.casefold() in ('arryadia programme','programme arryadia','programmes arryadia'):
            placeholders+=1;continue
        content=row.select_one('.grille-content,.info,.description,.programme-content') or row
        description=clean(content.get_text(' ',strip=True))
        if description.startswith(title):description=clean(description[len(title):])
        if description.startswith(label):description=clean(description[len(label):])
        # A generic SNRT Arryadia grid establishes the main service only.
        # No source statement establishes that TNT/HD1/HD2/HD3 simulcast it.
        ids=service_ids(row)
        start=wall_clock(day,time(int(match[1]),int(match[2])))
        for channel in ids:
            key=(channel,start,title.casefold())
            event={'channel':channel,'start':start,'title':title,'desc':description or title}
            if key not in events or len(event['desc'])>len(events[key]['desc']):events[key]=event
    result=infer_stops(events.values())
    return result,{'observed_programme_dates':sorted(dates),'grid_rows':len(rows),
                   'parsed_events':len(result),'undated_rows':undated,'invalid_rows':invalid,
                   'rejected_placeholder_rows':placeholders,'end_times':'inferred from next same-service start within 6h; terminal/isolated rows capped at 1h'}
