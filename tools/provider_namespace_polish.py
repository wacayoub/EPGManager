#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final deterministic polish for globally normalized receiver IDs.

Provider-specific canonical IDs that are already part of the receiver contract
must survive the generic global namespace pass.  In particular ADM has a proven
canonical identity policy used by EPGManager mappings; the generic ``ADM.*``
slugs are therefore translated back to those stable receiver IDs here.
"""
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

# These IDs are the receiver-facing contract already enforced by
# adm_identity_policy.py / adm_id_audit_policy.py.  The global normalizer creates
# descriptive ADM.* slugs; this exact table restores only proven ADM identities
# and leaves unknown/new ADM services untouched for audit instead of guessing.
ADM_CANONICAL = {
    'ADM.Abu.Dhabi.TV.ae': 'AbuDhabiTV.ae',
    'ADM.Al.Emarat.TV.ae': 'AbuDhabiEmirates.ae',
    'ADM.Abu.Dhabi.Sports.1.ae': 'AbuDhabiSports1.ae',
    'ADM.Abu.Dhabi.Sports.2.ae': 'AbuDhabiSports2.ae',
    'ADM.AD.Sports.Premium.1.ae': 'ADSportsPremium1.ae',
    'ADM.AD.Sports.Premium.2.ae': 'ADSportsPremium2.ae',
    'ADM.AD.Sports.Extra.ae': 'ADSportsExtra.ae',
    'ADM.Yas.TV.ae': 'YasTV.ae',
    'ADM.YAS.TV.ae': 'YasTV.ae',
    'ADM.YAS.TV.Extra.ae': 'YasTVExtra.ae',
    'ADM.Yas.TV.Extra.ae': 'YasTVExtra.ae',
    'ADM.Majid.ae': 'Majid.ae',
    'ADM.National.Geographic.Abu.Dhabi.ae': 'NationalGeographicAbuDhabi.ae',
    'ADM.Baynounah.TV.ae': 'BaynounahTV.ae',
}


def fix(cid):
    if cid in ADM_CANONICAL:
        return ADM_CANONICAL[cid]
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

    # Fail fast if the known ADM contract ever becomes internally ambiguous.
    if len(set(ADM_CANONICAL.values())) != len(ADM_CANONICAL.values()):
        raise SystemExit('ADM canonical polish table contains a collision')

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
