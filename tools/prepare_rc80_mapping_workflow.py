"""Offline, pinned Mapping workflow improvements; no receiver deployment."""
import ast
import hashlib

ORIGINAL = {
    "ui/channel_mapping.py": "dde5f807262278247605b75dff769f0c638eff6b2144858a10c20989b649ce19",
    "core/github_match_health.py": "f9c84797e3cae492f7c53ca5e0ad7552e47b5ce32233b22f46d72efdb5b04b59",
}

SNAPSHOT = '''def describe_cached(source_id, channel_id, now=None):
    # A last-event horizon is not proof of continuous programme coverage.
    observed = float(time.time() if now is None else now)
    result = {"status": "UNKNOWN", "latest_stop": None, "horizon_hours": None}
    key = source_key(source_id)
    if not key:
        return result
    with _lock:
        report = _reports.get(key)
    if not isinstance(report, dict):
        return result
    result["status"] = classify(report, channel_id, observed)
    generated = _epoch(report.get("generated_utc"))
    if not generated or observed < generated - 3600 or observed - generated > FRESH_SECONDS:
        return result
    rows = report.get("rows")
    if not isinstance(rows, dict):
        return result
    row = rows.get(str(channel_id or "").strip().casefold())
    if not isinstance(row, dict):
        return result
    stop = _epoch(row.get("latest_stop_utc"))
    if result["status"] in ("EPG_OK", "EPG_WARN", "SUSPECT", "SYNTHETIC") and stop > observed:
        result["latest_stop"] = stop
        result["horizon_hours"] = (stop - observed) / 3600.0
    return result


'''

MAIN_MENU = '''    def open_mapping_options(self):
        from Screens.ChoiceBox import ChoiceBox
        choices = [
            ("Search current reception", "search"),
            ("Filter channels", "channel_filters"),
            ("Change EPG guide", "change_menu"),
            ("Undo last mapping change", "undo"),
            ("Advanced tools", "advanced"),
        ]
        if getattr(self, "_remap_scope_refs", None):
            choices.append(("Cancel pending EPG change", "cancel_remap"))
        self.session.openWithCallback(self._mapping_option_selected, ChoiceBox,
                                      title="Smart Mapping", list=choices)
'''

ADVANCED_MENU = '''    def open_advanced_mapping_tools(self):
        from Screens.ChoiceBox import ChoiceBox
        choices = [
            ("Guide details - INFO", "guide_details"),
            ("Technical match diagnostics", "match_diagnostics"),
            ("Preview next programmes", "preview"),
            ("Smart Check", "smart_audit"),
            ("Review next conflict", "conflict"),
            ("Verify channel-list update", "verify_lamedb"),
            ("Refresh satellite mapping database", "rebuild_srp"),
            ("Guide health diagnostics", "direct_id_health"),
            ("Channel-ID coverage audit", "id_audit"),
            ("Source ID collisions", "collision_audit"),
            ("Mapping regression test", "regression_test"),
            ("Export diagnostic report", "export_diagnostic"),
            ("Performance profile", "perf_profile"),
        ]
        service = self._current("channels", self.bouquet_services) or {}
        if service and self._service_is_mapped(service):
            choices.append(("Remove current mapping...", "unmap"))
        if service and self._service_is_ignored(service):
            choices.append(("Restore ignored channel", "unignore"))
        elif service and not self._service_is_mapped(service):
            choices.append(("Ignore this channel...", "ignore"))
        choices.append(("Reset automatic / all mappings...", "reset_ids"))
        self.session.openWithCallback(self._advanced_mapping_selected, ChoiceBox,
                                      title="Mapping - Advanced tools", list=choices)
'''

