# -*- coding: utf-8 -*-
"""EPGManager rc70 global settings.

Remote EPG generation is handled by GitHub. The receiver owns one independent
Native Auto Import scheduler, which consumes the prepared/direct feeds.
"""
from __future__ import print_function

import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eTimer, gRGB

from . import theme
from .compat import adapt_skin



def _utc_offset_minutes():
    """Receiver local offset from UTC, in minutes (0 when the box runs on UTC)."""
    try:
        return int(round(time.localtime().tm_gmtoff / 60.0))
    except Exception:
        return 0


def _format_offset(minutes):
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(int(minutes)), 60)
    return "UTC%s%d" % (sign, hours) + (":%02d" % mins if mins else "")


def _import_time_label():
    """rc87: the import time is UTC; say what that is on this receiver.

    The plugin is used worldwide, and "GMT+0" alone was easy to read as local
    time (13:15 GMT+0 is 14:15 in Morocco, 08:15 in New York).
    """
    offset = _utc_offset_minutes()
    if not offset:
        return "EPG Import start time (UTC)"
    return "EPG Import start time (UTC; local %s)" % _format_offset(offset)


def _import_time_local_note(element):
    offset = _utc_offset_minutes()
    try:
        hh, mm = [int(x) for x in element.value[:2]]
    except Exception:
        return "Receiver clock: %s." % (_format_offset(offset) if offset else "UTC")
    total = (hh * 60 + mm + offset) % 1440
    if not offset:
        return ("This receiver runs on UTC, so %02d:%02d UTC is also %02d:%02d on its clock. "
                "If your local time differs, set your time zone in the receiver's Time settings." % (hh, mm, hh, mm))
    return "%02d:%02d UTC = %02d:%02d on this receiver (%s)." % (hh, mm, total // 60, total % 60, _format_offset(offset))

class EPGManagerSettingsScreen(Screen):
    ROWS = 16
    ROW_Y = 220
    ROW_H = 47

    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 1008, 360, 54, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 420, 1008, 400, 54, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 850, 1008, 500, 54, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1380, 1008, 510, 54, theme.BTN_BLUE)
    )

    _ROWS_SKIN = ""
    for _i in range(ROWS):
        _y = ROW_Y + _i * ROW_H
        _ROWS_SKIN += (
            '<widget name="rowbg%d" position="82,%d" size="1112,43" backgroundColor="%%(PANEL_ROW)s" />\n'
            '<widget name="rowcursor%d" position="82,%d" size="7,43" backgroundColor="%%(PANEL_ROW)s" />\n'
            '<widget name="rowlabel%d" position="108,%d" size="620,43" font="Regular;21" foregroundColor="%%(TEXT)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" valign="center" />\n'
            '<widget name="rowvalue%d" position="738,%d" size="425,43" font="Regular;21" foregroundColor="%%(TEXT)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" halign="right" valign="center" />\n'
        ) % (_i, _y, _i, _y, _i, _y, _i, _y)

    skin = ("""
    <screen name="EPGManagerSettingsScreen" position="0,0" size="1920,1080" title="EPG Manager - Settings" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,27" size="1100,58" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="date" position="1320,23" size="260,55" font="Regular;21" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="clock" position="1595,19" size="265,60" font="Regular;42" halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="header_rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

      <widget name="left_panel" position="55,122" size="1180,850" backgroundColor="%(PANEL)s" />
      <widget name="section" position="82,138" size="1112,38" font="Regular;26" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="hint" position="82,176" size="1112,34" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="config_rule" position="82,212" size="1112,2" backgroundColor="%(RULE_SOFT)s" />
      """ + _ROWS_SKIN + """

      <widget name="right_panel" position="1260,122" size="620,850" backgroundColor="%(PANEL_BLUE)s" />
      <widget name="help_title" position="1300,176" size="540,50" font="Regular;30" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="help_rule" position="1365,246" size="410,2" backgroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="help_text" position="1310,278" size="520,470" font="Regular;22" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="help_keys" position="1310,770" size="520,145" font="Regular;20" halign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />

      <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
      """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    HELP = {
        "Cycle schedule": ("Cycle schedule", "Daily is recommended. Weekly, monthly and interval modes remain available for advanced users."),
        "Cycle start time": ("Start time", "Local start time for the complete EPG cycle.\n\nThere is no separate import time in the normal workflow."),
        "Weekly cycle day": ("Weekly day", "Weekday used only when Cycle schedule is Weekly."),
        "Monthly cycle day": ("Monthly day", "Day 1-28 used only when Cycle schedule is Monthly."),
        "Cycle interval (hours)": ("Interval", "Used only with Every N hours mode."),
        "Native Import after SRP mapping": ("Native Import", "Use the working OE-Alliance EPG Import engine immediately after Service Reference mapping completes."),
        "Automatic EPG Import": ("Automatic import", "Enable a daily automatic Import All using the same fast path as the BLUE/YELLOW Import All action: selected Internet feeds are fetched directly and local EPGManager feeds use their prepared cache."),
        "EPG Import start time": ("Import start time", "Daily UTC time for automatic EPG Import All. Selected GitHub/online EPG feeds are imported automatically at this time."),
        "EPG days to fetch": ("EPG days", "Future EPG days requested by EPG Manager script sources."),
        "EPG XMLTV directory": ("XMLTV directory", "Shared XMLTV cache used by source downloads, SRP channel maps and Native Import. Press OK to edit."),
        "Load EPG only for IPTV channels": ("IPTV only", "Yes imports EPG only for IPTV service references. No imports both satellite and IPTV mappings."),
        "Delete current EPG before import": ("Clear EPG", "Yes clears the current Enigma2 EPG cache immediately before Native Import. No keeps current cache and updates it."),
        "Load long descriptions up to X days": ("Long descriptions", "Number of future days for which long XMLTV descriptions are loaded."),
        "Confirm before manual import": ("Import confirmation", "Ask for confirmation before starting a manual Native EPG Import."),
        "Parallel workers": ("Workers", "Maximum source workers. 2 is recommended on low-resource Enigma2 receivers to keep the interface responsive."),
        "Retry count": ("Retries", "Retries after a network error. Provider mirror fallback is handled separately where available."),
        "Timeout (seconds)": ("Timeout", "Network timeout for one request before retry/fallback."),
        "Logging level": ("Logging", "INFO is recommended. DEBUG is useful only while troubleshooting."),
        "Debug mode": ("Debug", "Enable extra diagnostics temporarily when investigating a problem."),
        "Auto jump to best EPG match": ("Smart Mapping", "When a receiver channel is selected, position EPG Source/Selection on the safest candidate. While browsing EPG Selection, also propose the safest receiver channel. Nothing is saved until you press Assign/OK."),
        "Protect manual mappings": ("Mapping lock", "Manual corrections are locked. Auto Mapping and source refreshes cannot replace a mapping you explicitly saved."),
        "Color XMLTV IDs by health": ("ID health colors", "Green ID = valid future EPG. Red ID = zero future EPG. Yellow/orange mapping badges show unmapped or stale/wrong Service References."),
        "Performance mode": ("Performance", "Low CPU uses one worker. Balanced is recommended for most Enigma2 receivers. Fast allows more parallel network work while local HTML scrapers still stay conservative."),
        "Safe EPG replacement": ("Last-known-good", "Reject an empty or catastrophically incomplete XMLTV refresh and restore the previous valid cache automatically."),
        "Reject abnormal event drop": ("Regression guard", "If a previously healthy source suddenly loses most of its programmes or channels, keep the previous valid EPG instead of replacing it."),
        "Reuse unchanged source cache": ("Unchanged cache", "Mark identical source refreshes as unchanged and reuse existing maps/quality state instead of doing extra work."),
    }

    _ALL_FIELDS = (
        "background_source_updates", "background_local_time",
        "download_schedule_enabled", "schedule_mode", "daily_update_time",
        "weekly_update_day", "monthly_update_day", "update_interval_hours",
        "auto_sync_enabled", "native_import_after_sync", "epg_days", "epg_output_dir",
        "native_import_only_iptv", "native_clear_before_import", "native_long_desc_days", "native_confirm_before_import",
        "parallel_workers", "retry_count", "timeout_seconds", "logging_level", "debug_mode",
        "auto_jump_best_match", "map_next_unmapped", "protect_manual_mappings", "color_id_health",
        "performance_mode", "safe_source_replacement", "reject_abnormal_drop", "skip_unchanged_sources",
        # Legacy switches are deliberately persisted OFF so an old config can
        # never start a second Native Import timer alongside the v6.3 cycle.
        "native_import_schedule_enabled", "native_import_time", "auto_import_after_update",
    )

    def __init__(self, session, config, start_key=None):
        Screen.__init__(self, session)
        self.session = session
        self.epg_config = config
        self._advanced = bool(start_key and start_key not in ("native_import_schedule_enabled", "native_import_time", "epg_days", "performance_mode"))
        self._page_indexes = {}
        self._index = 0
        self.entries = []
        self._original = self._take_snapshot()
        self._build_entries()
        if start_key:
            for i, entry in enumerate(self.entries):
                if entry[1] == start_key:
                    self._index = i
                    break

        for name in ("header_bg", "header_rule", "left_panel", "config_rule", "right_panel", "help_rule", "footer_bg", "key_strip_rule"):
            self[name] = Label("")
        self["title"] = Label("EPG MANAGER  /  MAIN SETTINGS")
        self["date"] = Label(""); self["clock"] = Label("")
        self["section"] = Label("GITHUB DIRECT FEEDS  /  IMPORT SETTINGS")
        self["hint"] = Label("UP/DOWN navigate   •   LEFT/RIGHT change   •   OK edit/select   •   MENU opens this page from main modules")
        self["help_title"] = Label("Setting help"); self["help_text"] = Label("")
        self["help_keys"] = Label("GREEN / 9  Save\nYELLOW  Basic / Advanced\nBLUE / 0  Diagnostics / Update\nRED / EXIT  Cancel")
        self["key_red_bar"] = Label(""); self["key_red"] = Label("Cancel")
        self["key_green_bar"] = Label(""); self["key_green"] = Label("Save")
        self["key_yellow_bar"] = Label(""); self["key_yellow"] = Label("Advanced")
        self["key_blue_bar"] = Label(""); self["key_blue"] = Label("More")
        for i in range(self.ROWS):
            self["rowbg%d" % i] = Label(""); self["rowcursor%d" % i] = Label("")
            self["rowlabel%d" % i] = Label(""); self["rowvalue%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["SetupActions", "NumberActions", "ColorActions", "OkCancelActions", "DirectionActions"],
            {"cancel": self.cancel, "red": self.cancel, "green": self.save,
             "yellow": self.toggle_advanced, "blue": self.open_tools, "0": self.open_tools, "9": self.save,
             "up": self._up, "down": self._down, "left": self._left,
             "right": self._right, "ok": self._ok}, -1)

        self._clock_timer = eTimer()
        cb = self._clock_timer.callback if hasattr(self._clock_timer, "callback") else self._clock_timer.timeout.get()
        cb.append(self._refresh_clock)
        self._clock_timer.start(1000, False)
        self.onClose.append(self._cleanup)
        self.onHide.append(self._clock_timer.stop)
        self.onShow.append(self._resume_settings)
        self._refresh_clock(); self._render_rows()

    def _resume_settings(self):
        self._clock_timer.start(1000, False)
        self._refresh_clock()
        self._render_rows()

    def _render_rows(self):
        # Only the old/new cursor rows or changed values require painting.
        if not hasattr(self, "_row_cache"): self._row_cache = {}
        self._render_rows_full()

    def toggle_advanced(self):
        self._page_indexes[self._advanced] = self._index
        self._advanced = not self._advanced
        self._index = self._page_indexes.get(self._advanced, 0)
        self._build_entries()
        self["section"].setText("ADVANCED SETTINGS" if self._advanced else "IMPORT SETTINGS")
        self["key_yellow"].setText("Basic" if self._advanced else "Advanced")
        self._row_cache = {}
        self._render_rows()

    def open_tools(self):
        from Screens.ChoiceBox import ChoiceBox
        self.session.openWithCallback(self._tool_selected, ChoiceBox, title="Settings / Tools",
            list=[("Diagnostics", "diagnostics"), ("Online update", "update"),
                  ("Native import options", "native"), ("Recommended values", "recommended")])

    def _tool_selected(self, answer):
        if not answer: return
        key = answer[1]
        if key == "diagnostics":
            from .main import open_diagnostics_menu
            open_diagnostics_menu(self.session, self.epg_config)
        elif key == "update":
            from .online_update import OnlineUpdateScreen
            self.session.open(OnlineUpdateScreen)
        elif key == "native": self.open_native_options()
        elif key == "recommended": self.recommended()

    def _native_config(self):
        return getattr(self.epg_config, "_c", None)

    @staticmethod
    def _clone_value(value):
        return list(value) if isinstance(value, (list, tuple)) else value

    def _take_snapshot(self):
        c = self._native_config(); out = {}
        if c is None: return out
        for name in self._ALL_FIELDS:
            element = getattr(c, name, None)
            if element is not None:
                try: out[name] = self._clone_value(element.value)
                except Exception: pass
        return out

    def _build_entries(self):
        c = self._native_config()
        if c is None:
            self.entries = []
            return
        rows = [
            ("Automatic EPG Import", "native_import_schedule_enabled", c.native_import_schedule_enabled),
            (_import_time_label(), "native_import_time", c.native_import_time),
            ("Native Import after SRP mapping", "native_import_after_sync", c.native_import_after_sync),
            ("EPG days to fetch", "epg_days", c.epg_days),
            ("Load long descriptions up to X days", "native_long_desc_days", c.native_long_desc_days),
            ("Performance mode", "performance_mode", c.performance_mode),
            ("Safe EPG replacement", "safe_source_replacement", c.safe_source_replacement),
            ("Reject abnormal event drop", "reject_abnormal_drop", c.reject_abnormal_drop),
            ("Reuse unchanged source cache", "skip_unchanged_sources", c.skip_unchanged_sources),
            ("Auto jump to best EPG match", "auto_jump_best_match", c.auto_jump_best_match),
            ("Protect manual mappings", "protect_manual_mappings", c.protect_manual_mappings),
            ("Color XMLTV IDs by health", "color_id_health", c.color_id_health),
            ("Retry count", "retry_count", c.retry_count),
            ("Timeout (seconds)", "timeout_seconds", c.timeout_seconds),
        ]
        basic = {"native_import_schedule_enabled", "native_import_time", "epg_days", "performance_mode"}
        self.entries = [row for row in rows if (row[1] not in basic) == self._advanced][:self.ROWS]
        if self._index >= len(self.entries): self._index = max(0, len(self.entries) - 1)

    def _display_text(self, key, element):
        try: value = element.value
        except Exception: return ""
        if key in ("daily_update_time", "background_local_time", "native_import_time"):
            try: return "%02d:%02d" % (int(value[0]), int(value[1]))
            except Exception: return "--:--"
        if key == "schedule_mode":
            return {"daily":"Daily", "weekly":"Weekly", "monthly":"Monthly", "interval":"Every N hours"}.get(str(value), str(value))
        if key == "weekly_update_day":
            names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            try: return names[int(value) % 7]
            except Exception: return str(value)
        if key in ("background_source_updates", "download_schedule_enabled", "auto_sync_enabled", "native_import_after_sync", "native_import_schedule_enabled", "debug_mode", "native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import", "auto_jump_best_match", "protect_manual_mappings", "color_id_health", "safe_source_replacement", "reject_abnormal_drop", "skip_unchanged_sources"):
            return "Yes" if bool(value) else "No"
        if key in ("safe_auto_map_threshold", "suggestion_threshold"):
            return "%d%%" % int(value)
        if key == "logging_level": return str(value).title()
        if key == "performance_mode":
            return {"low":"Low CPU", "balanced":"Balanced", "fast":"Fast"}.get(str(value), str(value))
        return str(value)

    def _set_bg(self, name, color):
        try: self[name].instance.setBackgroundColor(gRGB(int(color[1:], 16)))
        except Exception: pass

    def _set_fg(self, name, color):
        try: self[name].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception: pass

    def _render_rows_full(self):
        for i in range(self.ROWS):
            if i < len(self.entries):
                label, key, element = self.entries[i]; selected = i == self._index
                signature = (label, self._display_text(key, element), selected)
                if self._row_cache.get(i) == signature: continue
                self._row_cache[i] = signature
                bg = theme.PANEL_SELECTED if selected else (theme.PANEL_ROW if i % 2 == 0 else theme.PANEL_ROW_ALT)
                self["rowlabel%d" % i].setText(label); self["rowvalue%d" % i].setText(self._display_text(key, element))
                for name in ("rowbg%d" % i, "rowlabel%d" % i, "rowvalue%d" % i): self._set_bg(name, bg)
                self._set_bg("rowcursor%d" % i, theme.ACCENT_PRIMARY if selected else bg)
                self._set_fg("rowlabel%d" % i, theme.WHITE if selected else theme.TEXT)
                self._set_fg("rowvalue%d" % i, theme.ACCENT_PRIMARY if selected else theme.WHITE)
            else:
                if self._row_cache.get(i) == ("empty",): continue
                self._row_cache[i] = ("empty",)
                self["rowlabel%d" % i].setText(""); self["rowvalue%d" % i].setText("")
                self._set_bg("rowbg%d" % i, theme.PANEL); self._set_bg("rowcursor%d" % i, theme.PANEL)
        self._update_help()

    def _current(self): return self.entries[self._index] if self.entries else None
    def _up(self):
        if self.entries: self._index = (self._index - 1) % len(self.entries); self._render_rows()
    def _down(self):
        if self.entries: self._index = (self._index + 1) % len(self.entries); self._render_rows()

    @staticmethod
    def _clamp(value, low, high): return max(low, min(high, value))

    def _adjust_current(self, direction):
        cur = self._current()
        if not cur: return
        _label, key, element = cur
        try:
            value = element.value
            if key == "schedule_mode":
                values = ["daily", "weekly", "monthly", "interval"]
                idx = values.index(str(value)) if str(value) in values else 0
                element.value = values[(idx + direction) % len(values)]
                self._build_entries()
            elif key in ("daily_update_time", "background_local_time", "native_import_time"):
                h, m = int(value[0]), int(value[1])
                step = 5 if key == "native_import_time" else 15
                total = (h * 60 + m + direction * step) % 1440
                element.value = [total // 60, total % 60]
            elif key == "weekly_update_day": element.value = str((int(value) + direction) % 7)
            elif key == "monthly_update_day": element.value = self._clamp(int(value) + direction, 1, 28)
            elif key == "update_interval_hours": element.value = self._clamp(int(value) + direction, 1, 24)
            elif key == "safe_auto_map_threshold": element.value = self._clamp(int(value) + direction, 90, 100)
            elif key == "suggestion_threshold": element.value = self._clamp(int(value) + direction, 80, 96)
            elif key == "native_long_desc_days": element.value = self._clamp(int(value) + direction, 0, 14)
            elif key == "epg_days": element.value = self._clamp(int(value) + direction, 1, 14)
            elif key == "parallel_workers": element.value = self._clamp(int(value) + direction, 1, 8)
            elif key == "retry_count": element.value = self._clamp(int(value) + direction, 1, 10)
            elif key == "timeout_seconds": element.value = self._clamp(int(value) + direction * 5, 5, 60)
            elif key == "logging_level":
                values = ["DEBUG", "INFO", "WARNING", "ERROR"]
                idx = values.index(str(value)) if str(value) in values else 1
                element.value = values[(idx + direction) % len(values)]
            elif key == "performance_mode":
                values = ["low", "balanced", "fast"]
                idx = values.index(str(value)) if str(value) in values else 1
                element.value = values[(idx + direction) % len(values)]
            elif key in ("background_source_updates", "download_schedule_enabled", "auto_sync_enabled", "native_import_after_sync", "native_import_schedule_enabled", "debug_mode", "native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import", "auto_jump_best_match", "map_next_unmapped", "protect_manual_mappings", "color_id_health", "safe_source_replacement", "reject_abnormal_drop", "skip_unchanged_sources"):
                element.value = not bool(value)
            elif key == "epg_output_dir": return
        except Exception:
            pass
        self._render_rows()

    def _left(self): self._adjust_current(-1)
    def _right(self): self._adjust_current(1)

    def _ok(self):
        cur = self._current()
        if not cur: return
        _label, key, element = cur
        if key == "epg_output_dir":
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(self._path_edited, VirtualKeyBoard, title="EPG XMLTV directory", text=str(element.value))
            except Exception:
                self["help_text"].setText("Virtual keyboard is unavailable on this image. The directory was not changed.")
            return
        self._adjust_current(1)

    def _path_edited(self, text):
        if text:
            cur = self._current()
            if cur and cur[1] == "epg_output_dir":
                try: cur[2].value = str(text).strip()
                except Exception: pass
        self._render_rows()

    def _update_help(self):
        cur = self._current(); label = cur[0] if cur else ""
        if label.startswith("EPG Import start time"):
            title, text = self.HELP["EPG Import start time"]
            text = text + "\n\n" + _import_time_local_note(cur[2])
        else:
            title, text = self.HELP.get(label, ("Main Settings", "EPG Manager global configuration."))
        self["help_title"].setText(title); self["help_text"].setText(text)

    def _refresh_clock(self):
        now = time.localtime(); self["clock"].setText(time.strftime("%H:%M", now)); self["date"].setText(time.strftime("%a\n%d %b", now))

    def recommended(self):
        c = self._native_config()
        if c is None: return
        values = {
            "background_source_updates": False,
            "download_schedule_enabled": False, "schedule_mode": "daily", "daily_update_time": [6, 0],
            "native_import_schedule_enabled": True, "native_import_time": [8, 20],
            "auto_sync_enabled": False,
            "native_import_after_sync": True, "native_import_only_iptv": False, "native_clear_before_import": False,
            "native_long_desc_days": 5, "native_confirm_before_import": True, "epg_days": 7, "parallel_workers": 2,
            "retry_count": 2, "timeout_seconds": 20, "logging_level": "INFO", "debug_mode": False,
            "auto_jump_best_match": True, "map_next_unmapped": False, "protect_manual_mappings": True, "color_id_health": True,
            "performance_mode": "balanced", "safe_source_replacement": True,
            "reject_abnormal_drop": True, "skip_unchanged_sources": True,
        }
        for key, value in values.items():
            element = getattr(c, key, None)
            if element is not None:
                try: element.value = self._clone_value(value)
                except Exception: pass
        self._build_entries(); self._render_rows()

    def open_native_options(self):
        # Advanced/manual import options remain available without exposing a
        # second scheduler. The working EPG Import engine itself is unchanged.
        from .native_import import NativeImportScreen
        self.session.open(NativeImportScreen, self.epg_config, None)

    def save(self):
        c = self._native_config()
        if c is not None:
            # beta51: native_import_schedule_enabled is now a real user setting.
            # Keep the legacy immediate-import switch off to avoid two automatic
            # imports (one after update + one at the configured start time).
            try: c.auto_import_after_update.value = False
            except Exception: pass
            # rc58: receiver-side local source maintenance is retired. EPG
            # source generation runs on GitHub; keep the legacy switch OFF.
            try: c.background_source_updates.value = False
            except Exception: pass
            for name in self._ALL_FIELDS:
                element = getattr(c, name, None)
                if element is not None and hasattr(element, "save"):
                    try: element.save()
                    except Exception: pass
            try:
                from Components.config import configfile
                configfile.save()
            except Exception:
                pass
            try:
                if hasattr(self.epg_config, "save_native_settings"):
                    self.epg_config.save_native_settings()
            except Exception:
                pass
            # rc70: enable/disable/time changes take effect immediately.
            try:
                from .. import plugin as plugin_entry
                plugin_entry.refresh_native_import_scheduler()
            except Exception:
                pass
        self.close(True)

    def cancel(self):
        c = self._native_config()
        if c is not None:
            for name, original in self._original.items():
                element = getattr(c, name, None)
                if element is not None:
                    try: element.value = self._clone_value(original)
                    except Exception: pass
        self.close(False)

    def _cleanup(self):
        try: self._clock_timer.stop()
        except Exception: pass


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (EPGManagerSettingsScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
