"""Keep native shared-map coverage distinct from actual feed quality."""
import ast
import hashlib
from prepare_rc80_rytec_native import replace_once
ORIGINAL = {
    "ui/main.py":"16346f03c2043ba2d7f313a607d980bf0b46fc19fc9bccdb15309a7d0ac2e042",
    "ui/native_sources.py":"843d22df0107bf3848b483d47837800b3809d738ea39472615bddf1fc50daf65",
}
UNIQUE_ROWS = r'''
def _unique_mapping_rows(index):
    rows = []
    seen_native = set()
    for row in ((index or {}).get("sources") or {}).values():
        row = row or {}
        path = str(row.get("map_path") or "")
        if path and row.get("official") and row.get("provider") == "RYTEC":
            if path in seen_native:
                continue
            seen_native.add(path)
        rows.append(row)
    return rows
'''
NATIVE_DISPLAY = r'''
def _native_rytec_display(item, prepared):
    if str(item.get("provider") or "").upper() != "RYTEC":
        return
    # A shared channels file is a native mapping mechanism, not the channel
    # count of this country's programme feed or evidence of future schedules.
    item["_epg_ids_text"] = "Shared SRP"
    item["_channels"] = None
    state = str(item.get("_health") or "").upper()
    if state == "MAP NEEDED" or (state in ("NO EPG", "CHECKING", "CATALOG OK") and
                                  not bool((prepared or {}).get("complete"))):
        item["_health"] = "NATIVE MAP"
        item["_programmes"] = None
        item["_health_message"] = "Native EPGImport map; future programme availability not verified"
'''

def corrected(original):
    if set(original) != set(ORIGINAL):
        raise ValueError("Expected two pinned UI modules")
    for name,digest in ORIGINAL.items():
        if hashlib.sha256(original[name]).hexdigest()!=digest:
            raise ValueError("UI baseline drift: "+name)
    out={k:v.decode("utf-8") for k,v in original.items()}
    key="ui/main.py"
    out[key]=replace_once(out[key], 'rows = list((idx.get("sources") or {}).values())',
                          'rows = _unique_mapping_rows(idx)') + "\n" + UNIQUE_ROWS
    key="ui/native_sources.py";s=out[key]
    s=replace_once(s, 'text=str(mapped), color=theme.TEXT_INT if mapped else theme.MUTED_TEXT_INT,',
                   'text="NATIVE" if str(item.get("provider") or "").upper() == "RYTEC" else str(mapped), color=theme.TEXT_INT if mapped else theme.MUTED_TEXT_INT,')
    s=replace_once(s, '        # CHECKED is the newest observation time,',
                   '        _native_rytec_display(item, prepared)\n\n        # CHECKED is the newest observation time,')
    s=replace_once(s, '        if item.get("_best_source"):\n            role="Recommended for this receiver"',
                   '        if str(item.get("provider") or "").upper() == "RYTEC":\n            role="Native EPGImport source"\n        elif item.get("_best_source"):\n            role="Recommended for this receiver"')
    s=replace_once(s, '        ids_text=("%d/%d linked" % (linked,total)) if total else ("%d linked" % linked)',
                   '        ids_text=("%d/%d linked" % (linked,total)) if total else ("%d linked" % linked)\n        native_rytec = str(item.get("provider") or "").upper() == "RYTEC"\n        if native_rytec: ids_text="Shared provider file"\n        mapped_text="Native SRP links" if native_rytec else "%d mapped" % mapped')
    s=replace_once(s, '"%d mapped  •  ID map %s  •  Published / checked GMT+0: %s  •  %s" % (',
                   '"%s  •  ID map %s  •  Published / checked GMT+0: %s  •  %s" % (')
    s=replace_once(s, '                mapped, ids_text, source_updated, schedule_line))',
                   '                mapped_text, ids_text, source_updated, schedule_line))')
    out[key]=s+"\n"+NATIVE_DISPLAY
    result={k:v.encode("utf-8") for k,v in out.items()}
    for k,v in result.items():ast.parse(v,filename=k)
    return result
