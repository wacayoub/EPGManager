# -*- coding: utf-8 -*-
"""EPG-Importer style configuration and native Enigma2 import screen.

The UI intentionally follows the familiar EPG Import workflow, while the
engine remains fully standalone: no EPG-Importer plugin dependency is used.
"""
from __future__ import print_function

import threading
import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eTimer, gRGB

from ..core import native_importer
from ..core.logger import get_logger
from . import theme

log = get_logger(__name__)


class NativeImportScreen(Screen):
    ROWS = 13
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
        "AI assisted channel matching": ("Adaptive matching", "Local intelligent matching learns from your saved mappings and compares normalized channel names. No cloud service or account is used. Sources: Native + EPG-Importer + Online."),
        "AI confidence threshold": ("AI confidence", "Minimum confidence required before an unmapped XMLTV channel can be matched automatically. 94 is recommended; increase it for stricter matching."),
        "Preflight diagnostics": ("Preflight diagnostics", "Before importing, verify selected XMLTV files, XML validity and mapping coverage. Strongly recommended."),
        "Load EPG only for IPTV channels": ("IPTV only", "When enabled, satellite/DVB services are skipped during native import."),
        "Delete current EPG before import": ("Clear current EPG", "Flush the Enigma2 EPG cache immediately before importing. Leave disabled for normal incremental use."),
        "Load long descriptions up to X days": ("Long descriptions", "Keep full programme descriptions only for this many future days. 0 keeps titles/short text only."),
        "Confirm before manual import": ("Manual confirmation", "Ask Yes/No before starting a manual import."),
        "Restart Enigma2 after import": ("Restart after import", "If enabled, ask to restart Enigma2 when a manual import completes."),
        "EPG XMLTV directory": ("XMLTV directory", "Folder containing generated/downloaded XMLTV files used by the native importer."),
    }

    def __init__(self, session, config):
        Screen.__init__(self, session)
        self.session = session
        self.epg_config = config
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
        self["hint"] = Label("UP/DOWN navigate  •  LEFT/RIGHT change  •  OK edit/select  •  YELLOW manual  •  BLUE sources")
        self["right_title"] = Label("Automatic import EPG")
        self["right_text"] = Label("")
        self["right_state"] = Label("READY")
        for key, text in (("red", "Cancel"), ("green", "Save"), ("yellow", "Manual"), ("blue", "Sources")):
            self["key_%s_bar" % key] = Label("")
            self["key_%s" % key] = Label(text)
        for i in range(self.ROWS):
            self["rowbg%d" % i] = Label("")
            self["rowcursor%d" % i] = Label("")
            self["rowlabel%d" % i] = Label("")
            self["rowvalue%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["SetupActions", "ColorActions", "OkCancelActions", "DirectionActions", "MenuActions"],
            {"cancel": self.cancel, "red": self.cancel, "green": self.save,
             "yellow": self.manual_import, "blue": self.open_sources,
             "menu": self.open_mapping, "up": self._up, "down": self._down,
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
        for key in ("auto_import_after_update", "daily_update_time", "epg_days", "update_interval_hours",
                    "native_import_only_iptv", "native_clear_before_import", "native_long_desc_days",
                    "native_confirm_before_import", "native_restart_after_import", "native_ai_match",
                    "native_ai_threshold", "native_preflight", "epg_output_dir"):
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
        self.entries = [
            ("Automatic import EPG", "auto_import_after_update", c.auto_import_after_update, False),
            ("Automatic start time", "daily_update_time", c.daily_update_time, False),
            ("Choose days to import", "epg_days", c.epg_days, False),
            ("Hours after which import is repeated", "update_interval_hours", c.update_interval_hours, False),
            ("AI assisted channel matching", "native_ai_match", c.native_ai_match, False),
            ("AI confidence threshold", "native_ai_threshold", c.native_ai_threshold, False),
            ("Preflight diagnostics", "native_preflight", c.native_preflight, False),
            ("Load EPG only for IPTV channels", "native_import_only_iptv", c.native_import_only_iptv, False),
            ("Delete current EPG before import", "native_clear_before_import", c.native_clear_before_import, False),
            ("Load long descriptions up to X days", "native_long_desc_days", c.native_long_desc_days, False),
            ("Confirm before manual import", "native_confirm_before_import", c.native_confirm_before_import, False),
            ("Restart Enigma2 after import", "native_restart_after_import", c.native_restart_after_import, False),
            ("EPG XMLTV directory", "epg_output_dir", c.epg_output_dir, False),
        ]

    def _display(self, key, element):
        if element is None: return ""
        v = element.value
        if key == "daily_update_time":
            try: return "%02d:%02d" % (int(v[0]), int(v[1]))
            except Exception: return "06:00"
        if key in ("auto_import_after_update", "native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import", "native_restart_after_import", "native_ai_match", "native_preflight"):
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
            if key == "daily_update_time":
                total = (int(v[0]) * 60 + int(v[1]) + 15 * direction) % 1440
                e.value = [total // 60, total % 60]
            elif key == "epg_days": e.value = max(1, min(14, int(v) + direction))
            elif key == "update_interval_hours": e.value = max(1, min(24, int(v) + direction))
            elif key == "native_long_desc_days": e.value = max(0, min(14, int(v) + direction))
            elif key == "native_ai_threshold": e.value = max(85, min(100, int(v) + direction))
            elif key in ("auto_import_after_update", "native_import_only_iptv", "native_clear_before_import", "native_confirm_before_import", "native_restart_after_import", "native_ai_match", "native_preflight"):
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
        self["right_text"].setText(text + "\n\nYELLOW  Import now\nBLUE  Sources\nMENU  Smart Mapping")

    def save(self):
        """Persist every editable Native Import option and verify it immediately."""
        c = self._native_config()
        saved = 0
        errors = []
        if c is not None:
            for _label, key, element, fixed in self.entries:
                if fixed or element is None:
                    continue
                try:
                    if hasattr(element, "save"):
                        element.save()
                    saved += 1
                except Exception as exc:
                    errors.append("%s: %s" % (key, exc))
            try:
                from Components.config import configfile
                configfile.save()
            except Exception as exc:
                errors.append("configfile: %s" % exc)
        if errors:
            self["right_state"].setText("SAVE ERROR")
            self["right_text"].setText("Could not save all settings.\n\n" + "\n".join(errors[:4]))
            return False
        self._original = self._take_snapshot()
        self["right_state"].setText("SAVED • %d SETTINGS" % saved)
        self["right_text"].setText("Native Import settings saved successfully.\n\nChanges are active immediately and will survive an Enigma2 restart.")
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
        except Exception: return "/etc/epgimport/jedi_epg"

    def manual_import(self):
        if self._scan_busy: return
        if self.epg_config.get_native_confirm_before_import():
            self.session.openWithCallback(self._manual_confirmed, MessageBox,
                "Native EPG import will start.\nThis may take a few minutes.\n\nContinue?", MessageBox.TYPE_YESNO, default=True)
        else:
            self._manual_confirmed(True)

    def _manual_confirmed(self, confirmed):
        if not confirmed: return
        # Import always uses the values currently visible on screen.
        self.save()
        self._scan_busy = True
        self._phase = "scan"
        self._progress = {"phase":"scan","file_index":0,"total_files":0,"parsed_programmes":0,"events_ready":0}
        self.plan = None
        self["right_state"].setText("SCANNING...")
        self["right_text"].setText("Preparing native EPG import...\n\n0 XMLTV files processed\n0 programmes parsed\n0 mapped events ready")
        def worker():
            try:
                from ..core.native_source_store import NativeSourceStore
                selected_ids = NativeSourceStore().get_selected()
                threshold = self.epg_config.get_native_ai_threshold()
                adaptive = self.epg_config.get_native_ai_match()
                if self.epg_config.get_native_preflight():
                    self._progress = {"phase":"preflight","source_name":"Checking XMLTV sources",
                                      "file_index":0,"total_files":0,"parsed_programmes":0,"events_ready":0}
                    check = native_importer.preflight(self._epg_dir(), source_ids=selected_ids,
                        only_iptv=self.epg_config.get_native_import_only_iptv(),
                        min_score=threshold, adaptive=adaptive)
                    self._preflight_result = check
                    if not check.get("ok"):
                        raise RuntimeError("Preflight failed: " + "; ".join(check.get("errors") or ["No usable XMLTV source"]))
                def progress(info):
                    self._progress = dict(info or {})
                self._scan_result = native_importer.build_import_plan(
                    self._epg_dir(), min_score=threshold,
                    only_iptv=self.epg_config.get_native_import_only_iptv(),
                    long_desc_days=self.epg_config.get_native_long_desc_days(),
                    source_ids=selected_ids, progress_cb=progress, adaptive=adaptive)
                self._scan_error = None
            except Exception as exc:
                log.exception("Native import scan failed")
                self._scan_result = None; self._scan_error = str(exc)
        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(250, False)

    def _poll_scan(self):
        if self._phase == "scan":
            info=self._progress or {}
            fi=int(info.get("file_index",0) or 0); total=int(info.get("total_files",0) or 0)
            parsed=int(info.get("parsed_programmes",0) or 0); ready=int(info.get("events_ready",0) or 0)
            source=info.get("source_name") or "Preparing sources"
            pct=int((fi*100.0/total)) if total else 0
            phase=info.get("phase")
            self["right_state"].setText("AI PREFLIGHT" if phase == "preflight" else "SCANNING %d%%" % pct)
            self["right_text"].setText("%s\n\n%d / %d XMLTV sources\n%d programmes parsed\n%d mapped events ready" % (source,fi,total,parsed,ready))
            if not self._scan_busy or (not hasattr(self, "_scan_result") and not hasattr(self, "_scan_error")):
                return
            self._scan_busy = False
            err = getattr(self, "_scan_error", None); plan = getattr(self, "_scan_result", None)
            for name in ("_scan_error", "_scan_result"):
                if hasattr(self, name): delattr(self, name)
            if err or not plan:
                self._phase="idle"; self._timer.stop()
                self["right_state"].setText("ERROR")
                self["right_text"].setText(err or "Could not build native import plan.")
                return
            self.plan = plan
            if not plan.get("events_ready"):
                self._phase="idle"; self._timer.stop()
                self["right_state"].setText("MAPPING NEEDED")
                self["right_text"].setText("No mapped EPG events are ready.\n\n%d programmes parsed\n%d unmapped programmes\n\nOpen Smart Mapping with MENU and assign channels first." % (plan.get("parsed_programmes",0),plan.get("skipped_unmapped",0)))
                return
            self._start_incremental_import(plan)
            return
        if self._phase == "import":
            self._import_step()

    def _start_incremental_import(self, plan):
        try:
            from enigma import eEPGCache
            cache=eEPGCache.getInstance()
            if cache is None or not hasattr(cache,"importEvents"):
                raise RuntimeError("This Enigma2 image does not expose eEPGCache.importEvents")
            if self.epg_config.get_native_clear_before_import() and hasattr(cache,"flushEPG"):
                cache.flushEPG()
            self._import_cache=cache
            self._import_services=list(plan.get("services",{}).items())
            self._import_index=0
            self._imported_services=0
            self._imported_events=0
            self._import_failed=[]
            self._phase="import"
            self["right_state"].setText("IMPORTING 0%")
            self["right_text"].setText(self._plan_summary(plan, importing=True))
        except Exception as exc:
            log.exception("Native EPG import start failed")
            self._phase="idle"; self._timer.stop()
            self["right_state"].setText("IMPORT FAILED")
            self["right_text"].setText(str(exc))

    def _plan_summary(self, plan, importing=False):
        types=plan.get("service_types") or {}
        lines=[
            "%d XMLTV sources" % int(plan.get("files",0) or 0),
            "%d programmes parsed" % int(plan.get("parsed_programmes",0) or 0),
            "%d mapped Enigma2 channels" % int(plan.get("mapped_services",0) or 0),
            "%d events ready" % int(plan.get("events_ready",0) or 0),
            "%d unmapped programmes skipped" % int(plan.get("skipped_unmapped",0) or 0),
            "SAT %d  •  IPTV %d  •  DVB %d" % (types.get("SAT",0),types.get("IPTV",0),types.get("DVB",0)),
        ]
        if importing:
            lines += ["", "%d / %d channels imported" % (self._imported_services, len(getattr(self,"_import_services",[]))), "%d events imported" % self._imported_events]
        return "\n".join(lines)

    def _import_step(self):
        services=getattr(self,"_import_services",[])
        total=len(services)
        if self._import_index >= total:
            self._finish_incremental_import()
            return
        # Small chunks keep the Enigma2 UI responsive while still importing quickly.
        for _ in range(3):
            if self._import_index >= total: break
            service_ref, events=services[self._import_index]
            self._import_index += 1
            if not events: continue
            try:
                native_importer._import_events_compat(self._import_cache, service_ref, events)
                self._imported_services += 1
                self._imported_events += len(events)
            except Exception as exc:
                self._import_failed.append((service_ref,str(exc)))
        pct=int(self._import_index*100.0/total) if total else 100
        self["right_state"].setText("IMPORTING %d%%" % pct)
        self["right_text"].setText(self._plan_summary(self.plan, importing=True) + "\n\nCurrent: %d / %d services" % (self._import_index,total))

    def _finish_incremental_import(self):
        try:
            if hasattr(self._import_cache,"save"): self._import_cache.save()
        except Exception:
            pass
        self._timer.stop(); self._phase="idle"
        total=len(getattr(self,"_import_services",[]))
        failed=len(getattr(self,"_import_failed",[]))
        self["right_state"].setText("IMPORT COMPLETE" if self._imported_services else "IMPORT FAILED")
        failure_detail = ""
        if self._import_failed:
            failure_detail = "\n\nFirst errors:\n" + "\n".join("%s: %s" % (ref, err) for ref, err in self._import_failed[:3])
        self["right_text"].setText("Native EPG import finished.\n\n%d / %d channels imported\n%d events imported\n%d failed channels\n\n%s%s" % (
            self._imported_services,total,self._imported_events,failed,self._plan_summary(self.plan, importing=False),failure_detail))
        if self.epg_config.get_native_restart_after_import() and self._imported_services:
            self.session.openWithCallback(self._restart_answer, MessageBox,
                "EPG import finished.\n\nRestart Enigma2 now?", MessageBox.TYPE_YESNO, default=False)

    def _restart_answer(self, yes):
        if not yes: return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception:
            pass

    def open_sources(self):
        try:
            from ..plugin import get_manager
            from .native_sources import NativeSourcesScreen
            self.session.open(NativeSourcesScreen, get_manager(), self.epg_config)
        except Exception as exc:
            self.session.open(MessageBox, "Could not open Sources.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.epg_config, True)

    def _cleanup(self):
        for t in (self._timer, self._clock_timer):
            try: t.stop()
            except Exception: pass