NEW_METHODS = '''    def open_epg_change_menu(self):
        from Screens.ChoiceBox import ChoiceBox
        choices = [("Change guide for this channel", "change_one"),
                   ("Change guide for all linked services", "change_all_id")]
        self.session.openWithCallback(self._mapping_option_selected, ChoiceBox,
                                      title="Change EPG guide", list=choices)

    def open_other_channel_filters(self):
        from Screens.ChoiceBox import ChoiceBox
        choices = [("Unmapped", "unmapped"), ("Mapped", "mapped"),
                   ("Conflicts", "conflicts"), ("Ignored", "ignored"),
                   ("Auto-repaired", "auto"), ("Mapping issues only", "unresolved")]
        def selected(answer):
            if (isinstance(answer, (tuple, list)) and len(answer) == 2
                    and answer[1] in ("unmapped", "mapped", "conflicts", "ignored", "auto", "unresolved")):
                self.set_channel_filter(answer[1])
        self.session.openWithCallback(selected, ChoiceBox, title="Other channel filters", list=choices)

    def _mapping_action_context(self):
        service = self._current("channels", self.bouquet_services) or {}
        epg = self._current("selections", self.selection_entries) or {}
        return (service.get("ref"), epg.get("source_id"), epg.get("channel_id"),
                int(self.mapping_revision or 0), tuple(getattr(self, "_remap_scope_refs", []) or []))

    def _confirm_mapping_action(self, answer, expected, action):
        if not answer:
            return
        if expected != self._mapping_action_context():
            self.session.open(MessageBox, "Selection changed. Review the channel and try again.", MessageBox.TYPE_INFO)
            return
        allowed = {"assign": self._apply_current_mapping,
                   "unmap": self._remove_current_mapping,
                   "ignore": self._ignore_current_mapping}
        if action in allowed:
            allowed[action]()

    def assign_current(self):
        service = self._current("channels", self.bouquet_services) or {}
        epg = self._current("selections", self.selection_entries) or {}
        if not service or not epg or epg.get("separator"):
            return
        current = self._primary_mapping_info_for_ref(service.get("ref")) or {}
        count = len(getattr(self, "_remap_scope_refs", []) or []) or 1
        status = self._guide_status_text(github_match_health.health(epg.get("source_id"), epg.get("channel_id")))
        text = ("Apply this EPG guide?\\n\\nChannel: %s\\nCurrent: %s / %s\\nNew: %s / %s\\n"
                "Services affected: %d\\nSource guide status: %s") % (
                    str(service.get("name") or "Channel")[:80],
                    str(current.get("source_id") or "None")[:70], str(current.get("channel_id") or "-")[:70],
                    str(epg.get("source_id") or "Unknown")[:70], str(epg.get("channel_id") or "-")[:70],
                    count, status)
        expected = self._mapping_action_context()
        self.session.openWithCallback(lambda yes: self._confirm_mapping_action(yes, expected, "assign"),
                                      MessageBox, text, MessageBox.TYPE_YESNO, default=False)

    def unmap_current_channel(self):
        service = self._current("channels", self.bouquet_services) or {}
        if not service:
            return
        expected = self._mapping_action_context()
        text = "Remove the EPG mapping for %s?\\n\\nExisting programmes will not be deleted." % str(service.get("name") or "this channel")[:100]
        self.session.openWithCallback(lambda yes: self._confirm_mapping_action(yes, expected, "unmap"),
                                      MessageBox, text, MessageBox.TYPE_YESNO, default=False)

    def ignore_current_channel(self):
        service = self._current("channels", self.bouquet_services) or {}
        if not service:
            return
        expected = self._mapping_action_context()
        text = "Exclude %s from automatic mapping and review?" % str(service.get("name") or "this channel")[:100]
        self.session.openWithCallback(lambda yes: self._confirm_mapping_action(yes, expected, "ignore"),
                                      MessageBox, text, MessageBox.TYPE_YESNO, default=False)

    def _review_reason_for_service(self, service):
        try:
            if self._service_is_ignored(service):
                return "Excluded by owner"
            if self._service_has_conflict(service):
                return "Mapping conflict"
            if not self._paint_is_mapped((service or {}).get("ref")):
                return "No mapping saved"
            return {"EPG_OK": "", "NO_EPG": "No future programmes confirmed",
                    "EPG_WARN": "Guide needs verification", "SYNTHETIC": "Suspected generated guide",
                    "SUSPECT": "Suspected repeated programmes"}.get(
                        self._cached_primary_epg_health(service), "Guide information unknown")
        except Exception:
            return "Guide information unavailable"

    @staticmethod
    def _guide_status_text(status):
        return {"EPG_OK": "Confirmed EPG", "EPG_WARN": "EPG available - review",
                "NO_EPG": "Confirmed no future EPG", "SUSPECT": "Suspected repeated programmes",
                "SYNTHETIC": "Suspected generated guide"}.get(status, "Unknown - not verified")

    def show_match_reason(self):
        service = self._current("channels", self.bouquet_services) or {}
        epg = self._current("selections", self.selection_entries) or {}
        if not epg or epg.get("separator"):
            self.session.open(MessageBox, "Select a guide candidate first.", MessageBox.TYPE_INFO)
            return
        sid, cid = epg.get("source_id"), epg.get("channel_id")
        details = github_match_health.describe_cached(sid, cid)
        try:
            score = (max(0, min(100, int(round(float(epg["_precision_confidence"]) / 10))))
                     if "_precision_confidence" in epg else None)
        except (ValueError, TypeError, OverflowError):
            score = None
        score_text = ("%d%%" % score) if score is not None else "Not scored"
        stop = details.get("latest_stop")
        horizon = "Unknown"
        if stop is not None:
            horizon = time.strftime("%d/%m %H:%M GMT+0", time.gmtime(stop))
        source = self._source_by_id(sid) or {}
        lines = ["GUIDE DETAILS", "",
                 "Channel: %s" % str(service.get("name") or "Channel")[:80],
                 "Candidate: %s" % str(epg.get("display_name") or cid or "-")[:80],
                 "Source: %s" % str(epg.get("source_name") or sid or "-")[:80],
                 "Source language: %s" % str(epg.get("language") or source.get("language") or "Unknown")[:24],
                 "Match score: %s (not programme coverage)" % score_text,
                 "Source guide: %s" % self._guide_status_text(details.get("status")),
                 "Last source event ends: %s" % horizon,
                 "Continuous coverage / gaps: not measured",
                 "Reason: %s" % str(epg.get("_precision_reason") or "See technical diagnostics")[:140]]
        self.session.open(MessageBox, "\\n".join(lines), MessageBox.TYPE_INFO)

'''


