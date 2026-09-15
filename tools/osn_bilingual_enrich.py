#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact bilingual enrichment for frozen OSN + Premium International MENA.

Rules:
- schedule and Arabic descriptions stay untouched;
- English titles come only from the SAME upstream feed and exact channel/start/stop;
- Arabic editorial feeds stay Arabic-first;
- Nat Geo and Disney stay explicit REVIEW-only until exact reliable MENA donors are proven;
- no fuzzy time/title matching, no foreign-feed substitution.
"""
from __future__ import annotations

import argparse, gzip, hashlib, json, re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LEGACY_ALIASES = {
    "OSN Ya Hala.eg": "OSNYahala.ae@SD",
    "Osn Ya Hala Aflam.eg": "OSNYahalaAflam.ae@SD",
}
CANONICAL_OSN_IDS = {
    "OSNComedy.ae@SD","OSNKids.ae@SD","OSNMezze.ae@SD",
    "OSNMoviesAction.ae@SD","OSNMoviesHollywood.ae@SD","OSNMoviesPremiere.ae@SD",
    "OSNShowcase.ae@SD","OSNtv Crime.sa","OSNtv Documentary.sa","OSNtv iQIYI.sa",
    "OSNtv Movies Comedy.sa","OSNtv Movies Family.sa","OSNtv Movies Horror.sa",
    "OSNtv Now.sa","OSNtv One.sa","OSNtv Pop Up.sa","OSNtv Showcase Classics.sa",
    "OSNYahala.ae@SD","OSNYahalaAflam.ae@SD","OSNYahalaBilArabi.ae@SD",
}

# Exact same-feed bilingual MENA services verified from osn.com.
INTERNATIONAL_EN_AR_SITES = {
    "AnimalPlanetEurope.uk@SD":"osn.com",
    "DiscoveryChannelMiddleEastAfrica.us@SD":"osn.com",
    "InvestigationDiscovery.uk@SD":"osn.com",
    "HistoryMiddleEast.us@SD":"osn.com",
    "History2MiddleEast.us@SD":"osn.com",
    "TLCArabia.us@SD":"osn.com",
    "CartoonNetworkMENA.uk@SD":"osn.com",
    "NickelodeonArabia.ae@SD":"osn.com",
    "NickJrArabia.ae@SD":"osn.com",
    "NicktoonsArabia.ae@SD":"osn.com",
}

# Arabic editorial service with a healthy exact MENA guide.
INTERNATIONAL_AR_ONLY_SITES = {
    "CartoonNetworkArabic.ae@SD":"osn.com",
}

# Diagnostic-only rows. These are intentionally NOT declared production-safe.
# Nat Geo was moved here after exhaustive probing proved the available sources
# are either incomplete, empty or a different editorial timeline. Disney stays
# here until an exact MENA source is independently verified.
SOURCE_REVIEW_REASONS = {
    "NationalGeographicMiddleEast.uk@SD":"UPSTREAM_ELCINEMA_INCOMPLETE_48H",
    "NationalGeographicAbuDhabi.ae@SD":"NO_RELIABLE_EXACT_AR_MENA_GUIDE",
    "Disney Channel.sa":"EXACT_MENA_SOURCE_NOT_PROVEN",
    "Disney Junior.sa":"EXACT_MENA_SOURCE_NOT_PROVEN",
}
SOURCE_REVIEW_IDS = set(SOURCE_REVIEW_REASONS)
DONOR_IDS = CANONICAL_OSN_IDS | set(INTERNATIONAL_EN_AR_SITES)
LAT = re.compile(r"[A-Za-z]")
AR = re.compile(r"[\u0600-\u06ff]")


def read_xml(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def write_gz(path, root):
    ET.indent(root, space="  ")
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    Path(path).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def parse_time(value):
    parts = (value or "").strip().split()
    if not parts:
        return None
    stamp = parts[0]
    fmt = "%Y%m%d%H%M%S" if len(stamp) >= 14 else "%Y%m%d%H%M"
    stamp = stamp[:14] if len(stamp) >= 14 else stamp[:12]
    try:
        dt = datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    if len(parts) >= 2 and re.fullmatch(r"[+-]\d{4}", parts[1]):
        sign = 1 if parts[1][0] == "+" else -1
        off = sign * (int(parts[1][1:3])*3600 + int(parts[1][3:5])*60)
        return int(dt.timestamp()) - off
    return int(dt.timestamp())


def slot(p, cid):
    a, b = parse_time(p.get("start")), parse_time(p.get("stop"))
    return None if a is None or b is None else (cid, a, b)


def first(node, tag):
    for el in node.findall(tag):
        txt = (el.text or "").strip()
        if txt:
            return txt, (el.get("lang") or "").lower()
    return "", ""


def has_latin(text, lang=""):
    return lang.startswith(("en","fr")) or bool(LAT.search(text or ""))


def has_ar(text, lang=""):
    return lang.startswith("ar") or len(AR.findall(text or "")) >= 2


def catalog_rows(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r.get("xmltv_id"):r for r in obj.get("channels",[]) if r.get("xmltv_id")}


def build_channels(args):
    rows = catalog_rows(args.catalog_manifest)
    expected = {cid:"osn.com" for cid in CANONICAL_OSN_IDS}
    expected.update(INTERNATIONAL_EN_AR_SITES)
    root = ET.Element("channels")
    bad = []
    for cid in sorted(DONOR_IDS, key=str.casefold):
        row, site = rows.get(cid), expected[cid]
        if not row:
            bad.append(cid + ":MISSING"); continue
        if row.get("site") != site:
            bad.append("%s:SITE=%s expected=%s"%(cid,row.get("site"),site)); continue
        sid = str(row.get("site_id") or "").strip()
        if not sid:
            bad.append(cid + ":NO_SITE_ID"); continue
        ch = ET.SubElement(root,"channel",{
            "site":site,"site_id":sid,"lang":"en","xmltv_id":cid})
        ch.text = str(row.get("name") or cid)
    if bad or len(root.findall("channel")) != len(DONOR_IDS):
        raise SystemExit("English donor identity guard failed: " + "; ".join(bad))
    ET.indent(root, space="  ")
    Path(args.output).write_bytes(ET.tostring(root,encoding="utf-8",xml_declaration=True))
    print("English donor catalogue: osn=%d intl=%d total=%d" %
          (len(CANONICAL_OSN_IDS),len(INTERNATIONAL_EN_AR_SITES),len(DONOR_IDS)))
    return 0


def donor_titles(root):
    titles, events, english = {}, Counter(), Counter()
    conflicts = 0
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        if cid not in DONOR_IDS: continue
        events[cid] += 1
        key = slot(p,cid)
        title = first(p,"title")[0]
        if key is None or not title or not LAT.search(title): continue
        english[cid] += 1
        if key in titles and titles[key] != title:
            conflicts += 1
        else:
            titles[key] = title
    if conflicts:
        raise SystemExit("English donor conflicting slots=%d"%conflicts)
    return titles, events, english


def replace_titles(root, allowed, titles, aliases=None):
    aliases = aliases or {}
    stats, total, samples = defaultdict(Counter), Counter(), []
    for p in root.findall("programme"):
        cid = (p.get("channel") or "").strip()
        canonical = aliases.get(cid,cid)
        if canonical not in allowed: continue
        stats[cid]["events"] += 1; total["events"] += 1
        key = slot(p,canonical)
        new = titles.get(key) if key else None
        if not new:
            stats[cid]["unmatched"] += 1; total["unmatched"] += 1; continue
        stats[cid]["matched"] += 1; total["matched"] += 1
        n = p.find("title")
        if n is None: n = ET.SubElement(p,"title")
        old = (n.text or "").strip()
        if old != new or (n.get("lang") or "").lower() != "en":
            n.text, n.attrib["lang"] = new, "en"
            stats[cid]["replaced"] += 1; total["replaced"] += 1
            if len(samples) < 30 and old != new:
                samples.append({"id":cid,"old":old,"new":new})
    return stats,total,samples


def stat_rows(stats, aliases=None):
    aliases = aliases or {}
    out=[]
    for cid in sorted(stats,key=str.casefold):
        s=stats[cid]; n=max(1,s["events"])
        out.append({"id":cid,"canonical":aliases.get(cid,cid),"events":s["events"],
                    "matched":s["matched"],"matched_pct":round(100*s["matched"]/n,1),
                    "replaced":s["replaced"],"unmatched":s["unmatched"]})
    return out


def load_shards(out_dir):
    mpath=Path(out_dir)/"shards.json"
    manifest=json.loads(mpath.read_text(encoding="utf-8"))
    roots={}
    for stem in manifest.get("shards",{}):
        p=Path(out_dir)/(stem+".xml.gz")
        if p.exists(): roots[stem]=read_xml(p)
    return manifest,roots


def refresh_manifest(out_dir,manifest,touched):
    for stem in touched:
        p=Path(out_dir)/(stem+".xml.gz"); data=p.read_bytes()
        manifest["shards"][stem]["size_bytes"]=len(data)
        manifest["shards"][stem]["sha256"]=hashlib.sha256(data).hexdigest()
    (Path(out_dir)/"shards.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def profile(cid,name,programmes):
    valid=[]; invalid=overlaps=empty_t=empty_d=tlat=tar=dar=0
    for p in programmes:
        title,tlang=first(p,"title"); desc,dlang=first(p,"desc")
        a,b=parse_time(p.get("start")),parse_time(p.get("stop"))
        if not title: empty_t+=1
        else:
            tlat+=int(has_latin(title,tlang)); tar+=int(has_ar(title,tlang))
        if not desc: empty_d+=1
        else: dar+=int(has_ar(desc,dlang))
        if a is None or b is None or b<=a: invalid+=1
        else: valid.append((a,b))
    valid.sort(); active=None
    for a,b in valid:
        if active is not None and a<active: overlaps+=1
        active=b if active is None else max(active,b)
    coverage=span=gap_total=0.; gaps=0
    if valid:
        cs,ce=valid[0]; first_s=cs; last_e=ce
        for a,b in valid[1:]:
            if a<=ce: ce=max(ce,b)
            else:
                coverage+=(ce-cs)/3600.; gap=(a-ce)/3600.; gap_total+=gap
                gaps+=int(gap>2.); cs,ce=a,b
            last_e=max(last_e,b)
        coverage+=(ce-cs)/3600.; span=(last_e-first_s)/3600.
    n=len(programmes); nd=max(1,n-empty_d)
    return {"id":cid,"name":name,"events":n,"coverage_hours":round(coverage,2),
            "span_hours":round(span,2),"gaps_gt_2h":gaps,"gap_hours":round(gap_total,2),
            "invalid":invalid,"overlaps":overlaps,"empty_title":empty_t,"empty_desc":empty_d,
            "title_latin_pct":round(100*tlat/max(1,n),1),
            "title_ar_pct":round(100*tar/max(1,n),1),
            "desc_ar_pct":round(100*dar/nd,1) if n else 0.}


def audit_international(out_dir,roots,enrich_rows):
    targets=set(INTERNATIONAL_EN_AR_SITES)|set(INTERNATIONAL_AR_ONLY_SITES)|SOURCE_REVIEW_IDS
    channels={}; events=defaultdict(list); shard={}
    for stem,root in roots.items():
        for c in root.findall("channel"):
            cid=(c.get("id") or "").strip()
            if cid in targets: channels[cid]=c; shard[cid]=stem
        for p in root.findall("programme"):
            cid=(p.get("channel") or "").strip()
            if cid in targets: events[cid].append(p)
    match={r["id"]:r for r in enrich_rows}; rows=[]; counts=Counter()
    strict=set(INTERNATIONAL_EN_AR_SITES)|set(INTERNATIONAL_AR_ONLY_SITES)
    for cid in sorted(targets,key=str.casefold):
        c=channels.get(cid); name=cid
        if c is not None:
            name=next(((n.text or "").strip() for n in c.findall("display-name")
                       if (n.text or "").strip()),cid)
        r=profile(cid,name,events.get(cid,[])); r["shard"]=shard.get(cid,"")
        r["donor_match_pct"]=float((match.get(cid) or {}).get("matched_pct",0) or 0)
        issues=[]; warn=[]
        if not r["events"]: issues.append("NO_EPG")
        if r["invalid"]: issues.append("INVALID=%d"%r["invalid"])
        if r["overlaps"]: issues.append("OVERLAPS=%d"%r["overlaps"])
        if r["empty_title"]: issues.append("EMPTY_TITLE=%d"%r["empty_title"])
        if cid in INTERNATIONAL_EN_AR_SITES:
            r["policy"]="EN_TITLE_AR_DESC"
            if r["coverage_hours"]<24: warn.append("LOW_COVERAGE=%.1fh"%r["coverage_hours"])
            if r["gaps_gt_2h"]: warn.append("GAPS_GT_2H=%d"%r["gaps_gt_2h"])
            if r["title_latin_pct"]<90: warn.append("EN_TITLE_LOW=%.0f%%"%r["title_latin_pct"])
            if r["desc_ar_pct"]<95: warn.append("AR_DESC_LOW=%.0f%%"%r["desc_ar_pct"])
            if r["donor_match_pct"]<90: warn.append("DONOR_MATCH_LOW=%.0f%%"%r["donor_match_pct"])
        elif cid in INTERNATIONAL_AR_ONLY_SITES:
            r["policy"]="AR_TITLE_AR_DESC"
            if r["coverage_hours"]<24: warn.append("LOW_COVERAGE=%.1fh"%r["coverage_hours"])
            if r["gaps_gt_2h"]: warn.append("GAPS_GT_2H=%d"%r["gaps_gt_2h"])
            if r["title_ar_pct"]<80: warn.append("AR_TITLE_LOW=%.0f%%"%r["title_ar_pct"])
            if r["desc_ar_pct"]<95: warn.append("AR_DESC_LOW=%.0f%%"%r["desc_ar_pct"])
        else:
            r["policy"]="SOURCE_REVIEW_ONLY"
            warn.append(SOURCE_REVIEW_REASONS[cid])
        if cid in SOURCE_REVIEW_IDS:
            verdict="REVIEW"
        elif issues:
            verdict="FAIL"
        elif warn:
            verdict="REVIEW"
        else:
            verdict="PASS"
        r.update({"verdict":verdict,"issues":issues,"warnings":warn}); rows.append(r); counts[verdict]+=1

    # REVIEW is a quality signal, not a publication outage. A transient short
    # window/gap or metadata warning on one strict Premium International service
    # must not suppress hundreds of otherwise valid MENA channels. Only hard
    # structural FAIL rows block production. REVIEW rows remain fully visible in
    # reports so they can be repaired without taking the receiver feed offline.
    strict_fail=[r["id"] for r in rows if r["id"] in strict and r["verdict"]=="FAIL"]
    strict_review=[r["id"] for r in rows if r["id"] in strict and r["verdict"]=="REVIEW"]
    payload={"schema":3,"strict_expected_ids":sorted(strict,key=str.casefold),
             "source_review_ids":sorted(SOURCE_REVIEW_IDS,key=str.casefold),
             "source_review_reasons":SOURCE_REVIEW_REASONS,
             "summary":{"channels":len(rows),"strict_channels":len(strict),
                        "source_review_channels":len(SOURCE_REVIEW_IDS),
                        "counts":dict(counts),"strict_bad":strict_fail,
                        "strict_review":strict_review},
             "channels":rows}
    out=Path(out_dir)
    (out/"premium-international-audit.json").write_text(
        json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["PREMIUM INTERNATIONAL MENA - EXACT FEED AUDIT",
           "channels=%d strict=%d source_review=%d PASS=%d REVIEW=%d FAIL=%d strict_fail=%d strict_review=%d"%
           (len(rows),len(strict),len(SOURCE_REVIEW_IDS),counts["PASS"],counts["REVIEW"],
            counts["FAIL"],len(strict_fail),len(strict_review)),
           "policy=real MENA feed only; exact EN-title+AR-desc or Arabic-first; REVIEW stays diagnostic and only structural FAIL blocks publication",""]
    for r in rows:
        lines += ["[%s] %s | %s | shard=%s"%(r["verdict"],r["id"],r["policy"],r["shard"] or "MISSING"),
                  "  events=%d coverage=%.1fh gaps>2h=%d overlaps=%d invalid=%d titleLatin=%.0f%% titleAR=%.0f%% descAR=%.0f%% donorMatch=%.0f%%"%
                  (r["events"],r["coverage_hours"],r["gaps_gt_2h"],r["overlaps"],r["invalid"],
                   r["title_latin_pct"],r["title_ar_pct"],r["desc_ar_pct"],r["donor_match_pct"]),
                  "  notes=%s"%(", ".join(r["issues"]+r["warnings"]) if r["issues"]+r["warnings"] else "NONE")]
    (out/"premium-international-audit.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    status="FAIL" if strict_fail else "PASS"
    gate=["PREMIUM INTERNATIONAL MENA FINAL REGRESSION GATE: "+status,
          "strict_expected=%d source_review=%d"%(len(strict),len(SOURCE_REVIEW_IDS)),
          "- strict_fail=%s"%(",".join(strict_fail) or "NONE"),
          "- strict_review=%s"%(",".join(strict_review) or "NONE"),
          "- source_review_not_frozen=%s"%(",".join(sorted(SOURCE_REVIEW_IDS,key=str.casefold))),
          "- REVIEW rows remain diagnostic; only hard structural FAIL blocks publication"]
    (out/"premium-international-final-regression.txt").write_text("\n".join(gate)+"\n",encoding="utf-8")
    return payload,status


def enrich(args):
    target=Path(args.xml); out_dir=target.parent
    root=read_xml(target); donor=read_xml(args.donor)
    titles,devents,denglish=donor_titles(donor)
    present={(c.get("id") or "").strip() for c in root.findall("channel")}
    missing=sorted(CANONICAL_OSN_IDS-present,key=str.casefold)
    if missing: raise SystemExit("provider-osn missing canonical IDs: "+", ".join(missing))
    os,ot,osamples=replace_titles(root,CANONICAL_OSN_IDS,titles,LEGACY_ALIASES)
    write_gz(target,root); orows=stat_rows(os,LEGACY_ALIASES)
    op={"schema":2,"canonical_ids":len(CANONICAL_OSN_IDS),"events":ot["events"],
        "matched":ot["matched"],"replaced":ot["replaced"],"unmatched":ot["unmatched"],
        "match_pct":round(100*ot["matched"]/max(1,ot["events"]),1),"channels":orows,"samples":osamples}
    Path(args.report_json).write_text(json.dumps(op,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ol=["OSN OFFICIAL BILINGUAL ENRICHMENT",
        "canonical_ids=%d events=%d exact_matches=%d replaced=%d unmatched=%d match=%.1f%%"%
        (op["canonical_ids"],op["events"],op["matched"],op["replaced"],op["unmatched"],op["match_pct"])]
    Path(args.report_text).write_text("\n".join(ol)+"\n",encoding="utf-8")

    manifest,roots=load_shards(out_dir); istats=defaultdict(Counter); itotal=Counter(); touched=set()
    for stem,sroot in roots.items():
        before=ET.tostring(sroot,encoding="utf-8")
        s,t,_=replace_titles(sroot,set(INTERNATIONAL_EN_AR_SITES),titles)
        for cid,ctr in s.items(): istats[cid].update(ctr)
        itotal.update(t)
        if before!=ET.tostring(sroot,encoding="utf-8"):
            write_gz(out_dir/(stem+".xml.gz"),sroot); touched.add(stem)
    if touched: refresh_manifest(out_dir,manifest,touched)
    irows=stat_rows(istats)
    il=["PREMIUM INTERNATIONAL MENA - EXACT BILINGUAL ENRICHMENT",
        "strict_en_ar=%d strict_ar_only=%d source_review=%d events=%d exact_matches=%d replaced=%d unmatched=%d match=%.1f%% touched=%s"%
        (len(INTERNATIONAL_EN_AR_SITES),len(INTERNATIONAL_AR_ONLY_SITES),len(SOURCE_REVIEW_IDS),
         itotal["events"],itotal["matched"],itotal["replaced"],itotal["unmatched"],
         round(100*itotal["matched"]/max(1,itotal["events"]),1),",".join(sorted(touched)) or "NONE")]
    for r in irows:
        il.append("%s events=%d matched=%d (%.1f%%) replaced=%d unmatched=%d"%
                  (r["id"],r["events"],r["matched"],r["matched_pct"],r["replaced"],r["unmatched"]))
    (out_dir/"premium-international-enrichment.txt").write_text("\n".join(il)+"\n",encoding="utf-8")
    _,roots2=load_shards(out_dir); audit,status=audit_international(out_dir,roots2,irows)
    print(ol[-1]); print(il[1])
    print("Premium International: status=%s PASS=%d REVIEW=%d FAIL=%d strict_fail=%d strict_review=%d"%
          (status,audit["summary"]["counts"].get("PASS",0),audit["summary"]["counts"].get("REVIEW",0),
           audit["summary"]["counts"].get("FAIL",0),len(audit["summary"]["strict_bad"]),
           len(audit["summary"].get("strict_review",[]))))
    if status!="PASS":
        raise SystemExit("Premium International MENA gate failed; publication blocked")
    return 0


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build-channels"); b.add_argument("--catalog-manifest",required=True); b.add_argument("--output",required=True); b.set_defaults(func=build_channels)
    e=sub.add_parser("enrich"); e.add_argument("--xml",required=True); e.add_argument("--donor",required=True); e.add_argument("--report-json",required=True); e.add_argument("--report-text",required=True); e.set_defaults(func=enrich)
    args=ap.parse_args(); return args.func(args)

if __name__=="__main__":
    raise SystemExit(main())