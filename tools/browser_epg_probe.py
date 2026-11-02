#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio, json, re, sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

SOURCES = [
    ("dubaiplus", "https://www.dubaiplus.net/web/epg"),
    ("gobx", "https://www.gobx.com/en/whats-on"),
    ("stctv", "https://web.stctv.com/livetv"),
    ("starzplay", "https://www.starzplay.com/ar/live?selectcountry=MA&selectcity=Casablanca"),
]
KEY = re.compile(r"(epg|schedule|guide|programme|program|channel|listing|livetv|live-tv)", re.I)

async def probe(name, url, browser):
    ctx = await browser.new_context(
        locale="en-US",
        timezone_id="Africa/Casablanca",
        viewport={"width": 1365, "height": 900},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    )
    page = await ctx.new_page()
    net=[]
    async def on_response(resp):
        try:
            ct=(resp.headers.get("content-type") or "")
            if KEY.search(resp.url) or "json" in ct.lower():
                net.append({"url":resp.url,"status":resp.status,"content_type":ct})
        except Exception:
            pass
    page.on("response", on_response)
    row={"name":name,"url":url,"network":[]}
    try:
        resp=await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        row["main_status"]=resp.status if resp else None
        await page.wait_for_timeout(8000)
        # Small scroll only to trigger normal lazy-loaded EPG UI.
        for y in (700,1400,2100):
            await page.evaluate(f"window.scrollTo(0,{y})")
            await page.wait_for_timeout(1200)
        text=(await page.locator("body").inner_text(timeout=10000))[:50000]
        row["body_text"]=text
        row["title"]=await page.title()
        row["final_url"]=page.url
        row["network"]=net[:250]
        row["visible_epg_tokens"]=len(KEY.findall(text))
        row["contains_schedule_times"]=bool(re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b",text,re.I))
    except Exception as e:
        row["error"]=str(e)[:500]
        try:
            row["final_url"]=page.url
        except Exception:
            pass
        row["network"]=net[:250]
    finally:
        await ctx.close()
    return row

async def main():
    Path("reports/browser-probe").mkdir(parents=True, exist_ok=True)
    out=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        for i,(name,url) in enumerate(SOURCES):
            row=await probe(name,url,browser)
            out.append(row)
            Path(f"reports/browser-probe/{name}.json").write_text(json.dumps(row,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print("BROWSER_PROBE",json.dumps({
                "name":name,"main_status":row.get("main_status"),"final_url":row.get("final_url"),
                "title":row.get("title"),"network_matches":len(row.get("network",[])),
                "visible_epg_tokens":row.get("visible_epg_tokens"),"contains_schedule_times":row.get("contains_schedule_times"),
                "error":row.get("error")
            },ensure_ascii=False))
            # Cooldown so sources are never hit in parallel or in rapid succession.
            if i+1 < len(SOURCES):
                await asyncio.sleep(20)
        await browser.close()
    Path("reports/browser-probe/all.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0

if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
