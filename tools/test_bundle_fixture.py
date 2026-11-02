"""Coherent XMLTV sidecars for isolated publication test fixtures."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET


def write_bundle_metadata(path):
    raw = path.read_bytes()
    root = ET.fromstring(gzip.decompress(raw))
    key = path.name.removesuffix('.xml.gz')
    ids = [c.get('id') for c in root.findall('channel')]
    path.with_name(key + '.json').write_text(json.dumps({
        'label': key, 'sha256': hashlib.sha256(raw).hexdigest(),
        'size_bytes': len(raw), 'channels': len(ids),
        'programmes': len(root.findall('programme')),
        'generated_utc': datetime.now(timezone.utc).isoformat(),
    }), encoding='utf-8')
    path.with_name(key + '.txt').write_text(''.join(cid + '|Name\n' for cid in ids), encoding='utf-8')
