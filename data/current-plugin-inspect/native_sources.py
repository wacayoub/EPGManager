# -*- coding: utf-8 -*-
"""Unified EPG source browser with persistent source selection."""
from __future__ import print_function
import threading
import os
import time

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText
from enigma import eTimer, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER

from ..core import external_sources, source_catalog
from ..core.native_source_store import NativeSourceStore
from ..core.mapping_store import MappingStore
from . import theme



class NativeSourceList(MenuList):
    """Source list with a real green checkmark for selected providers."""
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", 25))
        self.l.setFont(1, gFont("Regular", 21))
        self.l.setItemHeight(45)

    def set_rows(self, items, selected_store, status_map, mapped_counts):
        rows=[]
        for index, item in enumerate(items):
            selected = selected_store.is_selected(item.get("id"))
            if item.get("kind") == "local":
                st=status_map.get(item.get("manager_id"), {})
                status=st.get("status") or "LOCAL"
            else:
                status=item.get("_display_status") or "REMOTE"
            mapped = int(mapped_counts.get(item.get("id"), 0) or mapped_counts.get(item.get("manager_id"), 0) or 0)
            bg = theme.PANEL_ROW_HEX_INT if index % 2 == 0 else theme.PANEL_ROW_ALT_HEX_INT
            marker = u"✓" if selected else u""
            marker_color = theme.STATUS_GREEN_INT if selected else theme.MUTED_TEXT_INT
            mapped_text = ("%d mapped" % mapped) if mapped else ""
            rows.append([
                item,
                MultiContentEntryText(pos=(10,0), size=(48,45), font=0, flags=RT_VALIGN_CENTER, text=marker,
                    color=marker_color, color_sel=marker_color, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(62,0), size=(1110,45), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=item.get("name", item.get("id", "Source")), color=theme.TEXT_INT, color_sel=theme.WHITE_INT,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1185,0), size=(280,45), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=mapped_text, color=theme.ACCENT_SECONDARY_INT if mapped else theme.MUTED_TEXT_INT,
                    color_sel=theme.ACCENT_SECONDARY_INT if mapped else theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1480,0), size=(300,45), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=status, color=theme.STATUS_GREEN_INT if status in ("SUCCESS","UPDATED","CACHED") else theme.MUTED_TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
            ])
        self.setList(rows)

    def current_item(self):
        cur=self.getCurrent()
        return cur[0] if cur else None


class NativeSourcesScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 1008, 330, 54, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 390, 1008, 500, 54, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 920, 1008, 470, 54, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1420, 1008, 470, 54, theme.BTN_BLUE)
    )
    skin = ("""
    <screen name="NativeSourcesScreen" position="0,0" size="1920,1080" title="EPG Sources" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,25" size="1250,60" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="1320,30" size="540,50" font="Regular;23" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />
      <widget name="group" position="55,125" size="1810,42" font="Regular;25" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="hint" position="55,166" size="1810,54" font="Regular;20" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="list" position="55,225" size="1810,665" font="Regular;25" itemHeight="45" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
      <widget name="detail" position="55,900" size="1810,80" font="Regular;20" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
      """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session, self.manager, self.config = session, manager, config
        self.store = NativeSourceStore()
        self.mapping_store = MappingStore()
        for n in ("header_bg", "rule", "footer_bg", "key_strip_rule"):
            self[n] = Label("")
        self["title"] = Label("EPG MANAGER  /  EPG SOURCES")
        self["summary"] = Label("")
        self["group"] = Label("")
        self["hint"] = Label("OK Select / unselect   •   GREEN Update selected   •   0 Selected only   •   CH+/CH- Page   •   1 All sources")
        self["list"] = NativeSourceList()
        self["detail"] = Label("")
        for key, text in (("red", "Close"), ("green", "Update Selected"), ("yellow", "Refresh Catalogue"), ("blue", "Smart Mapping")):
            self["key_%s_bar" % key] = Label(""); self["key_%s" % key] = Label(text)
        self._all_items, self._items = [], []
        self._show_selected_only = False
        self._busy = False
        self._pending_locals = []
        self._errors = []
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions", "ChannelSelectBaseActions", "NumberActions"], {
            "cancel": self.close, "red": self.close, "ok": self.toggle_current,
            "green": self.update_selected, "yellow": self.reload_catalogue, "blue": self.open_mapping,
            "up": self._up, "down": self._down, "pageUp": self._page_up, "pageDown": self._page_down,
            "0": self.toggle_selected_filter, "1": self.show_all,
        }, -1)
        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get(); cb.append(self._poll)
        self.reload_catalogue()

    def _epg_dir(self):
        try: return self.config.get_epg_output_dir()
        except Exception: return "/etc/epgimport/jedi_epg"

    def reload_catalogue(self):
        old_id = (self._current() or {}).get("id")
        self._all_items = source_catalog.all_sources()
        self._apply_filter(old_id)

    def _mapped_counts(self):
        counts={}
        try:
            for key, value in self.mapping_store.all().items():
                sid = key.split("::", 1)[0] if "::" in key else ""
                refs=(value or {}).get("refs") or []
                if sid and refs:
                    counts[sid]=counts.get(sid,0)+1
        except Exception:
            pass
        return counts

    def _apply_filter(self, keep_id=None):
        mapped_counts=self._mapped_counts()
        for item in self._all_items:
            aliases=[item.get("id"), item.get("manager_id")]
            item["_mapped_count"] = max([mapped_counts.get(a,0) for a in aliases if a] or [0])
            if item.get("kind") != "local":
                st=external_sources.source_status(item, self._epg_dir())
                if item.get("dynamic"): st="EPG-IMPORTER"
                item["_display_status"]=st
        # Mapped providers first, then selected providers, then normal catalogue order.
        ordered=sorted(enumerate(self._all_items), key=lambda pair: (
            -int(pair[1].get("_mapped_count",0)>0),
            -int(self.store.is_selected(pair[1].get("id"))),
            pair[0]))
        source_order=[x for _idx,x in ordered]
        self._items=[x for x in source_order if (not self._show_selected_only or self.store.is_selected(x.get("id")))]
        local_status={}
        try: local_status={x.get("id"):x for x in self.manager.get_status_all()}
        except Exception: pass
        self["list"].set_rows(self._items, self.store, local_status, mapped_counts)
        if keep_id:
            for i,it in enumerate(self._items):
                if it.get("id")==keep_id:
                    try: self["list"].moveToIndex(i)
                    except Exception:
                        try: self["list"].instance.moveSelectionTo(i)
                        except Exception: pass
                    break
        sel_count=sum(1 for x in self._all_items if self.store.is_selected(x.get("id")))
        mapped_sources=sum(1 for x in self._all_items if x.get("_mapped_count",0))
        self["summary"].setText("%d selected  •  %d mapped providers  •  %d sources" % (sel_count, mapped_sources, len(self._all_items)))
        self._detail()

    def _index(self):
        try: return self["list"].getSelectedIndex()
        except Exception:
            try: return self["list"].getCurrentIndex()
            except Exception: return 0
    def _current(self):
        try:
            item=self["list"].current_item()
            if item is not None:return item
        except Exception:pass
        i=self._index(); return self._items[i] if 0 <= i < len(self._items) else None
    def _detail(self):
        item=self._current()
        if not item: self["group"].setText(""); self["detail"].setText(""); return
        self["group"].setText(item.get("group","Other"))
        url=item.get("url", "Local EPG Manager generator")
        state="SELECTED for native import" if self.store.is_selected(item.get("id")) else "Not selected"
        mapped=int(item.get("_mapped_count",0) or 0)
        cache_info=""
        try:
            if item.get("kind") != "local":
                path=external_sources.local_xml_path(item, self._epg_dir()) if hasattr(external_sources,"local_xml_path") else None
                if path and os.path.exists(path):
                    age=max(0,int(time.time()-os.path.getmtime(path)))
                    size=os.path.getsize(path)
                    cache_info="  •  cache %.1f MB  •  age %dh" % (size/1048576.0, age//3600)
        except Exception: pass
        self["detail"].setText("%s  •  %s  •  %d mapped channel%s%s\n%s\nGroup: %s" % (
            item.get("name","Source"), state, mapped, "" if mapped==1 else "s", cache_info, url, item.get("group","Other")))
    def _up(self):
        try:self["list"].up()
        except Exception:pass
        self._detail()
    def _down(self):
        try:self["list"].down()
        except Exception:pass
        self._detail()
    def _page_up(self):
        for _ in range(12): self._up()
    def _page_down(self):
        for _ in range(12): self._down()

    def toggle_current(self):
        item=self._current()
        if not item:return
        self.store.toggle(item.get("id")); self._apply_filter(item.get("id"))
    def toggle_selected_filter(self):
        self._show_selected_only=not self._show_selected_only; self._apply_filter()
    def show_all(self):
        self._show_selected_only=False; self._apply_filter()

    def update_selected(self):
        if self._busy:return
        selected=[x for x in self._all_items if self.store.is_selected(x.get("id"))]
        if not selected:
            self.session.open(MessageBox,"No EPG sources selected.\nUse OK to select sources first.",MessageBox.TYPE_INFO);return
        self._busy=True; self._errors=[]
        self._pending_locals=[x.get("manager_id") for x in selected if x.get("kind")=="local" and x.get("manager_id")]
        external=[x for x in selected if x.get("kind")!="local"]
        self["detail"].setText("Updating %d selected sources..." % len(selected))
        def worker():
            for item in external:
                if item.get("dynamic"):
                    self._errors.append("%s: dynamic EPG-Importer URL" % item.get("name")); continue
                try: external_sources.download_source(item, self._epg_dir(), retries=2, timeout=30)
                except Exception as exc: self._errors.append("%s: %s" % (item.get("name"), exc))
            self._external_done=True
        threading.Thread(target=worker, daemon=True).start(); self._timer.start(250,False)

    def _poll(self):
        if getattr(self,"_external_done",False):
            del self._external_done
            if self._pending_locals:
                ids=list(self._pending_locals); self._pending_locals=[]
                ok=self.manager.update_selected_async(ids, on_complete=lambda result:setattr(self,"_local_result",result))
                if not ok: self._errors.append("Local EPG Manager update could not start"); self._local_result={}
            else:self._local_result={}
        if hasattr(self,"_local_result"):
            self._timer.stop(); del self._local_result; self._busy=False
            self.reload_catalogue()
            if self._errors:
                shown="\n".join(self._errors[:8]); more=len(self._errors)-8
                if more>0: shown += "\n... and %d more" % more
                self.session.open(MessageBox,"Selected sources finished with warnings:\n\n%s" % shown,MessageBox.TYPE_WARNING)
            else:self.session.open(MessageBox,"Selected EPG sources updated successfully.",MessageBox.TYPE_INFO,timeout=3)

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen,self.config,True)
