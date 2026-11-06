"""Measure the published bytes now, including when a scrape retained old LKG."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.audit_source_quality import analyze, parse_time
except ImportError:
    from audit_source_quality import analyze, parse_time


def inspect(root, source, now=None):
    try:
        return inspect_valid_publication(root, source, now)
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError, EOFError) as exc:
        return {'source': source, 'checked_utc': (now or datetime.now(timezone.utc)).isoformat(),
                'status': 'INVALID_PUBLICATION', 'reason': str(exc)[:160],
                'action_success_does_not_certify_guide_quality': True}


def inspect_valid_publication(root, source, now=None):
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    path = root / 'feeds' / (source + '.xml.gz')
    if not path.exists():
        return {'source': source, 'checked_utc': now.isoformat(), 'status': 'MISSING'}
    data = path.read_bytes()
    meta = json.loads((root/'feeds'/(source+'.json')).read_text(encoding='utf-8'))
    if (meta.get('label') != source or meta.get('sha256') != hashlib.sha256(data).hexdigest()
            or meta.get('size_bytes') != len(data)):
        raise ValueError('Published label, SHA256 or size mismatch')
    report = analyze(path, source, now)
    if (meta.get('channels') != report['channels'] or meta.get('programmes') != report['programmes']
            or report['invalid_dates']):
        raise ValueError('Published XMLTV counts or dates invalid')
    xml = ET.fromstring(gzip.decompress(data))
    ids=[c.get('id') for c in xml.findall('channel')]
    if (xml.tag!='tv' or not all(ids) or len(ids)!=len(set(ids))
            or any(p.get('channel') not in ids for p in xml.findall('programme'))):
        raise ValueError('Published XMLTV channel identities are invalid')
    index=(root/'feeds'/(source+'.txt')).read_text(encoding='utf-8').splitlines()
    indexed=[line.split('|',1)[0].strip() for line in index if line.strip()]
    if len(indexed)!=len(set(indexed)) or set(indexed)!=set(ids):
        raise ValueError('Published TXT channel index disagrees with XMLTV')
    ends = [parse_time(p.get('stop')) for p in xml.findall('programme')]
    ends = [x for x in ends if x and x > now]
    remaining = (max(ends) - now).total_seconds() / 3600 if ends else 0
    missing = report['channels_without_future_epg']
    flagged = report['suspected_placeholder_channels']
    decision_path = root / 'reports' / (source + '-publish-lkg.json')
    decision = {}
    if decision_path.exists():
        decision = json.loads(decision_path.read_text(encoding='utf-8'))
        decision = (decision.get('sources') or {}).get(source) or decision
    kept = decision.get('decision') == 'KEEP_LKG'
    return {
        'source': source, 'checked_utc': now.isoformat(),
        'feed_sha256': hashlib.sha256(data).hexdigest(),
        'status': 'NO_FUTURE_EPG' if not ends else 'DEGRADED' if (
            missing or flagged or remaining < 8 or kept or report.get('channels_with_overlapping_programmes') or report.get('viewer_reported_recurring_programmes') or report.get('smart_estimated_programmes') or report.get('automatic_fallback_programmes')) else 'AVAILABLE',
        'last_attempt_kept_lkg': kept,
        'channels': report['channels'],
        'channels_with_future_epg': report['channels_with_future_epg'],
        'channels_without_future_epg': missing,
        'suspected_placeholder_channels': flagged,
        'channels_with_overlapping_programmes': report.get('channels_with_overlapping_programmes',{}),
        'future_programmes_overlapping_previous': report.get('future_programmes_overlapping_previous',0),
        'viewer_reported_recurring_channels': report.get('viewer_reported_recurring_channels',[]),
        'viewer_reported_recurring_programmes': report.get('viewer_reported_recurring_programmes',0),
        'smart_estimated_channels': report.get('smart_estimated_channels',[]),
        'smart_estimated_programmes': report.get('smart_estimated_programmes',0),
        'automatic_fallback_channels': report.get('automatic_fallback_channels',[]),
        'automatic_fallback_programmes': report.get('automatic_fallback_programmes',0),
        'remaining_max_hours': round(remaining, 2),
        'channels_with_less_than_24h': report['channels_with_shorter_than_24h'],
        'action_success_does_not_certify_guide_quality': True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='.')
    ap.add_argument('--source', default='')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    root = Path(args.root)
    sources = [args.source] if args.source else json.loads(
        (root / 'config/canonical-channel-policy.json').read_text())['default_source_order']
    result = [inspect(root, name) for name in sources]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    for row in result:
        print(row['source'], row['status'], row.get('channels_with_future_epg', 0), '/', row.get('channels', 0))
        if row['status'] != 'AVAILABLE':
            print('::warning title=Published EPG requires review::' + row['source'] + ': ' + row['status'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
