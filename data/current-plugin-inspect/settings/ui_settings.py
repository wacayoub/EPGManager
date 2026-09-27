# -*- coding: utf-8 -*-
"""EPG Manager settings screen.

This screen deliberately does NOT depend on the native configuration-list screen for remote-key
handling.  Some OpenATV/Metrix builds render ConfigList correctly but do not
route LEFT/RIGHT/OK to a hidden or tiny ConfigList component.  The result is a settings page that looks correct but cannot be changed.

The editor below owns its focus/index and edits the native Enigma2 ConfigElement
objects directly.  GREEN persists them; RED/EXIT restores the snapshot taken
when the screen opened.  This makes the behaviour deterministic across images.
"""
from __future__ import print_function

import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eTimer, gRGB

from . import theme


class EPGManagerSettingsScreen(Screen):
    ROWS = 12
    ROW_Y = 270
    ROW_H = 58

    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 994, 420, 60, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 510, 994, 420, 60, theme.BTN_GREEN)
    )

    _ROWS_SKIN = ""
    for _i in range(ROWS):
        _y = ROW_Y + _i * ROW_H
        _ROWS_SKIN += (
            '<widget name="rowbg%d" position="82,%d" size="1112,54" backgroundColor="%%(PANEL_ROW)s" />\n'
            '<widget name="rowcursor%d" position="82,%d" size="7,54" backgroundColor="%%(PANEL_ROW)s" />\n'
            '<widget name="rowlabel%d" position="108,%d" size="490,54" font="Regular;25" '
            'foregroundColor="%%(TEXT)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" valign="center" />\n'
            '<widget name="rowvalue%d" position="610,%d" size="555,54" font="Regular;25" '
            'foregroundColor="%%(TEXT)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" '
            'halign="right" valign="center" />\n'
        ) % (_i, _y, _i, _y, _i, _y, _i, _y)

    skin = ("""
    <screen name="EPGManagerSettingsScreen" position="0,0" size="1920,1080"
            title="EPG Manager - Settings" backgroundColor="%(BG)s" flags="wfNoBorder">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,30" size="1120,62" font="Regular;42"
                 foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="date" position="1320,20" size="260,62" font="Regular;22"
                 halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="clock" position="1580,18" size="300,62" font="Regular;45"
                 halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="header_rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

        <widget name="left_panel" position="55,122" size="1180,850" backgroundColor="%(PANEL)s" />
        <widget name="section" position="82,144" size="1112,40" font="Regular;27"
                 foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="hint" position="82,188" size="1112,50" font="Regular;21"
                 foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="config_rule" position="82,246" size="1112,2" backgroundColor="%(MUTED_TEXT)s" />
        """ + _ROWS_SKIN + """

        <widget name="right_panel" position="1260,122" size="620,850" backgroundColor="%(PANEL_BLUE)s" />
        <widget name="help_title" position="1305,190" size="530,50" font="Regular;31"
                 halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="help_rule" position="1365,262" size="410,2" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="help_text" position="1320,300" size="500,390" font="Regular;23"
                 halign="center" valign="center" foregroundColor="%(TEXT)s"
                 backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="help_keys" position="1320,742" size="500,150" font="Regular;20"
                 halign="center" foregroundColor="%(MUTED_TEXT)s"
                 backgroundColor="%(PANEL_BLUE)s" transparent="1" />

        <widget name="footer_bg" position="0,972" size="1920,108" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_strip_rule" position="55,972" size="1825,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    HELP = {
        "Schedule mode": ("Automatic schedule", "Choose how often EPG Manager runs automatically.\n\nDaily is recommended for most users."),
        "Update time (Casablanca)": ("Update time", "LEFT / RIGHT changes the time in 15-minute steps.\n\nUsed by Daily, Weekly and Monthly modes."),
        "Weekly update day": ("Weekly day", "Choose the weekday used when Schedule mode is Weekly."),
        "Monthly update day (1-28)": ("Monthly day", "Choose the day used for Monthly updates.\n\n1-28 works safely for every month."),
        "Update interval (hours)": ("Interval", "Run again after this many hours when Schedule mode is Interval."),
        "EPG days to fetch": ("EPG coverage", "Number of future EPG days requested from each source.\n\n7 days is a good default."),
        "EPG export directory": ("XMLTV output", "Press OK to edit the folder used for generated XMLTV files."),
        "Retry count": ("Retries", "How many times a failed network request is retried."),
        "Timeout (seconds)": ("Network timeout", "Maximum wait for one network request before retrying."),
        "Parallel workers": ("Parallel updates", "How many sources can update together.\n\n2-3 is recommended for Vu+ Zero 4K."),
        "Logging level": ("Logging", "Info is recommended. Debug is useful only when troubleshooting."),
        "Debug mode": ("Debug mode", "Enable additional diagnostic output only when troubleshooting."),
        "Auto import after update": ("Automatic native import", "After a source update, automatically inject mapped XMLTV events into Enigma2. Leave disabled until your mappings are validated."),
        "Safe auto-map threshold": ("Auto-map safety", "Only automatic matches at or above this confidence are accepted. 95% is recommended."),
    }

    _ALL_FIELDS = (
        "schedule_mode", "daily_update_time", "weekly_update_day",
        "monthly_update_day", "update_interval_hours", "epg_days",
        "epg_output_dir", "retry_count", "timeout_seconds",
        "parallel_workers", "logging_level", "debug_mode",
        "auto_import_after_update", "safe_auto_map_threshold",
    )

    def __init__(self, session, config):
        Screen.__init__(self, session)
        self.session = session
        self.epg_config = config
        self._index = 0
        self.entries = []
        self._original = self._take_snapshot()
        self._build_entries()

        self["header_bg"] = Label("")
        self["title"] = Label("EPG MANAGER  /  SETTINGS")
        self["date"] = Label("")
        self["clock"] = Label("")
        self["header_rule"] = Label("")
        self["left_panel"] = Label("")
        self["right_panel"] = Label("")
        self["section"] = Label("GENERAL & SCHEDULER")
        self["hint"] = Label("UP/DOWN navigate   •   LEFT/RIGHT change   •   OK edit/select   •   GREEN save   •   RED cancel")
        self["config_rule"] = Label("")
        self["help_title"] = Label("Setting help")
        self["help_rule"] = Label("")
        self["help_text"] = Label("")
        self["help_keys"] = Label("UP / DOWN   Navigate\nLEFT / RIGHT   Change value\nOK   Edit / select\nGREEN   Save changes\nRED / EXIT   Cancel")
        # Background first, keys after it: prevents footer labels being hidden
        # on OpenATV/Metrix images that respect widget creation order.
        self["footer_bg"] = Label("")
        self["key_strip_rule"] = Label("")
        self["key_red_bar"] = Label("")
        self["key_red"] = Label("Cancel")
        self["key_green_bar"] = Label("")
        self["key_green"] = Label("Save")
        for i in range(self.ROWS):
            self["rowbg%d" % i] = Label("")
            self["rowcursor%d" % i] = Label("")
            self["rowlabel%d" % i] = Label("")
            self["rowvalue%d" % i] = Label("")

        # One single ActionMap owns every key on this screen.  This avoids the
        # native config-list action-map priority conflict seen on OpenATV/Metrix.
        self["actions"] = ActionMap(
            ["SetupActions", "ColorActions", "OkCancelActions", "DirectionActions"],
            {
                "cancel": self.cancel,
                "red": self.cancel,
                "green": self.save,
                "up": self._up,
                "down": self._down,
                "left": self._left,
                "right": self._right,
                "ok": self._ok,
            },
            -1,
        )

        self.onLayoutFinish.append(self._render_rows)
        self._clock_timer = eTimer()
        cb = self._clock_timer.callback if hasattr(self._clock_timer, "callback") else self._clock_timer.timeout.get()
        cb.append(self._refresh_clock)
        self._clock_timer.start(1000, False)
        self.onClose.append(self._cleanup)
        self._refresh_clock()

    def _native_config(self):
        c = getattr(self.epg_config, "_c", None)
        if c is not None:
            return c
        try:
            from Components.config import config as e2config
            return getattr(getattr(e2config, "plugins", None), "epgmanager", None)
        except Exception:
            return None

    @staticmethod
    def _clone_value(value):
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return tuple(value)
        return value

    def _take_snapshot(self):
        c = self._native_config()
        snap = {}
        if c is None:
            return snap
        for name in self._ALL_FIELDS:
            element = getattr(c, name, None)
            if element is not None:
                try:
                    snap[name] = self._clone_value(element.value)
                except Exception:
                    pass
        return snap

    def _build_entries(self):
        c = self._native_config()
        if c is None:
            self.entries = []
            return
        mode = str(getattr(c.schedule_mode, "value", "daily"))
        entries = [("Schedule mode", "schedule_mode", c.schedule_mode)]
        if mode in ("daily", "weekly", "monthly"):
            entries.append(("Update time (Casablanca)", "daily_update_time", c.daily_update_time))
        if mode == "weekly":
            entries.append(("Weekly update day", "weekly_update_day", c.weekly_update_day))
        elif mode == "monthly":
            entries.append(("Monthly update day (1-28)", "monthly_update_day", c.monthly_update_day))
        elif mode == "interval":
            entries.append(("Update interval (hours)", "update_interval_hours", c.update_interval_hours))
        entries.extend([
            ("EPG days to fetch", "epg_days", c.epg_days),
            ("EPG export directory", "epg_output_dir", c.epg_output_dir),
            ("Retry count", "retry_count", c.retry_count),
            ("Timeout (seconds)", "timeout_seconds", c.timeout_seconds),
            ("Parallel workers", "parallel_workers", c.parallel_workers),
            ("Logging level", "logging_level", c.logging_level),
            ("Debug mode", "debug_mode", c.debug_mode),
            ("Auto import after update", "auto_import_after_update", c.auto_import_after_update),
            ("Safe auto-map threshold", "safe_auto_map_threshold", c.safe_auto_map_threshold),
        ])
        self.entries = entries[:self.ROWS]
        if self.entries:
            self._index = max(0, min(self._index, len(self.entries) - 1))
        else:
            self._index = 0

    def _display_text(self, key, element):
        try:
            value = element.value
        except Exception:
            return ""
        if key == "daily_update_time":
            try:
                return "%02d:%02d" % (int(value[0]), int(value[1]))
            except Exception:
                return "06:00"
        if key == "schedule_mode":
            return {
                "daily": "Daily (fixed time)",
                "weekly": "Weekly",
                "monthly": "Monthly",
                "interval": "Every N hours",
            }.get(str(value), str(value))
        if key == "weekly_update_day":
            names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            try:
                return names[int(value) % 7]
            except Exception:
                return str(value)
        if key == "logging_level":
            return str(value).title()
        if key in ("debug_mode", "auto_import_after_update"):
            return "Yes" if bool(value) else "No"
        if key in ("epg_days", "retry_count", "timeout_seconds", "parallel_workers", "monthly_update_day", "update_interval_hours", "safe_auto_map_threshold"):
            try:
                return str(int(value))
            except Exception:
                return str(value)
        return str(value)

    def _set_bg(self, name, color):
        try:
            self[name].instance.setBackgroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _set_fg(self, name, color):
        try:
            self[name].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _render_rows(self):
        for i in range(self.ROWS):
            if i < len(self.entries):
                label, key, element = self.entries[i]
                selected = i == self._index
                bg = theme.PANEL_SELECTED if selected else (theme.PANEL_ROW if i % 2 == 0 else theme.PANEL_ROW_ALT)
                self["rowlabel%d" % i].setText(label)
                self["rowvalue%d" % i].setText(self._display_text(key, element))
                self._set_bg("rowbg%d" % i, bg)
                self._set_bg("rowlabel%d" % i, bg)
                self._set_bg("rowvalue%d" % i, bg)
                self._set_bg("rowcursor%d" % i, theme.ACCENT_PRIMARY if selected else bg)
                self._set_fg("rowlabel%d" % i, theme.WHITE if selected else theme.TEXT)
                self._set_fg("rowvalue%d" % i, theme.ACCENT_PRIMARY if selected else theme.WHITE)
            else:
                self["rowlabel%d" % i].setText("")
                self["rowvalue%d" % i].setText("")
                self._set_bg("rowbg%d" % i, theme.PANEL)
                self._set_bg("rowcursor%d" % i, theme.PANEL)
        self._update_help()

    def _current(self):
        if not self.entries:
            return None
        return self.entries[self._index]

    def _up(self):
        if self.entries:
            self._index = (self._index - 1) % len(self.entries)
            self._render_rows()

    def _down(self):
        if self.entries:
            self._index = (self._index + 1) % len(self.entries)
            self._render_rows()

    @staticmethod
    def _clamp(value, low, high):
        return max(low, min(high, value))

    def _adjust_current(self, direction):
        current = self._current()
        if not current:
            return
        _label, key, element = current
        try:
            value = element.value
            if key == "schedule_mode":
                values = ["daily", "weekly", "monthly", "interval"]
                idx = values.index(str(value)) if str(value) in values else 0
                element.value = values[(idx + direction) % len(values)]
                self._index = 0
                self._build_entries()
            elif key == "daily_update_time":
                h, m = int(value[0]), int(value[1])
                total = (h * 60 + m + direction * 15) % (24 * 60)
                element.value = [total // 60, total % 60]
            elif key == "weekly_update_day":
                element.value = str((int(value) + direction) % 7)
            elif key == "monthly_update_day":
                element.value = self._clamp(int(value) + direction, 1, 28)
            elif key == "update_interval_hours":
                element.value = self._clamp(int(value) + direction, 1, 24)
            elif key == "epg_days":
                element.value = self._clamp(int(value) + direction, 1, 14)
            elif key == "retry_count":
                element.value = self._clamp(int(value) + direction, 1, 10)
            elif key == "timeout_seconds":
                element.value = self._clamp(int(value) + direction * 5, 5, 60)
            elif key == "parallel_workers":
                element.value = self._clamp(int(value) + direction, 1, 8)
            elif key == "logging_level":
                values = ["DEBUG", "INFO", "WARNING", "ERROR"]
                idx = values.index(str(value)) if str(value) in values else 1
                element.value = values[(idx + direction) % len(values)]
            elif key in ("debug_mode", "auto_import_after_update"):
                element.value = not bool(value)
            elif key == "epg_output_dir":
                # Text is edited with OK; LEFT/RIGHT should never corrupt a path.
                pass
        except Exception:
            pass
        self._render_rows()

    def _left(self):
        self._adjust_current(-1)

    def _right(self):
        self._adjust_current(1)

    def _ok(self):
        current = self._current()
        if not current:
            return
        _label, key, element = current
        if key == "epg_output_dir":
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(
                    self._path_edited,
                    VirtualKeyBoard,
                    title="EPG export directory",
                    text=str(element.value),
                )
                return
            except Exception:
                # If the image has no VirtualKeyBoard, keep the path untouched.
                self["help_text"].setText("Virtual keyboard is not available on this image.\n\nThe current EPG export directory was not changed.")
                return
        # OK is useful on booleans/selections and gives predictable behaviour
        # even on remotes where LEFT/RIGHT is intercepted by a skin/keymap.
        self._adjust_current(1)

    def _path_edited(self, text):
        if text is None:
            return
        current = self._current()
        if current and current[1] == "epg_output_dir":
            value = str(text).strip()
            if value:
                try:
                    current[2].value = value
                except Exception:
                    pass
        self._render_rows()

    def _update_help(self):
        current = self._current()
        label = current[0] if current else ""
        title, text = self.HELP.get(label, ("Settings", "Select an option on the left to see a short explanation."))
        self["help_title"].setText(title)
        self["help_text"].setText(text)

    def _refresh_clock(self):
        now = time.localtime()
        self["clock"].setText(time.strftime("%H:%M", now))
        self["date"].setText(time.strftime("%A\n%d %b", now))

    def save(self):
        c = self._native_config()
        if c is not None:
            for name in self._ALL_FIELDS:
                element = getattr(c, name, None)
                if element is not None and hasattr(element, "save"):
                    try:
                        element.save()
                    except Exception:
                        pass
            try:
                from Components.config import configfile
                configfile.save()
            except Exception:
                pass
        self.close(True)

    def cancel(self):
        c = self._native_config()
        if c is not None:
            for name, original in self._original.items():
                element = getattr(c, name, None)
                if element is not None:
                    try:
                        element.value = self._clone_value(original)
                    except Exception:
                        pass
        self.close(False)

    def _cleanup(self):
        try:
            self._clock_timer.stop()
        except Exception:
            pass
