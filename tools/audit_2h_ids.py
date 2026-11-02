#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import gzip, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

AR = re.compile(r"[\u0600-\u06ff]")
LAT = re.compile(r"[A-Za-z]")

CASES = {
    "morocco": ("output/morocco/morocco.xml.gz", None, None),
    "bein": ("output/final/bein.hybrid.xml.gz", "output/source-build/bein.channels.xml", "config/bein-language-policy.json"),
    "elcinema": ("output/source-build/elcinema.raw.xml", "output/source-build/elcinema.channels.xml", "config/elcinema-language-policy.json"),
    "osn": ("output/source-build/osn.raw.xml", "output/source-build/osn.channels.xml", "config/osn-language-policy.json"),
    "shahid": ("output/source-build/shahid.raw.xml", "output/source-build/shahid.channels.xml", None),
    "rotana": ("output/source-build/rotana.raw.xml", None, None),
    "sport24": ("output/source-build/sport24.raw.xml", None, None),
    "starzplay": ("output/source-build/starzplay.raw.xml", None, "config/starzplay-language-policy.json"),
    "dubaiplus": ("output/source-build/dubaiplus.raw.xml", None, None),
}
DUBAI_HYBRID = {
    "DubaiOne.ae@SD","DubaiSports1.ae@SD","DubaiSports2.ae@SD",
    "DubaiRacing1.ae@SD","DubaiRacing2.ae@SD",
}

def read_root(path: str):
    p=Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    b=p.read_bytes()
    if p.suffix==".gz" or b[:2]==b"\x1f\x8b":
        b=gzip.decompress(b)
    try:
        return ET.fromstring(b)
    except Exception:
        return None

def parse_dt(s: str):
    s=(s or "").strip()
    if not s: return None
    m=re.match(r"^(\d{14})(?:\s*([+-]\d{4}))?", s)
    if not m: return None
    dt=datetime.strptime(m.group(1),"%Y%m%d%H%M%S")
    off=m.group(2)
    if off:
        sign=1 if off[0]=="+" else -1
        mins=sign*(int(off[1:3])*60+int(off[3:5]))
        dt=dt.replace(tzinfo=timezone(timedelta(minutes=mins))).astimezone(timezone.utc)
    else:
        dt=dt.replace(tzinfo=timezone.utc)
    return dt

def catalogue_ids(path: str|None):
    if not path: return set()
    root=read_root(path)
    if root is None: return set()
    out=set()
    for c in root.iter("channel"):
        cid=(c.get("xmltv_id") or c.get("id") or "").strip()
        if cid: out.add(cid)
    return out

def txt(node, tag):
    x=node.find(tag)
    return (x.text or "").strip() if x is not None else ""

def classify_policy(policy_path: str|None, source: str):
    h=set(); a=set()
    if policy_path and Path(policy_path).exists():
        try:
            d=json.loads(Path(policy_path).read_text(encoding="utf-8"))
            h=set(map(str,d.get("hybrid_ids",[])))
            a=set(map(str,d.get("arabic_native_ids",[])))
        except Exception:
            pass
    if source=="dubaiplus":
        h |= DUBAI_HYBRID
    return h,a

