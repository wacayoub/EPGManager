"""User-requested fallback blocks, distinct from broadcaster programmes."""
from datetime import datetime, time, timedelta, timezone

try:
    from tools.programme_authenticity import AUTO_SOURCE
except ModuleNotFoundError:
    from programme_authenticity import AUTO_SOURCE

# Main HD keeps Smart/Botola first; the automatic blocks only fill the hours
# those estimates leave empty (SNRT's grid stopped in September, so the main
# channel otherwise had an 8-9 h hole every day). HD2/HD3 keep the reported
# Grand Format loop.
SERVICES = {
    'Arryadia_HD': ('[Auto] برامج الرياضية', 2),
    'AFLAM.ma': ('[Auto] برامج قناة السابعة AFLAM', 3),
    'Arryadia_HD1': ('[Auto] برامج الرياضية HD1', 2),
    'Arryadia_TNT': ('[Auto] برامج الرياضية TNT المحلية', 2),
}
DESCRIPTION = (
    'جدول احتياطي تلقائي بطلب المستخدم، وليس برنامج بث رسمياً. '
    'لا يؤكد عنوان فيلم أو مباراة أو أن القناة تبث حالياً. '
    'تستبدله البرامج الرسمية المؤرخة للقناة نفسها عند توفرها.'
)


def is_auto_fallback(event):
    spec = SERVICES.get(event.channel)
    return bool(spec and event.source == AUTO_SOURCE and event.title == spec[0]
                and event.desc == DESCRIPTION and event.tl == event.dl == 'ar'
                and event.start and event.stop and event.start.tzinfo and event.stop.tzinfo
                and timedelta(0) < event.stop-event.start <= timedelta(hours=spec[1]))


def generate(verified, now, hours=168):
    """Fill only uncovered intervals; never copy another service's guide."""
    try:
        from tools import morocco_epg as base
    except ModuleNotFoundError:
        import morocco_epg as base
    if not now.tzinfo or not 0 < hours <= 168:
        raise ValueError('An aware clock and a window of at most seven days are required')
    end = now + timedelta(hours=hours)
    output = []
    for cid, (title, block_hours) in SERVICES.items():
        occupied = sorted((e.start, e.stop) for e in verified
                          if e.channel == cid and e.source != AUTO_SOURCE
                          and e.start and e.stop and e.stop > e.start
                          and e.start < end and e.stop > now)
        day = now.astimezone(base.TZ).date()
        while day <= end.astimezone(base.TZ).date():
            for hour in range(0, 24, block_hours):
                start = datetime.combine(day, time(hour), base.TZ)
                stop = start + timedelta(hours=block_hours)
                left, right = max(start, now), min(stop, end)
                if left >= right:
                    continue
                cursor = left
                for busy_start, busy_stop in occupied:
                    if busy_stop <= cursor or busy_start >= right:
                        continue
                    if cursor < busy_start:
                        output.append(base.Event(cid, cursor, title, DESCRIPTION,
                                                 busy_start, 'ar', 'ar', AUTO_SOURCE))
                    cursor = max(cursor, busy_stop)
                    if cursor >= right:
                        break
                if cursor < right:
                    output.append(base.Event(cid, cursor, title, DESCRIPTION,
                                             right, 'ar', 'ar', AUTO_SOURCE))
            day += timedelta(days=1)
    output.sort(key=lambda e: (e.channel, e.start))
    return output, {
        'checked_utc': now.astimezone(timezone.utc).isoformat(),
        'scope': 'user-requested automatic fallback; not official programme listings',
        'requested_hours': hours,
        'official_programmes_take_priority': True,
        'receiver_action': False,
        'channels': {cid: {'automatic_fallback_programmes': sum(e.channel == cid for e in output),
                          'block_hours': block_hours, 'official_broadcast_claim': False}
                     for cid, (_, block_hours) in SERVICES.items()},
    }
