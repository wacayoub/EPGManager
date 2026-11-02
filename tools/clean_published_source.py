"""Remove audited filler from exact SHA-pinned publications; preserve provider age."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    from tools.programme_authenticity import clean_programme
    from tools.source_feed_pack import parse_xmltv_dt
    from tools import audit_source_quality, validate_48h
except ModuleNotFoundError:
    from programme_authenticity import clean_programme
    from source_feed_pack import parse_xmltv_dt
    import audit_source_quality, validate_48h


def clean_payload(source, raw, metadata, expected_sha, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('UTC clock required')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha:
        raise ValueError('Publication changed since review')
    if (metadata.get('label') != source or metadata.get('sha256') != digest
            or metadata.get('size_bytes') != len(raw)):
        raise ValueError('Publication metadata mismatch')
    generated = datetime.fromisoformat(metadata['generated_utc'].replace('Z', '+00:00'))
    if generated.tzinfo is None or generated > now:
        raise ValueError('Invalid provider generation timestamp')
    root = ET.fromstring(gzip.decompress(raw))
    if root.tag != 'tv':
        raise ValueError('Not XMLTV')
    channels = root.findall('channel')
    ids = [c.get('id') for c in channels]
    if not ids or not all(ids) or len(ids) != len(set(ids)) or len(ids) != metadata.get('channels'):
        raise ValueError('Channel catalogue mismatch')
    names = {c.get('id'): [n.text for n in c.findall('display-name')] for c in channels}
    events = root.findall('programme')
    if len(events) != metadata.get('programmes'):
        raise ValueError('Programme count mismatch')
    removed = Counter()
    removed_desc = 0
    removed_titles = 0
    active, current = set(), set()
    for event in events:
        cid = event.get('channel')
        start, stop = parse_xmltv_dt(event.get('start')), parse_xmltv_dt(event.get('stop'))
        if cid not in names or start is None or stop is None or start >= stop:
            raise ValueError('Invalid original programme identity or interval')
        clean = clean_programme(event, names[cid])
        if clean is None:
            removed[cid] += 1
            root.remove(event)
            continue
        removed_desc += len(event.findall('desc')) - len(clean.findall('desc'))
        removed_titles += len(event.findall('title')) - len(clean.findall('title'))
        if ET.tostring(event) != ET.tostring(clean):
            position = list(root).index(event)
            root.remove(event)
            root.insert(position, clean)
        active.add(cid)
        if start <= now < stop:
            current.add(cid)
    # Serialization alone is never a reason to republish an unchanged source.
    changed = bool(removed or removed_desc or removed_titles)
    new_raw = gzip.compress(ET.tostring(root, encoding='utf-8', xml_declaration=True), compresslevel=9, mtime=0) if changed else raw
    new_meta = dict(metadata)
    report = {
        'source': source, 'operation': 'remove_only_explicit_filler_and_unavailable_descriptions',
        'input_sha256': digest, 'output_sha256': hashlib.sha256(new_raw).hexdigest(),
        'preserved_generated_utc': metadata['generated_utc'], 'repacked_utc': now.isoformat(),
        'removed_programmes': sum(removed.values()), 'removed_by_id': dict(sorted(removed.items())),
        'removed_unavailable_descriptions': removed_desc,
        'removed_placeholder_title_nodes': removed_titles,
        'original_programmes': len(events), 'remaining_programmes': len(root.findall('programme')),
        'catalogue_ids_preserved': ids, 'fabricated_programmes': 0,
        'receiver_modified': False, 'changed': changed,
    }
    if changed:
        new_meta.update({
            'sha256': report['output_sha256'], 'size_bytes': len(new_raw),
            'programmes': report['remaining_programmes'], 'active_channels': len(active),
            'zero_epg_channels': len(ids)-len(active), 'zero_epg_ids': sorted(set(ids)-active),
            'coverage_pct': round(100*len(active)/len(ids), 1) if ids else 0,
            'currently_on_air_channels': len(current),
            'current_coverage_pct': round(100*len(current)/len(ids), 1) if ids else 0,
            'currently_uncovered_ids': sorted(set(ids)-current),
            'repacked_utc': report['repacked_utc'],
            'quality_cleanup': {key: report[key] for key in (
                'input_sha256', 'output_sha256', 'preserved_generated_utc', 'repacked_utc')},
        })
    return new_raw, new_meta, report


def run(root, source, expected_sha, apply=False):
    root = Path(root)
    names = json.loads((root/'config/canonical-channel-policy.json').read_text(encoding='utf-8'))['default_source_order']
    if source not in names:
        raise ValueError('Unknown direct source')
    feed = root/'feeds'/(source+'.xml.gz')
    metadata = root/'feeds'/(source+'.json')
    raw = feed.read_bytes()
    body, meta, report = clean_payload(source, raw, json.loads(metadata.read_text(encoding='utf-8')), expected_sha)
    if apply and report['changed']:
        feed.write_bytes(body)
        metadata.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (root/'reports'/(source+'-quality-cleanup.json')).write_text(
            json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        now = datetime.now(timezone.utc)
        quality = audit_source_quality.analyze(feed, source, now)
        quality['source_sha256'] = meta['sha256']
        coverage = validate_48h.audit(str(feed), 48, 90)
        (root/'reports'/(source+'-quality.json')).write_text(
            json.dumps(quality, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (root/'reports'/(source+'-48h-audit.json')).write_text(json.dumps({
            'generated_utc': now.isoformat(), 'source_sha256': meta['sha256'],
            'mode': 'source-aware', 'feeds': [coverage]}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.source, args.expected_sha, args.apply), ensure_ascii=False, indent=2))
