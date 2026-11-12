# -*- coding: utf-8 -*-
"""Persistent Smart Sources selection for EPG Manager beta13.

Beta13 moves from one left/right logical source per country to a flat checkbox
selection of individual feeds.  The legacy country structure is retained for
backward compatibility and single-source actions, but selected_ids is the
canonical normal workflow.
"""
from __future__ import print_function
import json
import os
import copy

from . import source_catalog, source_variant_policy

PATH = "/etc/enigma2/epgmanager_source_preferences.json"
DEFAULTS = {}
_PREFS_CACHE = {}


def _path_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return (0, 0)


class SourcePreferences(object):
    def __init__(self, path=PATH):
        self.path = path
        self.data = {"version": 14, "countries": {}, "selected_ids": [], "pinned_ids": []}
        # beta72: Smart Mapping is opened frequently.  Re-normalizing every
        # country/feed on each screen construction is wasted CPU; reuse the
        # normalized process cache while the preferences file is unchanged.
        sig = _path_sig(self.path)
        cached = _PREFS_CACHE.get(self.path)
        if cached and cached.get("sig") == sig:
            self.data = copy.deepcopy(cached.get("data") or self.data)
        else:
            self._load()
            _PREFS_CACHE[self.path] = {"sig": _path_sig(self.path), "data": copy.deepcopy(self.data)}

    def _load(self):
        loaded = None
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except Exception:
            loaded = None
        if isinstance(loaded, dict):
            self.data = loaded
            self.data.setdefault("countries", {})
        else:
            self.data = {"version": 14, "countries": {}, "selected_ids": [], "pinned_ids": []}
        self._normalize()

    def _legacy_selected_ids(self):
        ids = []
        seen = set()
        countries = self.data.get("countries") or {}
        for code in source_catalog.COUNTRY_NAMES:
            state = countries.get(code) or {}
            if not bool(state.get("enabled", False)):
                continue
            source = str(state.get("source") or source_catalog.default_source(code))
            option = source_catalog.source_option(code, source) or {}
            for item in option.get("items") or []:
                sid = source_catalog.catalogue_source_id(item)
                if sid and sid not in seen:
                    seen.add(sid); ids.append(sid)
        return ids

    def _normalize(self):
        countries = self.data.setdefault("countries", {})
        # Keep legacy country rows valid for old screens/callers.
        for row in source_catalog.country_rows():
            code = row["code"]
            state = countries.get(code)
            if not isinstance(state, dict):
                state = {"enabled": False, "source": source_catalog.default_source(code)}
                countries[code] = state
            state["enabled"] = bool(state.get("enabled", False))
            source = str(state.get("source") or "")
            if not source:
                source = source_catalog.source_for_provider(code, state.get("provider") or "")
            if not source_catalog.source_option(code, source):
                source = source_catalog.default_source(code)
            state["source"] = source
            state["provider"] = source_catalog.source_option_provider(code, source)

        valid = set(x.get("id") for x in source_catalog.all_sources() if x.get("id"))
        raw_selected = self.data.get("selected_ids")
        if not isinstance(raw_selected, list):
            raw_selected = self._legacy_selected_ids()
        else:
            raw_selected = list(raw_selected)
        try:
            old_version = int(self.data.get("version") or 0)
        except Exception:
            old_version = 0
        # RC29/30 one-time migration: replace the old monolithic MENA cloud with
        # all exclusive country/provider shards. User can then deselect any
        # countries/providers they do not want.
        legacy_mena = str(getattr(source_catalog.external_sources, "MENA_CLOUD_ID",
                                  "ext_epgmanager_mena_arabic"))
        # Keep the legacy migration exclusive: aggregate Smart Sources added
        # in RC33 are opt-in and must never be auto-selected together with the
        # country/provider shards, otherwise the same XMLTV IDs can be imported
        # twice.
        aggregate_mena = {legacy_mena,
                          "ext_epgmanager_mena_general",
                          "ext_epgmanager_mena_premium"}
        new_mena = sorted(x for x in valid
                          if (x.startswith("ext_epgmanager_mena_")
                              or x.startswith("ext_epgmanager_provider_"))
                          and x not in aggregate_mena)
        if old_version < 7 and legacy_mena in raw_selected:
            raw_selected = [x for x in raw_selected if str(x or "") != legacy_mena]
            raw_selected.extend(x for x in new_mena if x not in raw_selected)
        # RC61: STARZPLAY GCC is healthy again. Users on the complete rc60
        # direct set receive STARZPLAY automatically; custom subsets are left
        # untouched. The feed is a GCC union (AE/SA/KW/QA/BH/OM) and source
        # priority keeps provider-specific feeds authoritative on overlaps.
        rc60_direct = {
            "ext_epgscrapers_morocco", "ext_epgscrapers_bein", "ext_epgscrapers_elcinema",
            "ext_epgscrapers_osn", "ext_epgscrapers_sport24", "ext_epgscrapers_dubaiplus",
            "ext_epgscrapers_shahid", "ext_epgscrapers_rotana", "ext_epgscrapers_stctv",
            "ext_epgscrapers_aljazeera",
        }
        if old_version < 12 and rc60_direct.issubset(set(str(x or "") for x in raw_selected)):
            if "ext_epgscrapers_starzplay" in valid and "ext_epgscrapers_starzplay" not in raw_selected:
                raw_selected.append("ext_epgscrapers_starzplay")

        # RC66: Al Kass is promoted from beIN/Qatar fallback ownership to its
        # dedicated official direct feed. Complete direct-source installs get
        # it automatically; intentionally customized subsets remain untouched.
        rc65_direct = set(rc60_direct) | {"ext_epgscrapers_starzplay"}
        if old_version < 13 and rc65_direct.issubset(set(str(x or "") for x in raw_selected)):
            if "ext_epgscrapers_alkass" in valid and "ext_epgscrapers_alkass" not in raw_selected:
                raw_selected.append("ext_epgscrapers_alkass")

        # RC68: TunisiaTV and Tabie QMC joined the validated daily EPG-Scrapers
        # pipeline. Complete rc67 direct-source installs receive both; custom
        # source subsets remain untouched.
        rc67_direct = set(rc65_direct) | {"ext_epgscrapers_alkass"}
        if old_version < 14 and rc67_direct.issubset(set(str(x or "") for x in raw_selected)):
            for _sid in ("ext_epgscrapers_tunisiatv", "ext_epgscrapers_tabie"):
                if _sid in valid and _sid not in raw_selected:
                    raw_selected.append(_sid)

        # RC23 one-link Morocco migration.  Any previous SNRT/2M/Chada/Medi1
        # Smart Sources selection becomes the single GitHub-generated Morocco
        # Cloud feed.  Existing manual channel mappings are migrated separately
        # by MappingStore and the original local scraper modules remain installed
        # as a recovery path, but Import All no longer runs them on the receiver.
        legacy_ma = set(getattr(source_catalog, "LEGACY_MOROCCO_CATALOGUE_IDS", ()))
        cloud_ma = str(getattr(source_catalog, "MOROCCO_CLOUD_CATALOGUE_ID", "ext_epgmanager_morocco"))
        if any(str(x or "") in legacy_ma for x in raw_selected):
            raw_selected = [x for x in raw_selected if str(x or "") not in legacy_ma]
            if cloud_ma not in raw_selected:
                raw_selected.append(cloud_ma)
        selected = []
        seen = set()
        for sid in raw_selected:
            sid = str(sid or "")
            if sid and sid in valid and sid not in seen:
                seen.add(sid); selected.append(sid)
        # Parallel AR/EN OpenEPG shards can publish the same XMLTV IDs.  A
        # configuration containing both is unsafe because Native EPGImport can
        # let the last feed overwrite the first.  Migrate ambiguous old configs
        # to the Arabic shard; an explicit future toggle may still choose EN.
        selected = source_variant_policy.normalize_selected(selected)
        self.data["selected_ids"] = selected
        raw_pinned = self.data.get("pinned_ids")
        if not isinstance(raw_pinned, list):
            raw_pinned = []
        # Never fan one legacy pinned source into 29 pinned shards. Drop the
        # old monolithic pin and let the user pin the preferred new shard.
        raw_pinned = [x for x in raw_pinned if str(x or "") != legacy_mena]
        if any(str(x or "") in legacy_ma for x in raw_pinned):
            raw_pinned = [x for x in raw_pinned if str(x or "") not in legacy_ma]
            if cloud_ma not in raw_pinned:
                raw_pinned.append(cloud_ma)
        pinned = []
        seen_pins = set()
        for sid in raw_pinned:
            sid = str(sid or "")
            if sid and sid in valid and sid not in seen_pins:
                seen_pins.add(sid); pinned.append(sid)
        self.data["pinned_ids"] = pinned
        self.data["version"] = max(int(self.data.get("version") or 0), 14)
        self._sync_legacy_from_selected()

    def _sync_legacy_from_selected(self):
        """Keep old country UI/status meaningful without constraining beta13."""
        selected = set(self.data.get("selected_ids") or [])
        by_id = source_catalog.by_catalogue_id()
        countries = self.data.setdefault("countries", {})
        by_country = {}
        for sid in selected:
            item = by_id.get(sid) or {}
            for code in item.get("countries") or []:
                by_country.setdefault(str(code), []).append(item)
        for code in source_catalog.COUNTRY_NAMES:
            state = countries.setdefault(code, {"enabled": False, "source": source_catalog.default_source(code)})
            rows = by_country.get(code) or []
            state["enabled"] = bool(rows)
            if rows:
                first = rows[0]
                exact = "feed:%s" % first.get("id")
                if source_catalog.source_option(code, exact):
                    state["source"] = exact
                else:
                    state["source"] = source_catalog.source_for_provider(code, first.get("provider") or "")
                state["provider"] = str(first.get("provider") or "")

    def save(self):
        self._sync_legacy_from_selected()
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            _PREFS_CACHE[self.path] = {"sig": _path_sig(self.path), "data": copy.deepcopy(self.data)}
        except Exception:
            pass
        # 7.0.7: a Smart Sources change starts/queues the one global Channel-ID
        # boot worker. The UI itself never downloads an ID list on selection click.
        try:
            from . import smart_catalog_boot
            smart_catalog_boot.ensure_async(force=True)
        except Exception:
            pass
        return True

    # --- beta13 flat checkbox API -------------------------------------
    def selected_catalogue_ids(self):
        valid = set(x.get("id") for x in source_catalog.all_sources() if x.get("id"))
        rows = [x for x in (self.data.get("selected_ids") or []) if x in valid]
        return source_variant_policy.normalize_selected(rows)

    def is_catalogue_selected(self, source_id):
        return str(source_id or "") in set(self.data.get("selected_ids") or [])

    def set_selected_catalogue_ids(self, ids, save=True, explicit_choice=None):
        valid = set(x.get("id") for x in source_catalog.all_sources() if x.get("id"))
        out = []
        seen = set()
        for sid in ids or []:
            sid = str(sid or "")
            if sid and sid in valid and sid not in seen:
                seen.add(sid); out.append(sid)
        out = source_variant_policy.normalize_selected(out, explicit_choice=explicit_choice)
        self.data["selected_ids"] = out
        if save: self.save()
        return list(out)

    def toggle_catalogue(self, source_id, save=True):
        sid = str(source_id or "")
        selected = self.selected_catalogue_ids()
        if sid in selected:
            selected.remove(sid); state = False
            self.set_selected_catalogue_ids(selected, save=save)
        else:
            selected.append(sid); state = True
            # One explicit click selects exactly that language variant and
            # automatically disables its parallel AR/EN sibling.
            self.set_selected_catalogue_ids(selected, save=save, explicit_choice=sid)
        return state

    # --- beta66 pinned/favourite source API ---------------------------
    def pinned_catalogue_ids(self):
        valid = set(x.get("id") for x in source_catalog.all_sources() if x.get("id"))
        return [x for x in (self.data.get("pinned_ids") or []) if x in valid]

    def is_pinned(self, source_id):
        return str(source_id or "") in set(self.data.get("pinned_ids") or [])

    def set_pinned_catalogue_ids(self, ids, save=True):
        valid = set(x.get("id") for x in source_catalog.all_sources() if x.get("id"))
        rows, seen = [], set()
        for sid in ids or []:
            sid = str(sid or "")
            if sid and sid in valid and sid not in seen:
                seen.add(sid); rows.append(sid)
        self.data["pinned_ids"] = rows
        if save:
            self.save()
        return list(rows)

    def toggle_pin(self, source_id, save=True):
        sid = str(source_id or "")
        rows = self.pinned_catalogue_ids()
        if sid in rows:
            rows.remove(sid); state = False
        else:
            rows.append(sid); state = True
        self.set_pinned_catalogue_ids(rows, save=save)
        return state

    def selected_mapping_ids(self):
        ids = []; seen = set(); by_id = source_catalog.by_catalogue_id()
        for sid in self.selected_catalogue_ids():
            item = by_id.get(sid); mid = source_catalog.mapping_source_id(item)
            if mid and mid not in seen:
                seen.add(mid); ids.append(mid)
        return ids

    def selected_contexts(self):
        by_id = source_catalog.by_catalogue_id()
        return source_catalog.source_contexts_for_items([by_id.get(x) for x in self.selected_catalogue_ids() if by_id.get(x)])

    def enabled_choices(self):
        """Compatibility view for old code; one tuple per selected feed."""
        out = []
        by_id = source_catalog.by_catalogue_id()
        for sid in self.selected_catalogue_ids():
            item = by_id.get(sid) or {}
            countries = item.get("countries") or ["INT"]
            code = str(countries[0])
            exact = "feed:%s" % sid
            key = exact if source_catalog.source_option(code, exact) else source_catalog.source_for_provider(code, item.get("provider") or "")
            out.append((code, key))
        return out

    def enabled_display_choices(self):
        out = []
        by_id = source_catalog.by_catalogue_id()
        for sid in self.selected_catalogue_ids():
            item = by_id.get(sid) or {}
            countries = item.get("countries") or ["INT"]
            code = str(countries[0])
            out.append((code, str(item.get("name") or sid)))
        return out

    # --- legacy one-country API retained for compatibility ------------
    def rows(self):
        out = []
        for row in source_catalog.country_rows():
            code = row["code"]; state = self.data.get("countries", {}).get(code, {})
            source = str(state.get("source") or source_catalog.default_source(code)); option = source_catalog.source_option(code, source) or {}
            item = dict(row); item["enabled"] = bool(state.get("enabled", False)); item["source"] = source
            item["source_label"] = str(option.get("label") or source); item["provider"] = str(option.get("provider") or "")
            out.append(item)
        return out

    def get(self, code):
        code = str(code); row = self.data.setdefault("countries", {}).setdefault(code, {"enabled": False, "source": source_catalog.default_source(code)})
        source = str(row.get("source") or source_catalog.default_source(code)); row["source"] = source; row["provider"] = source_catalog.source_option_provider(code, source)
        return dict(row)

    def is_enabled(self, code): return bool(self.get(code).get("enabled"))
    def set_enabled(self, code, enabled):
        code = str(code); row = self.data.setdefault("countries", {}).setdefault(code, {})
        row["enabled"] = bool(enabled); row.setdefault("source", source_catalog.default_source(code)); row["provider"] = source_catalog.source_option_provider(code, row.get("source"))
        # Legacy toggles map to all feeds in the selected logical option.
        option = source_catalog.source_option(code, row.get("source")) or {}
        selected = self.selected_catalogue_ids()
        ids = [source_catalog.catalogue_source_id(x) for x in option.get("items") or []]
        if enabled:
            for sid in ids:
                if sid and sid not in selected: selected.append(sid)
        else:
            selected = [x for x in selected if x not in ids]
        self.set_selected_catalogue_ids(selected, save=False)
        return row["enabled"]
    def toggle(self, code): return self.set_enabled(code, not self.is_enabled(code))
    def get_source(self, code): return str(self.get(code).get("source") or source_catalog.default_source(code))
    def get_source_label(self, code): return source_catalog.source_option_label(code, self.get_source(code))
    def set_source(self, code, source_key):
        code = str(code); source_key = str(source_key or ""); option = source_catalog.source_option(code, source_key)
        if not option: raise ValueError("Source %s is not available for %s" % (source_key, code))
        row = self.data.setdefault("countries", {}).setdefault(code, {})
        old = source_catalog.source_option(code, row.get("source")) or {}
        selected = self.selected_catalogue_ids()
        old_ids = [source_catalog.catalogue_source_id(x) for x in old.get("items") or []]
        selected = [x for x in selected if x not in old_ids]
        row["source"] = str(option.get("key")); row["provider"] = str(option.get("provider") or ""); row.setdefault("enabled", False)
        if row.get("enabled"):
            for item in option.get("items") or []:
                sid = source_catalog.catalogue_source_id(item)
                if sid and sid not in selected: selected.append(sid)
        self.set_selected_catalogue_ids(selected, save=False)
        return row["source"]
    def cycle_source(self, code, direction=1):
        code = str(code); options = source_catalog.source_options(code)
        if not options: return ""
        keys = [str(x.get("key") or "") for x in options]; cur = self.get_source(code)
        try: idx = keys.index(cur)
        except ValueError: idx = 0
        return self.set_source(code, keys[(idx + (1 if direction >= 0 else -1)) % len(keys)])
    def get_provider(self, code): return source_catalog.source_option_provider(code, self.get_source(code))
    def set_provider(self, code, provider): return self.set_source(code, source_catalog.source_for_provider(code, provider))
    def cycle_provider(self, code, direction=1): return self.cycle_source(code, direction)


