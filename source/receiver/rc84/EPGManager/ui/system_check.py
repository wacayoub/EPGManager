# -*- coding: utf-8 -*-
from __future__ import print_function

import threading
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ProgressBar import ProgressBar

from . import theme
from .compat import adapt_skin
from ..core import system_check


class SystemCheckScreen(Screen):
    skin = ("""
    <screen name="SystemCheckScreen" position="0,0" size="1920,1080" title="EPGManager System Check" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="head" position="0,0" size="1920,106" backgroundColor="%(PANEL)s" />
      <widget name="title" position="44,18" size="760,42" font="Regular;34" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="820,20" size="1035,38" font="Regular;23" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="hint" position="46,67" size="1780,26" font="Regular;17" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="progress" position="45,112" size="1830,8" borderWidth="0" backgroundColor="%(PROGRESS_TRACK)s" foregroundColor="%(ACCENT_SECONDARY)s" />
      <widget name="list" position="44,142" size="1832,778" scrollbarMode="showOnDemand" backgroundColor="%(PANEL_ALT)s" />
      <widget name="detail" position="47,932" size="1825,45" font="Regular;18" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="footer" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_red" position="42,1008" size="290,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_green" position="350,1008" size="390,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_yellow" position="760,1008" size="430,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_blue" position="1210,1008" size="500,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
    </screen>""") % theme.__dict__

    def __init__(self, session, config=None):
        Screen.__init__(self, session)
        self.config = config
        self.report = system_check.load_cache() or {}
        self.running = False
        self["head"] = Label(""); self["footer"] = Label("")
        self["title"] = Label("SYSTEM CHECK")
        self["summary"] = Label("Not checked yet")
        self["hint"] = Label("Offline-first: validates local XML, IDs, SRP ownership and receiver NOW/timezone alignment. Never changes mappings.")
        self["progress"] = ProgressBar(); self["progress"].setValue(0)
        self["list"] = MenuList([])
        self["detail"] = Label("")
        self["key_red"] = Label("Back")
        self["key_green"] = Label("Run Check")
        self["key_yellow"] = Label("Export Diagnostic")
        self["key_blue"] = Label("ID Health")
        self["actions"] = ActionMap(["OkCancelActions","ColorActions"], {
            "cancel": self.close, "red": self.close, "green": self.run_check,
            "yellow": self.export_diagnostic, "blue": self.open_id_health,
            "ok": self.show_selected,
        }, -1)
        self._paint(self.report)

    def _paint(self, report):
        report = report or {}
        rows = []
        for item in report.get("checks") or []:
            rows.append("[%s]  %-20s  %s" % (str(item.get("status") or "?").upper(), str(item.get("name") or ""), str(item.get("detail") or "")))
        tz = (report.get("timezone") or {}).get("sources") or {}
        if tz:
            rows.append("--- TIMEZONE BY SOURCE ---")
            for sid, item in sorted(tz.items()):
                rows.append("[%s]  %-28s  %s" % (str(item.get("status") or "?").upper(), sid, str(item.get("detail") or "")))
        if not rows:
            rows = ["Press GREEN to run the first full check."]
        self["list"].setList(rows)
        status = str(report.get("status") or "UNKNOWN")
        self["summary"].setText("%s • %d/%d sources • %d mapped • %d unresolved • %d no EPG" % (
            status, int(report.get("healthy_sources") or 0), int(report.get("direct_total") or 7),
            int(report.get("mapped_refs") or 0), int(report.get("unresolved") or 0), int(report.get("red_ids") or 0)))
        self["progress"].setValue(100 if status == "PASS" else (70 if status == "WARN" else (35 if status == "FAIL" else 0)))
        self["detail"].setText("Last check %.2fs • no network / no mapping changes" % float(report.get("elapsed") or 0.0) if report else "")

    def run_check(self):
        if self.running:
            self.session.open(MessageBox, "System Check is already running.", MessageBox.TYPE_INFO, timeout=2)
            return
        self.running = True
        self["summary"].setText("RUNNING • reading local feeds and receiver state…")
        self["progress"].setValue(15)
        self["key_green"].setText("Checking…")
        def worker():
            try:
                report = system_check.run(self.config)
                error = ""
            except Exception as exc:
                report = {}; error = str(exc)
            def done():
                self.running = False
                self["key_green"].setText("Run Check")
                if error:
                    self["summary"].setText("FAIL • %s" % error[:100])
                    self["progress"].setValue(10)
                    return
                self.report = report
                self._paint(report)
            try:
                from twisted.internet import reactor
                reactor.callFromThread(done)
            except Exception:
                done()
        threading.Thread(target=worker, name="EPGM-SystemCheck", daemon=True).start()

    def export_diagnostic(self):
        if self.running:
            self.session.open(MessageBox, "Wait for the current System Check to finish.", MessageBox.TYPE_INFO, timeout=2)
            return
        try:
            path = system_check.export_report(self.report or system_check.load_cache())
            self.session.open(MessageBox, "Diagnostic exported:\n%s\n\nA persistent copy is also kept in /etc/enigma2/epgmanager_last_diagnostic.txt" % path,
                              MessageBox.TYPE_INFO, timeout=7)
        except Exception as exc:
            self.session.open(MessageBox, "Diagnostic export failed:\n%s" % exc, MessageBox.TYPE_ERROR)

    def open_id_health(self):
        from .id_monitor import DirectIdMonitorScreen
        self.session.open(DirectIdMonitorScreen, self.config)

    def show_selected(self):
        try:
            idx = int(self["list"].getSelectedIndex())
            item = (self.report.get("checks") or [])[idx]
            self.session.open(MessageBox, "%s\n\n%s" % (item.get("name") or "Check", item.get("detail") or ""), MessageBox.TYPE_INFO)
        except Exception:
            pass

try:
    SystemCheckScreen.skin = adapt_skin(SystemCheckScreen.skin)
except Exception:
    pass
