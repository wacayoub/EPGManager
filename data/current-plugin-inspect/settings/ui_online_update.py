# -*- coding: utf-8 -*-
"""Online Update UI for EPG Manager."""
from __future__ import absolute_import

import threading
import time

from enigma import eTimer, gRGB, eSize
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label

from . import theme
from ..core.updater import OnlineUpdater, UpdateError, load_updater_settings, save_updater_settings
from ..version import __version__


class OnlineUpdateScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 994, 420, 60, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 510, 994, 420, 60, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 990, 994, 420, 60, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1470, 994, 420, 60, theme.BTN_BLUE)
    )
    skin = ("""
    <screen name="OnlineUpdateScreen" position="0,0" size="1920,1080"
            title="EPG Manager Online Update" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,30" size="1120,62" font="Regular;42" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="date" position="1320,20" size="260,62" font="Regular;22" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="clock" position="1580,18" size="300,62" font="Regular;45" halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="header_rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />

      <widget name="left_panel" position="55,122" size="1180,850" backgroundColor="%(PANEL)s" />
      <widget name="section" position="82,145" size="1112,45" font="Regular;28" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="status" position="82,215" size="1112,54" font="Regular;31" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="progress_track" position="82,292" size="1112,12" backgroundColor="%(PANEL_ROW_ALT)s" />
      <widget name="progress_fill" position="82,292" size="1,12" backgroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="current_label" position="82,345" size="300,42" font="Regular;23" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="current" position="400,345" size="760,42" font="Regular;26" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="latest_label" position="82,405" size="300,42" font="Regular;23" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="latest" position="400,405" size="760,42" font="Regular;26" foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="server_label" position="82,465" size="300,42" font="Regular;23" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="server" position="400,465" size="760,92" font="Regular;20" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="notes_title" position="82,590" size="1080,42" font="Regular;24" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="notes" position="82,638" size="1080,235" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />

      <widget name="right_panel" position="1260,122" size="620,850" backgroundColor="%(PANEL_BLUE)s" />
      <widget name="help_title" position="1305,190" size="530,50" font="Regular;31" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="help_rule" position="1365,262" size="410,2" backgroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="help_text" position="1320,310" size="500,470" font="Regular;23" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="help_status" position="1320,815" size="500,55" font="Regular;22" halign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />

      <widget name="footer_bg" position="0,972" size="1920,108" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_strip_rule" position="55,972" size="1825,2" backgroundColor="%(RULE)s" />
      """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session):
        Screen.__init__(self, session)
        self.session = session
        self.settings = load_updater_settings()
        self.updater = OnlineUpdater(__version__)
        self.manifest = None
        self.downloaded_ipk = None
        self._messages = []
        self._busy = False

        for name in ("header_bg", "header_rule", "left_panel", "progress_track", "progress_fill",
                     "right_panel", "help_rule", "footer_bg", "key_strip_rule"):
            self[name] = Label("")
        self["title"] = Label("EPG MANAGER  /  ONLINE UPDATE")
        self["date"] = Label("")
        self["clock"] = Label("")
        self["section"] = Label("ONE-CLICK ONLINE UPDATER")
        self["status"] = Label("Ready")
        self["current_label"] = Label("Installed version")
        self["current"] = Label(__version__)
        self["latest_label"] = Label("Online version")
        self["latest"] = Label("Not checked")
        self["server_label"] = Label("Update server")
        self["server"] = Label(self.settings.get("manifest_url") or "Not configured - press YELLOW")
        self["notes_title"] = Label("Release notes")
        self["notes"] = Label("")
        self["help_title"] = Label("Online Update")
        self["help_text"] = Label(
            "Configure the update.json URL once.\n\n"
            "GREEN checks the server.\n\n"
            "BLUE downloads, verifies and installs the new IPK.\n\n"
            "SHA-256 is verified when provided by the server.\n\n"
            "After installation you can restart Enigma2 directly."
        )
        self["help_status"] = Label("READY")
        for key, text in (("key_red", "Close"), ("key_green", "Check Update"),
                          ("key_yellow", "Update Server"), ("key_blue", "Install Update")):
            self[key + "_bar"] = Label("")
            self[key] = Label(text)

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions"],
            {"cancel": self.close, "red": self.close, "green": self.check_update,
             "yellow": self.edit_server, "blue": self.install_update, "ok": self.check_update}, -1)

        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get()
        cb.append(self._poll)
        self._timer.start(150, False)
        self._clock_timer = eTimer()
        cb2 = self._clock_timer.callback if hasattr(self._clock_timer, "callback") else self._clock_timer.timeout.get()
        cb2.append(self._refresh_clock)
        self._clock_timer.start(1000, False)
        self.onClose.append(self._cleanup)
        self._refresh_clock()
        if self.settings.get("manifest_url"):
            self.onLayoutFinish.append(self.check_update)

    def _refresh_clock(self):
        now = time.localtime()
        self["date"].setText(time.strftime("%A\n%d %b", now))
        self["clock"].setText(time.strftime("%H:%M", now))

    def _cleanup(self):
        for timer in (self._timer, self._clock_timer):
            try:
                timer.stop()
            except Exception:
                pass

    def _queue(self, kind, value=None):
        self._messages.append((kind, value))

    def _poll(self):
        while self._messages:
            kind, value = self._messages.pop(0)
            if kind == "status":
                self["status"].setText(str(value))
            elif kind == "latest":
                self["latest"].setText(str(value))
            elif kind == "notes":
                self["notes"].setText(str(value or ""))
            elif kind == "progress":
                done, total = value
                pct = int((100.0 * done / total)) if total else 0
                try:
                    self["progress_fill"].instance.resize(eSize(max(1, int(1112 * pct / 100.0)), 12))
                except Exception:
                    pass
                self["help_status"].setText("%d%%" % pct if total else "%d KB" % (done // 1024))
            elif kind == "checked":
                self.manifest = value
                tag = "%s%s" % (value.get("version"), "  NEW" if value.get("newer") else "  CURRENT")
                self["latest"].setText(tag)
                self["notes"].setText(value.get("notes") or "No release notes supplied.")
                self["status"].setText("Update available" if value.get("newer") else "You already have the latest version")
                self["help_status"].setText("UPDATE AVAILABLE" if value.get("newer") else "UP TO DATE")
                self._busy = False
            elif kind == "downloaded":
                self.downloaded_ipk = value
                self["status"].setText("Package verified. Installing...")
                thread = threading.Thread(target=self._install_worker)
                thread.daemon = True
                thread.start()
            elif kind == "installed":
                self._busy = False
                self["status"].setText("Update installed successfully")
                self["help_status"].setText("INSTALLED")
                self._ask_restart()
            elif kind == "error":
                self._busy = False
                self["status"].setText("ERROR: %s" % value)
                self["help_status"].setText("FAILED")

    def check_update(self):
        if self._busy:
            return
        url = (self.settings.get("manifest_url") or "").strip()
        if not url:
            self.edit_server()
            return
        self._busy = True
        self["status"].setText("Checking update server...")
        self["help_status"].setText("CHECKING")
        thread = threading.Thread(target=self._check_worker, args=(url,))
        thread.daemon = True
        thread.start()

    def _check_worker(self, url):
        try:
            info = self.updater.check(url)
            self._queue("checked", info)
        except Exception as exc:
            self._queue("error", exc)

    def edit_server(self):
        if self._busy:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(
                self._server_entered, VirtualKeyBoard,
                title="Online update manifest URL (update.json)",
                text=self.settings.get("manifest_url") or "https://")
        except Exception as exc:
            self._queue("error", "Keyboard unavailable: %s" % exc)

    def _server_entered(self, value):
        if value is None:
            return
        value = str(value).strip()
        self.settings["manifest_url"] = value
        try:
            save_updater_settings(self.settings)
            self["server"].setText(value or "Not configured - press YELLOW")
            self.manifest = None
            self["latest"].setText("Not checked")
            self["status"].setText("Update server saved")
            if value:
                self.check_update()
        except Exception as exc:
            self._queue("error", "Cannot save server URL: %s" % exc)

    def install_update(self):
        if self._busy:
            return
        if not self.manifest:
            self.check_update()
            return
        if not self.manifest.get("newer"):
            try:
                from Screens.MessageBox import MessageBox
                self.session.openWithCallback(
                    self._reinstall_answer, MessageBox,
                    "Version %s is already installed.\n\nReinstall it from the online server?" % __version__,
                    MessageBox.TYPE_YESNO, default=False)
                return
            except Exception:
                return
        self._start_download()

    def _reinstall_answer(self, answer):
        if answer:
            self._start_download()

    def _start_download(self):
        self._busy = True
        self["status"].setText("Downloading update...")
        self["help_status"].setText("DOWNLOADING")
        thread = threading.Thread(target=self._download_worker)
        thread.daemon = True
        thread.start()

    def _download_worker(self):
        try:
            path, _sha, _size = self.updater.download(self.manifest, progress=lambda d, t: self._queue("progress", (d, t)))
            self._queue("downloaded", path)
        except Exception as exc:
            self._queue("error", exc)

    def _install_worker(self):
        try:
            self.updater.install(self.downloaded_ipk)
            self._queue("installed", True)
        except Exception as exc:
            self._queue("error", exc)

    def _ask_restart(self):
        try:
            from Screens.MessageBox import MessageBox
            self.session.openWithCallback(
                self._restart_answer, MessageBox,
                "EPG Manager was updated successfully.\n\nRestart Enigma2 now?",
                MessageBox.TYPE_YESNO, default=True)
        except Exception:
            pass

    def _restart_answer(self, answer):
        if not answer:
            return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception:
            pass
