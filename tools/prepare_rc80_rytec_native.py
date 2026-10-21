"""Pinned Rytec native EPGImport repair; transformation only, no deployment."""
import ast
import hashlib

ORIGINAL = {
    "core/direct_native_import.py": "c8dc4d68712006f8fe86e64e698852d6dee8601667b9d880cff4dd3873d929f7",
    "core/native_epgimport_bridge.py": "8dbe9a36f15565c916c1f3b904714da5c2cdc16ca5af586315fae721a5f80681",
    "core/srp_channel_map.py": "833d042cdd6c379f7fe5ba41f505c0cd8d689b7d55ee0feafb8cff436009fcdc",
}

NATIVE_BRIDGE = r'''
class FilteredNativeChannelMap(object):
    """Delegate mirrors/custom maps/refresh to the installed EPGImport engine.

    Never alter its shared provider map: filtering and saved overrides belong
    to this source adapter only. Keep a valid cached map on download failure.
    """
    def __init__(self, native, fallback, transform):
        self.native = native
        self.fallback = fallback or {}
        self.transform = transform
        self.items = transform(self.fallback)

    def __getattr__(self, name):
        return getattr(self.native, name)

    def downloadables(self):
        return self.native.downloadables()

    def update(self, filter_callback, downloaded_file=None):
        self.native.update(filter_callback, downloaded_file)
        self.items = self.transform(self.native.items or self.fallback)


def load_rytec_native_source(item, fallback, transform):
    """Use EPGImport's installed definitions and EPGChannel implementation."""
    from copy import copy
    from urllib.parse import urlsplit
    wanted = os.path.basename(urlsplit(str(item.get("url") or "")).path)
    errors = []
    for prefix in ("Plugins.Extensions.EPGImport", "Plugins.SystemPlugins.EPGImport"):
        try:
            config_module = importlib.import_module(prefix + ".EPGConfig")
        except ImportError as exc:
            errors.append(str(exc))
            continue
        for candidate in config_module.enumSourcesFile("/etc/epgimport/rytec.sources.xml"):
            filenames = {os.path.basename(urlsplit(str(url)).path)
                         for url in candidate.urls}
            if wanted and wanted in filenames:
                source = copy(candidate)
                source.source_id = str(item.get("manager_id") or item.get("id") or "")
                source.channels = FilteredNativeChannelMap(candidate.channels, fallback, transform)
                return source
        # No matching installed definition: use the same native channel parser,
        # with the explicit provider mirrors from our catalogue. No name match.
        source = LocalEPGSource(item.get("id"), item.get("name"), item.get("url"), fallback)
        source.urls = list(dict.fromkeys([str(item.get("url") or "")] +
                                         list(item.get("urls") or [])))
        channel_urls = list(item.get("channels_urls") or [])
        if not channel_urls:
            raise RuntimeError("Rytec has no native channels definition")
        native = config_module.EPGChannel(item.get("channels_name") or "rytec.channels.xml.xz",
                                         urls=channel_urls, offset=0)
        source.channels = FilteredNativeChannelMap(native, fallback, transform)
        return source
    raise RuntimeError("Native Rytec EPGConfig unavailable: " + "; ".join(errors))
'''

