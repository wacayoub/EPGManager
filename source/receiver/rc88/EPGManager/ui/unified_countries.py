# -*- coding: utf-8 -*-
"""Virtual country merger for EPGManager beta71.

This is deliberately metadata/cache-only.  It never concatenates XMLTV files.
A country row represents the real provider feeds behind it and can enable the
best provider bundle without losing the original provider/channel IDs.
"""
from __future__ import print_function

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from enigma import eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER
from .compat import MultiContentEntryText

from ..core import source_catalog, source_channel_cache, source_readiness
from ..core.source_preferences import SourcePreferences
from . import theme
from .compat import adapt_skin, scale_y, ui_gFont


_GOOD = set(("READY", "EPG READY", "ONLINE", "LOCAL", "CATALOG OK", "OK", "DONE", "SUCCESS"))


class CountryList(MenuList):
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 24))
        self.l.setFont(1, ui_gFont("Regular", 20))
        self.l.setItemHeight(scale_y(50))

    def set_rows(self, rows):
        out=[]
        for idx, row in enumerate(rows):
            bg = theme.PANEL_ROW_HEX_INT if idx % 2 == 0 else theme.PANEL_ROW_ALT_HEX_INT
            best = row.get("best") or {}
            selected = int(row.get("selected") or 0)
            marker = u"✓" if selected else u""
            out.append([
                row,
                MultiContentEntryText(pos=(12,0), size=(42,50), font=0, flags=RT_VALIGN_CENTER,
                                      text=marker, color=theme.STATUS_GREEN_INT, color_sel=theme.STATUS_GREEN_INT,
                                      backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(58,0), size=(560,50), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                                      text=row.get("name") or row.get("code") or "Country", color=theme.TEXT_INT,
                                      color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(640,0), size=(170,50), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                                      text="%d providers" % int(row.get("providers") or 0), color=theme.MUTED_TEXT_INT,
                                      color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(830,0), size=(140,50), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                                      text="%d feeds" % int(row.get("feeds") or 0), color=theme.MUTED_TEXT_INT,
                                      color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(990,0), size=(170,50), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                                      text="%d IDs" % int(row.get("ids") or 0), color=theme.ACCENT_SECONDARY_INT,
                                      color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1190,0), size=(540,50), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                                      text=("BEST  %s" % (best.get("provider") or "—")), color=theme.ACCENT_PRIMARY_INT,
                                      color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
            ])
        self.setList(out)

    def current(self):
        cur=self.getCurrent()
        return cur[0] if cur else None


