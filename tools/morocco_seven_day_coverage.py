"""Observe actual per-ID coverage in the final 168-hour Morocco publication."""
import argparse,gzip,hashlib,json
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
try:
    from tools.source_health_snapshot import inspect
    from tools.source_feed_pack import parse_xmltv_dt
    from tools.programme_authenticity import viewer_reported_schedule,smart_estimated_schedule,automatic_fallback_schedule
except ModuleNotFoundError:
    from source_health_snapshot import inspect
    from source_feed_pack import parse_xmltv_dt
    from programme_authenticity import viewer_reported_schedule,smart_estimated_schedule,automatic_fallback_schedule


def coverage(feed,now=None):
    feed=Path(feed);now=now or datetime.now(timezone.utc);end=now+timedelta(hours=168)
    health=inspect(feed.parent.parent,'morocco',now)
    if health['status'] in ('INVALID_PUBLICATION','MISSING'):
        raise ValueError('Invalid final Morocco publication')
    raw=feed.read_bytes();xml=ET.fromstring(gzip.decompress(raw));zone=ZoneInfo('Africa/Casablanca')
    detail=[]
    for channel in xml.findall('channel'):
        cid=channel.get('id');rows=[]
        for node in xml.findall('programme'):
            if node.get('channel')!=cid:continue
            start,stop=parse_xmltv_dt(node.get('start')),parse_xmltv_dt(node.get('stop'))
            if start<end and stop>now:rows.append((start,stop,node))
        cursor=now;missing=0.0
        for start,stop,_ in sorted(rows,key=lambda r:(r[0],r[1])):
            if start>cursor:missing+=(min(start,end)-min(cursor,end)).total_seconds()/60
            cursor=max(cursor,stop)
        if cursor<end:missing+=(end-cursor).total_seconds()/60
        latest=max((stop for _,stop,_ in rows),default=None)
        viewer=sum(viewer_reported_schedule(n) for _,_,n in rows)
        smart=sum(smart_estimated_schedule(n) for _,_,n in rows)
        auto=sum(automatic_fallback_schedule(n) for _,_,n in rows)
        detail.append({'id':cid,'programmes_in_requested_window':len(rows),
                       'source_programmes':len(rows)-viewer-smart-auto,'viewer_reported_programmes':viewer,
                       'automatic_fallback_programmes':auto,
                       'smart_estimated_programmes':smart,
                       'programmes_by_start_date_local':dict(sorted(Counter(s.astimezone(zone).date().isoformat() for s,_,_ in rows).items())),
                       'last_stop_utc':latest.isoformat() if latest else None,
                       'remaining_max_hours':round(max(0,(latest-now).total_seconds()/3600),2) if latest else 0,
                       'uncovered_minutes_in_168h':round(missing,2),
                       'status':'NO_FUTURE_EPG' if not rows else 'FULL_REQUESTED_WINDOW' if missing==0 else 'PARTIAL_REQUESTED_WINDOW'})
    return {'checked_utc':now.isoformat(),'scope':'actual final published Morocco bytes; 7 days requested where available',
            'requested_hours':168,'feed_sha256':hashlib.sha256(raw).hexdigest(),
            'channels':len(detail),'channels_with_future_epg':sum(r['programmes_in_requested_window']>0 for r in detail),
            'receiver_release_status':'NO_GO','ids':detail}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed',type=Path,default=Path('feeds/morocco.xml.gz'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=coverage(args.feed)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in result['ids']:print(row['id'],row['status'],row['remaining_max_hours'])
