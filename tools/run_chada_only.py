#!/usr/bin/env python3
import argparse,gzip,hashlib,json
from pathlib import Path
from morocco_epg import scrape_chada,to_xml

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--days",type=int,default=2);ap.add_argument("--output-dir",default="output/chada-only")
 a=ap.parse_args();out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
 rows=scrape_chada(a.days)
 if len(rows)<6: raise SystemExit("Chada validation failed: %d events"%len(rows))
 xml=out/"chada.xml";counts=to_xml(rows,xml);raw=xml.read_bytes();gz=out/"chada.xml.gz"
 with gzip.GzipFile(filename="chada.xml",mode="wb",fileobj=gz.open("wb"),compresslevel=9,mtime=0) as f:f.write(raw)
 meta={"channels":len(counts),"programmes":sum(counts.values()),"channel_counts":counts,"size":gz.stat().st_size,"sha256":hashlib.sha256(gz.read_bytes()).hexdigest()}
 (out/"chada.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(meta,ensure_ascii=False))
if __name__=="__main__":main()