def one(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Mapping workflow source pattern drift")
    return text.replace(old, new, 1)


def method(text, cls, name, replacement):
    node = next(c for c in ast.parse(text).body if isinstance(c, ast.ClassDef) and c.name == cls)
    node = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = text.splitlines(keepends=True)
    return "".join(lines[:node.lineno-1]) + replacement.rstrip() + "\n" + "".join(lines[node.end_lineno:])


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError("Mapping workflow inventory drift")
    for name, pin in ORIGINAL.items():
        if hashlib.sha256(source[name]).hexdigest() != pin:
            raise ValueError("Mapping workflow preimage drift: " + name)
    ui = source["ui/channel_mapping.py"].decode("utf-8")
    ui = method(ui, "ChannelMappingScreen", "open_mapping_options", MAIN_MENU)
    ui = method(ui, "ChannelMappingScreen", "open_advanced_mapping_tools", ADVANCED_MENU)
    ui = method(ui, "ChannelMappingScreen", "open_attention_menu",
                "    def open_attention_menu(self):\n        self.open_channel_filters()\n")
    # Keep all existing mutation and diagnostic bodies byte-identical behind the new UI.
    for old, new in (("assign_current", "_apply_current_mapping"),
                     ("unmap_current_channel", "_remove_current_mapping"),
                     ("ignore_current_channel", "_ignore_current_mapping"),
                     ("show_match_reason", "show_match_diagnostics")):
        ui = one(ui, "    def " + old + "(self):", "    def " + new + "(self):")
    ui = one(ui, "    def open_mapping_options(self):", NEW_METHODS + "    def open_mapping_options(self):")
    ui = one(ui, '        if key == "unmap":\n',
             '        if key == "search":\n            self.search_channel()\n'
             '        elif key == "change_menu":\n            self.open_epg_change_menu()\n'
             '        elif key == "unmap":\n')
    ui = one(ui, '        elif key == "search": self.search_channel()\n',
             '        elif key == "search": self.search_channel()\n'
             '        elif key == "guide_details": self.show_match_reason()\n'
             '        elif key == "match_diagnostics": self.show_match_diagnostics()\n'
             '        elif key == "smart_audit": self.smart_audit_current_bouquet()\n'
             '        elif key == "conflict": self.review_next_conflict()\n'
             '        elif key == "ignore": self.ignore_current_channel()\n'
             '        elif key == "unignore": self.unignore_current_channel()\n'
             '        elif key == "reset_ids": self.open_reset_ids_menu()\n')
    ui = one(ui, '            ("Manual mappings", "locked"),\n',
             '            ("Manual mappings", "locked"),\n            ("Other filters", "other"),\n')
    ui = one(ui, '    def _channel_filter_selected(self, answer):\n',
             '    def _channel_filter_selected(self, answer):\n'
             '        if isinstance(answer, (tuple, list)) and len(answer) == 2 and answer[1] == "other":\n'
             '            self.open_other_channel_filters()\n            return\n')
    ui = one(ui, 'try: lead = "%d%%  " % max(0, min(100, int(score)))',
             'try: lead = "Match %d%%  " % max(0, min(100, int(score)))')
    ui = one(ui, '            state = "MATCH READY • OK TO APPLY"',
             '            green = "Apply selected guide"\n            state = "INFO: GUIDE DETAILS • OK: REVIEW CHANGE"')
    ui = one(ui, '        if remap_count:\n            state = "MANUAL CHANGE MODE"\n',
             '        if remap_count:\n            state = "MANUAL CHANGE MODE"\n'
             '            selected = self._current("selections", self.selection_entries) or {}\n'
             '            if self.focus == 3 and selected and not selected.get("separator"):\n'
             '                green = "Apply selected guide"\n')
    ui = one(ui, '        self._visible_service_by_ref = {str((x or {}).get("ref") or ""): x for x in self.bouquet_services if (x or {}).get("ref")}\n',
             '        for item in self.bouquet_services:\n'
             '            item["_review_reason"] = self._review_reason_for_service(item) if mode == "review" else ""\n'
             '        self._visible_service_by_ref = {str((x or {}).get("ref") or ""): x for x in self.bouquet_services if (x or {}).get("ref")}\n')
    ui = one(ui, '        if mapped:\n            epg_text =',
             '        if item.get("_review_reason"):\n            epg_text = item["_review_reason"]\n        elif mapped:\n            epg_text =')
    ui = one(ui, '"Mapped %d/%d  •  Unmapped %d  •  Conflicts %d  •  EPG IDs %d/%d"',
             '"Receiver: %d mapped / %d total • %d unmapped • %d conflicts • IDs %d/%d"')
    ui = one(ui,
             '        self["summary"].setText("Receiver: %d mapped / %d total • %d unmapped • %d conflicts • IDs %d/%d" %\n'
             '                                (mapped_services, total_services, unmapped, suspicious, id_linked, id_total))',
             '        id_text = "%d/%d" % (id_linked, id_total) if id_total else "unknown"\n'
             '        self["summary"].setText("Receiver: %d mapped / %d total • %d unmapped • %d conflicts • IDs %s" %\n'
             '                                (mapped_services, total_services, unmapped, suspicious, id_text))')
    # A failed submenu must never fall through to a broader reset dialog.
    ui = one(ui, '        except Exception:\n            return self.reset_all_sids_and_rebuild()\n',
             '        except Exception:\n            self.session.open(MessageBox, "Reset menu unavailable. No changes made.", MessageBox.TYPE_ERROR)\n')
    ui = one(ui, 'self.session.openWithCallback(self._confirm_reset_all_sids, MessageBox, text, MessageBox.TYPE_YESNO)',
             'self.session.openWithCallback(self._confirm_reset_all_sids, MessageBox, text, MessageBox.TYPE_YESNO, default=False)')
    core = source["core/github_match_health.py"].decode("utf-8")
    core = one(core, "def generation():\n", SNAPSHOT + "def generation():\n")
    result = {"ui/channel_mapping.py": ui.encode("utf-8"), "core/github_match_health.py": core.encode("utf-8")}
    for raw in result.values():
        ast.parse(raw)
    return result
