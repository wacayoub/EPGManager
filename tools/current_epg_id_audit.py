#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PROVIDER_STEMS = [
    'provider-alkass','provider-bein','provider-osn','provider-mbc','provider-rotana',
    'provider-adm','provider-dmi','provider-art','provider-ssc','provider-starz'
]
COUNTRY_STEMS = [
    'mena-eg','mena-sa','mena-ae','mena-qa','mena-kw','mena-bh','mena-om','mena-jo',
    'mena-lb','mena-iq','mena-ps','mena-ye','mena-dz','mena-tn','mena-ly','mena-sd',
    'mena-sy','mena-mr'
]
STEMS = PROVIDER_STEMS + COUNTRY_STEMS + ['mena-other']


def parse_dt(raw: str):
    raw = (raw or '').strip()
    m = re.match(r'^(\d{12}|\d{14})(?:\s*([+-]\d{4}|Z))?', raw)
    if not m:
        return None
    digits, off = m.groups()
    fmt = '%Y%m%d%H%M%S' if len(digits) == 14 else '%Y%m%d%H%M'
    try:
        dt = datetime.strptime(digits, fmt)
    except ValueError:
        return None
    if not off or off == 'Z':
        tz = timezone.utc
    else:
        sign = 1 if off[0] == '+' else -1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


def first_text(node, tag):
    for x in node.findall(tag):
        t = (x.text or '').strip()
        if t:
            return t
    return ''


def display_name(c):
    return first_text(c, 'display-name') or (c.get('id') or '')


def load_xml(path: Path):
    data = path.read_bytes()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return ET.fromstring(data)


def fmt_local(dt):
    if not dt:
        return ''
    casa = timezone(timedelta(hours=1))
    return dt.astimezone(casa).strftime('%Y-%m-%d %H:%M')


def audit_file(path: Path, now_utc: datetime):
    root = load_xml(path)
    channels = { (c.get('id') or '').strip(): c for c in root.findall('channel') if (c.get('id') or '').strip() }
    progs = defaultdict(list)
    for p in root.findall('programme'):
        cid = (p.get('channel') or '').strip()
        if cid not in channels:
            continue
        start, stop = parse_dt(p.get('start')), parse_dt(p.get('stop'))
        if not start or not stop or stop <= start:
            continue
        progs[cid].append((start, stop, p))
    for rows in progs.values():
        rows.sort(key=lambda x: x[0])

    out = []
    for cid in sorted(channels, key=str.casefold):
        rows = progs.get(cid, [])
        current = None
        next_row = None
        for row in rows:
            if row[0] <= now_utc < row[1]:
                current = row
                break
            if row[0] > now_utc and next_row is None:
                next_row = row
        if current:
            status = 'CURRENT'
            # next strictly after current
            for row in rows:
                if row[0] >= current[1]:
                    next_row = row
                    break
        elif not rows:
            status = 'NO_PROGRAMMES'
        elif rows[0][0] > now_utc:
            status = 'FUTURE_ONLY'
        elif rows[-1][1] <= now_utc:
            status = 'ENDED'
        else:
            status = 'GAP_NOW'
            if next_row is None:
                for row in rows:
                    if row[0] > now_utc:
                        next_row = row
                        break

        cur_title = first_text(current[2], 'title') if current else ''
        cur_desc = first_text(current[2], 'desc') if current else ''
        next_title = first_text(next_row[2], 'title') if next_row else ''
        out.append({
            'id': cid,
            'name': display_name(channels[cid]),
            'status': status,
            'current_title': cur_title,
            'current_desc': cur_desc,
            'current_start': fmt_local(current[0]) if current else '',
            'current_stop': fmt_local(current[1]) if current else '',
            'next_title': next_title,
            'next_start': fmt_local(next_row[0]) if next_row else '',
            'next_stop': fmt_local(next_row[1]) if next_row else '',
            'programme_count': len(rows),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--json', required=True)
    ap.add_argument('--txt', required=True)
    args = ap.parse_args()
    base = Path(args.dir)
    now = datetime.now(timezone.utc)
    sources = {}
    totals = defaultdict(int)
    total_ids = 0
    lines = []
    lines.append('CURRENT EPG ID-BY-ID AUDIT')
    lines.append('generated_utc=%s' % now.isoformat())
    lines.append('display_timezone=Africa/Casablanca (UTC+01 at audit time)')
    lines.append('')

    for stem in STEMS:
        path = base / (stem + '.xml.gz')
        if not path.exists():
            sources[stem] = []
            continue
        rows = audit_file(path, now)
        sources[stem] = rows
        total_ids += len(rows)
        for r in rows:
            totals[r['status']] += 1
        lines.append('=== %s | IDs=%d ===' % (stem, len(rows)))
        for r in rows:
            if r['status'] == 'CURRENT':
                lines.append('[CURRENT] %s | %s | %s -> %s | %s | NEXT: %s @ %s' % (
                    r['id'], r['name'], r['current_start'], r['current_stop'],
                    r['current_title'], r['next_title'] or '-', r['next_start'] or '-'))
            else:
                lines.append('[%s] %s | %s | current=- | NEXT: %s @ %s' % (
                    r['status'], r['id'], r['name'], r['next_title'] or '-', r['next_start'] or '-'))
        lines.append('')

    payload = {
        'schema': 1,
        'generated_utc': now.isoformat(),
        'display_timezone': 'Africa/Casablanca',
        'total_ids': total_ids,
        'status_counts': dict(sorted(totals.items())),
        'sources': sources,
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = 'TOTAL IDs=%d ' % total_ids + ' '.join('%s=%d' % (k, v) for k, v in sorted(totals.items()))
    lines.insert(3, summary)
    Path(args.txt).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(summary)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
