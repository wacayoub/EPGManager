#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

TARGETS = {
    "On.Time.Sports.HD.ae",
    "KSA.Sports.3.HD.ae",
    "Kuwait.Sport.HD.ae",
    "Kuwait.TV.Sport.Plus.HD.ae",
    "Jordan.Sport.HD.ae",
    "Palestine.Sport.ae",
}


def load(path):
    data=Path(path).read_bytes()
    if data[:2] == b'\x1f\x8b': data=gzip.decompress(data)
    return ET.fromstring(data), len(data)


def ids(root):
    return {(c.get('id') or '').strip() for c in root.findall('channel')}


def event_keys(root):
    return {((p.get('channel') or '').strip(), (p.get('start') or '').strip(),
             ((p.findtext('title') or '').strip())) for p in root.findall('programme')}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--before',required=True); ap.add_argument('--after',required=True)
    ap.add_argument('--before-audit',required=True); ap.add_argument('--after-audit',required=True)
    ap.add_argument('--json',required=True); ap.add_argument('--text',required=True)
    a=ap.parse_args()
    br,bb=load(a.before); ar,ab=load(a.after)
    bi,ai=ids(br),ids(ar); be,ae=event_keys(br),event_keys(ar)
    removed_ids=sorted(bi-ai); added_ids=sorted(ai-bi)
    removed_events=be-ae; added_events=ae-be
    non_target_removed_ids=[x for x in removed_ids if x not in TARGETS]
    non_target_removed_events=[k for k in removed_events if k[0] not in TARGETS]
    before=json.loads(Path(a.before_audit).read_text(encoding='utf-8'))['summary']
    after=json.loads(Path(a.after_audit).read_text(encoding='utf-8'))['summary']
    assert not non_target_removed_ids, non_target_removed_ids
    assert not non_target_removed_events, non_target_removed_events[:20]
    assert not added_ids, added_ids
    assert not added_events, list(added_events)[:20]
    assert after['FAIL']==0, after
    assert set(removed_ids).issubset(TARGETS), removed_ids
    report={
      'before_channels':len(bi),'after_channels':len(ai),
      'before_events':len(be),'after_events':len(ae),
      'removed_ids':removed_ids,'removed_events':len(removed_events),
      'non_target_removed_ids':non_target_removed_ids,
      'non_target_removed_events':len(non_target_removed_events),
      'xml_saved_bytes':bb-ab,'before_audit':before,'after_audit':after,
      'removed_event_examples':[list(x) for x in sorted(removed_events)[:30]],
    }
    Path(a.json).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=[
      'KNOWN CLONE QUARANTINE A/B',
      'before_channels=%d after_channels=%d removed_ids=%d' % (len(bi),len(ai),len(removed_ids)),
      'before_events=%d after_events=%d removed_events=%d' % (len(be),len(ae),len(removed_events)),
      'xml_saved_bytes=%d' % (bb-ab),
      'before_audit=%r' % before,
      'after_audit=%r' % after,
      '', 'REMOVED IDS'] + ['- '+x for x in removed_ids]
    Path(a.text).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))
    return 0

if __name__=='__main__': raise SystemExit(main())
