"""Verify an exact source publication in Git, even after a newer refresh."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

try:
    from tools.source_health_snapshot import inspect
    from tools.source_feed_pack import parse_xmltv_dt
except ModuleNotFoundError:
    from source_health_snapshot import inspect
    from source_feed_pack import parse_xmltv_dt


def git_bytes(root, *args):
    result=subprocess.run(['git',*args],cwd=root,capture_output=True,timeout=25)
    if result.returncode:
        raise ValueError('Git publication evidence unavailable')
    return result.stdout


def verify(root, source, run_id, attempt='1'):
    root=Path(root)
    if not re.fullmatch(r'[a-z][a-z0-9]{1,25}',source):
        raise ValueError('Invalid source')
    if not re.fullmatch(r'\d+',str(run_id)) or not re.fullmatch(r'\d+',str(attempt)):
        raise ValueError('A numeric producer run and attempt are required')
    report_path='reports/'+source+'-publish-lkg.json'
    receipt_path=f'reports/refresh-runs/{run_id}/{attempt}/{source}.json'
    receipt_commits=git_bytes(root,'log','-1','--format=%H','--',receipt_path).decode().splitlines()
    commits=git_bytes(root,'log','-100','--format=%H','--',report_path).decode().splitlines()
    chosen=None
    candidates=[(commit,receipt_path) for commit in receipt_commits]
    candidates.extend((commit,report_path) for commit in commits)
    for commit,path in candidates:
        data=json.loads(git_bytes(root,'show',commit+':'+path).decode('utf-8'))
        if str(data.get('github_run_id'))==str(run_id) and str(data.get('github_run_attempt','1'))==str(attempt):
            chosen=(commit,data)
            break
    if chosen is None:
        raise ValueError('No publication evidence for this source/run/attempt')
    commit,data=chosen
    row=data.get('sources',{}).get(source,{})
    if row.get('published') is not True or row.get('decision')!='PUBLISH':
        raise ValueError('Requested source attempt did not publish; LKG was retained')
    raw=git_bytes(root,'show',commit+':feeds/'+source+'.xml.gz')
    meta=json.loads(git_bytes(root,'show',commit+':feeds/'+source+'.json').decode('utf-8'))
    digest=hashlib.sha256(raw).hexdigest()
    if meta.get('label')!=source or meta.get('sha256')!=digest or meta.get('size_bytes')!=len(raw):
        raise ValueError('Producer commit contains a mismatched publication bundle')
    xml=ET.fromstring(gzip.decompress(raw))
    ids=[c.get('id') for c in xml.findall('channel')]
    programmes=xml.findall('programme')
    stats=row.get('candidate_stats') or {}
    if (xml.tag!='tv' or not ids or not all(ids) or len(ids)!=len(set(ids))
            or meta.get('channels')!=len(ids) or meta.get('programmes')!=len(programmes)
            or not programmes or stats.get('channels')!=len(ids) or stats.get('programmes')!=len(programmes)):
        raise ValueError('Producer commit XMLTV counts/IDs disagree with its publication decision')
    index=git_bytes(root,'show',commit+':feeds/'+source+'.txt').decode('utf-8').splitlines()
    indexed=[line.split('|',1)[0].strip() for line in index if line.strip()]
    if len(indexed)!=len(set(indexed)) or set(indexed)!=set(ids):
        raise ValueError('Producer commit ID index does not match XMLTV')
    for programme in programmes:
        try:
            start,stop=parse_xmltv_dt(programme.get('start')),parse_xmltv_dt(programme.get('stop'))
        except (ValueError,OverflowError):
            start=stop=None
        if programme.get('channel') not in ids or start is None or stop is None or start>=stop:
            raise ValueError('Producer commit contains invalid programme identity or interval')
    current=inspect(root,source)
    if current['status'] in ('MISSING','INVALID_PUBLICATION','NO_FUTURE_EPG'):
        raise ValueError('Current published source is unusable: '+current['status'])
    return {'source':source,'producer_run_id':str(run_id),'producer_attempt':str(attempt),
            'verified_publication_commit':commit,'verified_publication_sha256':digest,
            'programmes_at_publication':len(programmes),'current_health':current,
            'current_bytes_differ_from_attempt':current['feed_sha256']!=digest,
            'receiver_release_status':'NO_GO','scope':'exact_source_attempt_and_current_source_health'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--source',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--attempt',default='1')
    args=parser.parse_args()
    print(json.dumps(verify(args.root,args.source,args.run_id,args.attempt),ensure_ascii=False,indent=2))