def audit_source(source, xml_path, cat_path, policy_path, now, end):
    root=read_root(xml_path)
    if root is None:
        return {"source":source,"status":"NO_XML","xml":xml_path,"expected_ids":0,"window_ids":0,"programmes_2h":0,"zero_ids":[]}
    feed_ids={(c.get("id") or "").strip() for c in root.findall("channel") if (c.get("id") or "").strip()}
    expected=catalogue_ids(cat_path) or set(feed_ids)
    by={cid:[] for cid in expected|feed_ids}
    for p in root.findall("programme"):
        cid=(p.get("channel") or "").strip()
        st=parse_dt(p.get("start") or "")
        sp=parse_dt(p.get("stop") or "")
        if not cid or st is None: continue
        if sp is None: sp=st+timedelta(minutes=60)
        if st < end and sp > now:
            by.setdefault(cid,[]).append(p)
    window_ids={cid for cid, rows in by.items() if rows}
    zero=sorted(expected-window_ids)
    hybrid_ids, arabic_ids=classify_policy(policy_path, source)
    h_rows=h_en=h_ar_desc=h_bad_desc=0
    a_rows=a_ar_title=a_ar_desc=0
    samples={}
    for cid,rows in by.items():
        for p in rows:
            title=txt(p,"title"); desc=txt(p,"desc")
            if cid in hybrid_ids:
                h_rows+=1
                if LAT.search(title) and not AR.search(title): h_en+=1
                if desc:
                    if AR.search(desc): h_ar_desc+=1
                    else: h_bad_desc+=1
            if cid in arabic_ids:
                a_rows+=1
                if AR.search(title): a_ar_title+=1
                if desc and AR.search(desc): a_ar_desc+=1
            if cid not in samples:
                samples[cid]={"title":title[:120],"desc":desc[:180]}
    hybrid_status="N/A"
    if hybrid_ids:
        if h_rows==0: hybrid_status="NO_2H_EVENTS"
        else:
            title_rate=h_en/h_rows
            hybrid_status="PASS" if title_rate>=0.70 and h_bad_desc==0 else "WARN"
    status="PASS" if expected and not zero else ("WARN" if expected else "NO_IDS")
    return {
        "source":source,"status":status,"xml":xml_path,
        "expected_ids":len(expected),"feed_ids":len(feed_ids),"window_ids":len(window_ids),
        "programmes_2h":sum(len(v) for v in by.values()),
        "coverage_pct":round(100*len(window_ids)/len(expected),1) if expected else 0.0,
        "zero_ids":zero,
        "hybrid":{
            "status":hybrid_status,"declared_ids":len(hybrid_ids),"rows_2h":h_rows,
            "english_title_rows":h_en,"arabic_desc_rows":h_ar_desc,"non_arabic_desc_rows":h_bad_desc,
            "arabic_native_rows":a_rows,"arabic_native_title_rows":a_ar_title,"arabic_native_desc_rows":a_ar_desc,
        },
        "samples":samples,
    }

def probe_summary(path):
    p=Path(path)
    if not p.exists(): return {"status":"NO_REPORT"}
    try:
        d=json.loads(p.read_text(encoding="utf-8"))
        eps=d.get("candidate_endpoints",[])
        ok=[x for x in eps if x.get("status")==200]
        return {"status":"PROBE_ONLY","pages":len(d.get("pages",[])),"js_assets":len(d.get("js_assets",[])),"reachable_candidates":len(ok)}
    except Exception as e:
        return {"status":"REPORT_ERROR","error":str(e)}

def main():
    hours=2
    now=datetime.now(timezone.utc)
    end=now+timedelta(hours=hours)
    out={"generated_utc":now.isoformat(),"window_end_utc":end.isoformat(),"hours":hours,"sources":{},"probes":{}}
    for source,(xml,cat,policy) in CASES.items():
        out["sources"][source]=audit_source(source,xml,cat,policy,now,end)
    out["probes"]["stctv"]=probe_summary("reports/stctv-discovery.json")
    out["probes"]["gobx"]=probe_summary("reports/gobx-discovery.json")
    Path("reports").mkdir(exist_ok=True)
    Path("reports/all-sources-2h-audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=[
        "# All sources — 2h per-ID audit","",
        f"Window: {out['generated_utc']} -> {out['window_end_utc']}","",
        "| Source | IDs | IDs with EPG in 2h | Coverage | Programmes | Hybrid |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for s,r in out["sources"].items():
        lines.append(f"| {s} | {r.get('expected_ids',0)} | {r.get('window_ids',0)} | {r.get('coverage_pct',0)}% | {r.get('programmes_2h',0)} | {r.get('hybrid',{}).get('status','N/A')} |")
    lines += ["","## Zero-EPG IDs in the 2h window",""]
    for s,r in out["sources"].items():
        z=r.get("zero_ids",[])
        lines.append(f"- **{s}**: {len(z)}" + ((" — "+", ".join(z[:80])) if z else ""))
    lines += ["","## Probe-only new sources","",
              f"- **STC TV**: {out['probes']['stctv']}",
              f"- **GoBX**: {out['probes']['gobx']}"]
    Path("reports/all-sources-2h-audit.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({s:{k:v for k,v in r.items() if k not in ('samples','zero_ids')} for s,r in out["sources"].items()},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
