# -*- coding: utf-8 -*-
"""EPG-Importer style configuration and native Enigma2 import screen.

The UI follows the familiar EPG Import workflow. Manual Import deliberately
uses the receiver's installed OE-Alliance EPG Import parsing/import engine on
EPG Manager's local XMLTV files and direct EPG-Import-style SRP channel maps.
"""
from __future__ import print_function

import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eTimer, gRGB

from ..core import native_epgimport_bridge
from ..core.logger import get_logger
from . import theme
from .compat import adapt_skin

log = get_logger(__name__)


class NativeImportScreen(Screen):
    ROWS = 10
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 994, 370, 60, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 445, 994, 370, 60, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 860, 994, 430, 60, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1335, 994, 535, 60, theme.BTN_BLUE)
    )
    _ROWS_SKIN = "".join(
        '<widget name="rowbg%d" position="82,%d" size="1112,48" backgroundColor="%%(PANEL_ROW)s" />'
        '<widget name="rowcursor%d" position="82,%d" size="7,48" backgroundColor="%%(PANEL_ROW)s" />'
        '<widget name="rowlabel%d" position="105,%d" size="680,48" font="Regular;25" valign="center" foregroundColor="%%(TEXT)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" />'
        '<widget name="rowvalue%d" position="790,%d" size="385,48" font="Regular;25" halign="right" valign="center" foregroundColor="%%(WHITE)s" backgroundColor="%%(PANEL_ROW)s" transparent="0" />'
        % (i, 274 + i * 52, i, 274 + i * 52, i, 274 + i * 52, i, 274 + i * 52)
        for i in range(ROWS)
    )

    skin = ("""
    <screen name="NativeImportScreen" position="0,0" size="1920,1080" title="EPG Import Configuration" backgroundColor="%(BG)s" flags="wfNoBorder">
        <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
        <widget name="title" position="55,30" size="1220,60" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="date" position="1450,24" size="180,72" font="Regular;20" halign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="clock" position="1650,18" size="220,76" font="Regular;42" halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="header_rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

        <widget name="left_panel" position="55,122" size="1180,850" backgroundColor="%(PANEL)s" />
        <widget name="section" position="82,150" size="1112,44" font="Regular;28" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="hint" position="82,195" size="1112,42" font="Regular;21" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="config_rule" position="82,246" size="1112,2" backgroundColor="%(MUTED_TEXT)s" />
        """ + _ROWS_SKIN + """

        <widget name="right_panel" position="1260,122" size="620,850" backgroundColor="%(PANEL_BLUE)s" />
        <widget name="right_title" position="1300,185" size="540,52" font="Regular;32" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="right_rule" position="1360,260" size="420,2" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="right_text" position="1310,305" size="520,420" font="Regular;23" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
        <widget name="right_state" position="1350,750" size="440,60" font="Regular;23" halign="center" valign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />

        <widget name="footer_bg" position="0,972" size="1920,108" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="key_strip_rule" position="55,972" size="1825,2" backgroundColor="%(RULE)s" />
        """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    HELP = {
        "Automatic import EPG": ("Automatic import EPG", "Enable native EPG import automatically after a successful EPG Manager source update."),
        "Automatic start time": ("Automatic start time", "Time used by the EPG Manager scheduler in Casablanca time."),
        "Choose days to import": ("EPG days", "Maximum future EPG coverage requested from the source generators."),
        "Hours after which import is repeated": ("Repeat import", "Interval used when the scheduler is set to Every N hours."),
        "Load EPG only for IPTV channels": ("IPTV only", "When enabled, satellite/DVB services are skipped during native import."),
        "Delete current EPG before import": ("Clear current EPG", "Flush the Enigma2 EPG cache immediately before importing. Leave disabled for normal incremental use."),
        "Load long descriptions up to X days": ("Long descriptions", "Keep full programme descriptions only for this many future days. 0 keeps titles/short text only."),
        "Confirm before manual import": ("Manual confirmation", "Ask Yes/No before starting a manual import."),
        "EPG XMLTV directory": ("XMLTV directory", "Folder containing generated/downloaded XMLTV files used by the native importer."),
    }

    def __init__(self, session, config, manager=None):
        Screen.__init__(self, session)
        self.session = session
        self.epg_config = config
        self.manager = manager
        self.entries = []
        self._index = 0
        self._scan_busy = False
        self._phase = "idle"
        self._progress = {}
        self.plan = None
        self._original = self._take_snapshot()
        self._build_entries()

        for name in ("header_bg", "header_rule", "left_panel", "config_rule", "right_panel", "right_rule", "footer_bg", "key_strip_rule"):
            self[name] = Label("")
        self["title"] = Label("EPG Import Configuration")
        self["date"] = Label("")
        self["clock"] = Label("")
        self["section"] = Label("NATIVE EPG IMPORT")
        self["hint"] = Label("UP/DOWN Navigate  •  LEFT/RIGHT Change  •  OK Edit  •  0 GitHub Sync  •  1 Manual Import  •  9 Settings")
        self["right_title"] = Label("Automatic import EPG")
        self["right_text"] = Label("")
        self["right_state"] = Label("READY")
        for key, text in (("red", "Cancel"), ("green", "Save"), ("yellow", "1  Manual"), ("blue", "0  Sources")):
            self["key_%s_bar" % key] = Label("")
            self["key_%s" % key] = Label(text)
        for i in range(self.ROWS):
            self["rowbg%d" % i] = Label("")
            self["rowcursor%d" % i] = Label("")
            self["rowlabel%d" % i] = Label("")
            self["rowvalue%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["SetupActions", "NumberActions", "ColorActions", "OkCancelActions", "DirectionActions", "MenuActions"],
            {"cancel": self.cancel, "red": self.cancel, "green": self.save,
             "yellow": self.manual_import, "blue": self.open_sources,
             "0": self.open_sources, "1": self.manual_import, "9": self.open_main_settings,
             "menu": self.open_main_settings, "up": self._up, "down": self._down,
             "left": lambda: self._adjust(-1), "right": lambda: self._adjust(1), "ok": self._ok}, -1)

        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get()
        cb.append(self._poll_scan)
        self._clock_timer = eTimer()
        cb2 = self._clock_timer.callback if hasattr(self._clock_timer, "callback") else self._clock_timer.timeout.get()
        cb2.append(self._refresh_clock)
        self._clock_timer.start(1000, False)
        self.onLayoutFinish.append(self._render)
        self.onClose.append(self._cleanup)
        self._refresh_clock()

    def _native_config(self):
        return getattr(self.epg_config, "_c", None)

    @staticmethod
    def _clone(v):
        return list(v) if isinstance(v, list) else v

    def _take_snapshot(self):
        c = self._native_config(); out = {}
        if c is None: return out
        for key in ("native_import_only_iptv", "native_clear_before_import",
                    "native_long_desc_days", "native_confirm_before_import", "epg_output_dir"):
            e = getattr(c, key, None)
            if e is not None:
                try: out[key] = self._clone(e.value)
                except Exception: pass
        return out

    def _build_entries(self):
        c = self._native_config()
        if c is None:
            self.entries = []
            return
        # Scheduling moved to the single Main Settings screen in v6.2.
        # Keep this page focused on the import engine itself.
        self.entries = [
            ("Load EPG only for IPTV channels", "native_import_only_iptv", c.native_import_only_iptv, False),
            ("Delete current EPG before import", "native_clear_before_import", c.native_clear_before_import, False),
            ("Load long descriptions up to X days", "native_long_desc_days", c.native_long_desc_days, False),
            ("Confirm before manual import", "native_confirm_before_import", c.native_confirm_before_import, False),
            ("EPG XMLTV directory", "epg_output_dir", c.epg_output_dir, False),
        ]

    def _display(self, key, element):
        if element is None: return ""
        v = element.value
        if key in ("native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import"):
            return "Yes" if bool(v) else "No"
        return str(v)

    def _set_bg(self, name, color):
        try: self[name].instance.setBackgroundColor(gRGB(int(color[1:], 16)))
        except Exception: pass

    def _set_fg(self, name, color):
        try: self[name].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception: pass

    def _render(self):
        for i in range(self.ROWS):
            if i < len(self.entries):
                label, key, element, fixed = self.entries[i]
                selected = i == self._index
                bg = theme.PANEL_SELECTED if selected else (theme.PANEL_ROW if i % 2 == 0 else theme.PANEL_ROW_ALT)
                self["rowlabel%d" % i].setText(label)
                self["rowvalue%d" % i].setText(self._display(key, element))
                self._set_bg("rowbg%d" % i, bg)
                self._set_bg("rowlabel%d" % i, bg)
                self._set_bg("rowvalue%d" % i, bg)
                self._set_bg("rowcursor%d" % i, theme.ACCENT_PRIMARY if selected else bg)
                self._set_fg("rowlabel%d" % i, theme.WHITE if selected else theme.TEXT)
                self._set_fg("rowvalue%d" % i, theme.ACCENT_PRIMARY if selected and not fixed else theme.WHITE)
        self._update_help()

    def _current(self):
        return self.entries[self._index] if self.entries else None

    def _up(self):
        if self.entries: self._index = (self._index - 1) % len(self.entries); self._render()

    def _down(self):
        if self.entries: self._index = (self._index + 1) % len(self.entries); self._render()

    def _adjust(self, direction):
        cur = self._current()
        if not cur or cur[3]: return
        _label, key, e, _fixed = cur
        try:
            v = e.value
            if key == "native_long_desc_days": e.value = max(0, min(14, int(v) + direction))
            elif key in ("native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import"):
                e.value = not bool(v)
        except Exception: pass
        self._render()

    def _ok(self):
        cur = self._current()
        if not cur: return
        _label, key, e, fixed = cur
        if fixed: return
        if key == "epg_output_dir":
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(self._path_edited, VirtualKeyBoard, title="EPG XMLTV directory", text=str(e.value))
                return
            except Exception:
                return
        self._adjust(1)

    def _path_edited(self, text):
        if text:
            try: self._native_config().epg_output_dir.value = str(text).strip()
            except Exception: pass
        self._render()

    def _update_help(self):
        cur = self._current(); label = cur[0] if cur else ""
        title, text = self.HELP.get(label, ("Native EPG Import", "Standalone XMLTV import directly into the Enigma2 EPG cache."))
        self["right_title"].setText(title)
        try:
            ih, im = self.epg_config.get_native_import_time()
            auto = "ON" if self.epg_config.get_native_import_schedule_enabled() else "OFF"
            schedule = "\n\nAuto Import: %s at %02d:%02d (Casablanca)" % (auto, ih, im)
        except Exception:
            schedule = ""
        self["right_text"].setText(text + schedule + "\n\nYELLOW  Import now\nBLUE  GitHub Sync\nMENU  Main Settings")

    def save(self, close_after=True):
        """Persist Native Import settings reliably on OpenATV/OE-A.

        Like EPG Import, GREEN saves the current values and closes the screen.
        Manual import calls the same routine with close_after=False so the
        values visible on screen are committed before the import starts.
        """
        errors = []
        saved = 0
        try:
            if hasattr(self.epg_config, "save_native_settings"):
                self.epg_config.save_native_settings()
                saved = len([x for x in self.entries if not x[3]])
            else:
                c = self._native_config()
                if c is not None:
                    for _label, key, element, fixed in self.entries:
                        if fixed or element is None:
                            continue
                        if hasattr(element, "save"):
                            element.save()
                        saved += 1
                    from Components.config import configfile
                    configfile.save()
        except Exception as exc:
            log.exception("Native Import settings save failed")
            errors.append(str(exc))
        if errors:
            self["right_state"].setText("SAVE ERROR")
            self["right_text"].setText("Settings were not saved.\n\n%s" % "\n".join(errors[:3]))
            try:
                self.session.open(MessageBox, "Native Import settings could not be saved.\n\n%s" % errors[0], MessageBox.TYPE_ERROR)
            except Exception:
                pass
            return False
        self._original = self._take_snapshot()
        self["right_state"].setText("SAVED")
        self["right_text"].setText("Settings saved successfully.\n\nThey are active immediately. No Enigma2 restart is required.")
        if close_after:
            self.close(True)
        return True

    def cancel(self):
        c = self._native_config()
        if c is not None:
            for key, value in self._original.items():
                e = getattr(c, key, None)
                if e is not None:
                    try: e.value = self._clone(value)
                    except Exception: pass
        self.close(False)

    def _refresh_clock(self):
        self["clock"].setText(time.strftime("%H:%M"))
        self["date"].setText(time.strftime("%A\n%d %b"))

    def _epg_dir(self):
        try: return self.epg_config.get_epg_output_dir()
        except Exception: return "/etc/epgmanager_epg"

    def manual_import(self):
        if self._scan_busy: return
        if self.epg_config.get_native_confirm_before_import():
            self.session.openWithCallback(self._manual_confirmed, MessageBox,
                "Native EPG import will start.\nThis may take a few minutes.\n\nContinue?", MessageBox.TYPE_YESNO, default=True)
        else:
            self._manual_confirmed(True)

    def _manual_confirmed(self, confirmed):
        if not confirmed:
            return
        if not self.save(close_after=False):
            return

        # Manual Import is intentionally local/offline: it consumes jedi_epg only.
        # It does not scan Reception Lists, fuzzy-match channels or download data.
        # The selected-source/full cycle prepares direct EPG-Import-style
        # channel-id -> Service Reference maps before reaching this screen.
        self._scan_busy = True
        self._phase = "epgimport_bridge"
        try:
            from ..core.source_preferences import SourcePreferences
            selected_source_ids = SourcePreferences().selected_mapping_ids()
        except Exception:
            selected_source_ids = None
        self._runner = native_epgimport_bridge.EPGImportLocalRunner(
            epg_dir=self._epg_dir(),
            only_iptv=self.epg_config.get_native_import_only_iptv(),
            long_desc_days=self.epg_config.get_native_long_desc_days(),
            clear_before_import=self.epg_config.get_native_clear_before_import(),
            source_ids=selected_source_ids)

        try:
            sources, stats = self._runner.prepare()
        except Exception as exc:
            log.exception("Could not prepare Native Import")
            self._scan_busy = False
            self._phase = "idle"
            self["right_state"].setText("IMPORT FAILED")
            self["right_text"].setText("Could not read the Service Reference channel map.\n\n%s" % exc)
            return

        if not sources:
            self._scan_busy = False
            self._phase = "idle"
            self["right_state"].setText("NOTHING TO IMPORT")
            self["right_text"].setText(
                "%d local XMLTV files found\n%d empty files skipped\n%d source(s) have no Service Reference map\n\n"
                "Run Import Source / Import All first to prepare SRP maps. Manual Import itself stays offline." % (
                    int(stats.get("total_files", 0) or 0),
                    int(stats.get("empty_files", 0) or 0),
                    int(stats.get("unmapped_sources", 0) or 0)))
            return

        self["right_state"].setText("IMPORTING 0%")
        self["right_text"].setText(
            "Starting EPG Import engine...\n\n"
            "%d mapped XMLTV sources\n%d mapped channel IDs\n%d service references\n\nNo download. No channel scan." % (
                len(sources), int(stats.get("mapped_channel_ids", 0) or 0),
                int(stats.get("mapped_service_refs", 0) or 0)))

        try:
            self._runner.start()
        except Exception as exc:
            log.exception("Could not start OE-Alliance EPG Import engine")
            self._scan_busy = False
            self._phase = "idle"
            self["right_state"].setText("IMPORT FAILED")
            self["right_text"].setText(
                "EPG Import engine could not start.\n\n%s" % exc)
            return

        self._timer.start(250, False)

    def _poll_scan(self):
        if self._phase == "epgimport_bridge":
            runner = getattr(self, "_runner", None)
            if runner is None:
                self._timer.stop()
                self._scan_busy = False
                self._phase = "idle"
                self["right_state"].setText("IMPORT FAILED")
                self["right_text"].setText("Native Import runner was lost.")
                return

            info = runner.status()
            index = int(info.get("source_index", 0) or 0)
            total = int(info.get("total_sources", 0) or 0)
            events = int(info.get("events", 0) or 0)
            imported_live = int(info.get("imported_events", 0) or 0)
            failed_live = int(info.get("failed_events", 0) or 0)
            source = info.get("source_name") or "Preparing first mapped source"
            elapsed = float(info.get("elapsed", 0.0) or 0.0)
            # EPGImport pops the current source before importing it; count only
            # completed sources for the percentage while it is still running.
            completed = max(0, index - 1) if info.get("running") else total
            pct = int(completed * 100.0 / total) if total else 0
            if pct > 100:
                pct = 100

            self["right_state"].setText("IMPORTING %d%%" % pct)
            self["right_text"].setText(
                "Importing: %s\n\n%d / %d mapped XMLTV sources\n%d events processed\n%d events imported%s\n%.1f sec\n\nEngine: OE-Alliance EPG Import" % (
                    source, min(index, total), total, events, imported_live,
                    ("  •  %d failed" % failed_live) if failed_live else "", elapsed))

            if info.get("running"):
                return

            self._timer.stop()
            self._scan_busy = False
            self._phase = "idle"
            error = getattr(runner, "error", None)
            result = getattr(runner, "result", None)
            if error:
                self["right_state"].setText("IMPORT FAILED")
                self["right_text"].setText(str(error))
                return
            if not result:
                self["right_state"].setText("IMPORT FAILED")
                self["right_text"].setText("EPG Import finished without a result.")
                return

            count = int(result.get("imported_events", 0) or 0)
            if count <= 0:
                self["right_state"].setText("NOTHING IMPORTED")
                self["right_text"].setText(
                    "EPG Import finished but no mapped event was found.\n\n"
                    "%d mapped channel IDs\n%d mapped XMLTV sources\n%d empty files skipped\n\n"
                    "Check that the XMLTV channel IDs still match the saved Service Reference map." % (
                        int(result.get("mapped_channel_ids", 0) or 0),
                        int(result.get("sources", 0) or 0),
                        int(result.get("empty_files", 0) or 0)))
                return

            self["right_state"].setText("IMPORT COMPLETE")
            failed = int(result.get("failed_events", 0) or 0)
            self["right_text"].setText(
                "%d events imported\n%d Enigma2 services updated%s\n%d mapped XMLTV sources\n%d mapped channel IDs\n%.1f sec\n\nEPG is active immediately. No Enigma2 restart." % (
                    count, int(result.get("written_services", 0) or 0),
                    ("\n%d failed events" % failed) if failed else "",
                    int(result.get("sources", 0) or 0),
                    int(result.get("mapped_channel_ids", 0) or 0),
                    float(result.get("elapsed", 0.0) or 0.0)))
            return

    def open_sources(self):
        try:
            from ..plugin import get_manager
            from .github_sync import GitHubSyncScreen
            self.session.open(GitHubSyncScreen, self.manager or get_manager(), self.epg_config)
        except Exception as exc:
            self.session.open(MessageBox, "Could not open GitHub Sync.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def open_main_settings(self):
        try:
            from .settings import EPGManagerSettingsScreen
            self.session.open(EPGManagerSettingsScreen, self.epg_config, "native_import_schedule_enabled")
        except Exception:
            pass

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.epg_config, True, self.manager)

    def _cleanup(self):
        for t in (self._timer, self._clock_timer):
            try: t.stop()
            except Exception: pass


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (NativeImportScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