class FixedSourceSelection(object):
    """Read-only one-country/one-logical-source selection for source actions."""
    def __init__(self, country_code, source_key):
        self.country_code = str(country_code); option = source_catalog.source_option(self.country_code, source_key)
        if not option: raise ValueError("Unknown source %s for %s" % (source_key, country_code))
        self.source_key = str(option.get("key")); self.option = option
    def enabled_choices(self): return [(self.country_code, self.source_key)]
    def selected_catalogue_ids(self): return [source_catalog.catalogue_source_id(x) for x in self.option.get("items") or [] if source_catalog.catalogue_source_id(x)]
    def selected_mapping_ids(self): return [source_catalog.mapping_source_id(x) for x in self.option.get("items") or [] if source_catalog.mapping_source_id(x)]
    def selected_contexts(self): return source_catalog.source_contexts_for_choices(self.enabled_choices())


class FixedCatalogueSelection(object):
    """Read-only selection for exactly one Smart Sources catalogue feed.

    Used by the background maintainer so updating one source never expands to
    a provider bundle or triggers mapping/import work for unrelated feeds.
    """
    def __init__(self, source_id):
        self.source_id = str(source_id or "")
        self.item = source_catalog.by_catalogue_id().get(self.source_id)
        if not self.item:
            raise ValueError("Unknown catalogue source %s" % self.source_id)
    def selected_catalogue_ids(self):
        return [self.source_id]
    def selected_mapping_ids(self):
        mid = source_catalog.mapping_source_id(self.item)
        return [mid] if mid else []
    def selected_contexts(self):
        return source_catalog.source_contexts_for_items([self.item])
    def enabled_choices(self):
        countries = self.item.get("countries") or ["INT"]
        code = str(countries[0])
        return [(code, "feed:%s" % self.source_id)]


