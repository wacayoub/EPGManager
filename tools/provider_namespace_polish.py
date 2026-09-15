#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final deterministic polish for globally normalized receiver IDs."""
import argparse, gzip, json, re
import xml.etree.ElementTree as ET
from pathlib import Path

REPL = [
    (re.compile(r'^OSN\.OS\.Ntv\.'), 'OSN.'),
    (re.compile(r'^OSN\.OSNtv\.'), 'OSN.'),
    (re.compile(r'^OSN\.i\.QIYI\.'), 'OSN.iQIYI.'),
    (re.compile(r'^STARZ\.PLAY\.'), 'STARZ.'),
    (re.compile(r'^STARZ\.Play\.'), 'STARZ.'),
    (re.compile(r'^Te\.N\.'), 'TeN.'),
]

def fix(cid):
    out = cid
    for rx, rep in REPL:
        out = rx.sub(rep, out)
    return out

def read(path):
    raw = path.read_bytes()
    if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def write(path, root):
    ET.indent(root, space='  ')
    xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    path.write_bytes(gzip.compress(xml, compresslevel=9, mtime=0))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--dir', required=True); a = ap.parse_args()
    base = Path(a.dir); changes = {}
    # Build mapping from every published channel ID.
    for p in sorted(base.glob('*.xml.gz')):
        root = read(p)
        for c in root.findall('channel'):
            old = (c.get('id') or '').strip(); new = fix(old)
            if old and new != old: changes[old] = new
    for p in sorted(base.glob('*.xml.gz')):
        root = read(p); seen = set(); out = ET.Element('tv', dict(root.attrib))
        for c in root.findall('channel'):
            old = (c.get('id') or '').strip(); new = changes.get(old, old)
            if new in seen: raise SystemExit(f'collision {p.name}: {new}')
            c.set('id', new); seen.add(new); out.append(c)
        for e in root.findall('programme'):
            old = (e.get('channel') or '').strip(); e.set('channel', changes.get(old, old)); out.append(e)
        write(p, out)
        txt = p.with_suffix('').with_suffix('.txt')
        if txt.exists():
            names = {(c.get('id') or ''): next(((n.text or '').strip() for n in c.findall('display-name') if (n.text or '').strip()), c.get('id') or '') for c in out.findall('channel')}
            txt.write_text(''.join(f'{cid}|{names[cid]}\n' for cid in names), encoding='utf-8')
    (base/'provider-namespace-polish.json').write_text(json.dumps({'changes':changes}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'PROVIDER_NAMESPACE_POLISH PASS changes={len(changes)}')
    return 0
if __name__ == '__main__': raise SystemExit(main())
