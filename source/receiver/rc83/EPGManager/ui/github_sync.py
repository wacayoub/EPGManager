# -*- coding: utf-8 -*-
"""Read-only GitHub direct-source dashboard for rc35."""
from __future__ import print_function

import time

from enigma import eTimer, gRGB
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ProgressBar import ProgressBar

from . import theme
from .compat import adapt_skin
from ..core import github_direct_sync
from ..core.logger import get_logger

log = get_logger(__name__)


class GitHubSyncScreen(Screen):
    _KEYS = (
        theme.key_bar_skin("key_red", 32, 1008, 300, 52, theme.BTN_RED, 24) +
        theme.key_bar_skin("key_green", 356, 1008, 430, 52, theme.BTN_GREEN, 24) +
        theme.key_bar_skin("key_yellow", 812, 1008, 430, 52, theme.BTN_YELLOW, 24) +
        theme.key_bar_skin("key_blue", 1268, 1008, 620, 52, theme.BTN_BLUE, 24)
    )
    skin = ("""
    <screen name="GitHubSyncScreen" position="0,0" size="1920,1080" title="GitHub Direct Sync" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="head" position="0,0" size="1920,108" backgroundColor="%(PANEL)s" />
      <widget name="title" position="46,18" size="760,42" font="Regular;34" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="subtitle" position="48,65" size="1050,25" font="Regular;18" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="live" position="1470,24" size="390,38" font="Regular;22" halign="right" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="1220,69" size="640,24" font="Regular;17" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="40,106" size="1840,2" backgroundColor="%(ACCENT_PRIMARY)s" />

      <widget name="panel" position="40,134" size="1840,820" backgroundColor="%(PANEL_ALT)s" />
      <widget name="panel_title" position="70,158" size="870,34" font="Regular;24" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="panel_note" position="970,160" size="870,30" font="Regular;17" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />

      <widget name="name1" position="76,220" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar1" position="390,228" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="meta1" position="1200,214" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line1" position="76,272" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="name2" position="76,300" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar2" position="390,308" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(STATUS_GREEN)s" />
      <widget name="meta2" position="1200,294" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line2" position="76,352" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="name3" position="76,380" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar3" position="390,388" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(STATUS_VIOLET)s" />
      <widget name="meta3" position="1200,374" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line3" position="76,432" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="name4" position="76,460" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar4" position="390,468" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(ACCENT_SECONDARY)s" />
      <widget name="meta4" position="1200,454" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line4" position="76,512" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="name5" position="76,540" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar5" position="390,548" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(STATUS_YELLOW)s" />
      <widget name="meta5" position="1200,534" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line5" position="76,592" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="name6" position="76,620" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar6" position="390,628" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(STATUS_ORANGE)s" />
      <widget name="meta6" position="1200,614" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line6" position="76,672" size="1744,1" backgroundColor="%(RULE_SOFT)s" />


      <widget name="name7" position="76,700" size="300,34" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="bar7" position="390,708" size="780,16" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL_ROW)s" foregroundColor="%(ACCENT_SECONDARY)s" />
      <widget name="meta7" position="1200,694" size="620,42" font="Regular;19" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
      <widget name="line7" position="76,752" size="1744,1" backgroundColor="%(RULE_SOFT)s" />

      <widget name="architecture" position="76,782" size="1744,140" font="Regular;18" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />

      <widget name="footer" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="footer_rule" position="30,990" size="1860,2" backgroundColor="%(RULE_SOFT)s" />
      """ + _KEYS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session = session
        self.manager = manager
        self.config = config
        for n in ("head", "rule", "panel", "line1", "line2", "line3", "line4", "line5", "line6", "line7", "footer", "footer_rule"):
            self[n] = Label("")
        self["title"] = Label("GITHUB DIRECT SYNC")
        self["subtitle"] = Label("Read-only control plane • no receiver-side Source Manager • tiny JSON metadata sync")
        self["live"] = Label("● GITHUB LIVE")
        self["summary"] = Label("")
        self["panel_title"] = Label("VALIDATED DIRECT FEEDS")
        self["panel_note"] = Label("raw.githubusercontent.com • branch main")
        self["architecture"] = Label(
            "ARCHITECTURE\n"
            "GitHub = sources, validation, schedules and published XMLTV.  Vu+ = mapping + native import only.\n"
            "GREEN sync refreshes all 7 XML feeds + GitHub status.  OK opens per-ID monitor: green ON, red OFF."
        )
        for idx, (_sid, label, _stem) in enumerate(github_direct_sync.DIRECT_SOURCES, 1):
            self["name%d" % idx] = Label(label)
            self["bar%d" % idx] = ProgressBar(); self["bar%d" % idx].setValue(8)
            self["meta%d" % idx] = Label("waiting for GitHub…")
        self["key_red_bar"] = Label(""); self["key_red"] = Label("Back")
        self["key_green_bar"] = Label(""); self["key_green"] = Label("Sync GitHub Now")
        self["key_yellow_bar"] = Label(""); self["key_yellow"] = Label("Import All")
        self["key_blue_bar"] = Label(""); self["key_blue"] = Label("Smart Mapping")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.close, "red": self.close, "green": self.sync_now,
            "yellow": self.import_all, "blue": self.open_mapping, "ok": self.open_id_monitor,
        }, -1)
        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get()
        cb.append(self._refresh)
        self.onClose.append(self._cleanup)
        github_direct_sync.sync_async(force=False, min_age=45)
        self._timer.start(1000, False)
        self._refresh()

    def _refresh(self):
        data = github_direct_sync.load() or {}
        rows = data.get("sources") or {}
        ok_count = 0; channels = 0; programmes = 0
        for idx, (sid, label, stem) in enumerate(github_direct_sync.DIRECT_SOURCES, 1):
            row = rows.get(sid) or {}
            ok = bool(row.get("ok"))
            if ok: ok_count += 1
            channels += int(row.get("channels") or 0)
            programmes += int(row.get("programmes") or 0)
            self["bar%d" % idx].setValue(100 if ok else (15 if row else 5))
            if row:
                if ok:
                    mon = (data.get("id_monitor") or {}).get(stem, []) or []
                    ids_on = sum(1 for x in mon if x.get("on"))
                    ids_off = max(0, len(mon) - ids_on)
                    sync_tag = "SYNCED" if row.get("local_synced") else "LIVE"
                    meta = "%d ch • %d prog • IDs %d ON/%d OFF • %s" % (
                        int(row.get("channels") or 0), int(row.get("programmes") or 0), ids_on, ids_off, sync_tag)
                else:
                    meta = "OFFLINE / INVALID  •  %s" % str(row.get("error") or "no data")[:42]
            else:
                meta = "waiting for GitHub…"
            self["meta%d" % idx].setText(meta)
        checked = int(data.get("checked") or 0)
        age = max(0, int(time.time() - checked)) if checked else 0
        self["summary"].setText("%d/%d healthy  •  %d channels  •  %d programmes  •  sync %ss ago" % (ok_count, len(github_direct_sync.DIRECT_SOURCES), channels, programmes, age) if checked else "syncing…")
        if github_direct_sync.is_running():
            self["live"].setText("● SYNCING GITHUB")
        else:
            self["live"].setText("● GITHUB LIVE" if ok_count else "● GITHUB STATUS")

    def sync_now(self):
        if not github_direct_sync.sync_async(force=True, min_age=0):
            self.session.open(MessageBox, "GitHub sync is already running.", MessageBox.TYPE_INFO, timeout=2)
        self._timer.stop(); self._timer.start(700, False)

    def import_all(self):
        if self.manager.run_fast_import_async(on_complete=lambda _result: None):
            self.session.open(MessageBox, "Direct GitHub import started in background.", MessageBox.TYPE_INFO, timeout=3)
        else:
            self.session.open(MessageBox, "EPG Manager is already busy.", MessageBox.TYPE_INFO, timeout=3)

    def open_id_monitor(self):
        from .id_monitor import DirectIdMonitorScreen
        self.session.open(DirectIdMonitorScreen, self.config)

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.config, True, self.manager)

    def _cleanup(self):
        try: self._timer.stop()
        except Exception: pass


try:
    GitHubSyncScreen.skin = adapt_skin(GitHubSyncScreen.skin)
except Exception:
    pass
