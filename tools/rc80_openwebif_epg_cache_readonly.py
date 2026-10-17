#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only OpenWebif eEPGCache sample. Never proves XMLTV source provenance.
No user channel names, service refs, titles, credentials, settings or mappings
are printed/saved. Only HTTP GET to 127.0.0.1; no opkg, restart or GUI action.
"""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, Request, HTTPRedirectHandler
from urllib.error import URLError, HTTPError

PLUGIN = Path('/usr/lib/enigma2/python/Plugins/Extensions/EPGManager')
METHODS = ('getservices', 'epgservicenow', 'epgservicenext')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def get(method, args=None):
    if method not in METHODS:
        raise ValueError('non-read request denied')
    url = 'http://127.0.0.1:80/api/' + method
    if args:
        url += '?' + urlencode(args)
    request = Request(url, headers={'Accept':'application/json'}, method='GET')
    with build_opener(ProxyHandler({}), NoRedirect).open(request, timeout=3) as response:
        body = response.read(1000001)
        if len(body) > 1000000:
            raise ValueError('response limit')
        value = json.loads(body.decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('unexpected OpenWebif result')
        return value

def version():
    file = PLUGIN / 'version.py'
    try:
        if file.is_symlink():
            return 'UNKNOWN'
        parsed = ast.parse(file.read_text(encoding='utf-8'))
        versions = [n.value.value for n in parsed.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)
                    and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
        return versions[0] if len(versions)==1 else 'UNKNOWN'
    except (OSError, SyntaxError, ValueError, UnicodeError):
        return 'UNKNOWN'

def valid(events, now, kind):
    if not isinstance(events,list):
        return False
    for e in events[:12]:
        if not isinstance(e, dict) or not str(e.get('title') or '').strip():
            continue
        try:
            begin = int(e.get('begin_timestamp',e.get('begin')))
            duration = int(e.get('duration_sec',e.get('duration')))
        except (ValueError, TypeError):
            continue
        if duration <= 0 or duration > 86400:
            continue
        if (kind == 'now' and begin <= now < begin+duration) or (kind == 'next' and now <= begin <= now+172800):
            return True
    return False

def run():
    current = version()
    now = int(datetime.now(timezone.utc).timestamp())
    output = {'audit':'OpenWebif live EPG NOW/NEXT sample',
              'installed_version':current,
              'target':'RC79_BASELINE' if current=='2026.10.08-rc79.1'
                  else 'RC80_LAB2' if current=='2026.10.17-rc80-lab2' else 'OTHER',
              'read_only':True, 'receiver_modified':False,'opkg_executed':False,
              'network_scope':'localhost_only','source_provenance_verified':False,
              'rc80_hardware_certified':False,'authorized_to_install_rc80':False,
              'authorized_to_publish_rc80':False,'sampled_services':0,
              'services_with_now':0,'services_with_next':0,'api_errors':0,
              'measured_at_utc':datetime.fromtimestamp(now,timezone.utc).isoformat()}
    try:
        bouquets = get('getservices').get('services') or []
        if not isinstance(bouquets,list): raise ValueError('invalid bouquet response')
        refs = []
        for b in bouquets[:4]:
            if not isinstance(b,dict): continue
            bref = b.get('servicereference')
            if not isinstance(bref,str) or len(bref)>600: continue
            services = get('getservices',{'sRef':bref}).get('services') or []
            if not isinstance(services,list):continue
            for s in services[:160]:
                if not isinstance(s,dict):continue
                ref = s.get('servicereference')
                if isinstance(ref,str) and len(ref)<350 and ref.startswith('1:0:') and ref not in refs:
                    refs.append(ref)
                if len(refs)>=8:break
            if len(refs)>=8:break
        output['sampled_services']=len(refs)
        for ref in refs:
            for kind,endpoint in (('now','epgservicenow'),('next','epgservicenext')):
                try:
                    body = get(endpoint,{'sRef':ref})
                    if valid(body.get('events'),now,kind):
                        output['services_with_'+kind]+=1
                except (OSError,ValueError,UnicodeError,HTTPError,URLError,TimeoutError):
                    output['api_errors']+=1
        output['status'] = ('EPG_CACHE_SAMPLE_VISIBLE' if output['services_with_now']
                            else 'NO_CURRENT_EPG_IN_SAMPLE' if refs else 'NO_SAMPLE_SERVICES')
    except (OSError,ValueError,UnicodeError,HTTPError,URLError,TimeoutError) as e:
        output['status']='OPENWEBIF_LOCAL_UNAVAILABLE'
        output['error_category']=type(e).__name__
    return output

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2,sort_keys=True))
