"""Read-only diagnostic of Morocco official schedule HTML."""
import re
import requests
from bs4 import BeautifulSoup

for key, url in (
    ("alaoula", "https://www.snrt.ma/fr/al-aoula"),
    ("chada", "https://chada.ma/fr/tv/grille/"),
):
    print("\n=== SOURCE:", key, url, flush=True)
    try:
        resp = requests.get(url, timeout=(5, 17), headers={"User-Agent":"Mozilla/5.0", "Accept-Language":"fr-MA,fr;q=0.9"})
        print("HTTP", resp.status_code, "BYTES", len(resp.content), "URL", resp.url, flush=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup.find_all(string=re.compile(r"(?:[01]?\d|2[0-4])\s*[H:h:]\s*[0-5]\d\s*(?:[→-]|$)", re.I))[:8]:
            node = tag.parent
            print("CLOCK_TAG",repr(str(tag).strip()[:120]))
            for lvl in range(4):
                if not node: break
                print(" PARENT",lvl,node.name,"class",node.get("class"),"id",node.get("id"),"data",[(a,v) for a,v in node.attrs.items() if a.startswith("data-")][:5],"text",node.get_text(" ",strip=True)[:140])
                node=node.parent
        print("LIKELY:",[(x.name,x.get("class"),x.get("id"),x.get_text(" ",strip=True)[:75]) for x in soup.find_all(class_=re.compile("grid|grille|schedule|program|day|jour|tab|horaire",re.I))[:16]])
        for sel in ["script[type='application/ld+json']","script[type='application/json']"]:
            print("JSON",sel,[x.get_text()[:180] for x in soup.select(sel)][:3])
        if key == "alaoula":
            grid = soup.select("#main-roller-content .grille-item")
            print("SNRT_GRID_COUNT",len(grid))
            for row in grid[:4]:
                print("SNRT_ITEM",str(row)[:900].replace("\\n"," "),flush=True)
            print("SNRT_GRID_PARENT",str(soup.select_one("#main-roller-content"))[:1400].replace("\\n"," "),flush=True)
        if key == "chada":
            print("CHADA_ROW_COUNT",len(soup.select("article.chada-epg-row")))
            for row in soup.select("article.chada-epg-row")[:2]:
                print("CHADA_ITEM",str(row)[:1600].replace("\\n"," "),flush=True)
                parent=row.parent
                for level in range(3):
                    if parent:
                        print("CHADA_PANEL",level,parent.name,parent.get("class"),parent.get("id"),[(a,v) for a,v in parent.attrs.items() if a.startswith("data-")])
                        parent=parent.parent
            print("CHADA_TABS",[(e.name,e.get("class"),e.get("data-day"),e.get("data-weekday"),e.get("aria-controls"),e.get("id"),e.get_text(" ",strip=True)[:30]) for e in soup.select("[class*=day], [class*=tab]")[:24]],flush=True)
    except Exception as e:
        print('PROBE_ERROR',type(e).__name__,str(e)[:400],flush=True)
