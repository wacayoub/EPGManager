#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, json, hashlib
from pathlib import Path
from datetime import datetime
from morocco_epg import scrape_2m, infer, to_xml, TZ

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=1)
    ap.add_argument("--output-dir",default="output/2m-only")
    a=ap.parse_args()
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    rows=scrape_2m(a.days)
    if len(rows)<6:
        raise SystemExit(f"2M validation failed: only {len(rows)} events")
    infer(rows)
    xml=out/"2m.xml"
    counts=to_xml(rows,xml)
    raw=xml.read_bytes()
    gz=out/"2m.xml.gz"
    with gzip.GzipFile(filename="2m.xml",mode="wb",fileobj=gz.open("wb"),compresslevel=9,mtime=0) as f:
        f.write(raw)
    report={
        "generated":datetime.now(TZ).isoformat(),
        "days":a.days,
        "channels":len(counts),
        "programmes":sum(counts.values()),
        "channel_counts":counts,
        "size":gz.stat().st_size,
        "sha256":hashlib.sha256(gz.read_bytes()).hexdigest(),
    }
    (out/"2m.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
