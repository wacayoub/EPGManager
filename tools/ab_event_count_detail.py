#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List same-verdict channels whose event counts differ between published A/B reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def key(row):
    return (row.get("shard") or "", row.get("id") or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    a = ap.parse_args()

    before=json.loads(Path(a.before).read_text(encoding="utf-8"))
    after=json.loads(Path(a.after).read_text(encoding="utf-8"))
    b={key(x):x for x in before.get("channels",[])}
    n={key(x):x for x in after.get("channels",[])}
    rows=[]
    for k in sorted(set(b)&set(n), key=lambda x:(x[0],x[1].casefold())):
        old,new=b[k],n[k]
        if old.get("verdict") != new.get("verdict"):
            continue
        if old.get("events",0) == new.get("events",0):
            continue
        rows.append({
            "shard":k[0],"id":k[1],"name":new.get("name") or old.get("name") or "",
            "verdict":new.get("verdict"),
            "before_events":old.get("events",0),"after_events":new.get("events",0),
            "before_score":old.get("score"),"after_score":new.get("score"),
            "before_coverage_h":old.get("coverage_hours"),"after_coverage_h":new.get("coverage_hours"),
            "before_warnings":old.get("warnings",[]),"after_warnings":new.get("warnings",[]),
            "before_preview":old.get("preview",[])[:3],"after_preview":new.get("preview",[])[:3],
        })
    out={"schema":1,"count":len(rows),"rows":rows}
    Path(a.json).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["A-B SAME-VERDICT EVENT COUNT DETAIL","count=%d"%len(rows),""]
    for x in rows:
        lines.append("- %s | %s | %s | events %d -> %d | score %s -> %s | coverage %s -> %s"%(
            x["shard"],x["id"],x["verdict"],x["before_events"],x["after_events"],
            x["before_score"],x["after_score"],x["before_coverage_h"],x["after_coverage_h"]))
        if x["before_warnings"] or x["after_warnings"]:
            lines.append("    warnings: %s -> %s"%("; ".join(x["before_warnings"]),"; ".join(x["after_warnings"])))
        bp=[p.get("title","") if isinstance(p,dict) else str(p) for p in x["before_preview"]]
        apv=[p.get("title","") if isinstance(p,dict) else str(p) for p in x["after_preview"]]
        if bp or apv:
            lines.append("    preview: %s -> %s"%(" | ".join(bp)," | ".join(apv)))
    Path(a.text).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(lines[1])

if __name__ == "__main__":
    main()