class FixedCatalogueSelectionMany(object):
    """Read-only selection for an explicit batch of Smart Sources feeds.

    Used by beta86 batch Refresh / Import so the UI can submit one job instead
    of serially launching a new EPG cycle for every marked row.  The normal
    persistent ON/OFF selection is not modified.
    """
    def __init__(self, source_ids):
        by_id = source_catalog.by_catalogue_id()
        seen = set(); ids = []
        for raw in source_ids or []:
            sid = str(raw or "")
            if sid and sid in by_id and sid not in seen:
                seen.add(sid); ids.append(sid)
        if not ids:
            raise ValueError("No valid catalogue sources selected")
        # Explicit batch jobs must obey the same mutually-exclusive AR/EN feed
        # policy as normal source selection.  Otherwise a user could mark Saudi
        # 5+6 and reintroduce last-import-wins collisions.
        try:
            from . import source_variant_policy
            ids = source_variant_policy.normalize_selected(ids)
        except Exception:
            pass
        self.source_ids = ids
    def selected_catalogue_ids(self):
        return list(self.source_ids)
    def selected_mapping_ids(self):
        by_id = source_catalog.by_catalogue_id(); out=[]
        for sid in self.source_ids:
            mid = source_catalog.mapping_source_id(by_id.get(sid) or {})
            if mid and mid not in out: out.append(mid)
        return out
    def selected_contexts(self):
        by_id = source_catalog.by_catalogue_id()
        return source_catalog.source_contexts_for_items([by_id[sid] for sid in self.source_ids if sid in by_id])
    def enabled_choices(self):
        by_id = source_catalog.by_catalogue_id(); out=[]
        for sid in self.source_ids:
            item=by_id.get(sid) or {}; countries=item.get("countries") or ["INT"]
            out.append((str(countries[0]), "feed:%s" % sid))
        return out
