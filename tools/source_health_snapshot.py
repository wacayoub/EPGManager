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
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    path = root / 'feeds' / (source + '.xml.gz')
    if not path.exists():
        return {'source': source, 'checked_utc': now.isoformat(), 'status': 'MISSING'}
    data = path.read_bytes()
    report = analyze(path, source, now)
    xml = ET.fromstring(gzip.decompress(data))
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
            missing or flagged or remaining < 8 or kept) else 'AVAILABLE',
        'last_attempt_kept_lkg': kept,
        'channels': report['channels'],
        'channels_with_future_epg': report['channels_with_future_epg'],
        'channels_without_future_epg': missing,
        'suspected_placeholder_channels': flagged,
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
