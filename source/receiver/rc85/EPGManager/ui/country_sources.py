# -*- coding: utf-8 -*-
"""Country/logical-source selector for EPG Manager v6.3 beta2.

The user chooses the exact source wanted for each country.  YELLOW runs only
that logical source (download/generate -> direct SRP channel map -> Native Import) without
launching the full selected-source cycle.
"""
from __future__ import print_function

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ProgressBar import ProgressBar
from Components.MenuList import MenuList
from .compat import MultiContentEntryText
from enigma import eTimer,eListboxPythonMultiContent,gFont,RT_HALIGN_LEFT,RT_HALIGN_RIGHT,RT_VALIGN_CENTER

from ..core.source_preferences import SourcePreferences
from ..core import source_catalog
from . import theme
from .compat import adapt_skin, scale_y, ui_gFont


class CountrySourceList(MenuList):
    def __init__(self):
        MenuList.__init__(self,[],False,eListboxPythonMultiContent)
        self.l.setFont(0,ui_gFont("Regular",26)); self.l.setFont(1,ui_gFont("Regular",21)); self.l.setItemHeight(scale_y(56))

    def set_rows(self,rows):
        data=[]
        for i,item in enumerate(rows):
            bg=theme.PANEL_ROW_HEX_INT if i%2==0 else theme.PANEL_ROW_ALT_HEX_INT
            enabled=bool(item.get("enabled")); marker=u"✓" if enabled else u""
            mc=theme.STATUS_GREEN_INT if enabled else theme.MUTED_TEXT_INT
            label=item.get("source_label") or "-"; source_key=item.get("source") or ""
            feeds=len(source_catalog.source_option_items(item.get("code"),source_key))
            state="ACTIVE" if enabled else "OFF"; sc=theme.STATUS_GREEN_INT if enabled else theme.MUTED_TEXT_INT
            data.append([item,
              MultiContentEntryText(pos=(12,0),size=(48,56),font=0,flags=RT_VALIGN_CENTER,text=marker,color=mc,color_sel=mc,backcolor=bg,backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
              MultiContentEntryText(pos=(75,0),size=(520,56),font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=item.get("name") or item.get("code"),color=theme.TEXT_INT,color_sel=theme.WHITE_INT,backcolor=bg,backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
              MultiContentEntryText(pos=(615,0),size=(690,56),font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=label,color=theme.ACCENT_SECONDARY_INT,color_sel=theme.WHITE_INT,backcolor=bg,backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
              MultiContentEntryText(pos=(1320,0),size=(150,56),font=1,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=str(feeds),color=theme.TEXT_INT,color_sel=theme.WHITE_INT,backcolor=bg,backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
              MultiContentEntryText(pos=(1510,0),size=(200,56),font=1,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=state,color=sc,color_sel=sc,backcolor=bg,backcolor_sel=theme.PANEL_SELECTED_HEX_INT)])
        self.setList(data)

    def current_item(self):
        cur=self.getCurrent(); return cur[0] if cur else None


class CountrySourcesScreen(Screen):
    _KEY_BARS=(theme.key_bar_skin("key_red",30,1008,360,54,theme.BTN_RED)+
               theme.key_bar_skin("key_green",420,1008,420,54,theme.BTN_GREEN)+
               theme.key_bar_skin("key_yellow",870,1008,470,54,theme.BTN_YELLOW)+
               theme.key_bar_skin("key_blue",1370,1008,520,54,theme.BTN_BLUE))
    skin=("""
    <screen name="CountrySourcesScreen" position="0,0" size="1920,1080" title="EPG Sources" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,112" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,24" size="900,58" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="1100,28" size="760,48" font="Regular;23" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="55,104" size="1810,2" backgroundColor="%(RULE)s" />
      <widget name="section" position="55,126" size="1810,40" font="Regular;24" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="hint" position="55,168" size="1810,40" font="Regular;20" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_country" position="130,220" size="450,34" font="Regular;19" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_source" position="670,220" size="620,34" font="Regular;19" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_feeds" position="1365,220" size="120,34" font="Regular;19" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_state" position="1545,220" size="165,34" font="Regular;19" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="list" position="55,260" size="1810,610" itemHeight="56" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
      <widget name="detail" position="55,878" size="1810,48" font="Regular;20" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="progress_label" position="55,928" size="1420,32" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="progress" position="55,962" size="1690,12" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL)s" foregroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="progress_pct" position="1760,946" size="105,36" font="Regular;22" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
      """+_KEY_BARS+"""
    </screen>""")%theme.__dict__

    def __init__(self,session,config,manager=None):
        Screen.__init__(self,session)
        self.session=session; self.config=config; self.manager=manager
        self.preferences=SourcePreferences(); self.rows=self.preferences.rows()
        self._source_busy=False; self._source_result=None; self._source_label=""; self._source_operation=""
        for n in ("header_bg","rule","footer_bg","key_strip_rule"):self[n]=Label("")
        self["title"]=Label("EPG MANAGER  /  SOURCES")
        self["summary"]=Label("")
        self["section"]=Label("Choose the country, then choose the exact EPG source you want")
        self["hint"]=Label("OK / 1 Enable/disable   •   LEFT/RIGHT Source   •   2 Import   •   3 Update   •   0 / 9 Settings")
        self["h_country"]=Label("COUNTRY / REGION"); self["h_source"]=Label("EPG SOURCE"); self["h_feeds"]=Label("FEEDS"); self["h_state"]=Label("STATUS")
        self["detail"]=Label("")
        self["progress_label"]=Label("")
        self["progress_pct"]=Label("")
        self["progress"]=ProgressBar(); self["progress"].setValue(0)
        self["key_red_bar"]=Label(""); self["key_red"]=Label("Back")
        self["key_green_bar"]=Label(""); self["key_green"]=Label("Save")
        self["key_yellow_bar"]=Label(""); self["key_yellow"]=Label("2  Import Source")
        self["key_blue_bar"]=Label(""); self["key_blue"]=Label("3  Update Source")
        self["list"]=CountrySourceList()
        self["actions"]=ActionMap(["OkCancelActions","NumberActions","ColorActions","DirectionActions","MenuActions"],
            {"cancel":self.close,"red":self.close,"green":self.save,"yellow":self.import_current_source,
             "blue":self.update_current_source,"ok":self.toggle,"0":self.open_settings,"1":self.toggle,
             "2":self.import_current_source,"3":self.update_current_source,"9":self.open_settings,
             "left":lambda:self.change_source(-1),"right":lambda:self.change_source(1),
             "up":self.up,"down":self.down,"menu":self.open_settings},-1)
        self._timer=eTimer(); cb=self._timer.callback if hasattr(self._timer,"callback") else self._timer.timeout.get(); cb.append(self._poll)
        # No polling while idle. The progress timer is armed only while a
        # source import is actually running.
        self.onClose.append(self._cleanup); self._render()

    def _current(self): return self["list"].current_item()

    def _render(self,keep_code=None):
        current=keep_code or ((self._current() or {}).get("code"))
        self.rows=self.preferences.rows(); self["list"].set_rows(self.rows)
        if current:
            for i,row in enumerate(self.rows):
                if row.get("code")==current:
                    try:self["list"].moveToIndex(i)
                    except Exception:pass
                    break
        enabled=[x for x in self.rows if x.get("enabled")]
        feeds=sum(len(source_catalog.source_option_items(x.get("code"),x.get("source"))) for x in enabled)
        self["summary"].setText("%d selected  •  %d backend feed(s)"%(len(enabled),feeds)); self._detail()

    def _detail(self):
        item=self._current()
        if not item:self["detail"].setText("");return
        source_key=item.get("source") or ""; option=source_catalog.source_option(item.get("code"),source_key) or {}
        feeds=list(option.get("items") or []); provider=option.get("provider") or "-"
        names=", ".join(x.get("name") or x.get("id") for x in feeds[:5]); names += " ..." if len(feeds)>5 else ""
        self["detail"].setText("%s  →  %s  [%s]  •  %s"%(item.get("name"),option.get("label") or source_key,provider,names or "No backend feed"))
        if not self._source_busy:
            self["progress_label"].setText("")
            self["progress_pct"].setText("")
            self["progress"].setValue(0)

    def up(self): self["list"].up(); self._detail()
    def down(self): self["list"].down(); self._detail()

    def toggle(self):
        item=self._current()
        if item:
            code=item.get("code"); self.preferences.toggle(code); self._render(code)

    def change_source(self,direction):
        item=self._current()
        if item:
            code=item.get("code"); self.preferences.cycle_source(code,direction); self._render(code)

    def save(self):
        try:
            self.preferences.save()
            self.session.open(MessageBox,"EPG source choices saved.\n\nThe automatic cycle will use exactly these country/source selections.",MessageBox.TYPE_INFO,timeout=4)
        except Exception as exc:
            self.session.open(MessageBox,"Could not save sources.\n\n%s"%exc,MessageBox.TYPE_ERROR)

    def details(self):
        item=self._current()
        if not item:return
        code=item.get("code"); current=item.get("source"); lines=[item.get("name") or code,"Available sources:",""]
        for option in source_catalog.source_options(code):
            marker=u"✓ " if option.get("key")==current else "  "
            lines.append("%s%s  [%s]"%(marker,option.get("label"),option.get("provider")))
            names=", ".join(x.get("name") or x.get("id") for x in (option.get("items") or [])[:4])
            if names: lines.append("    %s"%names)
        self.session.open(MessageBox,"\n".join(lines),MessageBox.TYPE_INFO)

    def update_current_source(self):
        """Refresh XMLTV cache only; no SRP mapping and no Native Import."""
        item=self._current()
        if not item or self._source_busy:return
        if self.manager is None:
            self.session.open(MessageBox,"EPG Manager engine is unavailable on this screen.",MessageBox.TYPE_ERROR); return
        code=item.get("code"); source_key=item.get("source"); label=source_catalog.source_option_label(code,source_key)
        self._source_busy=True; self._source_result=None; self._source_label=label; self._source_operation="update"
        self["key_blue"].setText("Updating...")
        self["detail"].setText("%s  •  updating XMLTV cache only"%label)
        self["progress"].setValue(1); self["progress_pct"].setText("1%")
        self["progress_label"].setText("UPDATE  •  No mapping / no import")
        try:self._timer.start(500,False)
        except Exception:pass
        def done(result): self._source_result=result or {"ok":False,"message":"Unknown result"}
        started=self.manager.run_source_update_only_async(code,source_key,on_complete=done,force_download=True)
        if not started:
            self._source_busy=False; self["key_blue"].setText("3  Update Source")
            try:self._timer.stop()
            except Exception:pass
            self.session.open(MessageBox,"EPG Manager is already busy.",MessageBox.TYPE_INFO,timeout=3)

    def import_current_source(self):
        # Other sources are not imported: YELLOW is a strict source-only SRP import.
        item=self._current()
        if not item or self._source_busy:return
        if self.manager is None:
            self.session.open(MessageBox,"EPG Manager engine is unavailable on this screen.",MessageBox.TYPE_ERROR); return
        code=item.get("code"); source_key=item.get("source"); label=source_catalog.source_option_label(code,source_key)
        self._source_busy=True; self._source_result=None; self._source_label=label; self._source_operation="import"
        self["key_yellow"].setText("Importing...")
        self["detail"].setText("%s  •  cached XMLTV import (no download/scraping)"%label)
        self["progress"].setValue(1); self["progress_pct"].setText("1%")
        self["progress_label"].setText("LOCAL CACHE  •  Using existing XMLTV only")
        try:
            self._timer.start(500,False)
        except Exception:
            pass
        def done(result): self._source_result=result or {"ok":False,"message":"Unknown result"}
        started=self.manager.run_cached_source_import_async(code,source_key,on_complete=done)
        if not started:
            self._source_busy=False; self["key_yellow"].setText("2  Import Source")
            try:self._timer.stop()
            except Exception:pass
            self.session.open(MessageBox,"EPG Manager is already busy.",MessageBox.TYPE_INFO,timeout=3)

    def _poll(self):
        if self._source_busy:
            live=self.manager.get_cycle_status() if self.manager else {}
            if live.get("running"):
                pct=max(0,min(100,int(live.get("percent") or 0)))
                self["progress"].setValue(pct); self["progress_pct"].setText("%d%%"%pct)
                stage=live.get("stage") or "IMPORT"; detail=live.get("detail") or ""
                self["progress_label"].setText("%s  •  %s  •  %ds"%(stage,detail,int(live.get("elapsed") or 0)))
                if self._source_operation == "update":
                    self["detail"].setText("%s  •  updating XMLTV cache only; no mapping/import"%self._source_label)
                else:
                    self["detail"].setText("%s  •  fast cached import; no download/mapping rebuild"%self._source_label)
            if self._source_result is not None:
                result=self._source_result; self._source_result=None; self._source_busy=False
                self["key_yellow"].setText("2  Import Source"); self["key_blue"].setText("3  Update Source")
                try:self._timer.stop()
                except Exception:pass
                self["progress"].setValue(100 if result.get("ok") else 0); self["progress_pct"].setText("100%" if result.get("ok") else "")
                self["progress_label"].setText("DONE" if result.get("ok") else "IMPORT FAILED")
                if result.get("ok"):
                    if self._source_operation == "update":
                        self.session.open(MessageBox,"%s updated successfully.\n\nXMLTV cache refreshed only. Mapping cache was kept and no Native Import was run.\nDuration: %.1fs"%(
                            self._source_label,float(result.get("elapsed") or 0.0)),MessageBox.TYPE_INFO,timeout=6)
                    else:
                        imp=(result.get("import") or {}); events=int(imp.get("imported_events") or imp.get("events_imported") or 0)
                        mapping=(result.get("mapping") or {}); first_map=not bool(mapping.get("cache_reused",False)) and int(mapping.get("sources") or 0)>0
                        extra="\nOne-time mapping cache created." if first_map else "\nExisting mapping cache reused."
                        self.session.open(MessageBox,"%s imported successfully.\n\nNo download/scraping was run.%s\n%d event(s) imported.\nDuration: %.1fs"%(
                            self._source_label,extra,events,float(result.get("elapsed") or 0.0)),MessageBox.TYPE_INFO,timeout=6)
                else:
                    action="update" if self._source_operation == "update" else "import"
                    self.session.open(MessageBox,"%s %s failed.\n\n%s"%(self._source_label,action,result.get("message") or "Unknown error"),MessageBox.TYPE_ERROR)
                self._source_operation=""
                self._render((self._current() or {}).get("code"))

    def open_settings(self):
        try:
            from .settings import EPGManagerSettingsScreen; self.session.open(EPGManagerSettingsScreen,self.config)
        except Exception:pass

    def _cleanup(self):
        try:self._timer.stop()
        except Exception:pass


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (CountrySourcesScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
