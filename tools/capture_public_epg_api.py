#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

OUT=Path("reports/public-api-capture")
OUT.mkdir(parents=True, exist_ok=True)

SENSITIVE = re.compile(r"(token|authorization|cookie|session|password|secret|license|drm|playback|stream|manifest|api[_-]?key|url$)", re.I)

def sanitize(obj, depth=0):
    if depth > 8:
        return "<depth-limit>"
    if isinstance(obj, dict):
        out={}
        for k,v in obj.items():
            if SENSITIVE.search(str(k)):
                out[k]="<redacted>"
            else:
                out[k]=sanitize(v, depth+1)
        return out
    if isinstance(obj, list):
        return [sanitize(v, depth+1) for v in obj[:8]]
    if isinstance(obj, str) and len(obj)>1200:
        return obj[:1200]+"…"
    return obj

def schema(obj, depth=0):
    if depth > 5:
        return "…"
    if isinstance(obj, dict):
        return {k:schema(v,depth+1) for k,v in list(obj.items())[:80]}
    if isinstance(obj, list):
        return [schema(obj[0],depth+1)] if obj else []
    return type(obj).__name__

async def capture_json_response(resp, bucket, label):
    try:
        ct=(resp.headers.get("content-type") or "").lower()
        if "json" not in ct:
            return
        data=await resp.json()
        row={
            "label":label,
            "url":resp.url,
            "status":resp.status,
            "schema":schema(data),
            "sample":sanitize(data),
        }
        bucket.append(row)
    except Exception as e:
        bucket.append({"label":label,"url":resp.url,"status":resp.status,"error":str(e)[:300]})

async def dubai(browser):
    ctx=await browser.new_context(locale="en-US",timezone_id="Asia/Dubai",viewport={"width":1365,"height":900})
    page=await ctx.new_page()
    bucket=[]
    async def on_resp(resp):
        if "d1vr1mlm6fadud.cloudfront.net/content/channels" in resp.url:
            await capture_json_response(resp,bucket,"dubaiplus_channels")
    page.on("response",on_resp)
    r=await page.goto("https://www.dubaiplus.net/epg",wait_until="domcontentloaded",timeout=45000)
    await page.wait_for_timeout(12000)
    OUT.joinpath("dubaiplus.json").write_text(json.dumps({
        "main_status":r.status if r else None,
        "final_url":page.url,
        "captures":bucket,
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("DUBAIPLUS_CAPTURE",json.dumps({"main":r.status if r else None,"captures":len(bucket),"urls":[x.get("url") for x in bucket]},ensure_ascii=False))
    await ctx.close()

async def stc(browser):
    ctx=await browser.new_context(locale="ar-SA",timezone_id="Asia/Riyadh",viewport={"width":1365,"height":900})
    page=await ctx.new_page()
    bucket=[]
    async def on_resp(resp):
        u=resp.url.lower()
        if ("intigral-ott.net" in u or "stctv.com" in u) and any(k in u for k in ("channels","schedule","epg","listing")):
            await capture_json_response(resp,bucket,"stctv_epg")
    page.on("response",on_resp)
    r=await page.goto("https://web.stctv.com/",wait_until="domcontentloaded",timeout=45000)
    await page.wait_for_timeout(10000)
    clicked=False
    for label in ("البث المباشر","Live TV","Live"):
        try:
            loc=page.get_by_text(label,exact=True)
            if await loc.count():
                await loc.first.click(timeout=5000)
                clicked=True
                break
        except Exception:
            pass
    if not clicked:
        try:
            await page.goto("https://web.stctv.com/livetv",wait_until="domcontentloaded",timeout=45000)
        except Exception:
            pass
    await page.wait_for_timeout(12000)
    OUT.joinpath("stctv.json").write_text(json.dumps({
        "main_status":r.status if r else None,
        "final_url":page.url,
        "captures":bucket,
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("STCTV_CAPTURE",json.dumps({"main":r.status if r else None,"final":page.url,"captures":len(bucket),"urls":[x.get("url") for x in bucket]},ensure_ascii=False))
    await ctx.close()

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        await dubai(browser)
        await asyncio.sleep(20)
        await stc(browser)
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
