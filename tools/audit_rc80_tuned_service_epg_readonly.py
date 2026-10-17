#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPGManager RC80: read-only EPG-cache test of the currently tuned SAT service.

Only HTTP GET to OpenWebif on 127.0.0.1. Never prints the service reference,
channel name, provider, programme titles, URLs, user mappings or credentials.
Does not import, zap, save, restart, install or change receiver configuration.
IPTV service references are deliberately excluded to protect private URLs.
"""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE = 1000000
ALLOWED = frozenset(("getcurrent", "epgservicenow", "epgservicenext", "epgservice"))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def query(method, params=None):
    if method not in ALLOWED:
        raise ValueError("endpoint not permitted")
    url = "http://127.0.0.1:80/api/" + method
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"Accept": "application/json"}, method="GET")
    with build_opener(ProxyHandler({}), NoRedirect).open(req, timeout=4) as response:
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("API response too large")
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("unexpected API response")
    return obj

def inspect_current(obj):
    """OpenWebif /api/getcurrent has nested info, now, next dictionaries."""
    info = obj.get("info")
    if not isinstance(info, dict):
        return None, "OPENWEBIF_CURRENT_INFO_MISSING"
    if info.get("result") is not True:
        return None, "OPENWEBIF_CURRENT_INFO_FAILED"
    ref = info.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return None, "CURRENT_REF_MISSING"
    if len(ref) > 300:
        return None, "CURRENT_REF_OVERSIZED_OR_STREAM"
    low = ref.lower()
    if any(token in low for token in ("http", "%3a", "://", "@", "/")):
        return None, "CURRENT_SERVICE_STREAM_OR_URL"
    parts = ref.rstrip(":").split(":")
    if len(parts) != 10:
        return None, "CURRENT_REF_NOT_STANDARD_DVB"
    if parts[0] != "1" or parts[1] != "0":
        return None, "CURRENT_SERVICE_NOT_DVB_SAT_STYLE"
    if any(not part or not all(c in "0123456789abcdefABCDEF" for c in part) for part in parts[2:]):
        return None, "CURRENT_REF_UNEXPECTED_FORMAT"
    return ref, "DVB_SERVICE_REFERENCE_VALID"

def measure(events, epoch):
    valid = current = future = 0
    if not isinstance(events, list):
        return {"returned": 0, "valid": 0, "now": 0, "future": 0, "schema_ok": False}
    for row in events[:160]:
        if not isinstance(row, dict) or not str(row.get("title") or "").strip():
            continue
        try:
            start = int(row.get("begin_timestamp", row.get("begin")))
            duration = int(row.get("duration_sec", row.get("duration")))
        except (ValueError, TypeError, OverflowError):
            continue
        if not (1000000000 < start < 5000000000 and 0 < duration < 259200):
            continue
        valid += 1
        current += int(start <= epoch < start + duration)
        future += int(epoch <= start <= epoch + 172800)
    return {"returned": len(events), "valid": valid, "now": current,
            "future": future, "schema_ok": True}

def run():
    out = {"audit": "EPGManager tuned SAT service cache", "read_only": True,
           "receiver_modified": False, "network": "127.0.0.1 GET only",
           "rc80_hardware_certified": False, "authorized_to_install": False,
           "service_name_or_reference_disclosed": False, "epg": {}}
    try:
        cur = query("getcurrent")
        ref, classification = inspect_current(cur)
        out["current_reference_class"] = classification
        info = cur.get("info")
        if isinstance(info, dict):
            name = info.get("name")
            if isinstance(name, str):
                # Only a boolean is reported. The actual service name stays private.
                out["tuned_channel_name_contains_2m"] = "2m" in name.lower().replace(" ", "")
        if not ref:
            out["status"] = "CURRENT_SERVICE_IDENTIFIED_NO_SAT_EPG_QUERY"
            return out
        out["safe_sat_service_selected"] = True
        epoch = int(time.time())
        for key in ("now", "next"):
            embedded = cur.get(key)
            if isinstance(embedded, dict):
                out["epg"]["getcurrent_" + key] = measure([embedded], epoch)
        for name in ("epgservicenow", "epgservicenext", "epgservice"):
            try:
                reply = query(name, {"sRef": ref})
                out["epg"][name] = measure(reply.get("events"), epoch)
                out["epg"][name]["api_result_ok"] = reply.get("result") is not False
            except (OSError, ValueError, UnicodeError, TimeoutError, HTTPError, URLError) as err:
                out["epg"][name] = {"error_type": type(err).__name__}
        reports = list(out["epg"].values())
        visible = any(x.get("now", 0) or x.get("future", 0) for x in reports)
        has_error = any("error_type" in x or x.get("schema_ok") is False or not x.get("api_result_ok", True) for x in reports)
        out["status"] = ("EPG_VISIBLE_TUNED_SERVICE" if visible
                         else "INCONCLUSIVE_LOCAL_API" if has_error
                         else "NO_CURRENT_OR_FUTURE_EPG_TUNED_SERVICE")
    except (OSError, ValueError, UnicodeError, TimeoutError, HTTPError, URLError) as err:
        out["status"] = "OPENWEBIF_LOCAL_UNAVAILABLE"
        out["error_type"] = type(err).__name__
    return out

if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