NATIVE_POLICY = r'''
def _shared_rytec_map(items_by_mid, left, right):
    a = items_by_mid.get(str(left).lower()) or {}
    b = items_by_mid.get(str(right).lower()) or {}
    return (str(a.get("provider") or "").upper() == "RYTEC" and
            str(b.get("provider") or "").upper() == "RYTEC" and
            bool(a.get("channels_name")) and
            a.get("channels_name") == b.get("channels_name"))


def _rytec_map_context(items, parsed, registry_index, store, mapping_rows):
    """Source-specific protection around the native, shared provider map."""
    selected = {str(source_catalog.mapping_source_id(x)).lower(): x for x in items}
    manual = _manual_ref_targets(store, store_rows=mapping_rows)
    persisted = _persisted_ref_targets(store, store_rows=mapping_rows)
    receiver = (registry_index or ({}, {}))[0] or {}
    locked = {}
    for ref, candidates in persisted.items():
        eligible = [x for x in candidates if x.get("source_id") in selected]
        if not eligible:
            continue
        def rank(x):
            try:
                score = source_priority.ownership_rank(selected[x["source_id"]], receiver.get(_canon_ref(ref)) or {})
            except Exception:
                score = (-1, ())
            return _persisted_target_rank(x, score)
        winner = max(eligible, key=rank)
        locked[_canon_ref(ref)] = (winner["source_id"], winner["channel_id"])
    owners = {}
    for owner, channels in parsed.items():
        for cid, refs in channels.items():
            for ref in refs:
                owners.setdefault(_canon_ref(ref), set()).add((str(owner).lower(), str(cid).lower()))
    return {"selected": selected, "manual": manual, "locked": locked,
            "owners": owners, "receiver": receiver}


def _rytec_map_transform(item, items, parsed, registry_index, store, mapping_rows, only_iptv,
                         context=None):
    # One shared context per Import All; do not duplicate all receiver claims
    # in every Rytec adapter on a low-memory receiver.
    if context is None:
        context = _rytec_map_context(items, parsed, registry_index, store, mapping_rows)
    mid = str(source_catalog.mapping_source_id(item)).lower()
    selected = context["selected"]
    manual = context["manual"]
    locked = context["locked"]
    owners = context["owners"]
    receiver = context["receiver"]
    overlay = {}
    for key, row in mapping_rows.items():
        if not isinstance(row, dict) or "::" not in str(key):
            continue
        owner, cid = str(key).split("::", 1)
        if owner.lower() == mid and str(row.get("mode") or "").lower() in (
                "manual", "auto", "auto-repair", "smartmatch", "smartmatch-iptv"):
            overlay[cid.lower()] = list(row.get("refs") or [])

    def transform(native_items):
        combined = {str(cid).lower(): list(refs or []) for cid, refs in (native_items or {}).items()}
        combined.update(overlay)
        result = {}
        for cid, refs in combined.items():
            keep = []
            for ref in refs:
                raw = str(ref or "").strip()
                if not raw or (only_iptv and channel_mapper.classify_service_ref(raw) != "IPTV"):
                    continue
                canon = _canon_ref(raw)
                exact = (mid, cid)
                user_targets = manual.get(raw) or manual.get(canon)
                if user_targets:
                    if exact not in user_targets:
                        continue
                elif canon in locked:
                    if exact != locked[canon]:
                        continue
                elif owners.get(canon):
                    if exact not in owners[canon] and not any(
                            _shared_rytec_map(selected, mid, owner) for owner, _ in owners[canon]):
                        continue
                if receiver and canon not in receiver and cid not in overlay:
                    continue
                if raw not in keep:
                    keep.append(raw)
            if keep:
                result[cid] = keep
        return result
    return transform
'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned replacement anchor drift")
    return source.replace(old, new, 1)


def corrected(original):
    if set(original) != set(ORIGINAL):
        raise ValueError("Expected the three pinned installed modules")
    for name, digest in ORIGINAL.items():
        if hashlib.sha256(original[name]).hexdigest() != digest:
            raise ValueError("Installed baseline drift: " + name)
    out = {name: raw.decode("utf-8") for name, raw in original.items()}
    key = "core/native_epgimport_bridge.py"
    out[key] += "\n" + NATIVE_BRIDGE
    key = "core/direct_native_import.py"
    s = out[key]
    s = replace_once(s, "CountingEPGCacheProxy, _load_epgimport_class)",
                     "CountingEPGCacheProxy, _load_epgimport_class, load_rytec_native_source)")
    s += "\n" + NATIVE_POLICY
    s = correct_arbitration(s)
    s = replace_once(s, """        if not channel_items:
            stats["unmapped_sources"] += 1""", """        native_rytec = str(item.get("provider") or "").upper() == "RYTEC"
        if not channel_items and not native_rytec:
            stats["unmapped_sources"] += 1""")
    s = replace_once(s, "        source = LocalEPGSource(mid, name, url, channel_items)", """        if native_rytec:
            transform = _rytec_map_transform(item, items, parsed, registry_index,
                                            store, mapping_rows, only_iptv)
            source = load_rytec_native_source(item, channel_items, transform)
        else:
            source = LocalEPGSource(mid, name, url, channel_items)""")
    s = replace_once(s, "    sources = []\n    stats = {", "    native_context = (_rytec_map_context(items, parsed, registry_index, store, mapping_rows)\n                      if any(str(x.get(\"provider\") or \"\").upper() == \"RYTEC\" for x in items) else None)\n    sources = []\n    stats = {")
    s = replace_once(s, "store, mapping_rows, only_iptv)\n            source = load_rytec", "store, mapping_rows, only_iptv, context=native_context)\n            source = load_rytec")
    out[key] = s
    key = "core/srp_channel_map.py"
    out[key] = replace_once(out[key], """        # The official Rytec map already is the direct channel-id -> SRP map.
        return {"source_id": source_id, "map_path": path, "provider": provider,
                "official": True, "channels": 0, "refs": 0, "unmapped": 0,""", """        # Report actual official-map counts, not zero placeholders.
        official_map = parse_channels_xml(path)
        return {"source_id": source_id, "map_path": path, "provider": provider,
                "official": True, "channels": len(official_map),
                "refs": sum(len(refs) for refs in official_map.values()), "unmapped": 0,""")
    result = {name: text.encode("utf-8") for name, text in out.items()}
    for name, raw in result.items():
        ast.parse(raw, filename=name)
    return result

ARBITRATION_OLD = r'''                if target != (lmid, lcid):
                    suppressed += 1
                    continue'''
ARBITRATION_NEW = r'''                if target != (lmid, lcid):
                    # Rytec feeds share a global ID map, not programme membership.
                    # The native parser must see every official ID in each feed.
                    # Saved exact owners and unrelated provider priorities remain.
                    shared_native = (cref not in locked_simple and target and
                                     _shared_rytec_map(item_by_mid, lmid, target[0]))
                    if not shared_native:
                        suppressed += 1
                        continue'''

def correct_arbitration(source):
    return replace_once(source, ARBITRATION_OLD, ARBITRATION_NEW)
