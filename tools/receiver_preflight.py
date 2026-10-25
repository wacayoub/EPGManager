#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast receiver hardening preflight.

Canonicalizes the published LKG namespace before selection, resolves exact alias
bridges, quarantines unknown reserved provider IDs, and proves the central
receiver contract is internally coherent. This runs before the expensive grab.
"""
from __future__ import annotations
import argparse, gzip, json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
from receiver_contract import is_unknown_reserved, validate_contract

def read_root(path: Path):
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def load_aliases(path: Path):
    if not path.exists():
        return {}
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        return {str(k):str(v) for k,v in (data.get("mapping") or {}).items()}
    except Exception:
        return {}

def resolve(cid, mapping):
    seen=set()
    cur=cid
    while cur in mapping and mapping[cur] != cur and cur not in seen:
        seen.add(cur); cur=mapping[cur]
    return cur

def canonicalize_lkg(source: Path, target: Path, mapping):
    root=read_root(source)
    channels={(c.get("id") or "").strip():c for c in root.findall("channel")}
    counts=Counter((p.get("channel") or "").strip() for p in root.findall("programme"))
    grouped={}
    quarantine=[]
    resolved_count=0
    for old,c in channels.items():
        new=resolve(old,mapping)
        if new != old: resolved_count += 1
        if is_unknown_reserved(new):
            quarantine.append(old); continue
        grouped.setdefault(new,[]).append(old)
    winner={}
    for new,olds in grouped.items():
        olds.sort(key=lambda old:(-counts[old],0 if old==new else 1,old.casefold()))
        winner[olds[0]]=new
    kept_raw=set(winner)
    out=ET.Element("tv",dict(root.attrib))
    kept_ids=set()
    for old,new in winner.items():
        c=ET.fromstring(ET.tostring(channels[old],encoding="utf-8"))
        c.set("id",new)
        if new in kept_ids:
            raise SystemExit("LKG duplicate after canonicalization: "+new)
        kept_ids.add(new); out.append(c)
    kept_programmes=0
    for p in root.findall("programme"):
        old=(p.get("channel") or "").strip()
        if old not in kept_raw: continue
        cp=ET.fromstring(ET.tostring(p,encoding="utf-8"))
        cp.set("channel",winner[old]); out.append(cp); kept_programmes += 1
    ET.indent(out,space="  ")
    xml=ET.tostring(out,encoding="utf-8",xml_declaration=True)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(gzip.compress(xml,compresslevel=9,mtime=0))
    return {
        "input_channels":len(channels),"output_channels":len(kept_ids),
        "programmes":kept_programmes,"aliases_resolved":resolved_count,
        "quarantined_reserved_ids":sorted(quarantine,key=str.casefold),
        "collapsed_aliases":sum(max(0,len(v)-1) for v in grouped.values()),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--lkg",required=True); ap.add_argument("--aliases",required=True)
    ap.add_argument("--output-lkg",required=True); ap.add_argument("--json"); ap.add_argument("--text")
    a=ap.parse_args()
    errors=validate_contract()
    report={"schema":1,"status":"FAIL" if errors else "PASS","errors":errors}
    source=Path(a.lkg)
    if source.exists() and source.stat().st_size:
        stats=canonicalize_lkg(source,Path(a.output_lkg),load_aliases(Path(a.aliases)))
        report["lkg"]=stats
    else:
        Path(a.output_lkg).write_bytes(gzip.compress(b'<?xml version="1.0" encoding="UTF-8"?><tv></tv>',mtime=0))
        report["lkg"]={"input_channels":0,"output_channels":0,"programmes":0,"aliases_resolved":0,"quarantined_reserved_ids":[],"collapsed_aliases":0}
    if a.json: Path(a.json).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=[f"RECEIVER PREFLIGHT: {report['status']}"]
    if errors: lines += ["- "+x for x in errors]
    s=report["lkg"]
    lines.append("LKG canonicalized input=%d output=%d programmes=%d aliases=%d collapsed=%d quarantined=%d" % (
        s["input_channels"],s["output_channels"],s["programmes"],s["aliases_resolved"],s["collapsed_aliases"],len(s["quarantined_reserved_ids"])))
    if s["quarantined_reserved_ids"]:
        lines.append("QUARANTINED_RESERVED_IDS="+",".join(s["quarantined_reserved_ids"]))
    if a.text: Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))
    return 1 if errors else 0

if __name__=="__main__":
    raise SystemExit(main())

# Manual full validation trigger 2026-10-25
