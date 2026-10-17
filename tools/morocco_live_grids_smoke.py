"""Read-only live check against broadcaster programme grids."""
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from morocco_current_official import parse_alaoula_html, parse_chada_html

today = datetime.now(ZoneInfo("Africa/Casablanca")).date()
for name,url,parser,min_count in [
    ("AlAoula","https://www.snrt.ma/ar/al-aoula",parse_alaoula_html,8),
    ("Chada TV","https://chada.ma/fr/tv/grille/",parse_chada_html,20),
]:
    # SNRT sometimes times out on a cold connection from GitHub runners.
    # Retry the same official URL only; never substitute French or a donor.
    import time
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(
                url, timeout=(12, 20),
                headers={"User-Agent":"Mozilla/5.0","Accept-Language":"ar,fr;q=.9"}
            )
            r.raise_for_status()
            last_error = None
            break
        except requests.RequestException as exc:
            last_error = exc
            print("OFFICIAL_SOURCE_RETRY",name,attempt+1,str(exc)[:160],flush=True)
            if attempt < 2:
                time.sleep(1)
    if last_error is not None:
        raise last_error
    events=parser(r.text,today)
    print("LIVE_OFFICIAL_SOURCE",name,"http=",r.status_code,"events=",len(events),
          "first=",events[0].start if events else None,
          "last=",events[-1].stop if events else None,
          "titles=",[e.title[:36] for e in events[:3]],flush=True)
    assert len(events)>=min_count,(name,len(events))
    if name == "AlAoula":
        import re
        assert all(e.tl=="ar" and e.dl=="ar" and re.search(r"[\u0600-\u06ff]",e.title) for e in events)
        assert all(e.source=="snrt-official-arabic" for e in events)
    assert all(e.stop>e.start for e in events)
    assert any(e.stop>datetime.now(ZoneInfo("Africa/Casablanca")) for e in events)
print("MOROCCO_ACTIVE_GRIDS: PASS")
