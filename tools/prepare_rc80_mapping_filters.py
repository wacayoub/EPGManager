"""SHA-pinned, offline-only Mapping filters for the installed readable RC80 UI."""
import ast
import hashlib

ORIGINAL = {"ui/channel_mapping.py": "3bcbbf34f567d53427b585709e1f459696512f07612073957be028adf92fc421"}

FILTER_METHODS = '''    def _cached_primary_epg_health(self, service):
        """Use the active owner and resident report; unknown is never empty."""
        try:
            ref = (service or {}).get("ref")
            if not ref:
                return "UNKNOWN"
            owner = self._primary_mapping_info_for_ref(ref) or {}
            sid, cid = owner.get("source_id"), owner.get("channel_id")
            if not sid or not cid:
                return "UNKNOWN"
            return github_match_health.health(sid, cid) or "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _service_has_no_future_epg(self, service):
        return self._cached_primary_epg_health(service) == "NO_EPG"

    def _service_needs_review(self, service):
        try:
            return (not self._paint_is_mapped((service or {}).get("ref"))
                    or self._service_has_conflict(service)
                    or self._service_is_ignored(service)
                    or self._cached_primary_epg_health(service) != "EPG_OK")
        except Exception:
            return True  # A lookup error needs review, never a green guide.

    def open_channel_filters(self):
        from Screens.ChoiceBox import ChoiceBox
        choices = [
            ("All channels", "all"),
            ("No future EPG - confirmed source cache", "no_future"),
            ("Needs review - unmapped / uncertain guide", "review"),
            ("Manual mappings", "locked"),
        ]
        self.session.openWithCallback(self._channel_filter_selected, ChoiceBox,
                                      title="Filter channels - current reception", list=choices)

    def _channel_filter_selected(self, answer):
        if (isinstance(answer, (tuple, list)) and len(answer) == 2
                and answer[1] in ("all", "no_future", "review", "locked")):
            self.set_channel_filter(answer[1])

'''


def one(text, old, new):
    if text.count(old) != 1:
        raise ValueError("mapping filters source pattern drift")
    return text.replace(old, new, 1)


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError("mapping filters inventory drift")
    name = "ui/channel_mapping.py"
    if hashlib.sha256(source[name]).hexdigest() != ORIGINAL[name]:
        raise ValueError("mapping filters preimage drift")
    text = source[name].decode("utf-8")
    text = one(text, "    def open_mapping_options(self):", FILTER_METHODS + "    def open_mapping_options(self):")
    text = one(text, '            choices += [\n                ("1  Change EPG for this service", "change_one"),',
               '            choices += [\n                ("Filter channels - All / No EPG / Review / Manual", "channel_filters"),\n                ("1  Change EPG for this service", "change_one"),')
    text = one(text, '        elif key == "filter_unresolved":\n            self.set_channel_filter("unresolved")',
               '        elif key == "channel_filters":\n            self.open_channel_filters()\n        elif key == "filter_unresolved":\n            self.set_channel_filter("unresolved")')
    text = one(text, '        if mode == "unresolved":\n',
               '        if mode == "no_future":\n            all_services = [x for x in all_services if self._service_has_no_future_epg(x)]\n        elif mode == "review":\n            all_services = [x for x in all_services if self._service_needs_review(x)]\n        elif mode == "unresolved":\n')
    text = one(text, 'if mode not in ("all", "unresolved", "unmapped", "mapped", "conflicts", "locked", "auto", "ignored"):',
               'if mode not in ("all", "no_future", "review", "unresolved", "unmapped", "mapped", "conflicts", "locked", "auto", "ignored"):')
    text = one(text, '        self.channel_filter_mode = mode\n        self.refresh_channels()\n        self._set_index("channels", 0)',
               '        previous_ref = (self._current("channels", self.bouquet_services) or {}).get("ref")\n        self.channel_filter_mode = mode\n        self.refresh_channels()\n        selected = next((i for i, item in enumerate(self.bouquet_services)\n                         if previous_ref and item.get("ref") == previous_ref), 0)\n        self._set_index("channels", selected)')
    text = one(text, '            "all": "ALL receiver channels",\n',
               '            "all": "ALL receiver channels",\n            "no_future": "NO FUTURE EPG - confirmed source cache",\n            "review": "NEEDS REVIEW - unmapped / uncertain guide",\n')
    text = one(text, 'self._initial_filter in ("unmapped", "mapped", "conflicts", "locked", "auto", "ignored")',
               'self._initial_filter in ("no_future", "review", "unresolved", "unmapped", "mapped", "conflicts", "locked", "auto", "ignored")')
    # Health-dependent filters must not reuse rows from an older cache snapshot.
    old = 'if not self._repair_mode and view_key.startswith("SAT:"):'
    if text.count(old) != 2:
        raise ValueError("mapping row cache guard drift")
    text = text.replace(old, 'if (not self._repair_mode and view_key.startswith("SAT:")\n                and self.channel_filter_mode not in ("no_future", "review")):')
    text = one(text, '        self["hdr_channel"].setText(("> " if visible_focus == 1 else "") + "RECEIVER CHANNEL")',
               '        filter_name = {"no_future": "NO FUTURE", "review": "REVIEW", "locked": "MANUAL"}.get(\n            getattr(self, "channel_filter_mode", "all"), "")\n        self["hdr_channel"].setText(("> " if visible_focus == 1 else "") + "RECEIVER CHANNEL"\n                                    + ((" - " + filter_name) if filter_name else ""))')
    ast.parse(text)
    return {name: text.encode("utf-8")}