class UnifiedCountrySourcesScreen(Screen):
    skin = ("""
    <screen name="UnifiedCountrySourcesScreen" position="0,0" size="1920,1080" title="Unified Country Sources" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header" position="0,0" size="1920,106" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,22" size="1040,48" font="Regular;38" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="subtitle" position="58,70" size="1500,28" font="Regular;19" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="1450,31" size="410,42" font="Regular;22" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />

      <widget name="hero" position="55,126" size="1810,96" backgroundColor="%(PANEL_AI)s" />
      <widget name="hero_title" position="82,143" size="800,34" font="Regular;24" foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL_AI)s" transparent="1" />
      <widget name="hero_text" position="82,181" size="1735,28" font="Regular;18" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_AI)s" transparent="1" />

      <widget name="h_country" position="115,238" size="500,34" font="Regular;18" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_meta" position="650,238" size="500,34" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="h_best" position="1190,238" size="540,34" font="Regular;18" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="list" position="55,278" size="1810,596" itemHeight="50" backgroundColor="%(PANEL)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
      <widget name="detail" position="55,890" size="1810,82" font="Regular;20" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />

      <widget name="footer" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="keys" position="55,1014" size="1810,42" font="Regular;22" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
    </screen>""") % theme.__dict__

    def __init__(self, session, manager=None, config=None):
        Screen.__init__(self, session)
        self.session=session; self.manager=manager; self.config=config
        self.preferences=SourcePreferences()
        for n in ("header","hero","footer"):
            self[n]=Label("")
        self["title"]=Label("UNIFIED COUNTRY SOURCES")
        self["subtitle"]=Label("One logical country source • real providers stay separate internally • no XML merge")
        self["summary"]=Label("")
        self["hero_title"]=Label("SMART COUNTRY LAYER")
        self["hero_text"]=Label("GREEN selects the best cached provider bundle for this country. Provider/channel IDs remain authoritative behind the scenes.")
        self["h_country"]=Label("COUNTRY / REGION")
        self["h_meta"]=Label("PROVIDERS • FEEDS • CACHED IDS")
        self["h_best"]=Label("BEST PROVIDER")
        self["list"]=CountryList()
        try: self["list"].onSelectionChanged.append(self._detail)
        except Exception: pass
        self["detail"]=Label("")
        self["keys"]=Label("RED Close     GREEN Use Best     YELLOW Providers     BLUE Smart Mapping     OK Details")
        self.rows=[]
        self["actions"]=ActionMap(["OkCancelActions","ColorActions","DirectionActions"],{
            "cancel":self.close,"red":self.close,"green":self.use_best,"yellow":self.providers,
            "blue":self.open_mapping,"ok":self.providers,"up":self._up,"down":self._down,
            "left":self._page_up,"right":self._page_down,"pageUp":self._page_up,"pageDown":self._page_down,
        },-1)
        self._build()

    def _ids_for(self, item):
        sid=source_catalog.mapping_source_id(item)
        if not sid: return 0
        try: return len(source_channel_cache.get(sid) or [])
        except Exception: return 0

    def _feed_score(self, item, selected):
        sid=str(item.get("id") or "")
        mid=source_catalog.mapping_source_id(item)
        ids=self._ids_for(item)
        score=min(35, ids//10)
        if sid in selected: score += 30
        if item.get("kind") == "local": score += 22
        try:
            state=str((source_readiness.get(mid) or {}).get("state") or "").upper()
            if state in _GOOD: score += 18
        except Exception: pass
        provider=str(item.get("provider") or "")
        return score, provider

    def _build(self):
        selected=set(self.preferences.selected_catalogue_ids())
        grouped={}
        for item in source_catalog.all_sources():
            code=str(source_catalog.primary_country_code(item) or "INT")
            grouped.setdefault(code,[]).append(item)
        rows=[]
        for code, feeds in grouped.items():
            providers={str(x.get("provider") or "ONLINE").upper() for x in feeds}
            ids=sum(self._ids_for(x) for x in feeds)
            by_provider={}
            for item in feeds:
                p=str(item.get("provider") or "ONLINE").upper()
                by_provider.setdefault(p,[]).append(item)
            ranked=[]
            preferred = str(source_catalog.default_provider(code) or "").upper()
            for provider, items in by_provider.items():
                score=sum(self._feed_score(x, selected)[0] for x in items)
                # Receiver policy wins over incidental cache size.  A provider
                # with a huge generic catalogue must not outrank the maintained
                # country-native source (e.g. Poland Azman, Spain via the Rytec family).
                if str(provider).upper() == preferred:
                    score += 120
                cached=sum(self._ids_for(x) for x in items)
                chosen=sum(1 for x in items if x.get("id") in selected)
                ranked.append((score,cached,chosen,provider,items))
            ranked.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]))
            best={}
            if ranked:
                sc,cached,chosen,provider,items=ranked[0]
                best={"score":sc,"ids":cached,"selected":chosen,"provider":provider,"items":items}
            rows.append({"code":code,"name":source_catalog.COUNTRY_NAMES.get(code, code),"feeds":len(feeds),
                         "providers":len(providers),"ids":ids,"selected":sum(1 for x in feeds if x.get("id") in selected),
                         "feeds_rows":feeds,"best":best,"ranked":ranked})
        rows.sort(key=lambda x:(0 if x.get("code")=="MA" else 1 if x.get("code")=="MENA" else 2, str(x.get("name") or "").casefold()))
        self.rows=rows
        self["list"].set_rows(rows)
        self["summary"].setText("%d countries • %d feeds" % (len(rows), sum(int(x.get("feeds") or 0) for x in rows)))
        self._detail()

    def _current(self):
        try:return self["list"].current()
        except Exception:return None

    def _detail(self):
        row=self._current()
        if not row:
            self["detail"].setText("No country sources available.")
            return
        best=row.get("best") or {}
        self["detail"].setText("%s  •  %d provider(s)  •  %d feed(s)  •  %d cached channel IDs\nBest now: %s  •  %d feed(s) already selected in this country" % (
            row.get("name") or row.get("code"), int(row.get("providers") or 0), int(row.get("feeds") or 0), int(row.get("ids") or 0),
            best.get("provider") or "not enough cached data", int(row.get("selected") or 0)))

    def _up(self):
        try:self["list"].up()
        except Exception:pass
        self._detail()
    def _down(self):
        try:self["list"].down()
        except Exception:pass
        self._detail()
    def _page_up(self):
        try:self["list"].pageUp()
        except Exception:
            for _ in range(10): self._up()
        self._detail()
    def _page_down(self):
        try:self["list"].pageDown()
        except Exception:
            for _ in range(10): self._down()
        self._detail()

    def use_best(self):
        row=self._current(); best=(row or {}).get("best") or {}
        items=list(best.get("items") or [])
        if not items:
            self.session.open(MessageBox,"No provider has enough cached information yet for this country.",MessageBox.TYPE_INFO,timeout=4)
            return
        country_ids=set(str(x.get("id") or "") for x in (row.get("feeds_rows") or []) if x.get("id"))
        best_ids=[str(x.get("id") or "") for x in items if x.get("id")]
        selected=[x for x in self.preferences.selected_catalogue_ids() if x not in country_ids]
        selected.extend(x for x in best_ids if x not in selected)
        self.preferences.set_selected_catalogue_ids(selected,save=True)
        self._build()
        self.session.open(MessageBox,"%s now uses the %s provider bundle (%d feed%s).\n\nNo XML files were merged; original provider IDs stay intact." % (
            row.get("name") or row.get("code"), best.get("provider") or "best", len(best_ids), "s" if len(best_ids)!=1 else ""),MessageBox.TYPE_INFO,timeout=5)

    def providers(self):
        row=self._current()
        if not row:return
        choices=[]
        selected=set(self.preferences.selected_catalogue_ids())
        for score,cached,chosen,provider,items in row.get("ranked") or []:
            langs=sorted(set(str(x.get("language") or "").upper() for x in items if x.get("language")))
            lang=(" • "+"/".join(langs)) if langs else ""
            choices.append(("%s  •  %d feeds  •  %d cached IDs%s%s" % (provider,len(items),cached,lang,"  ✓" if chosen else ""),provider))
        if not choices:
            choices=[("No provider details","none")]
        self.session.open(ChoiceBox,title="%s • providers behind the unified source" % (row.get("name") or "Country"),list=choices)

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen,self.config,True,self.manager)


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (UnifiedCountrySourcesScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
