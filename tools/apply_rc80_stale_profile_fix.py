"""SHA-locked offline repair for measured stale-profile false NO_EPG verdicts."""
import ast
import hashlib

ORIGINAL = {
    'core/source_quality.py': '9de76ea39c0c1e73dabfed124c156c40ccf3055e478b69b7e7fd4d4879880290',
    'core/github_direct_sync.py': '61f93bb6486ce08df6aeecc04ef596547a8883c56056e0d7d32cf6813423bae6',
}


def one(text, old, new):
    if text.count(old) != 1:
        raise ValueError('stale-profile repair source pattern drift')
    return text.replace(old, new, 1)


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError('stale-profile repair inventory drift')
    for path, pin in ORIGINAL.items():
        if hashlib.sha256(source[path]).hexdigest() != pin:
            raise ValueError('stale-profile repair preimage drift: '+path)
    result = {}
    quality = source['core/source_quality.py'].decode('utf-8')
    quality = one(quality,
        '        out["specific"] = True\n        out["profile_complete"] = bool(data.get("complete"))',
        '        out["specific"] = True\n        out["profile_checked"] = data.get("checked")\n        out["profile_complete"] = bool(data.get("complete"))')
    direct = source['core/github_direct_sync.py'].decode('utf-8')
    direct = one(direct,
        '    now = int(time.time())\n\n    # A complete profile',
        '    now = int(time.time())\n    from .quality_profile_freshness import authoritative_profile\n    payload = data if isinstance(data, dict) else load()\n    try:\n        monitor_checked = int(payload.get("id_monitor_checked") or payload.get("checked") or 0)\n    except (ValueError, TypeError, OverflowError):\n        return "UNKNOWN"\n\n    # A complete profile')
    direct = one(direct,
        '        if (local.get("specific") and local.get("profile_complete") and\n                "future_last" in local):',
        '        if authoritative_profile(local, monitor_checked, now):')
    direct = one(direct,
        '    payload = data if isinstance(data, dict) else load()\n    row = id_monitor_row(sid, cid, data=payload)',
        '    row = id_monitor_row(sid, cid, data=payload)')
    direct = one(direct,
        '    if status == "OK":\n        return "OK"\n    return "WARNING"',
        '    # Positive monitor metadata is not a measured usable guide.\n    # Keep review until a current channel-specific profile can certify it.\n    return "WARNING"')
    for path, text in [('core/source_quality.py', quality), ('core/github_direct_sync.py', direct)]:
        ast.parse(text)
        result[path] = text.encode('utf-8')
    return result
