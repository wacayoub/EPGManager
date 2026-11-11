# -*- coding: utf-8 -*-
"""Persistent channel mapping overrides for EPG Manager standalone mode.

The store is intentionally simple JSON so mappings survive plugin upgrades and
can be inspected/recovered without a database.  Keys are XMLTV channel ids and
values are Enigma2 service references.
"""
import json
import os
import time
import shutil
import copy

from .logger import get_logger

log = get_logger(__name__)
DEFAULT_PATH = "/etc/enigma2/epgmanager_mappings.json"
_STORE_CACHE = {}
_STORE_INSTANCES = {}

_RETIRED_SOURCE_PREFIXES = ("ext_iptv_epg_", "ext_iptvepg_")
_RETIRED_SOURCE_IDS = set(("almajd", "local_almajd"))

def _retired_mapping_key(key):
    source_id = str(key or "").split("::", 1)[0].lower()
    return source_id in _RETIRED_SOURCE_IDS or any(source_id.startswith(p) for p in _RETIRED_SOURCE_PREFIXES)


def _path_sig(path):
    try:
        st = os.stat(path)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return (0, 0)



class MappingStore(object):
    def __new__(cls, path=DEFAULT_PATH):
        # RC25 Performance: one resident store per mapping file. Smart Mapping,
        # Mapping Health and helper workers previously reparsed the same JSON on
        # every ``MappingStore()`` construction even when the file had not changed.
        # Returning the resident instance eliminates that repeated JSON rebuild.
        key = os.path.abspath(str(path or DEFAULT_PATH))
        inst = _STORE_INSTANCES.get(key)
        if inst is None:
            inst = object.__new__(cls)
            inst._store_key = key
            inst._initialized = False
            inst._loaded_sig = None
            _STORE_INSTANCES[key] = inst
        return inst

    def __init__(self, path=DEFAULT_PATH):
        path = os.path.abspath(str(path or DEFAULT_PATH))
        sig = _path_sig(path)
        if getattr(self, "_initialized", False) and getattr(self, "_loaded_sig", None) == sig:
            return
        self.path = path
        cached = _STORE_CACHE.get(path)
        if cached and cached.get("sig") == sig:
            try:
                if cached.get("raw") is not None:
                    self._data = json.loads(cached.get("raw") or "{}")
                else:
                    self._data = copy.deepcopy(cached.get("data") or {})
            except Exception:
                self._data = self._load()
        else:
            self._data = self._load()
        self._data.setdefault("version", 3)
        self._data.setdefault("mappings", {})
        self._data.setdefault("history", [])
        self._data.setdefault("blocked_refs", [])
        self._data.setdefault("ignored_refs", [])
        self._data.setdefault("orphaned_mappings", {})
        retired_changed = self._drop_retired_sources()
        morocco_changed = self._migrate_morocco_cloud_mappings()
        orphan_changed = self._quarantine_orphaned_sources()
        # 7.0.1: legacy beta builds stored complete mapping snapshots for every
        # manual edit.  On a Zero 4K this could turn a tiny one-channel save into
        # multi-megabyte JSON serialization.  Compact only *old undo history* in
        # memory; live mappings are never touched.  The next normal save persists
        # the compact form.
        self._compact_legacy_history()
        if retired_changed or morocco_changed or orphan_changed:
            self._save()
        try:
            current_sig = _path_sig(path)
            _STORE_CACHE[path] = {"sig": current_sig, "raw": json.dumps(self._data, separators=(",", ":"), ensure_ascii=False)}
            self._loaded_sig = current_sig
        except Exception:
            self._loaded_sig = sig
        self._initialized = True

    def _load(self):
        if not os.path.exists(self.path):
            return {"version": 3, "mappings": {}, "history": [], "blocked_refs": [], "ignored_refs": [], "orphaned_mappings": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("mapping file root is not an object")
            data.setdefault("version", 1)
            data.setdefault("mappings", {})
            data.setdefault("history", [])
            data.setdefault("blocked_refs", [])
            data.setdefault("ignored_refs", [])
            data.setdefault("orphaned_mappings", {})
            return data
        except Exception as exc:
            log.warning("Could not load channel mapping store %s: %s", self.path, exc)
            return {"version": 3, "mappings": {}, "history": [], "blocked_refs": [], "ignored_refs": [], "orphaned_mappings": {}}

    def _drop_retired_sources(self):
        """Remove ownership claims for source backends intentionally retired from the catalogue."""
        mappings = self._data.get("mappings") or {}
        stale = [key for key in list(mappings.keys()) if _retired_mapping_key(key)]
        if not stale:
            return False
        for key in stale:
            mappings.pop(key, None)
        self._data["mappings"] = mappings
        # Old undo snapshots can re-introduce retired claims; drop them on this migration.
        self._data["history"] = []
        return True

    def _migrate_morocco_cloud_mappings(self):
        """Copy legacy Morocco source ownership to the RC23 one-link cloud feed.

        Channel IDs themselves are intentionally unchanged in morocco.xml.gz, so
        a manual/learned mapping can be reused byte-for-byte.  Keep the original
        entries as rollback data; only add the cloud counterpart when absent.
        """
        cloud_id = "ext_epgmanager_morocco"
        legacy = set(("medi1tv", "chada_2m", "snrt", "arryadia"))
        mappings = self._data.setdefault("mappings", {})
        additions = {}
        for key, record in list(mappings.items()):
            text = str(key or "")
            if "::" not in text:
                continue
            sid, cid = text.split("::", 1)
            if sid.lower() not in legacy or not cid:
                continue
            new_key = self._key(cloud_id, cid)
            if new_key in mappings or new_key in additions:
                continue
            additions[new_key] = copy.deepcopy(record)
        if not additions:
            return False
        mappings.update(additions)
        self._data.setdefault("history", []).append({
            "ts": int(time.time()), "action": "migrate_morocco_cloud",
            "count": len(additions), "target": cloud_id,
        })
        self._data["history"] = self._data["history"][-100:]
        return True

    def _quarantine_orphaned_sources(self):
        """Move mappings for removed external catalogue sources out of active ownership.

        Only ``ext_*`` source IDs are eligible. Unknown custom/local mapping IDs
        are preserved. Quarantined rows remain recoverable in the same JSON file
        and are never considered active by Import All / Smart Mapping.
        """
        try:
            from . import source_catalog
            valid = set(str(x or "").lower() for x in source_catalog.by_mapping_id().keys())
        except Exception:
            return False
        mappings = self._data.get("mappings") or {}
        orphans = self._data.setdefault("orphaned_mappings", {})
        moved = 0
        now = int(time.time())
        for key in list(mappings.keys()):
            source_id = str(key or "").split("::", 1)[0].lower()
            if not source_id.startswith("ext_") or source_id in valid:
                continue
            record = mappings.pop(key, None)
            if record is None:
                continue
            orphans[key] = {"mapping": record, "source_id": source_id,
                            "orphaned_at": now, "reason": "source-not-in-catalogue"}
            moved += 1
        if moved:
            # Bound stale recovery history: keep at most 500 removed claims.
            if len(orphans) > 500:
                ordered = sorted(orphans.items(), key=lambda kv: int((kv[1] or {}).get("orphaned_at") or 0), reverse=True)
                self._data["orphaned_mappings"] = dict(ordered[:500])
            self._data.setdefault("history", []).append({"ts": now, "action": "quarantine_orphans", "count": moved})
            self._data["history"] = self._data["history"][-100:]
            return True
        return False

    def orphaned(self):
        return dict(self._data.get("orphaned_mappings") or {})

    def _compact_legacy_history(self):
        """Bound old full-snapshot undo history without touching live mappings.

        beta builds could retain 30 complete copies of the mapping table.  Keep
        the newest three bulk snapshots plus the newest Auto Repair snapshot and
        twenty lightweight events.  New manual edits use delta history instead.
        """
        hist = list(self._data.get("history") or [])
        bulk_idx = [i for i, item in enumerate(hist) if str((item or {}).get("action") or "") == "bulk_snapshot"]
        if len(hist) <= 21 and len(bulk_idx) <= 1:
            return False
        keep = set(bulk_idx[-1:])
        for i in reversed(bulk_idx):
            if str((hist[i] or {}).get("label") or "").lower().startswith("auto repair"):
                keep.add(i)
                break
        light = [i for i, item in enumerate(hist) if str((item or {}).get("action") or "") != "bulk_snapshot"]
        keep.update(light[-20:])
        compact = [item for i, item in enumerate(hist) if i in keep]
        if len(compact) == len(hist):
            return False
        self._data["history"] = compact[-24:]
        return True

    def _save(self):
        # Universal RC: prevent consecutive Auto Repair/Bulk operations from
        # accumulating complete mapping-table snapshots in RAM/flash.
        self._compact_legacy_history()
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        # Serialize once.  Older builds json.dump()'d to disk and then deep-copied
        # the entire mapping/history tree into _STORE_CACHE, doubling CPU/RAM.
        payload = json.dumps(self._data, separators=(",", ":"), ensure_ascii=False)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)
        try:
            current_sig = _path_sig(self.path)
            _STORE_CACHE[self.path] = {"sig": current_sig, "raw": payload}
            self._loaded_sig = current_sig
        except Exception:
            pass

    @staticmethod
    def _key(source_id, channel_id):
        return "%s::%s" % (str(source_id or "").lower(), str(channel_id or "").lower())

    def get(self, source_id, channel_id):
        return self._data.get("mappings", {}).get(self._key(source_id, channel_id))

    def set(self, source_id, channel_id, refs, mode="manual", display_name=None):
        if isinstance(refs, str):
            refs = [refs]
        clean = []
        seen = set()
        for ref in refs or []:
            ref = str(ref).strip()
            if ref and ref not in seen:
                seen.add(ref)
                clean.append(ref)
        # A new explicit mapping cancels any previous manual-unmap tombstone.
        blocked = list(self._data.setdefault("blocked_refs", []))
        if blocked:
            self._data["blocked_refs"] = [old for old in blocked if not any(self._refs_match(old, ref) for ref in clean)]
        ignored = list(self._data.setdefault("ignored_refs", []))
        if ignored:
            self._data["ignored_refs"] = [old for old in ignored if not any(self._refs_match(old, ref) for ref in clean)]
        key = self._key(source_id, channel_id)
        previous = self._data.setdefault("mappings", {}).get(key)
        self._data.setdefault("history", []).append({"ts": int(time.time()), "action": "set", "key": key, "previous": previous})
        self._data["history"] = self._data["history"][-100:]
        self._data.setdefault("mappings", {})[key] = {
            "refs": clean,
            "mode": mode,
            "display_name": display_name or "",
        }
        self._save()
        try:
            from . import srp_channel_map
            srp_channel_map.apply_mapping_override(source_id, channel_id, clean, remove=False)
        except Exception:
            pass
        return clean

    def remove(self, source_id, channel_id):
        key = self._key(source_id, channel_id)
        if key in self._data.get("mappings", {}):
            previous = self._data["mappings"].get(key)
            self._data.setdefault("history", []).append({"ts": int(time.time()), "action": "remove", "key": key, "previous": previous})
            self._data["history"] = self._data["history"][-100:]
            del self._data["mappings"][key]
            self._save()
            try:
                from . import srp_channel_map
                srp_channel_map.apply_mapping_override(source_id, channel_id, [], remove=True)
            except Exception:
                pass
            return True
        return False

    def all(self):
        return dict(self._data.get("mappings", {}))

    def all_readonly(self):
        """Return the live mapping dictionary for read-only hot paths.

        Import All can contain tens of thousands of rows.  Returning a shallow
        copy several times per import wastes RAM/CPU on small receivers.  Callers
        using this view MUST NOT mutate it.
        """
        return self._data.get("mappings", {})

    def bulk_apply_auto(self, records, source_ids=None, remove_stale=True):
        """Apply automatic mappings in one atomic write; preserve manual overrides."""
        mappings = self._data.setdefault("mappings", {})
        selected_sources = set(str(x or "").lower() for x in (source_ids or []))
        incoming = {}
        for row in records or []:
            source_id=str(row.get("source_id") or "").lower(); channel_id=str(row.get("channel_id") or "").lower()
            if not source_id or not channel_id: continue
            key=self._key(source_id,channel_id); existing=mappings.get(key) or {}
            if str(existing.get("mode") or "").lower()=="manual": continue
            refs=[]; seen=set()
            for ref in row.get("refs") or []:
                ref=str(ref or "").strip()
                if ref and ref not in seen: seen.add(ref); refs.append(ref)
            if not refs: continue
            incoming[key]={"refs":refs,"mode":str(row.get("mode") or "auto"),"display_name":str(row.get("display_name") or ""),
                           "confidence":int(row.get("confidence") or 0),"reason":str(row.get("reason") or ""),
                           "country":str(row.get("country") or ""),"provider":str(row.get("provider") or "")}
        changed=0
        if remove_stale and selected_sources:
            for key in list(mappings.keys()):
                source_id=str(key).split("::",1)[0].lower(); current=mappings.get(key) or {}
                if source_id in selected_sources and str(current.get("mode") or "").lower()!="manual" and key not in incoming:
                    del mappings[key]; changed+=1
        for key,value in incoming.items():
            if mappings.get(key)!=value: mappings[key]=value; changed+=1
        if changed:
            self._data.setdefault("history",[]).append({"ts":int(time.time()),"action":"bulk_auto","count":len(incoming),"changed":changed})
            self._data["history"]=self._data["history"][-100:]; self._save()
        return {"applied":len(incoming),"changed":changed}

    def prune_invalid_auto_ids(self, valid_ids_by_source=None, dead_ids_by_source=None, label="Direct ID repair"):
        """Remove stale/dead AUTOMATIC ID ownership while preserving manual locks.

        ``valid_ids_by_source`` is the current XMLTV header universe per source.
        ``dead_ids_by_source`` contains IDs proven by monitoring to have zero
        future EPG.  Manual mappings are never removed here; they remain visible
        to the operator but the SRP health gate can keep a dead ID out of active
        import until real EPG returns.
        """
        valid = dict((str(k or "").lower(), set(str(x or "").lower() for x in (v or []) if str(x or "").strip()))
                     for k, v in (valid_ids_by_source or {}).items())
        dead = dict((str(k or "").lower(), set(str(x or "").lower() for x in (v or []) if str(x or "").strip()))
                    for k, v in (dead_ids_by_source or {}).items())
        mappings = self._data.setdefault("mappings", {})
        before_records = {}
        affected = []
        reasons = {"missing_id": 0, "no_epg": 0}
        for key in list(mappings.keys()):
            saved = mappings.get(key) or {}
            if str(saved.get("mode") or "").lower() == "manual":
                continue
            text = str(key or "")
            if "::" not in text:
                continue
            sid, cid = text.split("::", 1)
            sid = sid.lower(); cid = cid.lower()
            if sid not in valid and sid not in dead:
                continue
            reason = ""
            if cid in dead.get(sid, set()):
                reason = "no_epg"
            elif sid in valid and valid.get(sid) and cid not in valid.get(sid, set()):
                reason = "missing_id"
            if not reason:
                continue
            before_records[text] = copy.deepcopy(saved)
            affected.append(text)
            mappings.pop(text, None)
            reasons[reason] += 1
        if affected:
            self._data.setdefault("history", []).append({
                "ts": int(time.time()), "action": "prune_invalid_auto",
                "label": str(label or "Direct ID repair"),
                "before_records": before_records, "affected_keys": affected,
            })
            self._data["history"] = self._data["history"][-100:]
            self._save()
        return {"removed": len(affected), "missing_id": reasons["missing_id"], "no_epg": reasons["no_epg"]}

    def manual_refs(self):
        refs=set()
        for record in self._data.get("mappings",{}).values():
            if str((record or {}).get("mode") or "").lower()!="manual": continue
            refs.update(str(x) for x in ((record or {}).get("refs") or []) if x)
        return refs


    def ignored_refs(self):
        """Receiver services explicitly marked NO EPG / IGNORE by the user."""
        return list(self._data.get("ignored_refs") or [])

    def is_ignored(self, ref):
        wanted = self._ref_lookup_keys(ref)
        if not wanted:
            return False
        for saved in self._data.get("ignored_refs") or []:
            if wanted.intersection(self._ref_lookup_keys(saved)):
                return True
        return False

    def ignore_ref(self, ref):
        """Exclude one receiver service from automatic repair/health expectations.

        IGNORE is intentionally separate from a normal manual Unmap tombstone so
        Mapping Repair can exclude it from the health denominator and the user
        can restore it later without losing the meaning of other unmap choices.
        Existing deterministic code sees ignored refs through ``blocked_refs()``.
        """
        raw = str(ref or "").strip()
        if not raw:
            return False
        rows = list(self._data.setdefault("ignored_refs", []))
        if any(self._refs_match(raw, old) for old in rows):
            return False
        # IGNORE and manual UNMAPPED are two different user intentions.  Move
        # the ref out of the normal tombstone set while ignored, otherwise a
        # later Restore-to-Repair would still remain silently blocked.
        blocked = list(self._data.setdefault("blocked_refs", []))
        previous_blocked = [old for old in blocked if self._refs_match(raw, old)]
        if previous_blocked:
            self._data["blocked_refs"] = [old for old in blocked if not self._refs_match(raw, old)]
        rows.append(raw)
        self._data["ignored_refs"] = rows[-10000:]
        self._data.setdefault("history", []).append({
            "ts": int(time.time()), "action": "ignore_ref", "ref": raw,
            "previous_blocked": previous_blocked})
        self._data["history"] = self._data["history"][-100:]
        self._save()
        return True

    def unignore_ref(self, ref, save=True):
        raw = str(ref or "").strip()
        if not raw:
            return False
        before = list(self._data.setdefault("ignored_refs", []))
        after = [old for old in before if not self._refs_match(raw, old)]
        if after == before:
            return False
        self._data["ignored_refs"] = after
        if save:
            self._data.setdefault("history", []).append({"ts": int(time.time()), "action": "unignore_ref", "ref": raw})
            self._data["history"] = self._data["history"][-100:]
            self._save()
        return True

    def blocked_refs(self):
        """Return refs that automatic/deterministic mapping must not reclaim.

        This is the union of explicit UNMAPPED tombstones and NO EPG / IGNORE
        refs. Existing callers therefore become IGNORE-aware without a second
        mapping engine or compatibility branch.
        """
        rows = list(self._data.get("blocked_refs") or []) + list(self._data.get("ignored_refs") or [])
        out = []
        for ref in rows:
            if ref and not any(self._refs_match(ref, old) for old in out):
                out.append(ref)
        return out

    def is_blocked(self, ref):
        wanted = self._ref_lookup_keys(ref)
        if not wanted:
            return False
        for saved in self.blocked_refs():
            if wanted.intersection(self._ref_lookup_keys(saved)):
                return True
        return False

    def block_ref(self, ref):
        raw = str(ref or "").strip()
        if not raw:
            return False
        rows = list(self._data.setdefault("blocked_refs", []))
        if any(self._refs_match(raw, old) for old in rows):
            return False
        rows.append(raw)
        self._data["blocked_refs"] = rows[-10000:]
        self._data.setdefault("history", []).append({"ts": int(time.time()), "action": "block_ref", "ref": raw})
        self._data["history"] = self._data["history"][-100:]
        self._save()
        return True

    def unblock_ref(self, ref, save=True):
        raw = str(ref or "").strip()
        if not raw:
            return False
        before = list(self._data.setdefault("blocked_refs", []))
        after = [old for old in before if not self._refs_match(raw, old)]
        if after == before:
            return False
        self._data["blocked_refs"] = after
        if save:
            self._save()
        return True


    def backup(self, destination=None):
        """Create a safe copy of the current mapping file."""
        if not os.path.exists(self.path):
            self._save()
        destination = destination or (self.path + '.bak')
        shutil.copy2(self.path, destination)
        return destination


    @staticmethod
    def _prune_generated_source_refs(source_id, affected_refs):
        """Remove receiver refs from an old generated source map.

        A manual source replacement must change Native EPGImport ownership too,
        not only the JSON UI override.  This edits only the generated
        ``*.channels.xml`` cache referenced by the Golden SRP index; Golden Python
        mapping files are never modified.
        """
        source_id = str(source_id or "").strip()
        raw_refs = [str(x or "").strip() for x in (affected_refs or []) if str(x or "").strip()]
        if not source_id or not raw_refs:
            return {"changed": 0, "backup": "", "path": ""}
        try:
            from . import srp_channel_map, channel_registry
            row = (srp_channel_map.load_index().get("sources") or {}).get(source_id)
            if row is None:
                row = (srp_channel_map.load_index().get("sources") or {}).get(source_id.lower())
            path = str((row or {}).get("map_path") or "")
            if not path or not os.path.isfile(path):
                return {"changed": 0, "backup": "", "path": path}
            mapping = srp_channel_map._parse_channels_xml_preserve(path)
            wanted = set()
            for ref in raw_refs:
                wanted.add(ref)
                try:
                    wanted.add(channel_registry.canonical_service_ref(ref) or ref)
                except Exception:
                    pass
            changed = 0
            for cid in list(mapping.keys()):
                before = list(mapping.get(cid) or [])
                after = []
                for ref in before:
                    canon = ref
                    try:
                        canon = channel_registry.canonical_service_ref(ref) or ref
                    except Exception:
                        pass
                    if ref in wanted or canon in wanted:
                        changed += 1
                        continue
                    after.append(ref)
                if after:
                    mapping[cid] = after
                else:
                    mapping.pop(cid, None)
            backup = ""
            if changed:
                backup = "%s.pre-bulk-%d" % (path, int(time.time()))
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    backup = ""
                srp_channel_map.write_channels_xml(mapping, path)
            return {"changed": changed, "backup": backup, "path": path}
        except Exception as exc:
            log.warning("Could not prune old source map %s: %s", source_id, exc)
            return {"changed": 0, "backup": "", "path": ""}

    @staticmethod
    def _ref_lookup_keys(ref):
        """Canonical forms used when one receiver service changes EPG owner."""
        raw = str(ref or "").strip()
        if not raw:
            return set()
        out = set([raw.lower(), raw.rstrip(":").lower()])
        try:
            from . import channel_registry
            canon = str(channel_registry.canonical_service_ref(raw) or "").strip()
            if canon:
                out.add(canon.lower())
                out.add(canon.rstrip(":").lower())
        except Exception:
            pass
        return set(x for x in out if x)

    @classmethod
    def _refs_match(cls, left, right):
        return bool(cls._ref_lookup_keys(left).intersection(cls._ref_lookup_keys(right)))

    @staticmethod
    def _canonical_ref_key(ref):
        """Stable one-owner key for a receiver Service Reference."""
        raw = str(ref or "").strip()
        if not raw:
            return ""
        try:
            from . import channel_registry
            canon = str(channel_registry.canonical_service_ref(raw) or "").strip()
        except Exception:
            canon = ""
        return (canon or raw).rstrip(":").lower()

    @staticmethod
    def _merge_generated_target_refs(source_id, channel_id, refs):
        """Merge refs into the target generated channels map without losing siblings.

        ``srp_channel_map.apply_mapping_override`` intentionally replaces one
        channel-id record.  For a manual ownership move we need merge semantics
        so one Al Aoula/2M EPG id can continue feeding multiple receiver variants.
        """
        source_id = str(source_id or "").strip()
        channel_id = str(channel_id or "").strip()
        clean_refs = [str(x or "").strip() for x in (refs or []) if str(x or "").strip()]
        if not source_id or not channel_id or not clean_refs:
            return {"changed": 0, "backup": "", "path": ""}
        try:
            from . import srp_channel_map, channel_registry
            sources = (srp_channel_map.load_index().get("sources") or {})
            row = sources.get(source_id) or sources.get(source_id.lower())
            if row is None:
                for key, value in sources.items():
                    if str(key or "").lower() == source_id.lower():
                        row = value; break
            path = str((row or {}).get("map_path") or "")
            if not path or not os.path.isfile(path):
                return {"changed": 0, "backup": "", "path": path}
            mapping = srp_channel_map._parse_channels_xml_preserve(path)
            actual_cid = next((key for key in mapping if str(key).lower() == channel_id.lower()), channel_id)
            current = list(mapping.get(actual_cid) or [])
            signatures = set()
            for ref in current:
                raw = str(ref or "").strip()
                canon = ""
                try:
                    canon = str(channel_registry.canonical_service_ref(raw) or "").strip()
                except Exception:
                    pass
                signatures.add((canon or raw).rstrip(":").lower())
            changed = 0
            for ref in clean_refs:
                canon = ""
                try:
                    canon = str(channel_registry.canonical_service_ref(ref) or "").strip()
                except Exception:
                    pass
                sig = (canon or ref).rstrip(":").lower()
                if sig and sig not in signatures:
                    current.append(ref)
                    signatures.add(sig)
                    changed += 1
            backup = ""
            if changed:
                backup = "%s.pre-exclusive-%d" % (path, int(time.time()))
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    backup = ""
                mapping[actual_cid] = current
                srp_channel_map.write_channels_xml(mapping, path)
            return {"changed": changed, "backup": backup, "path": path}
        except Exception as exc:
            log.warning("Could not merge target source map %s/%s: %s", source_id, channel_id, exc)
            return {"changed": 0, "backup": "", "path": ""}

    @staticmethod
    def _merge_generated_target_refs_bulk(rows):
        """Merge many automatic targets with one parse/write per source.

        beta135 performance path.  The previous Auto Repair called
        _merge_generated_target_refs() once per mapped channel, so 60 mappings
        into the same provider could parse and rewrite the same channels.xml 60
        times.  This method groups by source, parses once, applies all channel
        IDs in memory, then writes once while preserving the same backup/undo
        semantics.
        """
        grouped = {}
        for row in rows or []:
            sid = str((row or {}).get("source_id") or "").strip().lower()
            cid = str((row or {}).get("channel_id") or "").strip()
            refs = [str(x or "").strip() for x in ((row or {}).get("refs") or []) if str(x or "").strip()]
            if not sid or not cid or not refs:
                continue
            grouped.setdefault(sid, []).append((cid, refs))
        if not grouped:
            return []
        out = []
        try:
            from . import srp_channel_map, channel_registry
            sources = (srp_channel_map.load_index().get("sources") or {})
        except Exception as exc:
            log.warning("Could not load generated source index for bulk target merge: %s", exc)
            return []

        for sid, targets in grouped.items():
            try:
                row = sources.get(sid) or sources.get(sid.lower())
                if row is None:
                    for key, value in sources.items():
                        if str(key or "").lower() == sid:
                            row = value; break
                path = str((row or {}).get("map_path") or "")
                if not path or not os.path.isfile(path):
                    out.append({"source_id": sid, "changed": 0, "backup": "", "path": path})
                    continue
                mapping = srp_channel_map._parse_channels_xml_preserve(path)
                key_lookup = {str(key).lower(): key for key in mapping.keys()}
                changed = 0
                for cid, refs in targets:
                    actual_cid = key_lookup.get(str(cid).lower(), cid)
                    current = list(mapping.get(actual_cid) or [])
                    signatures = set()
                    for ref in current:
                        raw = str(ref or "").strip()
                        try:
                            canon = str(channel_registry.canonical_service_ref(raw) or "").strip()
                        except Exception:
                            canon = ""
                        signatures.add((canon or raw).rstrip(":").lower())
                    local_changed = 0
                    for ref in refs:
                        try:
                            canon = str(channel_registry.canonical_service_ref(ref) or "").strip()
                        except Exception:
                            canon = ""
                        sig = (canon or ref).rstrip(":").lower()
                        if sig and sig not in signatures:
                            current.append(ref)
                            signatures.add(sig)
                            local_changed += 1
                    if local_changed:
                        mapping[actual_cid] = current
                        key_lookup[str(actual_cid).lower()] = actual_cid
                        changed += local_changed
                backup = ""
                if changed:
                    backup = "%s.pre-exclusive-bulk-%d" % (path, int(time.time()))
                    try:
                        shutil.copy2(path, backup)
                    except Exception:
                        backup = ""
                    srp_channel_map.write_channels_xml(mapping, path)
                out.append({"source_id": sid, "changed": changed, "backup": backup, "path": path})
            except Exception as exc:
                log.warning("Could not bulk merge target source map %s: %s", sid, exc)
                out.append({"source_id": sid, "changed": 0, "backup": "", "path": ""})
        return out

    def assign_exclusive(self, source_id, channel_id, refs, display_name=None,
                         old_source_ids=None, label="Manual Smart Mapping",
                         defer_generated=False, mode="manual"):
        """Give receiver refs one explicit EPG owner.

        ``mode`` defaults to MANUAL for compatibility, but 8.2.1 also uses this
        path for one-channel SmartMatch so every write obeys the same exclusive
        receiver-owner invariant.

        7.0.1 keeps the user-visible save path intentionally tiny: JSON ownership
        is committed immediately with a delta undo record.  The expensive derived
        ``*.channels.xml`` synchronization can be deferred by the Smart Mapping UI
        to a serial background worker, keeping the remote-control workflow fluid.
        """
        if isinstance(refs, str):
            refs = [refs]
        clean_refs = []
        for ref in refs or []:
            ref = str(ref or "").strip()
            if ref and all(not self._refs_match(ref, old) for old in clean_refs):
                clean_refs.append(ref)
        sid = str(source_id or "").strip().lower()
        canonical_cid = str(channel_id or "").strip()
        cid = canonical_cid.lower()
        mode_value = str(mode or "manual").strip().lower() or "manual"
        if not sid or not cid or not clean_refs:
            return {"changed": 0, "records": 0}

        blocked = list(self._data.setdefault("blocked_refs", []))
        if blocked:
            self._data["blocked_refs"] = [old for old in blocked if not any(self._refs_match(old, ref) for ref in clean_refs)]
        ignored = list(self._data.setdefault("ignored_refs", []))
        if ignored:
            self._data["ignored_refs"] = [old for old in ignored if not any(self._refs_match(old, ref) for ref in clean_refs)]

        mappings = self._data.setdefault("mappings", {})
        target_key = self._key(sid, cid)
        source_ids = set(str(x or "").strip().lower() for x in (old_source_ids or []) if str(x or "").strip())

        # Capture only records that can actually change.  This replaces the old
        # copy.deepcopy(mappings) full snapshot, which was the largest manual-map
        # latency source on mapping tables with hundreds/thousands of channels.
        before_records = {}
        affected_keys = set([target_key])
        for key, record in list(mappings.items()):
            refs_now = list((record or {}).get("refs") or [])
            if key == target_key or any(any(self._refs_match(existing, wanted) for wanted in clean_refs) for existing in refs_now):
                affected_keys.add(key)
                before_records[key] = copy.deepcopy(record)
                if "::" in str(key):
                    source_ids.add(str(key).split("::", 1)[0].lower())
        if target_key not in before_records:
            before_records[target_key] = None

        changed = 0
        for key in list(affected_keys):
            if key not in mappings:
                continue
            rec = dict(mappings.get(key) or {})
            before = list(rec.get("refs") or [])
            after = [r for r in before if not any(self._refs_match(r, wanted) for wanted in clean_refs)]
            if after == before:
                continue
            changed += 1
            if after:
                rec["refs"] = after
                mappings[key] = rec
            else:
                mappings.pop(key, None)

        # Preserve receiver siblings already sharing the same target ID.  The
        # exclusivity rule is per receiver ref, not per XMLTV ID: several HD/SD
        # receiver variants may legitimately consume one EPG ID.
        existing_target = before_records.get(target_key) or {}
        target_refs = []
        for ref in existing_target.get("refs") or []:
            if not any(self._refs_match(ref, wanted) for wanted in clean_refs):
                target_refs.append(str(ref))
        for ref in clean_refs:
            if all(not self._refs_match(ref, old) for old in target_refs):
                target_refs.append(ref)
        # Never silently downgrade an existing MANUAL owner through an automatic
        # caller. Normal SmartMatch already protects manual refs; this is a final
        # store-level guard for races/legacy callers.
        existing_mode = str(existing_target.get("mode") or "").lower()
        final_mode = "manual" if existing_mode == "manual" and mode_value != "manual" else mode_value
        value = {"refs": target_refs, "mode": final_mode, "display_name": display_name or channel_id or ""}
        if mappings.get(target_key) != value:
            mappings[target_key] = value
            changed += 1

        generated_backups = []
        target_changed = 0
        if not defer_generated:
            for old_sid in sorted(source_ids):
                meta = self._prune_generated_source_refs(old_sid, clean_refs)
                if (meta or {}).get("backup") and (meta or {}).get("path"):
                    generated_backups.append({"source_id": old_sid,
                                              "backup": str(meta.get("backup")),
                                              "path": str(meta.get("path"))})
            target_meta = self._merge_generated_target_refs(sid, canonical_cid, target_refs)
            target_changed = int((target_meta or {}).get("changed") or 0)
            tpath = str((target_meta or {}).get("path") or "")
            tbackup = str((target_meta or {}).get("backup") or "")
            if tbackup and tpath and not any(x.get("path") == tpath for x in generated_backups):
                generated_backups.append({"source_id": sid, "backup": tbackup, "path": tpath})

        history_id = "%d-%s-%s" % (int(time.time() * 1000), sid[:16], cid[:24])
        if changed or target_changed:
            self._data.setdefault("history", []).append({
                "ts": int(time.time()), "action": "manual_delta",
                "history_id": history_id,
                "label": str(label or "Manual Smart Mapping"),
                "affected_keys": sorted(affected_keys),
                "before_records": before_records,
                "generated_backups": generated_backups})
            self._data["history"] = self._data["history"][-40:]
            self._save()

        sync_job = None
        if defer_generated and (changed or source_ids):
            sync_job = {
                "history_id": history_id,
                "source_id": sid,
                "channel_id": canonical_cid,
                "refs": list(clean_refs),
                "target_refs": list(target_refs),
                "old_source_ids": sorted(source_ids),
            }
        return {"changed": changed, "records": 1,
                "generated_maps": len(generated_backups),
                "target_map_changed": target_changed,
                "history_id": history_id,
                "sync_job": sync_job}

    def sync_generated_assignment(self, job):
        """Synchronize derived Native EPGImport maps for one saved manual edit.

        This method performs filesystem-only work and is safe to run in the
        Smart Mapping serial background worker.  It first verifies that the
        requested target still owns the refs, so an old queued job cannot undo a
        newer manual correction.
        """
        job = dict(job or {})
        sid = str(job.get("source_id") or "").strip().lower()
        canonical_cid = str(job.get("channel_id") or "").strip()
        cid = canonical_cid.lower()
        refs = [str(x or "").strip() for x in (job.get("refs") or []) if str(x or "").strip()]
        target_refs = [str(x or "").strip() for x in (job.get("target_refs") or refs) if str(x or "").strip()]
        if not sid or not cid or not refs:
            return []
        current = self.get(sid, cid) or {}
        current_refs = list(current.get("refs") or [])
        if not all(any(self._refs_match(wanted, saved) for saved in current_refs) for wanted in refs):
            return []
        backups = []
        for old_sid in sorted(set(str(x or "").strip().lower() for x in (job.get("old_source_ids") or []) if str(x or "").strip())):
            meta = self._prune_generated_source_refs(old_sid, refs)
            if (meta or {}).get("backup") and (meta or {}).get("path"):
                backups.append({"source_id": old_sid,
                                "backup": str(meta.get("backup")),
                                "path": str(meta.get("path"))})
        meta = self._merge_generated_target_refs(sid, canonical_cid, target_refs)
        path = str((meta or {}).get("path") or "")
        backup = str((meta or {}).get("backup") or "")
        if backup and path and not any(x.get("path") == path for x in backups):
            backups.append({"source_id": sid, "backup": backup, "path": path})
        return backups

    def attach_generated_backups(self, history_id, backups):
        """Attach async generated-map backups to the matching delta undo record."""
        if not history_id or not backups:
            return False
        hist = self._data.setdefault("history", [])
        for item in reversed(hist):
            if str((item or {}).get("history_id") or "") != str(history_id):
                continue
            rows = list((item or {}).get("generated_backups") or [])
            paths = set(str((x or {}).get("path") or "") for x in rows)
            for meta in backups:
                path = str((meta or {}).get("path") or "")
                if path and path not in paths:
                    rows.append(dict(meta or {})); paths.add(path)
            item["generated_backups"] = rows
            self._save()
            return True
        return False

    def bulk_assign_auto_exclusive(self, records, label="Automatic Satellite Repair"):
        """Atomically install high-confidence automatic mappings.

        This is the batch counterpart of Smart Mapping's one-channel SAFE path.
        It is intentionally conservative:
        - receiver refs already owned by a MANUAL mapping are never touched;
        - GREEN-unmap tombstones remain blocked and are never resurrected;
        - only the supplied receiver refs are removed from older AUTO owners;
        - generated Native EPGImport maps are kept in sync;
        - the whole operation is one undoable snapshot.

        ``records`` rows may contain source_id/channel_id/refs/display_name plus
        confidence/reason metadata.  The caller is responsible for submitting
        only PrecisionMatch ``auto=True`` candidates.
        """
        mappings = self._data.setdefault("mappings", {})
        blocked = list(self.blocked_refs())

        # Build a canonical set of manually owned refs. Automatic repair must
        # never override a user's explicit correction.
        manual_refs = []
        for rec in mappings.values():
            if str((rec or {}).get("mode") or "").lower() == "manual":
                manual_refs.extend(str(x or "").strip() for x in ((rec or {}).get("refs") or []) if str(x or "").strip())

        def protected(ref):
            raw = str(ref or "").strip()
            if not raw:
                return True
            if any(self._refs_match(raw, old) for old in blocked):
                return True
            return any(self._refs_match(raw, old) for old in manual_refs)

        # 8.2.1: reduce the incoming batch to ONE target per receiver ref
        # before touching persisted data. Older code grouped by XMLTV key first,
        # so the same ServiceRef could survive in several different source/ID
        # rows from a single Auto Repair batch.
        skipped_protected = 0
        winner_by_ref = {}
        for order, row in enumerate(records or []):
            sid = str((row or {}).get("source_id") or "").strip().lower()
            canonical_cid = str((row or {}).get("channel_id") or "").strip()
            cid = canonical_cid.lower()
            if not sid or not cid:
                continue
            confidence = int((row or {}).get("confidence") or 0)
            for ref in (row or {}).get("refs") or []:
                ref = str(ref or "").strip()
                if not ref:
                    continue
                if protected(ref):
                    skipped_protected += 1
                    continue
                ref_key = self._canonical_ref_key(ref)
                if not ref_key:
                    continue
                candidate = {
                    "source_id": sid, "channel_id": canonical_cid,
                    "display_name": str((row or {}).get("display_name") or canonical_cid),
                    "confidence": confidence,
                    "reason": str((row or {}).get("reason") or ""),
                    "ref": ref, "order": int(order),
                }
                previous = winner_by_ref.get(ref_key)
                # Confidence is authoritative. On an exact tie keep the earlier
                # caller-ranked record, making the result deterministic and
                # preserving the batch matcher's own priority order.
                if previous is None or confidence > int(previous.get("confidence") or 0):
                    winner_by_ref[ref_key] = candidate

        grouped = {}
        for candidate in winner_by_ref.values():
            sid = str(candidate.get("source_id") or "")
            cid = str(candidate.get("channel_id") or "")
            key = self._key(sid, cid)
            slot = grouped.setdefault(key, {
                "source_id": sid, "channel_id": cid,
                "display_name": str(candidate.get("display_name") or cid),
                "confidence": int(candidate.get("confidence") or 0),
                "reason": str(candidate.get("reason") or ""),
                "refs": []})
            if int(candidate.get("confidence") or 0) > int(slot.get("confidence") or 0):
                slot["confidence"] = int(candidate.get("confidence") or 0)
                slot["reason"] = str(candidate.get("reason") or "")
            ref = str(candidate.get("ref") or "").strip()
            if ref and not any(self._refs_match(ref, old) for old in slot["refs"]):
                slot["refs"].append(ref)

        if not grouped:
            return {"changed": 0, "records": 0, "refs": 0,
                    "skipped_protected": skipped_protected,
                    "generated_maps": 0}

        snapshot = copy.deepcopy(mappings)
        affected_refs = [ref for row in grouped.values() for ref in row.get("refs") or []]
        old_source_ids = set()

        # Discover previous AUTO owners of the affected receiver refs. Manual
        # rows were filtered above and remain untouched.
        for key, rec in list(mappings.items()):
            if str((rec or {}).get("mode") or "").lower() == "manual":
                continue
            refs_now = list((rec or {}).get("refs") or [])
            if any(any(self._refs_match(existing, wanted) for wanted in affected_refs) for existing in refs_now):
                if "::" in str(key):
                    old_source_ids.add(str(key).split("::", 1)[0].lower())

        generated_backups = []
        for old_sid in sorted(old_source_ids):
            meta = self._prune_generated_source_refs(old_sid, affected_refs)
            if (meta or {}).get("backup") and (meta or {}).get("path"):
                generated_backups.append({"source_id": old_sid,
                                          "backup": str(meta.get("backup")),
                                          "path": str(meta.get("path"))})

        changed = 0
        # Remove affected refs only from non-manual persisted owners.
        for key in list(mappings.keys()):
            rec = dict(mappings.get(key) or {})
            if str(rec.get("mode") or "").lower() == "manual":
                continue
            before = list(rec.get("refs") or [])
            after = [r for r in before if not any(self._refs_match(r, wanted) for wanted in affected_refs)]
            if after == before:
                continue
            changed += 1
            if after:
                rec["refs"] = after
                mappings[key] = rec
            else:
                mappings.pop(key, None)

        # Add new automatic owners. Other receiver variants already using the
        # same auto target are preserved.
        for key, row in grouped.items():
            existing = dict(mappings.get(key) or {})
            refs = []
            if existing and str(existing.get("mode") or "").lower() != "manual":
                refs = list(existing.get("refs") or [])
            for ref in row.get("refs") or []:
                if not any(self._refs_match(ref, old) for old in refs):
                    refs.append(ref)
            value = {
                "refs": refs,
                "mode": "auto-repair",
                "display_name": row.get("display_name") or row.get("channel_id") or "",
                "confidence": int(row.get("confidence") or 0),
                "reason": str(row.get("reason") or ""),
            }
            if mappings.get(key) != value:
                mappings[key] = value
                changed += 1

        # Keep generated target maps aligned with the new owners.  beta135
        # batches all target IDs by source so each channels.xml is parsed and
        # rewritten once, not once per mapped channel.
        generated_changed = 0
        target_metas = self._merge_generated_target_refs_bulk(list(grouped.values()))
        for meta in target_metas:
            generated_changed += int((meta or {}).get("changed") or 0)
            path = str((meta or {}).get("path") or "")
            backup = str((meta or {}).get("backup") or "")
            if backup and path and not any(x.get("path") == path for x in generated_backups):
                generated_backups.append({"source_id": str((meta or {}).get("source_id") or ""),
                                          "backup": backup, "path": path})

        if changed or generated_changed:
            self._data.setdefault("history", []).append({
                "ts": int(time.time()), "action": "bulk_snapshot",
                "label": str(label or "Automatic Satellite Repair"),
                "previous_mappings": snapshot,
                "generated_backups": generated_backups})
            self._data["history"] = self._data["history"][-30:]
            self._save()

        return {"changed": changed, "records": len(grouped),
                "refs": len(affected_refs),
                "skipped_protected": skipped_protected,
                "generated_maps": generated_changed}

    def bulk_assign(self, records, label="Bulk Smart Mapping", remove_source_id=None, remove_source_ids=None):
        """Apply several manual assignments atomically with one-step undo.

        ``records`` is an iterable of dicts containing source_id, channel_id,
        refs and optionally display_name.  Multiple receiver refs targeting
        the same XMLTV key are merged before the single disk write.  The full
        previous mapping dictionary is kept in the bounded history so a bulk
        source repair can be reverted with one Undo action.
        """
        grouped = {}
        for row in records or []:
            sid = str((row or {}).get("source_id") or "").strip().lower()
            canonical_cid = str((row or {}).get("channel_id") or "").strip()
            cid = canonical_cid.lower()
            if not sid or not cid:
                continue
            key = self._key(sid, cid)
            slot = grouped.setdefault(key, {
                "source_id": sid, "channel_id": canonical_cid,
                "display_name": str((row or {}).get("display_name") or canonical_cid),
                "refs": []})
            for ref in (row or {}).get("refs") or []:
                ref = str(ref or "").strip()
                if ref and ref not in slot["refs"]:
                    slot["refs"].append(ref)
        if not grouped:
            return {"changed": 0, "records": 0}
        mappings = self._data.setdefault("mappings", {})
        snapshot = copy.deepcopy(mappings)
        changed = 0
        # Source replacement semantics: remove only the affected receiver refs
        # from the old manual/override source before assigning their new owner.
        old_sid = str(remove_source_id or "").strip().lower()
        old_sids = set(str(x or "").strip().lower() for x in (remove_source_ids or []) if str(x or "").strip())
        if old_sid:
            old_sids.add(old_sid)
        affected_refs = set(ref for row in grouped.values() for ref in row.get("refs") or [])
        if affected_refs:
            blocked = list(self._data.setdefault("blocked_refs", []))
            self._data["blocked_refs"] = [old for old in blocked if not any(self._refs_match(old, ref) for ref in affected_refs)]
            ignored = list(self._data.setdefault("ignored_refs", []))
            self._data["ignored_refs"] = [old for old in ignored if not any(self._refs_match(old, ref) for ref in affected_refs)]
        # beta60: source replacement must also remove the old source ownership
        # from the generated channels.xml used by Native EPGImport. Previously
        # the UI JSON changed but the old source could still feed the service.
        generated_backups = []
        pruned_generated = 0
        for old_source in sorted(old_sids):
            meta = self._prune_generated_source_refs(old_source, affected_refs) if affected_refs else {"changed": 0, "backup": "", "path": ""}
            pruned_generated += int((meta or {}).get("changed") or 0)
            if (meta or {}).get("backup") and (meta or {}).get("path"):
                generated_backups.append({"source_id": old_source, "backup": str(meta.get("backup")), "path": str(meta.get("path"))})
        if affected_refs:
            # A user-confirmed source replacement is exclusive: remove those
            # receiver services from any previous persisted override, not only
            # the source label that happened to be displayed first.
            for old_key in list(mappings.keys()):
                old_rec = dict(mappings.get(old_key) or {})
                before = list(old_rec.get("refs") or [])
                refs = [str(x) for x in before if not any(self._refs_match(x, wanted) for wanted in affected_refs)]
                if refs != before:
                    changed += 1
                    if refs:
                        old_rec["refs"] = refs
                        mappings[old_key] = old_rec
                    else:
                        mappings.pop(old_key, None)
        for key, row in grouped.items():
            existing = mappings.get(key) or {}
            refs = list(existing.get("refs") or []) if str(existing.get("mode") or "").lower() == "manual" else []
            for ref in row["refs"]:
                if ref not in refs:
                    refs.append(ref)
            value = {"refs": refs, "mode": "manual", "display_name": row["display_name"]}
            if mappings.get(key) != value:
                mappings[key] = value
                changed += 1
        # Keep target generated maps in sync as well; preserve receiver siblings
        # already mapped to the same XMLTV ID.
        target_generated_changed = 0
        for row in grouped.values():
            meta = self._merge_generated_target_refs(row["source_id"], row["channel_id"], row["refs"])
            target_generated_changed += int((meta or {}).get("changed") or 0)
            path = str((meta or {}).get("path") or "")
            backup = str((meta or {}).get("backup") or "")
            if backup and path and not any(x.get("path") == path for x in generated_backups):
                generated_backups.append({"source_id": row["source_id"], "backup": backup, "path": path})
        if not changed and not pruned_generated and not target_generated_changed:
            return {"changed": 0, "records": len(grouped)}
        self._data.setdefault("history", []).append({
            "ts": int(time.time()), "action": "bulk_snapshot",
            "label": str(label or "Bulk Smart Mapping"),
            "previous_mappings": snapshot,
            "generated_backups": generated_backups})
        self._data["history"] = self._data["history"][-30:]
        self._save()
        return {"changed": changed, "records": len(grouped), "pruned_generated_refs": pruned_generated}

    def reset_auto_mappings(self):
        """Remove AUTO/AUTO-REPAIR ownership while preserving manual locks.

        The operation is atomic at the JSON-store level and undoable through the
        normal bulk snapshot history.  Manual mappings, manual-unmap tombstones
        and ignored refs are intentionally preserved.  Generated source maps are
        pruned only for receiver refs that were owned automatically.
        """
        mappings = self._data.setdefault("mappings", {})
        snapshot = copy.deepcopy(mappings)
        removed_keys = []
        refs_by_source = {}
        for key, rec in list(mappings.items()):
            mode = str((rec or {}).get("mode") or "").lower()
            if mode == "manual":
                continue
            refs = [str(x or "").strip() for x in ((rec or {}).get("refs") or []) if str(x or "").strip()]
            sid = str(key or "").split("::", 1)[0].lower()
            if refs and sid:
                refs_by_source.setdefault(sid, []).extend(refs)
            removed_keys.append(key)
            mappings.pop(key, None)

        generated_backups = []
        generated_changed = 0
        for sid, refs in sorted(refs_by_source.items()):
            uniq = []
            for ref in refs:
                if not any(self._refs_match(ref, old) for old in uniq):
                    uniq.append(ref)
            try:
                meta = self._prune_generated_source_refs(sid, uniq)
            except Exception:
                meta = {}
            generated_changed += int((meta or {}).get("changed") or 0)
            if (meta or {}).get("backup") and (meta or {}).get("path"):
                generated_backups.append({"source_id": sid,
                                          "backup": str(meta.get("backup")),
                                          "path": str(meta.get("path"))})

        if removed_keys or generated_changed:
            self._data.setdefault("history", []).append({
                "ts": int(time.time()), "action": "bulk_snapshot",
                "label": "Reset AUTO mappings",
                "previous_mappings": snapshot,
                "generated_backups": generated_backups})
            self._data["history"] = self._data["history"][-30:]
            self._save()
        return {"ok": True, "removed_mappings": len(removed_keys),
                "generated_maps": generated_changed,
                "manual_preserved": sum(1 for rec in mappings.values()
                                          if str((rec or {}).get("mode") or "").lower() == "manual")}

    def reset_all_sid_mappings(self):
        """Clear all EPGManager SID ownership without touching receiver services.

        This intentionally removes mapping ownership, tombstones and undo history
        so a full rebuild can start from the current XMLTV catalogues.  Bouquets,
        lamedb and Enigma2 service definitions live outside this store and are
        never modified here.
        """
        before = len(self._data.get("mappings") or {})
        self._data["mappings"] = {}
        self._data["history"] = []
        self._data["blocked_refs"] = []
        self._data["ignored_refs"] = []
        self._data["orphaned_mappings"] = {}
        self._data["version"] = max(3, int(self._data.get("version") or 3))
        self._save()
        return {"ok": True, "removed_mappings": int(before)}

    def can_undo_last_auto_repair(self):
        """Return True only when the latest mapping snapshot is an Auto Repair.

        We intentionally do not jump over later manual edits: restoring an older
        full snapshot after the user corrected channels could erase those edits.
        """
        history = self._data.setdefault('history', [])
        if not history:
            return False
        item = history[-1] or {}
        return (item.get('action') == 'bulk_snapshot' and
                str(item.get('label') or '').lower().startswith('auto repair'))

    def undo_last_auto_repair(self):
        if not self.can_undo_last_auto_repair():
            return False
        return self.undo_last()

    def undo_last(self):
        history = self._data.setdefault('history', [])
        if not history:
            return False
        item = history.pop()
        action = str(item.get('action') or '')
        if action == 'ignore_ref':
            ref = str(item.get('ref') or '').strip()
            before = list(self._data.setdefault('ignored_refs', []))
            self._data['ignored_refs'] = [old for old in before if not self._refs_match(ref, old)]
            # Restore an older explicit UNMAPPED tombstone if IGNORE replaced
            # one. This keeps the generic Mapping Options Undo lossless.
            blocked = list(self._data.setdefault('blocked_refs', []))
            for old in item.get('previous_blocked') or []:
                old = str(old or '').strip()
                if old and not any(self._refs_match(old, saved) for saved in blocked):
                    blocked.append(old)
            self._data['blocked_refs'] = blocked[-10000:]
            self._save()
            return True
        if action == 'unignore_ref':
            ref = str(item.get('ref') or '').strip()
            rows = list(self._data.setdefault('ignored_refs', []))
            if ref and not any(self._refs_match(ref, old) for old in rows):
                rows.append(ref)
                self._data['ignored_refs'] = rows[-10000:]
            self._save()
            return True
        if action in ('manual_delta', 'prune_invalid_auto'):
            mappings = self._data.setdefault('mappings', {})
            before_records = dict(item.get('before_records') or {})
            affected_keys = set(str(x or '') for x in (item.get('affected_keys') or []))
            affected_keys.update(before_records.keys())
            for key in affected_keys:
                mappings.pop(key, None)
            for key, previous in before_records.items():
                if isinstance(previous, dict):
                    mappings[str(key)] = previous
            self._save()
            backups = list(item.get('generated_backups') or [])
            for meta in backups:
                backup = str((meta or {}).get('backup') or '')
                map_path = str((meta or {}).get('path') or '')
                if backup and map_path and os.path.isfile(backup):
                    try:
                        shutil.copy2(backup, map_path)
                    except Exception:
                        log.warning('Could not restore generated source map backup %s', backup)
            return True
        if item.get('action') == 'bulk_snapshot':
            previous = item.get('previous_mappings')
            if isinstance(previous, dict):
                current = copy.deepcopy(self._data.get('mappings', {}))
                self._data['mappings'] = previous
                self._save()
                # Restore the old generated source map snapshot when a source
                # replacement pruned automatic ownership. This makes Undo truly
                # restore both UI JSON and Native EPGImport routing.
                backups = list(item.get('generated_backups') or [])
                # beta60 compatibility for history written before beta61.
                if item.get('generated_backup') and item.get('generated_map_path'):
                    backups.append({'backup': item.get('generated_backup'), 'path': item.get('generated_map_path')})
                restored_paths = set()
                for meta in backups:
                    backup = str((meta or {}).get('backup') or '')
                    map_path = str((meta or {}).get('path') or '')
                    if not backup or not map_path or map_path in restored_paths or not os.path.isfile(backup):
                        continue
                    try:
                        shutil.copy2(backup, map_path)
                        restored_paths.add(map_path)
                    except Exception:
                        log.warning("Could not restore generated source map backup %s", backup)
                # If exact generated-map snapshots were restored, they are the
                # complete source of truth and must not be followed by the old
                # per-channel override reconciliation (which could erase sibling
                # refs on the target XMLTV ID).  Keep reconciliation only for
                # legacy history entries that had no generated map backup.
                if not restored_paths:
                    try:
                        from . import srp_channel_map
                        keys = set(current) | set(previous)
                        for key in keys:
                            if current.get(key) == previous.get(key) or '::' not in str(key):
                                continue
                            sid, cid = str(key).split('::', 1)
                            rec = previous.get(key) or {}
                            if str(rec.get('mode') or '').lower() == 'manual' and rec.get('refs'):
                                srp_channel_map.apply_mapping_override(sid, cid, list(rec.get('refs') or []), remove=False)
                            else:
                                srp_channel_map.apply_mapping_override(sid, cid, [], remove=True)
                    except Exception:
                        pass
                return True
            return False
        key = item.get('key')
        # Older bulk_auto history records did not carry a reversible key. Skip
        # them safely instead of accidentally touching a None mapping entry.
        if not key:
            self._save()
            return False
        previous = item.get('previous')
        if previous is None:
            self._data.setdefault('mappings', {}).pop(key, None)
        else:
            self._data.setdefault('mappings', {})[key] = previous
        self._save()
        return True
