"""Pinned, workstation-only transformation; never deploys or edits user data."""
import ast
import hashlib

ORIGINAL = {
    'core/direct_native_import.py': '9b3136277475b4ac72510acd55ab8bbd73e6553e711de4af8bd899ac9dc0f63e',
    'ui/settings.py': '3af69607eb83ef25f789d386db006e952a755ad4687f002cd631e6032a783f28',
}

SCORE = '''    # Names are a tie-breaker only. Cache by their actual inputs rather than
    # source/ref claims: dozens of shared Rytec maps repeat the same names.
    # Both caches are confined to this invocation; no user/cache files change.
    identity_score_cache = {}
    def identity_score(mid, cid, cref):
        service = receiver_by_ref.get(cref) or {}
        service_name = str(service.get("clean_name") or service.get("name") or "")
        label = display_index.get((str(mid or "").lower(), str(cid or "").lower())) or str(cid or "")
        key = (label, service_name)
        cached = identity_score_cache.get(key)
        if cached is not None:
            return cached
        try:
            value = int(channel_mapper._token_score(label, service_name) or 0)
        except Exception:
            value = 0
        identity_score_cache[key] = value
        return value

    def claim_score(mid, cid, cref):
        return (owner_score(mid, cref), identity_score(mid, cid, cref))

    def better_claim(mid, cid, cref, previous):
        if previous == (mid, cid):
            return False
        current_owner = owner_score(mid, cref)
        previous_owner = owner_score(previous[0], cref)
        if current_owner != previous_owner:
            return current_owner > previous_owner
        return identity_score(mid, cid, cref) > identity_score(previous[0], previous[1], cref)

'''


def optimize_arbitration(source):
    """Retain lexicographic rank and stable ties, evaluating names only if needed."""
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == '_fast_single_owner_maps')
    lines = source.splitlines(keepends=True)
    body = ''.join(lines[fn.lineno - 1:fn.end_lineno])
    start = body.index('    claim_rank_cache = {}\n')
    stop = body.index('    mapping_rows = ', start)
    body = body[:start] + SCORE + body[stop:]
    old = 'claim_score(lmid, lcid, cref) > claim_score(prev[0], prev[1], cref)'
    if body.count(old) != 2:
        raise ValueError('Arbitration comparison drift')
    body = body.replace(old, 'better_claim(lmid, lcid, cref, prev)')
    result = ''.join(lines[:fn.lineno - 1]) + body + ''.join(lines[fn.end_lineno:])
    ast.parse(result)
    return result


def corrected(original):
    if set(original) != set(ORIGINAL):
        raise ValueError('Expected two pinned installed modules')
    for name, digest in ORIGINAL.items():
        if hashlib.sha256(original[name]).hexdigest() != digest:
            raise ValueError('Installed baseline drift: ' + name)
    direct = optimize_arbitration(original['core/direct_native_import.py'].decode('utf-8'))
    settings = original['ui/settings.py'].decode('utf-8')
    old = 'Daily Casablanca local time for automatic EPG Import All.'
    if settings.count(old) != 1:
        raise ValueError('UTC help preimage drift')
    settings = settings.replace(old, 'Daily GMT+0 (UTC) time for automatic EPG Import All.')
    result = {'core/direct_native_import.py': direct.encode('utf-8'),
              'ui/settings.py': settings.encode('utf-8')}
    for name, raw in result.items():
        ast.parse(raw, filename=name)
    return result
