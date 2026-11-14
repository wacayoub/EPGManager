# -*- coding: utf-8 -*-
"""Cache-only EPG status / health summary."""
from __future__ import print_function
import time
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eTimer,gRGB
from ..core import activity_store
from ..core.source_preferences import SourcePreferences
from . import theme
from .compat import adapt_skin

class EPGStatusScreen(Screen):
    _KEY_BARS=(theme.key_bar_skin("key_red",30,1008,360,54,theme.BTN_RED)+theme.key_bar_skin("key_green",420,1008,430,54,theme.BTN_GREEN)+theme.key_bar_skin("key_yellow",880,1008,470,54,theme.BTN_YELLOW)+theme.key_bar_skin("key_blue",1380,1008,510,54,theme.BTN_BLUE))
    skin=("""
    <screen name="EPGStatusScreen" position="0,0" size="1920,1080" title="EPG Status" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,112" backgroundColor="%(PANEL)s" /><widget name="title" position="55,24" size="900,58" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" /><widget name="clock" position="1550,24" size="310,52" font="Regular;34" halign="right" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" /><widget name="rule" position="55,104" size="1810,2" backgroundColor="%(RULE)s" />
      <widget name="state" position="55,145" size="1810,70" font="Regular;34" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(NAV_PANEL)s" transparent="0" />
      <widget name="cycle" position="55,245" size="1810,180" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="0" />
      <widget name="mapping" position="55,455" size="1810,220" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="0" />
      <widget name="sources" position="55,705" size="1810,190" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="0" />
      <widget name="hint" position="55,920" size="1810,55" font="Regular;23" foregroundColor="%(TEXT)s" backgroundColor="%(NAV_PANEL)s" transparent="0" /><widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" /><widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />"""+_KEY_BARS+"""</screen>""")%theme.__dict__
    def __init__(self,session,manager,config):
        Screen.__init__(self,session); self.session=session; self.manager=manager; self.config=config; self.preferences=SourcePreferences()
        for n in ("header_bg","rule","footer_bg","key_strip_rule"): self[n]=Label("")
        self["title"]=Label("EPG MANAGER  /  STATUS"); self["clock"]=Label(""); self["state"]=Label(""); self["cycle"]=Label(""); self["mapping"]=Label(""); self["sources"]=Label("")
        self["hint"]=Label("0 GitHub Sync  •  2 Health  •  3 Smart Mapping  •  4 Native Import  •  8 Online Update  •  9 Shortcut Help  •  MENU Settings")
        self["key_red_bar"]=Label("");self["key_red"]=Label("Back");self["key_green_bar"]=Label("");self["key_green"]=Label("Update + Import");self["key_yellow_bar"]=Label("");self["key_yellow"]=Label("Native Import");self["key_blue_bar"]=Label("");self["key_blue"]=Label("GitHub Sync")
        self["actions"]=ActionMap(["OkCancelActions","NumberActions","ColorActions","MenuActions"],{"cancel":self.close,"red":self.close,"ok":self.refresh,"green":self.run_cycle,"yellow":self.open_native,"blue":self.open_sources,"0":self.open_sources,"2":self.open_health,"3":self.open_mapping,"4":self.open_native,"8":self.open_update,"9":self.open_shortcuts,"menu":self.open_settings},-1)
        self._timer=eTimer(); cb=self._timer.callback if hasattr(self._timer,"callback") else self._timer.timeout.get(); cb.append(self.refresh); self._timer.start(5000,False); self.onClose.append(self._cleanup); self.refresh()
    @staticmethod
    def _age(ts):
        try:d=max(0,int(time.time()-float(ts)))
        except Exception:return "never"
        if d<60:return "just now"
        if d<3600:return "%d min ago"%(d//60)
        if d<86400:return "%dh %02dm ago"%(d//3600,(d%3600)//60)
        return "%d day(s) ago"%(d//86400)
    def _fg(self,name,color):
        try:self[name].instance.setForegroundColor(gRGB(int(color[1:],16)))
        except Exception:pass
    def refresh(self):
        self["clock"].setText(time.strftime("%d %b  %H:%M",time.localtime())); activity=activity_store.load(); cycle=activity.get("cycle") or {}; imp=activity.get("last_import") or {}; dl=activity.get("last_download") or {}; report=activity.get("mapping") or {}; live=self.manager.get_cycle_status() if self.manager else {}
        if live.get("running"): self["state"].setText("RUNNING  •  %s  •  %ds"%(live.get("stage") or "EPG CYCLE",int(live.get("elapsed") or 0))); self._fg("state",theme.STATUS_YELLOW)
        elif cycle and not cycle.get("ok",True): self["state"].setText("WARNING  •  %s"%(cycle.get("message") or "Last cycle failed")); self._fg("state",theme.STATUS_ORANGE)
        else:self["state"].setText("READY"); self._fg("state",theme.STATUS_GREEN)
        hh,mm=self.config.get_cycle_time(); self["cycle"].setText("AUTOMATIC EPG CYCLE\nLast cycle: %s  •  %.1fs\nNext configured start: %02d:%02d  •  %s\nLast native import: %s  •  %d events"%(self._age(cycle.get("timestamp")),float(cycle.get("elapsed") or 0.0),hh,mm,"ON" if self.config.get_cycle_enabled() else "OFF",self._age(imp.get("timestamp")),int(imp.get("imported_events") or 0)))
        methods=report.get("methods") or {}; exact=int(methods.get("exact_service_name") or 0); canonical=int(methods.get("canonical_service_name") or 0); manual=int(methods.get("manual") or 0)
        mapped_ids=int(report.get("mapped_channel_ids") or 0); unresolved=int(report.get("unmapped_channel_ids") or 0); total_ids=mapped_ids+unresolved
        coverage=int(round(100.0*mapped_ids/total_ids)) if total_ids else 0
        self["mapping"].setText("COVERAGE / BETA1 SERVICE NAME MAP\nCoverage: %d%%  •  Mapped XMLTV IDs: %d  •  Unresolved IDs: %d\nExact names: %d  •  Canonical names: %d  •  Manual: %d  •  Receiver service refs: %d\nSAT mapping engine is frozen to beta12/beta1; Smart Mapping is for manual SAT correction + IPTV."%(coverage,mapped_ids,unresolved,exact,canonical,manual,int(report.get("mapped_service_refs") or 0)))
        choices=self.preferences.enabled_display_choices(); self["sources"].setText("SELECTED SMART SOURCES\n%s\nLast source stage: %s  •  %d feed(s)  •  %d failed  •  %d fallback  •  %d unchanged"%(("   •   ".join("✓ %s"%p for _c,p in choices[:8])) or "No source selected",self._age(dl.get("timestamp")),int(dl.get("sources") or 0),int(dl.get("failed") or 0),int(dl.get("fallback") or 0),int(dl.get("unchanged") or 0)))
    def run_cycle(self):
        if self.manager.run_epg_cycle_async(on_complete=lambda result:None): self.session.open(MessageBox,"Full EPG cycle started in background.\n\nSelected sources → Service Name map → Native Import",MessageBox.TYPE_INFO,timeout=4)
        else:self.session.open(MessageBox,"EPG Manager is already busy or no source is selected.",MessageBox.TYPE_INFO,timeout=3)
    def open_native(self):
        from .native_import import NativeImportScreen; self.session.open(NativeImportScreen,self.config,self.manager)
    def open_sources(self):
        from .github_sync import GitHubSyncScreen; self.session.open(GitHubSyncScreen,self.manager,self.config)
    def open_settings(self):
        from .settings import EPGManagerSettingsScreen; self.session.open(EPGManagerSettingsScreen,self.config)
    def open_health(self):
        from .health_check import EPGHealthCheckScreen; self.session.open(EPGHealthCheckScreen,self.manager,self.config)
    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen; self.session.open(ChannelMappingScreen,self.config,True,self.manager)
    def open_update(self):
        from .online_update import OnlineUpdateScreen; self.session.open(OnlineUpdateScreen)
    def open_shortcuts(self):
        from .shortcuts import RemoteShortcutsScreen; self.session.open(RemoteShortcutsScreen)
    def _cleanup(self):
        try:self._timer.stop()
        except Exception:pass


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (EPGStatusScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
