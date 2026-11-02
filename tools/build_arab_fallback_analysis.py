#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build Arabic fallback-analysis packs from EPGShare + OpenEPG.

These outputs are for analysis only. They never replace healthy direct feeds.
Morocco is intentionally excluded by config.
"""
from __future__ import annotations
import csv, gzip, io, json, re, hashlib
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

CFG=Path("config/arab-fallback-sources.json")
OUT=Path("analysis/arab-fallback")
REPORT=Path("reports")
AE1_AUDIT_REPORT=REPORT/"ae1-source-audit.json"
UA="Mozilla/5.0 EPGManager-ArabFallback/1.0"

# Explicit non-MENA sources that must never enter Arab fallback analysis.
EXCLUDED_SOURCES={("epgshare","AR1"): "ARGENTINA_LATAM_NOT_ARAB"}

def parse_dt(raw):
    raw=(raw or "").strip()
    if len(raw)<14: return None
    try:
        dt=datetime.strptime(raw[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def read_xml_bytes(data):
    if data[:2]==b"\x1f\x8b":
        data=gzip.decompress(data)
    return ET.fromstring(data)

def fetch(sess,url):
    r=sess.get(url,timeout=40,allow_redirects=True)
    r.raise_for_status()
    return r.content, r

def source_metrics(root):
    now=datetime.now(timezone.utc)
    channels={c.get("id",""):c for c in root.findall("channel") if c.get("id")}
    progs=defaultdict(list)
    invalid=0
    for p in root.findall("programme"):
        cid=p.get("channel","")
        s=parse_dt(p.get("start")); e=parse_dt(p.get("stop"))
        if not s or not e or not s<e:
            invalid+=1
            continue
        if e>now:
            progs[cid].append((p,s,e))
    rows=[]
    for cid,ch in channels.items():
        arr=progs.get(cid,[])
        names=[(x.text or "").strip() for x in ch.findall("display-name") if (x.text or "").strip()]
        descs=0
        for p,_,_ in arr:
            if any((d.text or "").strip() for d in p.findall("desc")):
                descs+=1
        latest=max((e for _,_,e in arr),default=now)
        sample_title=""
        sample_desc=""
        schedule_signature=""
        if arr:
            # Earliest future event is the most useful human-review sample.
            sample_p=min(arr,key=lambda item:item[1])[0]
            sample_title=" | ".join((t.text or "").strip() for t in sample_p.findall("title") if (t.text or "").strip())
            sample_desc=" | ".join((d.text or "").strip() for d in sample_p.findall("desc") if (d.text or "").strip())
            # Fingerprint the first future events so cloned schedules assigned to
            # many unrelated channel IDs can be detected reliably.
            parts=[]
            for p,s,e in sorted(arr,key=lambda item:item[1])[:8]:
                title=" | ".join((t.text or "").strip() for t in p.findall("title") if (t.text or "").strip())
                parts.append(f"{s.isoformat()}|{e.isoformat()}|{title}")
            schedule_signature=hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest() if parts else ""
        rows.append({
            "id":cid,
            "name":names[0] if names else cid,
            "future_programmes":len(arr),
            "future_hours":round(max(0,(latest-now).total_seconds()/3600),1),
            "desc_pct":round(100*descs/len(arr),1) if arr else 0.0,
            "sample_title":sample_title,
            "sample_desc":sample_desc,
            "schedule_signature":schedule_signature,
            "suspicious_clone":False,
            "clone_group_size":1,
        })
    return rows, invalid

def existing_covered():
    now=datetime.now(timezone.utc)
    covered=set()
    for p in Path("feeds").glob("*.xml.gz"):
        try:
            root=read_xml_bytes(p.read_bytes())
        except Exception:
            continue
        future=defaultdict(int)
        for pr in root.findall("programme"):
            e=parse_dt(pr.get("stop"))
            if e and e>now:
                future[pr.get("channel","")]+=1
        for ch in root.findall("channel"):
            cid=ch.get("id","")
            if cid and future.get(cid,0)>0:
                covered.add(cid)
    return covered

def merge_roots(items):
    tv=ET.Element("tv",{"generator-info-name":"EPGManager Arab fallback analysis"})
    seen_ch=set()
    seen_prog=set()
    for source,root in items:
        for ch in root.findall("channel"):
            cid=ch.get("id","")
            if not cid or cid in seen_ch: continue
            seen_ch.add(cid)
            tv.append(ch)
        for p in root.findall("programme"):
            cid=p.get("channel","")
            key=(cid,p.get("start",""),p.get("stop",""),
                 " ".join((t.text or "").strip() for t in p.findall("title")))
            if not cid or key in seen_prog: continue
            seen_prog.add(key)
            tv.append(p)
    ET.indent(tv,space="  ")
    return ET.tostring(tv,encoding="utf-8",xml_declaration=True)

def build_ae1_audit(rows):
    ae=[r for r in rows if r.get("provider")=="epgshare" and r.get("source")=="AE1"]
    active=[r for r in ae if int(r.get("future_programmes") or 0)>0]
    cloned=[r for r in active if r.get("suspicious_clone")]
    bad_title_re=re.compile(r"(?:tv\s*guide\s*is\s*not\s*available|edge\s*of\s*the\s*unknown\s*with\s*jimmy\s*chin|no\s*scheduled\s*events)",re.I)
    placeholder=[r for r in active if bad_title_re.search((r.get("sample_title") or "")+" "+(r.get("sample_desc") or ""))]
    title_counts=Counter((r.get("sample_title") or "").strip() for r in active if (r.get("sample_title") or "").strip())
    repeated_titles={k:v for k,v in title_counts.items() if v>=5}
    trustworthy=[
        r for r in active
        if not r.get("suspicious_clone")
        and not bad_title_re.search((r.get("sample_title") or "")+" "+(r.get("sample_desc") or ""))
    ]
    return {
        "source":"epgshare:AE1",
        "policy":"QUARANTINE_AUTOMATIC_MAPPING",
        "reason":"high_channel_programme_mismatch_risk",
        "channels_total":len(ae),
        "active_channels_raw":len(active),
        "suspicious_clone_channels":len(cloned),
        "placeholder_or_known_bad_sample_channels":len(placeholder),
        "remaining_nonclone_nonplaceholder_channels":len(trustworthy),
        "clone_pct_of_active":round(100*len(cloned)/len(active),1) if active else 0.0,
        "bad_sample_pct_of_active":round(100*len(placeholder)/len(active),1) if active else 0.0,
        "top_repeated_sample_titles":sorted(
            [{"title":k,"channels":v} for k,v in repeated_titles.items()],
            key=lambda x:(-x["channels"],x["title"])
        )[:30],
        "integration":"manual_whitelist_only_until_source_mapping_is_reliable"
    }

def main():
    cfg=json.loads(CFG.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True,exist_ok=True)
    REPORT.mkdir(parents=True,exist_ok=True)
    sess=requests.Session()
    sess.headers.update({"User-Agent":UA,"Accept":"application/xml,text/xml,*/*;q=0.8"})
    health=[]
    all_rows=[]
    merged={}
    for provider in ("epgshare","openepg"):
        roots=[]
        for spec in cfg[provider]:
            row={"provider":provider,"source":spec["name"],"url":spec["url"]}
            excluded_reason=EXCLUDED_SOURCES.get((provider,spec["name"]))
            if excluded_reason:
                row.update({"status":"excluded","reason":excluded_reason})
                health.append(row)
                continue
            try:
                data,r=fetch(sess,spec["url"])
                root=read_xml_bytes(data)
                metrics,invalid=source_metrics(root)

                # Quarantine exact schedule clones reused across many channel IDs.
                # A threshold of 8 avoids penalizing normal simulcasts while
                # catching source-wide mapping/filler corruption such as AE1.
                sig_groups=defaultdict(list)
                for x in metrics:
                    if x["future_programmes"]>0 and x.get("schedule_signature"):
                        sig_groups[x["schedule_signature"]].append(x)
                cloned_channels=0
                clone_groups=0
                for arr in sig_groups.values():
                    if len(arr)>=8:
                        clone_groups+=1
                        cloned_channels+=len(arr)
                        for x in arr:
                            x["suspicious_clone"]=True
                            x["clone_group_size"]=len(arr)

                active=sum(1 for x in metrics if x["future_programmes"]>0 and not x.get("suspicious_clone"))
                programmes=sum(x["future_programmes"] for x in metrics)
                row.update({
                    "status":"ok","http":r.status_code,"bytes":len(data),
                    "channels":len(metrics),"active_channels":active,
                    "future_programmes":programmes,"invalid_times":invalid,
                    "suspicious_clone_groups":clone_groups,
                    "suspicious_clone_channels":cloned_channels,
                    "sha256":hashlib.sha256(data).hexdigest(),
                })
                for x in metrics:
                    all_rows.append({**x,"provider":provider,"source":spec["name"],"url":spec["url"]})
                roots.append((spec["name"],root))
            except Exception as e:
                row.update({"status":"error","error":str(e)[:300]})
            health.append(row)
        if roots:
            xml=merge_roots(roots)
            gz=gzip.compress(xml,compresslevel=9)
            out=OUT/f"{provider}.xml.gz"
            out.write_bytes(gz)
            merged[provider]={"file":str(out),"bytes":len(gz),"sources_ok":len(roots)}

    covered=existing_covered()
    rescue=[r for r in all_rows if r["future_programmes"]>0 and not r.get("suspicious_clone") and r["id"] not in covered]
    rescue.sort(key=lambda r:(-r["future_programmes"],-r["future_hours"],-r["desc_pct"],r["name"].casefold()))

    # Deduplicate rescue candidates by provider/source/id; keep all alternatives for analysis.
    fields=["provider","source","id","name","future_programmes","future_hours","desc_pct","sample_title","sample_desc","suspicious_clone","clone_group_size","url"]
    with (REPORT/"arab-fallback-candidates.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in rescue: w.writerow({k:r[k] for k in fields})
    with (REPORT/"arab-fallback-all-channels.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in all_rows: w.writerow({k:r[k] for k in fields})

    ae1_audit=build_ae1_audit(all_rows)
    AE1_AUDIT_REPORT.write_text(json.dumps(ae1_audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    summary={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "purpose":"analysis_only_zero_epg_rescue",
        "morocco_excluded":True,
        "production_ids_with_future_epg":len(covered),
        "fallback_rows_total":len(all_rows),
        "rescue_rows_not_currently_covered":len(rescue),
        "health":health,
        "merged":merged,
    }
    (REPORT/"arab-fallback-health.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# Arab fallback analysis","",
           f"Production IDs with future EPG: **{len(covered)}**  ",
           f"Fallback channel rows: **{len(all_rows)}**  ",
           f"Candidate rows not currently covered: **{len(rescue)}**","",
           "| Provider | Source | Status | Channels | Active | Future programmes | Invalid times |",
           "|---|---|---|---:|---:|---:|---:|"]
    for h in health:
        lines.append(f'| {h["provider"]} | {h["source"]} | {h["status"]} | {h.get("channels",0)} | {h.get("active_channels",0)} | {h.get("future_programmes",0)} | {h.get("invalid_times",0)} |')
    (REPORT/"arab-fallback-health.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
    print("RESCUE_CANDIDATES",len(rescue))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
