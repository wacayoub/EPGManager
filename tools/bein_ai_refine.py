#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional AI quality pass for beIN MENA Arabic XMLTV.

Uses GitHub Models only on titles/descriptions that still look mixed, awkward,
or contain untranslated broadcast jargon. It never scrapes beIN. Failures are
non-fatal and deterministic post-processing remains authoritative.
"""
from __future__ import annotations

import argparse, gzip, hashlib, json, os, re, time
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

OPENAI_API="https://api.openai.com/v1/chat/completions"
GEMINI_API_BASE="https://generativelanguage.googleapis.com/v1beta/models"
MODEL=os.environ.get("BEIN_AI_MODEL","gpt-5.6-mini")
AR=re.compile(r"[\u0600-\u06FF]")
LATIN=re.compile(r"[A-Za-z]")
BAD_TOKENS=re.compile(r"\b(?:Spanish|French|Weekly Review|Final|Finals|W/M|M|MD\d+|FBL\d+|EP\.\d+)\b",re.I)
TEAM_VS=re.compile(r"^(.+?\s+vs\s+.+?)(?:\s+-\s+|$)",re.I)

SYSTEM_PROMPT="""You are a broadcast EPG Arabic editor for beIN MENA.
Return JSON only with keys title and desc.
Rules:
- Preserve club/team names in Latin script exactly when they are part of a match title.
- Translate league/competition/sport/programme names into natural Modern Standard Arabic.
- Never translate beIN, ATP Tennis, or club names.
- Remove dates, times, season/week/day/round/matchday tokens from title; such metadata belongs in desc only.
- Normalize these user-approved forms exactly:
  Ligue 1 Show => ملخص الدوري الفرنسي
  Ligue 1 Weekly Review => الملخص الأسبوعي للدوري الفرنسي
  Premier League Netbusters => ملخص الدوري الإنجليزي الممتاز
  Ipswich Town story => قصص الدوري الإنجليزي - Ipswich Town
  AFC Champions League Elite / MD1 => دوري أبطال آسيا
- Remove filler labels such as Spanish/French before an already translated league name.
- Keep the meaning factual; do not invent details.
- Improve awkward machine Arabic into concise TV-guide Arabic.
"""

def read(path):
    b=Path(path).read_bytes()
    if path.endswith(".gz") or b[:2]==b"\x1f\x8b": b=gzip.decompress(b)
    return ET.fromstring(b)

def write(path,root):
    ET.indent(root,space="  ")
    b=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    Path(path).write_bytes(gzip.compress(b,9,mtime=0) if path.endswith(".gz") else b)

def norm(s): return re.sub(r"\s+"," ",s or "").strip()

def needs_ai(title,desc):
    if BAD_TOKENS.search(title): return True
    # Mixed-language titles are candidates unless the Latin span is clearly a vs matchup.
    if AR.search(title) and LATIN.search(title):
        m=TEAM_VS.search(title)
        if not m: return True
        rest=title[m.end():]
        if LATIN.search(rest): return True
    # Catch common literal-machine phrasing in descriptions.
    bad_desc=("المقعد الساخن","أفضل لعب","المدافعون عن اللقب","في منتصف الأسبوع")
    return any(x in desc for x in bad_desc)

def key(title,desc): return hashlib.sha256((title+"\n"+desc).encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True); ap.add_argument("--output",required=True)
    ap.add_argument("--cache",default="data/bein_ai_cache.json")
    ap.add_argument("--report",default="reports/bein-ai-refine.json")
    a=ap.parse_args()
    openai_key=os.environ.get("OPENAI_API_KEY","").strip()
    gemini_key=os.environ.get("GEMINI_API_KEY","").strip()
    provider="openai" if openai_key else ("gemini" if gemini_key else "none")
    root=read(a.input)
    cache_path=Path(a.cache)
    try: cache=json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except Exception: cache={}
    stats={"provider":provider,"candidates":0,"changed":0,"cache_hits":0,"api_calls":0,"failures":[]}
    ses=requests.Session()
    if openai_key:
        ses.headers.update({"Authorization":f"Bearer {openai_key}","Content-Type":"application/json"})

    for p in root.findall("programme"):
        t=p.find("title"); d=p.find("desc")
        if t is None: continue
        title=norm(t.text); desc=norm(d.text if d is not None else "")
        if not needs_ai(title,desc): continue
        stats["candidates"]+=1
        k=key(title,desc)
        result=cache.get(k)
        if result:
            stats["cache_hits"]+=1
        elif provider=="openai":
            payload={"model":MODEL,"messages":[
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user","content":json.dumps({"title":title,"desc":desc},ensure_ascii=False)}
            ],"temperature":0.1,"response_format":{"type":"json_object"}}
            try:
                r=ses.post(OPENAI_API,json=payload,timeout=45); r.raise_for_status()
                txt=r.json()["choices"][0]["message"]["content"]
                result=json.loads(txt)
                cache[k]=result; stats["api_calls"]+=1; time.sleep(0.12)
            except Exception as e:
                stats["failures"].append({"title":title[:120],"error":str(e)})
                continue
        elif provider=="gemini":
            payload={
                "system_instruction":{"parts":[{"text":SYSTEM_PROMPT}]},
                "contents":[{"role":"user","parts":[{"text":json.dumps({"title":title,"desc":desc},ensure_ascii=False)}]}],
                "generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}
            }
            try:
                model=os.environ.get("BEIN_GEMINI_MODEL","gemini-2.5-flash")
                url=f"{GEMINI_API_BASE}/{model}:generateContent?key={gemini_key}"
                r=ses.post(url,json=payload,timeout=45); r.raise_for_status()
                txt=r.json()["candidates"][0]["content"]["parts"][0]["text"]
                result=json.loads(txt)
                cache[k]=result; stats["api_calls"]+=1; time.sleep(0.12)
            except Exception as e:
                stats["failures"].append({"title":title[:120],"error":str(e)})
                continue
        else:
            continue

        nt=norm((result or {}).get("title","")); nd=norm((result or {}).get("desc",""))
        if nt and nt!=title:
            t.text=nt; stats["changed"]+=1
        if nd and nd!=desc:
            if d is None:
                d=ET.SubElement(p,"desc"); d.set("lang","ar")
            d.text=nd; d.set("lang","ar"); stats["changed"]+=1

    cache_path.parent.mkdir(parents=True,exist_ok=True)
    cache_path.write_text(json.dumps(cache,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    Path(a.report).parent.mkdir(parents=True,exist_ok=True)
    Path(a.report).write_text(json.dumps(stats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    write(a.output,root)
    if provider=="none":
        stats["note"]="AI provider not configured; deterministic Arabic normalization applied, AI pass skipped."
        Path(a.report).write_text(json.dumps(stats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("BEIN_AI_REFINE",json.dumps(stats,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
