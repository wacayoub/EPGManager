#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, privacy-preserving OpenWebif EPG baseline probe for Enigma2.

Samples service identities across TV bouquets, never outputs their names/refs
or programmes. Only localhost GET API calls. No EPG import or configuration.
This measures cached event visibility, NOT provenance or RC80 functionality.
"""
from datetime import datetime, timezone
import json
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

MAX_BOUQUETS = 8
MAX_SERVICES = 32
MAX_REPLY = 1000000
METHODS = frozenset(('getservices', 'epgservicenow', 'epgservicenext', 'epgservice'))

class DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def api(method, params=None):
    if method not in METHODS:
        raise ValueError('unsafe API method')
    url = 'http://127.0.0.1:80/api/' + method
    if params:
        url += '?' + urlencode(params)
    req = Request(url, headers={'Accept':'application/json'}, method='GET')
    opener = build_opener(ProxyHandler({}), DenyRedirect)
    with opener.open(req, timeout=3) as response:
        data = response.read(MAX_REPLY + 1)
    if len(data) > MAX_REPLY:
        raise ValueError('OpenWebif response too large')
    result = json.loads(data.decode('utf-8'))
    if not isinstance(result, dict):
        raise ValueError('invalid OpenWebif response type')
    return result

def positions(n, k):
    """Deterministically spread k sample slots across a collection."""
    if n <= 0 or k <= 0:
        return []
    if n <= k:
        return list(range(n))
    return sorted({(i*(n-1))//(k-1) for i in range(k)}) if k > 1 else [n//2]

def safe_ref(item):
    if not isinstance(item, dict):
        return None
    ref = item.get('servicereference')
    if not isinstance(ref, str) or len(ref) > 300 or not ref.startswith('1:0:'):
        return None
    lowered = ref.lower()
    # IPTV URLs may embed credentials. Never include one in a request URL/log.
    if any(token in lowered for token in ('http', '%3a', '@', '://')):
        return None
    return ref

def get_events(response, state):
    if response.get('result') is False:
        state['api_declared_failure'] += 1
        return []
    events = response.get('events')
    if not isinstance(events, list):
        state['invalid_event_response'] += 1
        return []
    state['returned_event_items'] += len(events)
    return events

def classify(events, epoch):
    now = future = past = 0
    for event in events[:160]:
        if not isinstance(event, dict):
            continue
        title = str(event.get('title') or '').strip()
        if not title or title.lower() in ('n/a', 'none', 'unknown', 'no epg'):
            continue
        try:
            start = int(event.get('begin_timestamp', event.get('begin')))
            seconds = int(event.get('duration_sec', event.get('duration')))
        except (TypeError, ValueError, OverflowError):
            continue
        if not (1000000000 < start < 5000000000 and 0 < seconds < 259200):
            continue
        if start <= epoch < start + seconds:
            now += 1
        elif epoch <= start <= epoch + 172800:
            future += 1
        elif start + seconds <= epoch:
            past += 1
    return bool(now), bool(future), bool(past)

def run(fetch=api, epoch=None):
    when = int(datetime.now(timezone.utc).timestamp()) if epoch is None else int(epoch)
    out = {
        'audit': 'EPGManager RC79/OpenWebif cached programme baseline v2',
        'measured_at_utc': datetime.fromtimestamp(when, timezone.utc).isoformat(),
        'read_only': True, 'network_scope':'localhost_only',
        'receiver_modified': False, 'opkg_executed': False,
        'authorized_to_install_rc80':False, 'authorized_to_publish_rc80':False,
        'rc80_hardware_certified':False, 'source_provenance_verified':False,
        'bouquets_sampled':0,'services_sampled':0, 'services_with_now':0,
        'services_with_next':0, 'services_with_schedule_current':0,
        'services_with_schedule_future':0, 'services_with_schedule_past_only':0,
        'api_errors':0,'api_declared_failure':0,'invalid_event_response':0,
        'returned_event_items':0,'excluded_unsafe_references':0,
    }
    try:
        bouquets = fetch('getservices').get('services')
        if not isinstance(bouquets, list):
            raise ValueError('bouquet list is missing')
        selections = []
        for i in positions(len(bouquets), MAX_BOUQUETS):
            bouquet = bouquets[i]
            if not isinstance(bouquet, dict):
                continue
            bref = bouquet.get('servicereference')
            if not isinstance(bref, str) or len(bref) > 600:
                continue
            try:
                services = fetch('getservices', {'sRef': bref}).get('services')
                if not isinstance(services, list):
                    out['invalid_event_response'] += 1
                    continue
                out['bouquets_sampled'] += 1
                safe = []
                for service in services:
                    ref = safe_ref(service)
                    if ref:
                        safe.append(ref)
                    elif isinstance(service, dict) and 'servicereference' in service:
                        out['excluded_unsafe_references'] += 1
                for index in positions(len(safe), 4):
                    ref = safe[index]
                    if ref not in selections:
                        selections.append(ref)
                    if len(selections) == MAX_SERVICES:
                        break
            except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError):
                out['api_errors'] += 1
        out['services_sampled'] = len(selections)
        for ref in selections:
            args = {'sRef':ref}
            now_seen = next_seen = False
            for method, category in (('epgservicenow','services_with_now'),
                                     ('epgservicenext','services_with_next')):
                try:
                    events = get_events(fetch(method, args), out)
                    has_now, has_future, _ = classify(events,when)
                    yes = has_now if category == 'services_with_now' else has_future
                    if yes:
                        out[category] += 1
                    if category == 'services_with_now':
                        now_seen = yes
                    else:
                        next_seen = yes
                except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError):
                    out['api_errors'] += 1
            # Independent schedule endpoint reveals API/sampling discrepancies.
            if not now_seen or not next_seen:
                try:
                    events = get_events(fetch('epgservice', args), out)
                    has_now, has_future, has_past = classify(events, when)
                    if has_now:
                        out['services_with_schedule_current'] += 1
                    if has_future:
                        out['services_with_schedule_future'] += 1
                    if has_past and not has_now and not has_future:
                        out['services_with_schedule_past_only'] += 1
                except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError):
                    out['api_errors'] += 1
        if not selections:
            out['status'] = 'INCONCLUSIVE_NO_VALID_SAMPLE'
        elif out['services_with_now'] or out['services_with_next'] or out['services_with_schedule_current'] or out['services_with_schedule_future']:
            out['status'] = 'EPG_EVENTS_VISIBLE_IN_SAMPLE'
        elif out['api_errors'] or out['api_declared_failure'] or out['invalid_event_response']:
            out['status'] = 'INCONCLUSIVE_API_OR_SCHEMA'
        else:
            out['status'] = 'NO_CURRENT_OR_FUTURE_EPG_IN_SAMPLE'
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError):
        out['status'] = 'OPENWEBIF_LOCAL_UNAVAILABLE'
    return out

if __name__ == '__main__':
    print(json.dumps(run(), indent=2, ensure_ascii=False, sort_keys=True))
