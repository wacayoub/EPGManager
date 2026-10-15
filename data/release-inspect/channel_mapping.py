# -*- coding: utf-8 -*-
"""Receiver-first Smart Mapping UI for EPG Manager standalone mode.

Primary workflow:
    Reception/Bouquet -> Receiver Service -> Best EPG Match

Satellite Reception Lists stay first for fast DVB-S/S2 work, while real receiver
bouquets remain available. Source details/override live on YELLOW -> Source / Details.
"""

from enigma import eTimer, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER
import threading
import time
import os
import unicodedata
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ProgressBar import ProgressBar
from Components.MenuList import MenuList
from .compat import MultiContentEntryText

from ..core import channel_mapper, channel_registry, external_sources, activity_store, source_catalog, source_metadata, mapping_safety, srp_channel_map, smartmatch_ai, source_channel_cache, source_channel_sync, source_quality, programme_preview_cache, openepg_id_catalog, srp_master_engine, channel_identity_resolver, id_coverage_audit, source_id_harvester, performance_cache, smart_mapping_warm_cache, smart_context_match, source_variant_policy, smart_name_index_cache, unified_channel_identity, smart_context_engine, source_collision_audit, mapping_regression, perf_profiler, multilingual_channel_aliases, multilingual_guard, service_variant_guard, programme_feed_policy, bouquet_lazy, smart_mapping_view, precision_match_engine, mapping_view_cache, id_mapping_engine, mapping_strategy, manual_match_lite, remote_channel_catalog, smart_catalog_boot, smart_boot_name_index, source_priority, sid_reset, github_direct_sync, system_check, smart_source_cache_refresh
from ..core.mapping_store import MappingStore
from ..core.source_preferences import SourcePreferences
from ..core.epgimport_export import save_sourcexml
from ..core.logger import get_logger
from ..core.native_importer import parse_xmltv_time
from . import theme
from .compat import adapt_skin, scale_y, ui_gFont

log = get_logger(__name__)


def _alpha_text(value):
    """Stable case/accent-insensitive sort key for receiver/EPG channel names."""
    text = str(value or "").strip()
    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    except Exception:
        pass
    text = text.casefold()
    # Natural numeric ordering (Almajd 2 before Almajd 10).
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text))


class JediPaneList(MenuList):
    """Small compatibility wrapper: MenuList APIs vary slightly by image."""
    def move_up(self):
        try:
            self.up()
        except Exception:
            try:
                self.instance.moveSelection(self.instance.moveUp)
            except Exception:
                pass

    def move_down(self):
        try:
            self.down()
        except Exception:
            try:
                self.instance.moveSelection(self.instance.moveDown)
            except Exception:
                pass


class ChannelPaneList(MenuList):
    """Receiver-channel pane with colour-first Smart Mapping status.

    The row colour is the mapping state; repeated words such as MAPPED,
    SUGGESTION and NO MATCH are intentionally not rendered in the channel name.
    Only ownership is textual: [M] = manual lock, [A] = automatic mapping.

    A compact second line can show the programme currently visible in Enigma2's
    local EPG cache.  It is display-only and never touches XMLTV/network.
    """
    def __init__(self, status_colors=False):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 22))
        self.l.setFont(1, ui_gFont("Regular", 15))
        self.l.setItemHeight(scale_y(54))
        self.status_colors = bool(status_colors)
        self._rendered_rows = []

    def setList(self, items):
        self._rendered_rows = list(items or [])
        try:
            return MenuList.setList(self, self._rendered_rows)
        except Exception:
            try:
                self.l.setList(self._rendered_rows)
            except Exception:
                pass

    def _row_color(self, mapped, state):
        if not self.status_colors:
            return theme.TEXT_INT
        state = str(state or "").lower()
        if state == "manual":
            return theme.ACCENT_PRIMARY_INT
        if state in ("wrong", "ambiguous"):
            return theme.STATUS_ORANGE_INT
        if mapped or state == "mapped":
            return theme.STATUS_GREEN_INT
        if state in ("suggestion", "review"):
            return theme.STATUS_YELLOW_INT
        if state == "ignored":
            return theme.STATUS_RED_INT
        return theme.STATUS_RED_INT

    def _build_row(self, item, mapped, tag, state, programme=""):
        ref = item.get("ref")
        name = item.get("name", "Unnamed service")
        color = self._row_color(mapped, state)
        title = (name + (("  " + tag) if tag else "")).strip()
        row = [item]
        row.append(MultiContentEntryText(pos=(10,1), size=(620,29), font=0,
            flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title,
            color=color, color_sel=color))
        attention = str(item.get("_attention_reason") or "").strip()
        # rc52: mapping state is carried by the row colour.  Do not waste the
        # second line on MAPPED/UNMAPPED/AUTO MAPPED words.  For a mapped service
        # it is reserved for the live Enigma2 programme title.  Only actionable
        # warning states keep text on an unmapped row.
        if mapped:
            epg_text = ("NOW  •  " + str(programme or "").strip()) if programme else ""
        else:
            low_attention = attention.upper()
            if state in ("wrong", "ambiguous", "ignored") or "CONFLICT" in low_attention or "BLOCKED" in low_attention:
                epg_text = attention
            else:
                epg_text = ""
        row.append(MultiContentEntryText(pos=(16,29), size=(610,21), font=1,
            flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=epg_text,
            color=(theme.STATUS_ORANGE_INT if state in ("wrong","ambiguous") else
                   theme.STATUS_RED_INT if state == "ignored" else theme.MUTED_TEXT_INT),
            color_sel=theme.WHITE_INT))
        return row

    def build_rows(self, services, mapped_refs, mapping_visual_cb, programme_cb=None, programme_refs=None):
        """Build receiver rows without querying NOW EPG for the whole satellite.

        RC25 Performance keeps the bulk paint allocation-only.  If a programme
        callback is supplied, it is evaluated only for refs explicitly listed in
        ``programme_refs``.  This avoids hundreds of eEPGCache lookups when a
        satellite contains many mapped services.
        """
        rows = []
        wanted = set(programme_refs or [])
        for item in services:
            ref = item.get("ref")
            mapped = ref in mapped_refs
            tag, state = mapping_visual_cb(ref)
            programme = ""
            if mapped and callable(programme_cb) and ref in wanted:
                try:
                    programme = str(programme_cb(ref) or "").strip()
                except Exception:
                    programme = ""
            rows.append(self._build_row(item, mapped, tag, state, programme))
        return rows

    def update_programme_row(self, index, item, mapped, tag, state, programme):
        """Repaint one selected receiver row; fall back safely on older images."""
        try:
            index = int(index)
        except Exception:
            return False
        if index < 0:
            return False
        row = self._build_row(item, mapped, tag, state, programme)
        try:
            if index < len(self._rendered_rows):
                self._rendered_rows[index] = row
        except Exception:
            pass
        try:
            if hasattr(self.l, "modifyEntry"):
                self.l.modifyEntry(index, row)
                return True
        except Exception:
            pass
        # Older OpenATV list implementations may lack modifyEntry. Re-setting
        # the already-rendered list is still cheap and does not rebuild rows.
        try:
            self.setList(self._rendered_rows)
            return True
        except Exception:
            return False

    def move_up(self):
        try:
            self.up()
        except Exception:
            try: self.instance.moveSelection(self.instance.moveUp)
            except Exception: pass

    def move_down(self):
        try:
            self.down()
        except Exception:
            try: self.instance.moveSelection(self.instance.moveDown)
            except Exception: pass


class SafeMatchPaneList(MenuList):
    """Colour-first SAFE EPG list with deterministic one-row navigation.

    rc52 keeps exactly one logical list item per XMLTV ID.  There are no hidden
    separator payloads in normal direct-source browsing.  The left status rail
    shows mapping ownership (cyan mapped / grey unmapped), while the actual ID
    text is GREEN for healthy EPG and RED for proven NO EPG.  WARN/unknown stays
    orange.  This removes repeated MAPPED and EPG OK text from every row.
    """
    def __init__(self, health_colors=True):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.health_colors = bool(health_colors)
        self._previews = []
        self._meta = []
        self._raw_rows = []
        try:
            self.l.setFont(0, ui_gFont("Regular", 18))
            self.l.setFont(1, ui_gFont("Regular", 16))
            self.l.setItemHeight(scale_y(48))
        except Exception:
            pass

    def setPreviews(self, previews):
        self._previews = list(previews or [])

    def setMeta(self, meta):
        self._meta = list(meta or [])

    @staticmethod
    def _flatten(value, preview=""):
        return " ".join(str(value or "").split()) or "—"

    def _build_row(self, index, value):
        text = self._flatten(value)
        preview = self._flatten(self._previews[index], "") if index < len(self._previews) and self._previews[index] else ""
        meta = self._meta[index] if index < len(self._meta) else {}
        if not isinstance(meta, dict) or meta.get("separator"):
            return [value, MultiContentEntryText(pos=(10,0), size=(610,48), font=0,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=text,
                color=theme.MUTED_TEXT_INT, color_sel=theme.WHITE_INT)]

        mapped = bool(meta.get("mapped"))
        health = str(meta.get("health") or "EPG ?").upper()
        if health == "EPG OK":
            health_color = theme.STATUS_GREEN_INT
            # rc74: green text already means healthy. Do not waste the narrow
            # right rail repeating an "OK" badge on every row.
            health_text = ""
        elif health == "NO EPG":
            health_color = theme.STATUS_RED_INT
            health_text = "NO EPG"
        else:
            health_color = theme.STATUS_ORANGE_INT
            health_text = "?"
        # Mapping ownership gets its own colour and never becomes text noise.
        map_color = theme.ACCENT_SECONDARY_INT if mapped else theme.STATUS_GREY_INT
        return [value,
            MultiContentEntryText(pos=(8,0), size=(24,48), font=1,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="●",
                color=map_color, color_sel=map_color),
            MultiContentEntryText(pos=(36,0), size=(690,25), font=0,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=text,
                color=health_color if self.health_colors else theme.TEXT_INT,
                color_sel=health_color if self.health_colors else theme.WHITE_INT),
            MultiContentEntryText(pos=(42,24), size=(684,22), font=1,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=preview,
                color=theme.MUTED_TEXT_INT, color_sel=theme.WHITE_INT),
            MultiContentEntryText(pos=(735,0), size=(120,48), font=1,
                flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=health_text,
                color=health_color, color_sel=health_color)]

    def update_preview(self, index, preview):
        """Update only one visible NOW/NEXT line without moving the cursor."""
        try:
            index = int(index)
        except Exception:
            return False
        if index < 0 or index >= len(self._raw_rows):
            return False
        while len(self._previews) < len(self._raw_rows):
            self._previews.append("")
        self._previews[index] = str(preview or "")
        row = self._build_row(index, self._raw_rows[index])
        try:
            if index < len(self.list):
                self.list[index] = row
        except Exception:
            pass
        try:
            if hasattr(self.l, "modifyEntry"):
                self.l.modifyEntry(index, row)
                return True
        except Exception:
            pass
        try:
            current = int(self.getSelectedIndex())
        except Exception:
            current = index
        try:
            MenuList.setList(self, [self._build_row(i, v) for i, v in enumerate(self._raw_rows)])
            try: self.moveToIndex(current)
            except Exception: pass
            return True
        except Exception:
            return False

    def setList(self, items):
        self._raw_rows = list(items or [])
        rows = [self._build_row(i, value) for i, value in enumerate(self._raw_rows)]
        self.list = rows
        try:
            return MenuList.setList(self, rows)
        except Exception:
            try:
                self.l.setList(rows)
            except Exception:
                pass

    def _move_one(self, delta):
        # ChannelPaneList already proves MultiContent selection is stable on this
        # receiver family. Prefer native movement, then direct eListbox movement.
        before = 0
        try: before = int(self.getSelectedIndex())
        except Exception: pass
        try:
            if delta > 0:
                self.down()
            else:
                self.up()
        except Exception:
            try:
                mover = self.instance.moveDown if delta > 0 else self.instance.moveUp
                self.instance.moveSelection(mover)
            except Exception:
                pass
        try:
            after = int(self.getSelectedIndex())
        except Exception:
            after = before
        if after == before and self._raw_rows:
            target = max(0, min(len(self._raw_rows)-1, before + (1 if delta > 0 else -1)))
            if target != before:
                try: self.instance.moveSelectionTo(target)
                except Exception:
                    try: self.moveToIndex(target)
                    except Exception: pass

    def move_up(self):
        self._move_one(-1)

    def move_down(self):
        self._move_one(1)


class AutoScanResultList(MenuList):
    """Live Auto Repair result column with status colours.

    GREEN  = SAFE/PROVEN mapping applied automatically
    YELLOW = candidate exists but manual review is required
    RED    = no compatible prepared EPG candidate was found
    """
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 20))
        self.l.setFont(1, ui_gFont("Regular", 18))
        self.l.setItemHeight(scale_y(44))

    def update_rows(self, items):
        rows = []
        for item in list(items or []):
            status = str((item or {}).get("status") or "").upper()
            name = str((item or {}).get("name") or "Channel")
            target = str((item or {}).get("target") or "")
            if status in ("MAPPED", "SAFE"):
                color = theme.STATUS_GREEN_INT
                tag = "SAFE" if status == "SAFE" else "MAPPED"
            elif status in ("REVIEW", "AMBIGUOUS", "MANUAL"):
                color = theme.STATUS_YELLOW_INT
                tag = "MANUAL"
            else:
                color = theme.STATUS_RED_INT
                tag = "NO MATCH"
            detail = name
            if target and status in ("MAPPED", "SAFE"):
                detail += "  →  " + target
            row = [item,
                   MultiContentEntryText(pos=(8,0), size=(112,44), font=1,
                       flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=tag,
                       color=color, color_sel=color,
                       backcolor=theme.PANEL_ALT_HEX_INT, backcolor_sel=theme.PANEL_ALT_HEX_INT),
                   MultiContentEntryText(pos=(124,0), size=(410,44), font=0,
                       flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=detail,
                       color=color, color_sel=color,
                       backcolor=theme.PANEL_ALT_HEX_INT, backcolor_sel=theme.PANEL_ALT_HEX_INT)]
            rows.append(row)
        self.setList(rows)
        try:
            if rows:
                self.moveToIndex(len(rows) - 1)
        except Exception:
            pass

    def move_up(self):
        try: self.up()
        except Exception: pass

    def move_down(self):
        try: self.down()
        except Exception: pass


class ChannelMappingScreen(Screen):
    """Four-pane receiver mapping workflow.

    Pane 0: satellite Reception Lists plus real receiver bouquets
    Pane 1: receiver services in the selected reception list/bouquet
    Pane 2: source override for deterministic manual mapping
    Pane 3: safe EPG matches from the selected or suggested source
    """

    skin = """
    <screen name="ChannelMappingScreen" position="0,0" size="1920,1080" title="EPG Manager - Smart Mapping" backgroundColor="%(GLASS_BG)s" flags="wfNoBorder">
        <!-- 7.0.5 AJPanel FastEntry UI: one opaque body, flat lists, no glass/cards.
             Static eLabel separators are rendered by Enigma2 directly and do not
             create Python Label components or alpha-composited surfaces. -->
        <eLabel zPosition="0" position="0,0" size="1920,58" backgroundColor="%(GLASS_PANEL)s" />
        <widget zPosition="1" name="title" position="28,8" size="720,44" font="Regular;30" foregroundColor="#00FFFFBB" backgroundColor="%(GLASS_PANEL)s" transparent="0" valign="center" />
        <widget zPosition="1" name="status_caption" position="1220,7" size="170,22" font="Regular;15" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" halign="right" />
        <widget zPosition="1" name="status_label" position="1400,6" size="480,28" font="Regular;19" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" halign="right" valign="center" />
        <widget zPosition="1" name="catalog_progress_text" position="1760,32" size="120,22" font="Regular;15" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" halign="right" />
        <widget zPosition="1" name="catalog_progress" position="1530,45" size="210,5" borderWidth="0" backgroundColor="%(GLASS_PANEL)s" foregroundColor="%(ACCENT_PRIMARY)s" />

        <widget zPosition="1" name="subtitle" position="28,66" size="1060,28" font="Regular;18" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="1" name="summary" position="28,96" size="1300,34" font="Regular;18" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="1" name="match_caption" position="1250,66" size="180,24" font="Regular;14" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="1" name="source_title" position="1250,90" size="630,40" font="Regular;18" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(GLASS_BG)s" transparent="0" valign="center" />
        <eLabel zPosition="0" position="24,138" size="1872,1" backgroundColor="%(GLASS_RULE)s" />

        <!-- Flat AJPanel-style table headers -->
        <widget zPosition="1" name="hdr_bouquet" position="30,148" size="315,34" font="Regular;20" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" valign="center" />
        <widget zPosition="1" name="hdr_channel" position="365,148" size="625,34" font="Regular;20" foregroundColor="%(WHITE)s" backgroundColor="%(GLASS_BG)s" transparent="0" valign="center" />
        <widget zPosition="1" name="hdr_source" position="1002,148" size="1,1" font="Regular;1" foregroundColor="%(GLASS_BG)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="1" name="hdr_selection" position="1010,148" size="870,34" font="Regular;20" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(GLASS_BG)s" transparent="0" valign="center" />
        <eLabel zPosition="0" position="24,184" size="1872,1" backgroundColor="%(GLASS_RULE)s" />
        <eLabel zPosition="0" position="355,145" size="1,810" backgroundColor="%(GLASS_RULE_SOFT)s" />
        <eLabel zPosition="0" position="1000,145" size="1,810" backgroundColor="%(GLASS_RULE_SOFT)s" />
        
        <!-- Focus rails: only four tiny solid widgets change colour. -->
        <widget zPosition="1" name="focus1" position="30,185" size="315,4" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget zPosition="1" name="focus2" position="365,185" size="625,4" backgroundColor="%(RULE_SOFT)s" />
        <widget zPosition="1" name="focus3" position="1002,185" size="1,1" backgroundColor="%(GLASS_BG)s" />
        <widget zPosition="1" name="focus4" position="1010,185" size="870,4" backgroundColor="%(RULE_SOFT)s" />

        <!-- Opaque lists, no cards, no per-row pixmap. -->
        <widget zPosition="1" name="bouquets" position="30,194" size="315,758" font="Regular;22" itemHeight="44" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_BG)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(GLASS_SELECTED)s" scrollbarMode="showOnDemand" />
        <widget zPosition="1" name="channels" position="365,194" size="625,758" font="Regular;22" itemHeight="54" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_BG)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(GLASS_SELECTED)s" scrollbarMode="showOnDemand" />
        <widget zPosition="1" name="sources" position="1002,194" size="1,1" font="Regular;1" itemHeight="1" foregroundColor="%(GLASS_BG)s" backgroundColor="%(GLASS_BG)s" selectionForegroundColor="%(GLASS_BG)s" selectionBackgroundColor="%(GLASS_BG)s" scrollbarMode="showNever" />
        <widget zPosition="1" name="selections" position="1010,194" size="870,758" font="Regular;18" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_BG)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(GLASS_SELECTED)s" scrollbarMode="showOnDemand" />

        <!-- Auto Repair shares the same opaque/no-glass philosophy. -->
        <widget zPosition="3" name="auto_search_bg" position="24,142" size="1260,812" backgroundColor="%(GLASS_BG)s" />
        <widget zPosition="3" name="auto_search_caption" position="55,175" size="1190,44" font="Regular;27" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_current" position="70,275" size="1160,90" font="Regular;25" halign="center" valign="center" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_match" position="90,400" size="1120,100" font="Regular;22" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_stats" position="90,555" size="1120,60" font="Regular;22" halign="center" valign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_recent" position="110,660" size="1080,210" font="Regular;18" halign="center" valign="top" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <eLabel zPosition="0" position="1295,142" size="1,812" backgroundColor="%(GLASS_RULE_SOFT)s" />
        <widget zPosition="3" name="auto_search_results_bg" position="1305,142" size="590,812" backgroundColor="%(GLASS_BG)s" />
        <widget zPosition="3" name="auto_search_results_title" position="1325,166" size="550,34" font="Regular;21" foregroundColor="%(WHITE)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_results_legend" position="1325,202" size="550,38" font="Regular;16" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />
        <widget zPosition="3" name="auto_search_results" position="1315,250" size="565,650" backgroundColor="%(GLASS_BG)s" transparent="0" scrollbarMode="showOnDemand" />
        <widget zPosition="3" name="auto_search_results_count" position="1325,910" size="550,28" font="Regular;16" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(GLASS_BG)s" transparent="0" />

        <!-- Flat colour-key footer, AJPanel style. -->
        <eLabel zPosition="0" position="0,970" size="1920,110" backgroundColor="%(GLASS_PANEL)s" />
        <widget zPosition="1" name="key_red_bar" position="30,991" size="8,56" backgroundColor="%(BTN_RED)s" />
        <widget zPosition="1" name="key_red" position="50,991" size="300,56" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" valign="center" />
        <widget zPosition="1" name="key_green_bar" position="390,991" size="8,56" backgroundColor="%(BTN_GREEN)s" />
        <widget zPosition="1" name="key_green" position="410,991" size="500,56" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" valign="center" />
        <widget zPosition="1" name="key_yellow_bar" position="955,991" size="8,56" backgroundColor="%(BTN_YELLOW)s" />
        <widget zPosition="1" name="key_yellow" position="975,991" size="360,56" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" valign="center" />
        <widget zPosition="1" name="key_blue_bar" position="1410,991" size="8,56" backgroundColor="%(BTN_BLUE)s" />
        <widget zPosition="1" name="key_blue" position="1430,991" size="430,56" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(GLASS_PANEL)s" transparent="0" valign="center" />
    </screen>
    """ % theme.__dict__

    def __init__(self, session, config=None, standalone=True, manager=None, initial_filter=None, initial_reception=None, repair_mode=False, auto_repair_start=False):
        Screen.__init__(self, session)
        self.session = session
        self.config = config
        self.standalone = standalone
        self.manager = manager
        self.store = MappingStore()
        self.source_preferences = SourcePreferences()
        # beta81: ONE source policy.  Smart Mapping now uses exactly the feeds
        # selected in Smart Sources.  The legacy epgmanager_mapping_sources.json
        # allow-list is ignored (left untouched on disk for rollback).  Two
        # independent allow-lists caused stale EN feeds and inconsistent Find
        # Suggestion behaviour.
        self.auto_selected_source_ids = set(source_variant_policy.normalize_selected(
            self.source_preferences.selected_mapping_ids()))
        self.auto_source_filter_active = bool(self.auto_selected_source_ids)
        self._perf_open_started = time.time()
        self.focus = 0
        # Panes already entered keep their selected row highlighted.  This
        # mirrors Jedi's workflow: the bouquet remains visibly chosen while
        # the user moves on to Channel, EPG Source and EPG Selection.
        self.only_unmapped = False
        # beta57 receiver-side filters are separate from the EPG Selection
        # "hide mapped IDs" switch. This keeps Smart Mapping review focused.
        self.channel_filter_mode = "all"
        self._initial_filter = str(initial_filter or "all")
        # rc46: "all" means no receiver-side filter and is already applied.
        # rc45 left this False, so Start Search waited forever for a filter
        # transition that can never occur when initial_filter == "all".
        self._initial_filter_applied = self._initial_filter in ("", "all")
        self._initial_reception = str(initial_reception or "")
        self._initial_reception_applied = False
        self._repair_mode = bool(repair_mode)
        # beta133: Auto Repair is a dedicated SEARCH workflow, not a second
        # four-column Mapping Repair table.  The normal panes stay available
        # afterwards for explicit manual corrections.
        self._auto_search_mode = bool(auto_repair_start)
        self._auto_search_done = False
        self._auto_repair_live = {}
        self._auto_repair_recent = []
        self._auto_repair_result_rows = []
        self._auto_results_painted_count = -1
        # One batch index is built once per Auto Search screen and reused for
        # every receiver channel.  This replaces 150 repeated provider scans.
        self._auto_batch_index = None
        self._auto_batch_index_key = None
        self.catalog = []
        self.epg_channels = []
        self.epg_by_source = {}
        self.selection_cache = {}
        # 7.0.1 manual-turbo caches. Country bundles and static EPG row labels
        # are reused across receiver channels instead of rebuilt for every manual
        # mapping.  Derived channels.xml writes are serialized in a background queue.
        self._bundle_entries_cache = {}
        self._epg_label_cache = {}
        self._manual_source_rank_cache = {}
        # RC13: cache-only programme preview for mapped receiver rows.  The key
        # includes a minute bucket so event changes refresh naturally without
        # any provider/XML work or a background network task.
        self._programme_title_cache = {}
        self._programme_cache_minute = -1
        # RC16: SAFE EPG MATCH gets a display-only NOW/NEXT line from compact
        # source-quality cache.  RC17 adds a separate preview-only shard that can
        # be warmed for non-imported external feeds in a daemon after the pane is
        # already painted.  Cursor movement itself remains cache-only.
        self._safe_epg_preview_cache = {}
        self._safe_preview_warm_busy = False
        self._safe_preview_warm_result = None
        self._safe_preview_warm_token = 0
        self._safe_preview_warm_attempted = {}
        self._safe_preview_warm_service_counts = {}
        self._manual_sync_queue = []
        self._manual_sync_busy = False
        self._manual_sync_lock = threading.Lock()
        # beta60: per-source lightweight name/token indexes. These keep Smart Mapping
        # responsive on large XMLTV catalogues without rescoring thousands of IDs.
        self._source_search_indexes = {}
        # beta79 hot-path caches: source/ref lookups are used for every remote
        # control keypress and must never scan whole catalogues repeatedly.
        self._source_lookup = {}
        self._ref_keys_cache = {}
        # 7.0.2 Smart Mapping Lite: colour hints are derived only from the
        # already-prepared compact ID/name index. No source load, XML parse or
        # network work is allowed while painting receiver rows.
        self._visible_service_by_ref = {}
        self._channel_status_hint_cache = {}
        self._channel_status_repaint_pending = False
        self._smart_recommended_display_name = ""
        self._receiver_idle_ref = ""
        self._source_idle_id = ""
        # beta63: low-impact name-first suggestion index. It is built once in a
        # background thread from local hints and already-cached remote channel IDs.
        # No programme XML and no network request is involved.
        self._smart_name_index = {}
        self._smart_name_index_ready = False
        self._smart_name_index_building = False
        self._smart_name_index_dirty = False
        self._smart_name_hint_cache = {}
        self._source_language_rank_cache = {}
        # beta83: exact-ID AR/EN alias bridge cache. This lets an English/Latin
        # receiver name discover the Arabic feed row (Egypt 1, Saudi 1/3/5,
        # Qatar 4) without selecting/importing the parallel English programme feed.
        self._variant_alias_rows_cache = {}
        self._smart_recommended_source_id = ""
        self._smart_recommended_channel_id = ""
        self._smart_pending_preferred_source_id = ""
        # EPG Match rows are bound to the receiver service/source context that
        # produced them. This prevents stale Saudi/France rows from being reused
        # by Refresh Source after the receiver cursor has moved.
        self._selection_bound_ref = ""
        self._selection_bound_source_ids = set()
        self._selection_forced_source_id = ""
        # beta122: Source Override is a first-class third pane again; GREEN may still
        # jump directly to safe suggestions, but RIGHT/LEFT manual navigation never skips it.
        self._manual_source_override_active = False
        # RC25 Performance: large provider catalogues are paged in manual browse
        # instead of injecting thousands of MultiContent rows into Enigma2.
        self._manual_browse_page = 0
        self._manual_browse_page_size = 120
        self._manual_browse_page_count = 1
        self._manual_browse_context = None
        # beta82: human-in-the-loop suggestion browser. GREEN Find Suggestions
        # aggregates every prepared candidate for the same channel identity across
        # providers, then lets the user choose the exact feed/language manually.
        self._suggestion_browser_active = False
        self._suggestion_browser_ref = ""
        self._suggestion_browser_pure_name = ""
        self.mapping_revision = 0
        self._mapped_epg_keys = set()
        self._mapped_refs = set()
        self._mapped_info_by_ref = {}
        self.bouquet_labels = []
        self.all_bouquet_records = []
        self.bouquet_records = []
        self.expanded_bouquets = set()
        self.bouquet_services = []
        self.source_groups = []
        self._all_source_groups = []
        self.expanded_source_groups = set()
        self.selection_entries = []
        self.selection_rows = []
        self._download_busy = False
        self._local_update_sid = None
        self._download_result = None
        self._download_error = None
        self._startup_refresh_started = False
        self._startup_refresh_busy = False
        self._startup_refresh_count = 0
        # beta71 instant-open staged receiver refresh. The screen paints from
        # trusted caches first; lamedb/bouquet verification runs afterwards in
        # a daemon worker and only updates the UI if something really changed.
        self._receiver_verify_busy = False
        self._receiver_verify_result = None
        self._receiver_snapshot_ready = False
        # beta123: opening must never validate thousands of mappings on the UI
        # thread. Mapping ownership is prepared in a daemon and swapped in later.
        self._mapping_view_busy = False
        self._mapping_view_result = None
        self._mapping_view_token = 0
        # beta125: once a validated ownership snapshot is loaded, opening the
        # screen must not re-run PrecisionMatch for every SRP row.
        self._mapping_view_cache_applied = False
        self._mapping_view_partial = False
        self._bouquet_service_cache = {}
        # 8.1.2 Instant Status: keep one in-memory satellite->services grouping
        # and lazy sorted views. Reception cursor movement never rescans the SAT
        # registry; MAPPED/SUGGESTION/UNMAPPED is decided from resident O(1) maps.
        self._sat_services_by_reception = {}
        self._sat_sorted_services_by_reception = {}
        self._reception_nav_pending = False
        # 8.1.2 Instant Status: no delayed per-satellite suggestion scanner.
        # Every receiver row carries a precomputed fast_match_key and yellow
        # status is one compact-index dictionary lookup during first paint.
        # Keep only a few fully rendered SAT panes. Returning to a recently
        # visited satellite then becomes a setList() rather than thousands of
        # Python/MultiContent allocations. Mapping/index generations are part of
        # the cache key, so stale ownership rows are never reused.
        self._sat_channel_row_cache = {}
        # beta72: zero-block opening.  The UI paints first and consumes a
        # pre-warmed in-process snapshot only when it is ready.  No JSON file is
        # opened from the Enigma2 UI thread during initial Smart Mapping entry.
        self._warm_wait_started = time.time()
        self._startup_srp_stats = {}
        self._startup_audit_summary = {}
        self._resident_srp_state = {}
        self._smart_index_deferred = False
        # beta21: remote channel-ID sync is strictly on-demand, one source at a time.
        self._direct_sync_busy = False
        self._direct_sync_source_id = None
        self._direct_sync_result = None
        self._direct_sync_attempted = set()

        # beta97: all expensive suggestion enumeration runs outside Enigma2's
        # UI thread.  The result is polled by the existing lightweight timer;
        # an 8s watchdog discards a pathological search instead of freezing the
        # receiver.  Cursor movement never starts this worker.
        self._suggestion_job_busy = False
        self._suggestion_job_result = None
        self._suggestion_job_token = 0
        self._suggestion_job_started = 0.0
        self._suggestion_job_ref = ""
        self._suggestion_job_force_sid = ""
        self._suggestion_job_timeout = 3.0
        # beta129: one GREEN press is enough. If the persistent/global Smart
        # name index is not ready yet, remember the exact receiver service and
        # automatically continue Find Matches as soon as the background index
        # becomes READY. This removes the confusing "GREEN again when READY"
        # workflow and prevents an apparent no-op on the first press.
        self._pending_find_after_index = None

        # beta132: satellite batch repair. Mapping Repair can request one
        # automatic pass over the selected reception list. The worker reuses
        # exactly the beta131 targeted SAFE matcher, never downloads XMLTV and
        # writes only PrecisionMatch auto=True winners. Ambiguous/REVIEW rows
        # remain visible for manual correction afterwards.
        self._auto_repair_requested = bool(auto_repair_start)
        self._auto_repair_started = False
        self._auto_repair_busy = False
        self._auto_repair_result = None
        self._auto_repair_token = 0
        self._auto_repair_progress = {"done": 0, "total": 0, "mapped": 0, "review": 0, "nomatch": 0}
        # rc45: normal Smart Mapping never starts a silent repair. Auto Mapping
        # is an explicit GREEN action. Manual mappings/tombstones remain
        # authoritative and are never rewritten by the automatic path.
        self._smart_auto_repair_pending = False
        self._smart_auto_repair_seen = set()
        self._smart_auto_repair_silent = False
        self._smart_auto_repair_active_key = None
        self._auto_repair_current_reception = ""
        # beta138: lightweight per-satellite unresolved-state cache. It is persisted
        # outside the plugin package, so restart/reopen restores the last Auto
        # Search result without rescanning just to recover REVIEW/NO MATCH rows.
        self._repair_exception_kind = {}
        self._ignored_ref_keys = set()

        # beta69: cheap live-lamedb verification state.  Only file signatures
        # are checked during normal navigation; a registry/SRP rebuild happens
        # only when the receiver database actually changed.
        self._lamedb_state = "CHECKING"
        self._lamedb_stamp = ""
        self._lamedb_rebuilt = False

        # beta63: explicit remap mode.  A user correction never relies on a
        # fuzzy bulk replacement anymore. The user chooses the target provider
        # and the exact XMLTV ID in columns 3/4, then OK applies it either to
        # one receiver service or to every service sharing the old EPG ID.
        self._remap_scope_refs = []
        self._remap_scope_names = []
        self._remap_scope_mode = ""
        self._remap_old_info = {}

        for name in ("header_bg", "status_card", "match_card", "column_bar", "pane1_bg", "pane2_bg", "pane3_bg", "pane4_bg",
                     "header_rule", "column_rule", "footer_bg", "footer_rule",
                     "focus1", "focus2", "focus3", "focus4",
                     "key_red_bar", "key_green_bar", "key_yellow_bar", "key_blue_bar"):
            self[name] = Label("")

        self["title"] = Label("MAPPING REPAIR" if self._repair_mode else "SMART MAPPING")
        self["source_title"] = Label("CHANNEL — • EPG ID —\nSelect a receiver channel")
        self["subtitle"] = Label(("Repair view • unresolved first" if self._repair_mode else "FAST VIEW • SAT → CHANNEL → BEST EPG • source details in BLUE → More"))
        self["summary"] = Label("Mapped 0 • Unmapped 0 • EPG IDs 0/0")
        self["status_caption"] = Label("SMART ENGINE")
        self["match_caption"] = Label("SELECTED EPG")
        self["status_label"] = Label("FAST • READY")
        self["catalog_progress"] = ProgressBar(); self["catalog_progress"].setValue(100)
        self["catalog_progress_text"] = Label("READY")
        self._catalog_progress_started = 0.0
        self._smart_index_total = 0
        self._smart_index_done = 0
        self["hdr_bouquet"] = Label("RECEPTION")
        self["hdr_channel"] = Label("RECEIVER CHANNEL")
        self["hdr_source"] = Label("")
        self["hdr_selection"] = Label("BEST EPG MATCH  •  CHANNEL / EPG ID / NOW")

        self["bouquets"] = JediPaneList([])
        self["channels"] = ChannelPaneList(status_colors=True)
        self["sources"] = JediPaneList([])
        try:
            _id_colors = bool(self.config.get_color_id_health()) if self.config is not None else True
        except Exception:
            _id_colors = True
        self["selections"] = SafeMatchPaneList(health_colors=_id_colors)
        self["auto_search_bg"] = Label("")
        self["auto_search_caption"] = Label("AUTO MAPPING • DRY-RUN FIRST")
        self["auto_search_current"] = Label("Preparing selected satellite…")
        self["auto_search_match"] = Label("Search finds SAFE / PROVEN matches first. Nothing changes until you confirm APPLY.")
        self["auto_search_stats"] = Label("WAITING FOR RECEIVER CACHE")
        self["auto_search_recent"] = Label("")
        self["auto_search_results_bg"] = Label("")
        self["auto_search_results_title"] = Label("SCANNED CHANNELS")
        self["auto_search_results_legend"] = Label("GREEN safe found   •   YELLOW review   •   RED no match")
        self["auto_search_results"] = AutoScanResultList()
        self["auto_search_results_count"] = Label("0 scanned")
        # First frame is useful immediately.  Real rows arrive from the resident
        # warm cache in the background instead of delaying screen creation.
        self._set_list("bouquets", ["Opening receiver reception lists / bouquets…"])
        self._set_list("channels", [[None, MultiContentEntryText(pos=(12,0), size=(390,48), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="Loading cached channels…")]])
        self._set_list("sources", ["Source details moved to BLUE → More"])
        self._set_list("selections", ["SAFE MATCH uses direct remote Channel IDs", "No full XMLTV download in Smart Mapping"])

        self["key_red"] = Label("Back")
        self["key_green"] = Label("Auto Mapping" if not self._auto_search_mode else "Start Search")
        self["key_yellow"] = Label("Source / Details")
        self["key_blue"] = Label("More")

        self["actions"] = ActionMap(
            ["OkCancelActions", "NumberActions", "ColorActions", "DirectionActions", "ChannelSelectBaseActions", "MenuActions", "InfoActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.green_pressed,
                "yellow": self.yellow_pressed,
                "blue": self.blue_pressed,
                "ok": self.ok_pressed,
                "left": self.focus_left,
                "right": self.focus_right,
                "up": self.move_up,
                "down": self.move_down,
                "pageUp": self.page_down,
                "pageDown": self.page_up,
                "channelUp": self.page_down,
                "channelDown": self.page_up,
                "prevBouquet": self.page_down,
                "nextBouquet": self.page_up,
                "0": self.open_mapping_options,
                "8": self.open_manual_source_override,
                "9": self.open_main_settings,
                "info": self.show_match_reason,
                "menu": self.open_main_settings,
            }, -1)

        self._timer = eTimer()
        if hasattr(self._timer, "callback"):
            self._timer.callback.append(self._timer_tick)
        else:
            self._timer.timeout.get().append(self._timer_tick)
        # Smart-name indexing is intentionally delayed until after the screen is
        # visible.  It is background-only and can also start immediately on a
        # GREEN suggestion request.
        self._idle_timer = eTimer()
        if hasattr(self._idle_timer, "callback"):
            self._idle_timer.callback.append(self._idle_warmup)
        else:
            self._idle_timer.timeout.get().append(self._idle_warmup)
        # Receiver/source cursor debouncing is the main beta79 fluidity change.
        # UP/DOWN paints immediately; fuzzy matching and large EPG-list rebuilds
        # happen only after the user pauses for a fraction of a second.
        self._receiver_nav_timer = eTimer()
        _rcb = self._receiver_nav_timer.callback if hasattr(self._receiver_nav_timer, "callback") else self._receiver_nav_timer.timeout.get()
        _rcb.append(self._receiver_nav_idle)
        self._source_nav_timer = eTimer()
        _scb = self._source_nav_timer.callback if hasattr(self._source_nav_timer, "callback") else self._source_nav_timer.timeout.get()
        _scb.append(self._source_nav_idle)
        # RC17: non-imported SAFE EPG previews warm in their own daemon/timer.
        # The timer only polls an atomic result and repaints cached rows; provider
        # I/O never executes on Enigma2's UI thread.
        self._safe_preview_timer = eTimer()
        _pcb = self._safe_preview_timer.callback if hasattr(self._safe_preview_timer, "callback") else self._safe_preview_timer.timeout.get()
        _pcb.append(self._poll_safe_preview_warm)
        # 8.1.1: debounce Reception/Bouquet UP/DOWN exactly like receiver/source
        # navigation. Rapid satellite scrolling moves only the left highlight;
        # the channel pane is rebuilt once after the user pauses.
        self._reception_nav_timer = eTimer()
        _bcb = self._reception_nav_timer.callback if hasattr(self._reception_nav_timer, "callback") else self._reception_nav_timer.timeout.get()
        _bcb.append(self._reception_nav_idle)
        # Yellow suggestion colouring is enriched in tiny time-budgeted chunks.
        # This timer never performs network/XML work; it only checks the resident
        # compact Smart-name index and yields back to Enigma2 every few ms.
        self.onLayoutFinish.append(self.update_focus)
        self.onLayoutFinish.append(self._apply_auto_search_layout)
        self.onLayoutFinish.append(self._apply_smart_auto_repair_layout)
        self.onClose.append(self._cleanup_fast_open)
        try:
            smart_mapping_warm_cache.ensure_async()
            smart_catalog_boot.ensure_async()
            smart_source_cache_refresh.ensure_async(force=False)
        except Exception:
            pass
        self._timer.start(40, True)

    def _timer_tick(self):
        # beta72: zero-block first frame.  Initial receiver/source JSON reads are
        # performed by smart_mapping_warm_cache outside the Enigma2 UI thread.
        # Until that RAM snapshot is ready, keep the screen interactive and only
        # animate this tiny status indicator.
        if not self._receiver_snapshot_ready:
            if not self.rescan():
                try:
                    elapsed = max(0.0, time.time() - float(self._warm_wait_started or time.time()))
                    pct = min(88, 6 + int(elapsed * 28))
                    self["catalog_progress"].setValue(pct)
                    self["catalog_progress_text"].setText("%d%%" % pct)
                    self["status_label"].setText("INSTANT VIEW PREPARING • %d%%" % pct)
                except Exception:
                    pass
                try:
                    self._timer.start(80, True)
                except Exception:
                    pass
                return

        if self._suggestion_job_busy:
            self._poll_suggestion_job()
            if self._suggestion_job_busy:
                try:
                    elapsed = max(0.0, time.time() - float(self._suggestion_job_started or time.time()))
                    pct = min(94, 12 + int(elapsed * 10))
                    self["catalog_progress"].setValue(pct)
                    self["catalog_progress_text"].setText("%d%%" % pct)
                    self["status_label"].setText("FINDING SAFE MATCHES • %d%%" % pct)
                    self._timer.start(180, False)
                except Exception:
                    pass
                return

        if self._receiver_verify_busy:
            self._poll_receiver_verify()
            if self._receiver_verify_busy:
                # Keep the lightweight poll alive until the worker has returned;
                # do not fall through to the READY branch which stops the timer.
                try:
                    self._timer.start(250, False)
                except Exception:
                    pass
                return

        if self._mapping_view_busy:
            self._poll_mapping_view_build()
            if self._mapping_view_busy and not self._auto_search_mode:
                try:
                    self._timer.start(120, False)
                except Exception:
                    pass
                return

        # beta128 Mapping Repair: select the requested satellite before applying
        # the receiver-side filter. This is pure in-memory navigation over the
        # resident Reception List cache; it performs no lamedb/XMLTV/network scan.
        if not self._initial_reception_applied and self.catalog and self._initial_reception:
            self._initial_reception_applied = True
            try:
                for _i, _row in enumerate(self.bouquet_records or []):
                    if _row.get("virtual") == "reception" and str(_row.get("reception_list") or "") == self._initial_reception:
                        self._set_index("bouquets", _i)
                        self.refresh_channels()
                        break
            except Exception:
                pass

        # beta66: allow callers (Smart Sources conflict shortcut) to open the
        # mapping workspace directly on a lightweight receiver-side filter.
        if (not self._initial_filter_applied and self.catalog and
                self._initial_filter in ("unmapped", "mapped", "conflicts", "locked", "auto", "ignored")):
            self._initial_filter_applied = True
            try:
                self.set_channel_filter(self._initial_filter)
            except Exception:
                pass

        # beta132: an automatic Mapping Repair starts only after the selected
        # satellite and its ownership view are ready. This prevents the batch
        # worker from competing with the partial SRP validation on small boxes.
        if (self._auto_repair_requested and not self._auto_repair_started and
                self._receiver_snapshot_ready and
                (not self._initial_reception or self._initial_reception_applied) and
                (not self._initial_filter or self._initial_filter_applied)):
            self._start_auto_repair_batch()
            return

        if self._auto_repair_busy:
            self._poll_auto_repair_batch()
            if self._auto_repair_busy:
                try:
                    progress = dict(self._auto_repair_progress or {})
                    done = int(progress.get("done") or 0); total = max(1, int(progress.get("total") or 0))
                    pct = min(99, max(3, int(100.0 * done / total)))
                    self["catalog_progress"].setValue(pct)
                    self["catalog_progress_text"].setText("%d%%" % pct)
                    self["status_label"].setText("AUTO REPAIR • %d/%d • %d SAFE" % (done, int(progress.get("total") or 0), int(progress.get("mapped") or 0)))
                    self._update_auto_search_overlay()
                    self._timer.start(220, False)
                except Exception:
                    pass
                return

        if self._smart_auto_repair_pending and not self._auto_repair_busy:
            if self._start_pending_smart_auto_repair():
                try:
                    self._timer.start(120, False)
                except Exception:
                    pass
                return

        if self._direct_sync_busy:
            try:
                elapsed = max(0.0, time.time() - float(self._catalog_progress_started or time.time()))
                pct = min(92, 8 + int(elapsed * 11))
                self["catalog_progress"].setValue(pct)
                self["catalog_progress_text"].setText("%d%%" % pct)
                self["status_label"].setText("CHANNEL CATALOG PREPARING • %d%%" % pct)
            except Exception:
                pass
            self._poll_direct_sync()
        elif self._smart_name_index_building:
            # 7.1.0: poll the single process-global lean index; never run a
            # screen-local catalogue builder. This branch is O(1).
            try:
                compact = smart_boot_name_index.snapshot() or {}
                istate = smart_boot_name_index.status() or {}
            except Exception:
                compact = {}; istate = {}
            if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2:
                self._smart_name_index = compact
                self._smart_name_index_ready = True
                self._smart_name_index_building = False
                self._smart_index_done = 1
                self._channel_status_hint_cache.clear()
                self._channel_status_repaint_pending = True
                try:
                    self["catalog_progress"].setValue(100)
                    self["catalog_progress_text"].setText("READY")
                    self["status_label"].setText("FAST MATCH INDEX READY")
                    self._timer.start(20, True)
                except Exception:
                    pass
                return
            try:
                self["catalog_progress"].setValue(20 if istate.get("busy") else 5)
                self["catalog_progress_text"].setText("RAM")
                self["status_label"].setText("FAST MATCH INDEX PREPARING")
                self._timer.start(150, False)
            except Exception:
                pass
            return
        elif self._smart_name_index_ready and self._channel_status_repaint_pending:
            # One repaint after a background index build is enough to turn
            # eligible unmapped rows yellow. No matching work runs on cursor move.
            self._channel_status_repaint_pending = False
            try:
                if self.focus in (0, 1) and self.bouquet_services:
                    keep = self._get_index("channels")
                    self.refresh_channels()
                    self._set_index("channels", min(keep, max(0, len(self.bouquet_services) - 1)))
            except Exception:
                pass
            try:
                self._timer.start(20, True)
            except Exception:
                pass
            return
        elif self._smart_name_index_ready and self._pending_find_after_index and not self._suggestion_job_busy:
            # beta129: resume the user's original GREEN request automatically.
            # Only continue if the cursor is still on the same receiver service;
            # moving away cancels the queued request instead of showing stale
            # candidates for a different channel.
            pending = self._pending_find_after_index or {}
            self._pending_find_after_index = None
            current = self._current("channels", self.bouquet_services) or {}
            if str(current.get("ref") or "") == str(pending.get("ref") or ""):
                force = self._source_by_id(pending.get("force_sid")) if pending.get("force_sid") else None
                self.select_best_name_suggestion(force_source=force)
                return
        elif self._download_busy or getattr(self, "_channel_refresh_busy", False):
            self._poll_download()
        elif self._local_update_sid:
            self._poll_local_update()
        else:
            try:
                self["catalog_progress"].setValue(100)
                self["catalog_progress_text"].setText("READY")
                if str(getattr(self, "_lamedb_state", "") or "").upper() == "UPDATED":
                    self["status_label"].setText("READY • LAMEDB UPDATED")
                else:
                    self["status_label"].setText("READY")
            except Exception:
                pass
            try:
                self._timer.stop()
            except Exception:
                pass

    def _idle_warmup(self):
        """Build Smart name buckets only after the mapping screen is visible."""
        self._smart_index_deferred = False
        if self._smart_name_index_ready or self._smart_name_index_building:
            return
        try:
            self._prime_local_channel_hints()
        except Exception:
            pass
        self._start_smart_name_index()

    def _schedule_idle_warmup(self):
        """beta122: do not build the global Smart index just because the screen opened.

        A cached persistent index is still consumed instantly by ``rescan``. If
        no valid cache exists, GREEN starts the rebuild explicitly. This removes
        the long 1%..100% SMART CATALOG job while the user is merely browsing.
        """
        self._smart_index_deferred = True
        return

    def _cleanup_fast_open(self):
        # 8.1.1 timers are UI-local; never leave callbacks targeting a closed screen.
        for _timer_name in ("_reception_nav_timer",):
            try:
                getattr(self, _timer_name).stop()
            except Exception:
                pass
        self._pending_find_after_index = None
        self._auto_repair_token = int(getattr(self, "_auto_repair_token", 0) or 0) + 1
        self._auto_repair_requested = False
        self._auto_repair_busy = False
        self._auto_repair_result = None
        self._suggestion_job_busy = False
        self._suggestion_job_result = None
        self._suggestion_job_token = int(getattr(self, "_suggestion_job_token", 0) or 0) + 1
        self._safe_preview_warm_token = int(getattr(self, "_safe_preview_warm_token", 0) or 0) + 1
        self._safe_preview_warm_busy = False
        for timer in (getattr(self, "_timer", None), getattr(self, "_idle_timer", None), getattr(self, "_receiver_nav_timer", None), getattr(self, "_source_nav_timer", None), getattr(self, "_safe_preview_timer", None)):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:
                pass

    def _epg_dir(self):
        if self.config is not None and hasattr(self.config, "get_epg_output_dir"):
            try:
                return self.config.get_epg_output_dir()
            except Exception:
                pass
        return channel_mapper.EPG_DIR

    def _safe_epg_preview_text(self, epg, service=None):
        """Small cache-only programme line for SAFE EPG MATCH.

        Current active owner prefers Enigma2's own eEPGCache (the exact guide
        currently visible on the receiver). Alternative candidates use the tiny
        NOW/NEXT metadata already captured by Source Refresh/Prepare. No network,
        XML parsing or mapping operation is allowed here.
        """
        epg = epg or {}
        if not isinstance(epg, dict) or epg.get("separator"):
            return ""
        sid = str(epg.get("source_id") or "")
        cid = str(epg.get("channel_id") or "")
        if not sid or not cid:
            return ""
        service = service or {}
        ref = str(service.get("ref") or "")
        minute = int(time.time() // 60)
        key = (minute, sid.casefold(), cid.casefold(), self._fast_raw_key(ref))
        cached = self._safe_epg_preview_cache.get(key)
        if cached is not None:
            return cached
        if len(self._safe_epg_preview_cache) > 2048:
            self._safe_epg_preview_cache.clear()

        text = ""
        # The green/current owner is best validated against what Enigma2 itself
        # is showing after import. This also reflects Qatar1's transient Arabic
        # overlay instead of an older English provider quality sample.
        try:
            owner = self._primary_mapping_info_for_ref(ref) if ref else {}
            if owner and str(owner.get("source_id") or "").casefold() == sid.casefold() and \
                    str(owner.get("channel_id") or "").casefold() == cid.casefold():
                title = self._current_programme_title(ref)
                if title:
                    text = "NOW  •  %s" % title
        except Exception:
            pass

        if not text:
            try:
                preview = source_quality.get_programme_preview_cached(sid, cid, max_future_hours=96) or {}
            except Exception:
                preview = {}
            # RC17: sources need not be imported first.  If Refresh/Prepare has
            # not produced a quality sample, consume the independent preview-only
            # shard warmed asynchronously from the provider URL.  This cache is
            # presentation-only and can never influence mapping/quality scores.
            if not preview:
                try:
                    preview = programme_preview_cache.get(sid, cid, max_future_hours=96) or {}
                except Exception:
                    preview = {}
            title = " ".join(str(preview.get("title") or "").split())
            if len(title) > 62:
                title = title[:59].rstrip() + "…"
            if title:
                if str(preview.get("kind") or "").upper() == "NEXT":
                    try:
                        hhmm = time.strftime("%H:%M", time.localtime(int(preview.get("start") or 0)))
                    except Exception:
                        hhmm = ""
                    text = "NEXT%s  •  %s" % ((" " + hhmm) if hhmm else "", title)
                else:
                    text = "NOW  •  %s" % title
        self._safe_epg_preview_cache[key] = text
        return text

    def _queue_safe_preview_warm(self, entries, service):
        """Warm missing candidate NOW/NEXT metadata without blocking Smart Mapping.

        The pane is painted first from RAM.  Up to twenty-four candidate IDs
        per receiver service are warmed in a daemon worker.  Reads stay streamed
        and bounded, so UP/DOWN/LEFT/RIGHT never waits for provider XML parsing.
        """
        if self._safe_preview_warm_busy:
            return False
        service = service or {}
        ref = str(service.get("ref") or "")
        if not ref:
            return False
        budget = max(0, 24 - int(self._safe_preview_warm_service_counts.get(ref) or 0))
        if budget <= 0:
            return False
        now = time.time()
        jobs = []
        seen = set()
        for epg in entries or []:
            if budget <= 0:
                break
            if not isinstance(epg, dict) or epg.get("separator"):
                continue
            sid = str(epg.get("source_id") or "")
            cid = str(epg.get("channel_id") or "")
            if not sid or not cid:
                continue
            pair = (sid.casefold(), cid.casefold())
            if pair in seen:
                continue
            seen.add(pair)
            # Already visible from Enigma2/source-quality/preview cache -> no I/O.
            if self._safe_epg_preview_text(epg, service):
                continue
            src = self._source_by_id(sid) or {}
            is_external = bool(src.get("external") or str(src.get("kind") or "").lower() == "external" or
                               src.get("url") or src.get("urls"))
            if not src or not is_external:
                continue
            # Do not retry a failed/empty remote sample on every repaint.  A
            # thirty-minute window is enough for live programme changes while
            # protecting small receivers and provider servers.
            attempt_key = (ref, sid.casefold(), cid.casefold())
            last = float(self._safe_preview_warm_attempted.get(attempt_key) or 0.0)
            if last and now - last < 1800.0:
                continue
            self._safe_preview_warm_attempted[attempt_key] = now
            jobs.append((sid, cid, dict(src)))
            budget -= 1

        if not jobs:
            return False
        self._safe_preview_warm_service_counts[ref] = int(self._safe_preview_warm_service_counts.get(ref) or 0) + len(jobs)
        self._safe_preview_warm_token = int(self._safe_preview_warm_token or 0) + 1
        token = self._safe_preview_warm_token
        self._safe_preview_warm_result = None
        self._safe_preview_warm_busy = True

        def worker():
            changed = 0
            errors = 0
            deferred = 0
            # Group by source so multiple candidate IDs from one provider share
            # one bounded HTTP stream.
            grouped = {}
            for sid, cid, src in jobs:
                bucket = grouped.setdefault(sid, {"src": src, "ids": []})
                if cid not in bucket["ids"]:
                    bucket["ids"].append(cid)
            for sid, bundle in grouped.items():
                if int(getattr(self, "_safe_preview_warm_token", 0) or 0) != token:
                    return
                try:
                    src_item = bundle.get("src") or {}
                    ids = bundle.get("ids") or []
                    previews = {}
                    # If Refresh Source has already produced a local provider
                    # cache, parse that first: it is complete, zero-network and
                    # lets SAFE EPG MATCH show EPG for IDs located late in a
                    # large XMLTV file.  External caches are presentation-only.
                    try:
                        ext = external_sources.BY_ID.get(str(sid))
                        local_path = external_sources.local_xml_path(ext, self._epg_dir()) if ext else ""
                    except Exception:
                        local_path = ""
                    if local_path and os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                        with open(local_path, "rb") as fh:
                            previews, _scanned, _programmes = source_channel_sync.parse_programme_previews(
                                fh, ids, max_scan=max(24 * 1024 * 1024, os.path.getsize(local_path) + 1),
                                max_programmes=100000, cooperative_sleep=0.001)
                    else:
                        previews, _meta = source_channel_sync.fetch_programme_previews(
                            src_item, ids, timeout=8, connect_timeout=3.0,
                            max_scan=24 * 1024 * 1024, max_programmes=30000,
                            cooperative_sleep=0.002)
                    if previews and programme_preview_cache.merge(sid, previews):
                        changed += len(previews)
                except source_channel_sync.SyncDeferred:
                    # Native EPGImport wins immediately.  Do not consume this
                    # service's preview budget: allow a later repaint to retry
                    # after import finishes, while keeping the UI untouched.
                    errors += 1
                    deferred += len(bundle.get("ids") or [])
                    for cid in bundle.get("ids") or []:
                        self._safe_preview_warm_attempted[(ref, sid.casefold(), str(cid).casefold())] = 0.0
                except Exception:
                    errors += 1
            result = {"token": token, "ref": ref, "changed": changed, "errors": errors,
                      "deferred": deferred}
            if int(getattr(self, "_safe_preview_warm_token", 0) or 0) == token:
                self._safe_preview_warm_result = result

        threading.Thread(target=worker, daemon=True).start()
        try:
            self._safe_preview_timer.start(220, False)
        except Exception:
            pass
        return True

    def _poll_safe_preview_warm(self):
        result = self._safe_preview_warm_result
        if self._safe_preview_warm_busy and result is None:
            try:
                self._safe_preview_timer.start(300, False)
            except Exception:
                pass
            return
        self._safe_preview_warm_busy = False
        self._safe_preview_warm_result = None
        if not result:
            return
        if int(result.get("token") or -1) != int(self._safe_preview_warm_token or 0):
            return
        deferred = int(result.get("deferred") or 0)
        if deferred:
            ref = str(result.get("ref") or "")
            self._safe_preview_warm_service_counts[ref] = max(
                0, int(self._safe_preview_warm_service_counts.get(ref) or 0) - deferred)
        if int(result.get("changed") or 0) <= 0:
            return
        current = self._current("channels", self.bouquet_services) or {}
        if str(current.get("ref") or "") != str(result.get("ref") or ""):
            return
        # Refresh the selected detail strip and only the highlighted candidate
        # row.  modifyEntry keeps the OpenATV/Metrix cursor stable.
        self._safe_epg_preview_cache.clear()
        try:
            self.update_source_title()
        except Exception:
            pass
        try:
            idx = self._get_index("selections")
            epg = self._current("selections", self.selection_entries) or {}
            if isinstance(epg, dict) and not epg.get("separator"):
                preview = self._safe_epg_preview_text(epg, current)
                widget = self["selections"]
                if hasattr(widget, "update_preview"):
                    widget.update_preview(idx, preview)
        except Exception:
            pass

    def _set_list(self, key, rows):
        """Paint lists with zero provider work on the navigation path.

        rc48 deliberately does not build NOW/NEXT previews for every SAFE row.
        Only the currently highlighted candidate is resolved by
        ``update_source_title``; this removes the last O(N) preview loop from
        Smart Mapping list refreshes.
        """
        if key == "selections":
            try:
                widget = self[key]
                entries = list(getattr(self, "selection_entries", []) or [])
                service = self._current("channels", self.bouquet_services) or {}
                previews = []
                meta = []
                for idx in range(len(list(rows or []))):
                    epg = entries[idx] if idx < len(entries) else {}
                    if not isinstance(epg, dict) or epg.get("separator"):
                        meta.append({"separator": True})
                        previews.append("")
                    else:
                        meta.append({"mapped": bool(self._is_mapped(epg)),
                                     "health": self._epg_health_text(epg)})
                        # Cache-only NOW/NEXT lookup. No provider request or XML
                        # parsing runs in the navigation path.  Missing previews
                        # stay blank until the highlighted ID is warmed.
                        previews.append(self._safe_epg_preview_text(epg, service))
                if hasattr(widget, "setPreviews"):
                    widget.setPreviews(previews)
                if hasattr(widget, "setMeta"):
                    widget.setMeta(meta)
            except Exception:
                pass
        try:
            self[key].setList(rows)
        except Exception:
            self[key].l.setList(rows)

    def _get_index(self, key):
        try:
            return int(self[key].getSelectedIndex())
        except Exception:
            try:
                return int(self[key].l.getCurrentSelectionIndex())
            except Exception:
                return 0

    def _current(self, key, backing):
        if not backing:
            return None
        idx = self._get_index(key)
        if idx < 0 or idx >= len(backing):
            idx = 0
        return backing[idx]

    def _set_index(self, key, index):
        try:
            self[key].moveToIndex(max(0, int(index)))
            return
        except Exception:
            pass
        try:
            self[key].instance.moveSelectionTo(max(0, int(index)))
        except Exception:
            pass

    def _source_label(self, source_id):
        for _fn, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
            if pair[0] == source_id:
                return pair[1]
        try:
            item = external_sources.BY_ID.get(source_id)
            if item:
                return item.get("name") or source_id
        except Exception:
            pass
        return source_id or "EPG"

    def _source_display_label(self, source_id, fallback=None):
        src = self._source_by_id(source_id) if hasattr(self, "_all_source_groups") else None
        return self._compact_source_name(source_id, src, fallback)

    def _rebuild_mapping_cache(self, srp_state=None):
        """Merge manual + persistent SRP Master mappings for the Smart Mapping UI.

        Manual rows plus deterministic SRP Master mappings are shown. SAT
        mappings come from the live receiver lamedb; no learned/AI overlay.
        """
        self._mapped_epg_keys = set()
        self._mapped_refs = set()
        self._mapped_info_by_ref = {}
        raw_by_canon = {}
        service_by_canon = {}
        try:
            for service in self.catalog or []:
                raw = str((service or {}).get("ref") or "").strip()
                canon = channel_registry.canonical_service_ref(raw)
                if raw and canon:
                    raw_by_canon[canon] = raw
                    service_by_canon[canon] = service
        except Exception:
            pass

        def add_ref(ref, info):
            raw = str(ref or "").strip()
            canon = channel_registry.canonical_service_ref(raw)
            actual = raw_by_canon.get(canon) or raw
            for key in (raw, canon, actual):
                if not key:
                    continue
                self._mapped_refs.add(key)
                lst = self._mapped_info_by_ref.setdefault(key, [])
                sig = (info.get("source_id"), info.get("channel_id"), info.get("mode"))
                if all((x.get("source_id"), x.get("channel_id"), x.get("mode")) != sig for x in lst):
                    lst.append(dict(info))

        # beta121: never let a stale automatic SRP/learned row paint a receiver
        # service as safely mapped after provider/country/feed policy changed.
        # Manual user ownership remains absolute by design.  This is deliberately
        # an in-memory read-time firewall: it fixes the UI immediately and does
        # not trigger the expensive global Service Map rebuild removed in beta120.
        try:
            _source_by_mid = source_catalog.by_mapping_id() or {}
        except Exception:
            _source_by_mid = {}

        try:
            _active_auto_sources = set(self._auto_allowed_source_ids() or set())
        except Exception:
            _active_auto_sources = set()

        def auto_mapping_allowed(canon_ref, source_id, channel_id, display_name):
            # RC32: stale AUTO ownership from a source now switched OFF is not
            # considered mapped. Manual locks remain untouched below.
            if str(source_id or "") not in _active_auto_sources:
                return False
            service = service_by_canon.get(canon_ref) or {}
            try:
                variant_ok, _variant_reason = service_variant_guard.check(
                    service, candidate_name=channel_id, display_name=display_name or "")
            except Exception:
                variant_ok = False
            if not variant_ok:
                return False
            src = _source_by_mid.get(str(source_id or ""))
            if src is None:
                try:
                    src = self._source_by_id(source_id)
                except Exception:
                    src = None
            if src:
                try:
                    route_ok, _route_score, _route_reason = programme_feed_policy.route(
                        service, src, {"channel_id": channel_id,
                                       "display_name": display_name or channel_id})
                except Exception:
                    route_ok = False
                if not route_ok:
                    return False
            return True

        # Manual mapping remains absolute and is shown first.  Track manual
        # ownership per receiver ref so an older automatic SRP claim from a
        # different source is hidden after the user explicitly replaces it.
        manual_owner_by_ref = {}
        try:
            all_maps = self.store.all()
            for key, saved in all_maps.items():
                if not saved or not saved.get("refs"):
                    continue
                try:
                    source_id, channel_id = key.split("::", 1)
                except ValueError:
                    source_id, channel_id = "", key
                info = {
                    "source_id": source_id,
                    "source_name": self._source_label(source_id),
                    "channel_id": channel_id,
                    "display_name": saved.get("display_name") or channel_id,
                    "mode": saved.get("mode") or "manual",
                }
                is_manual = str(saved.get("mode") or "manual").lower() == "manual"
                accepted_any = False
                for ref in saved.get("refs") or []:
                    raw = str(ref or "").strip()
                    canon = channel_registry.canonical_service_ref(raw) or raw
                    if is_manual:
                        if raw:
                            manual_owner_by_ref.setdefault(raw, set()).add(str(source_id).lower())
                        if canon:
                            manual_owner_by_ref.setdefault(canon, set()).add(str(source_id).lower())
                    elif not auto_mapping_allowed(canon, source_id, channel_id, info.get("display_name") or ""):
                        continue
                    add_ref(ref, info)
                    accepted_any = True
                if accepted_any:
                    self._mapped_epg_keys.add(key)
        except Exception:
            pass

        # Persistent OpenEPG/local SRP maps generated from the real lamedb.
        # Only selected sources count as active/mapped in the UI.
        try:
            selected_mids = set(str(x or "") for x in self.source_preferences.selected_mapping_ids())
            if isinstance(srp_state, dict):
                state = srp_state
            elif isinstance(getattr(self, "_resident_srp_state", None), dict) and self._resident_srp_state:
                state = self._resident_srp_state
            else:
                state = srp_master_engine.load_state() or {}
            self._resident_srp_state = state
            for cref, infos in (state.get("service_map") or {}).items():
                for row in infos or []:
                    sid = str((row or {}).get("source_id") or "")
                    if sid not in selected_mids:
                        continue
                    raw_ref = str(cref or "").strip()
                    # beta64 GREEN Unmap tombstone: a user-explicit unmap must
                    # stay unmapped even when the frozen SRP state still contains
                    # the deterministic match. Golden SRP data is untouched.
                    try:
                        if self.store.is_blocked(raw_ref):
                            continue
                    except Exception:
                        pass
                    canon_ref = channel_registry.canonical_service_ref(raw_ref) or raw_ref
                    owners = manual_owner_by_ref.get(raw_ref) or manual_owner_by_ref.get(canon_ref) or set()
                    if owners and str(sid).lower() not in owners:
                        # Explicit user ownership wins over persistent automatic
                        # SRP claims. Golden maps remain untouched on disk.
                        continue
                    cid = str((row or {}).get("channel_id") or "")
                    info = {
                        "source_id": sid,
                        "source_name": str((row or {}).get("source_name") or self._source_label(sid)),
                        "channel_id": cid,
                        "display_name": str((row or {}).get("display_name") or cid),
                        "mode": str((row or {}).get("method") or "srp"),
                        "confidence": int((row or {}).get("confidence") or 0),
                    }
                    if not auto_mapping_allowed(canon_ref, sid, cid, info.get("display_name") or ""):
                        continue
                    if sid and cid:
                        self._mapped_epg_keys.add(self.store._key(sid, cid))
                    add_ref(cref, info)
        except Exception:
            pass

        # 8.2.1: sort each resident ownership bucket once, at cache-build time.
        # The hot receiver-list painter can then read infos[0] in O(1) without
        # rescoring providers while the user scrolls. Duplicate SRP rows remain
        # available as alternatives, but only the first row is the active owner.
        try:
            for key, rows in self._mapped_info_by_ref.items():
                if len(rows or []) <= 1:
                    continue
                canon = channel_registry.canonical_service_ref(key) or str(key or "")
                service = service_by_canon.get(canon) or {}
                rows.sort(key=lambda info: self._mapping_info_rank(info, service), reverse=True)
        except Exception:
            pass

        # Keep Mapping Repair's NO EPG denominator synchronized after manual
        # assign/unmap/restore operations without reloading any receiver data.
        try:
            _ignored = self.store.ignored_refs()
            self._refresh_ignored_ref_keys(_ignored)
            smart_mapping_warm_cache.update_ignored_refs(_ignored)
        except Exception:
            pass


    # beta59: all receiver-side mapping decisions go through canonical service
    # reference helpers.  Older UI code compared raw strings directly with
    # ``_mapped_refs``.  DVB refs can differ by case/trailing colon and IPTV
    # refs can be stored without the final colon, making a genuinely unmapped
    # service appear mapped (or vice versa).  The helpers below are cheap and
    # use only the already-built in-memory mapping cache.
    def _ref_lookup_keys(self, ref):
        raw = str(ref or "").strip()
        if not raw:
            return []
        cached = self._ref_keys_cache.get(raw)
        if cached is not None:
            return cached
        out = []
        def add(value):
            value = str(value or "").strip()
            if value and value not in out:
                out.append(value)
        add(raw)
        add(raw.rstrip(":"))
        try:
            add(channel_registry.canonical_service_ref(raw))
        except Exception:
            pass
        # 8.1.1: satellite receivers can easily expose >8k services across
        # Reception Lists. Clearing the whole cache at 8192 caused severe cache
        # thrashing while moving between satellites. Keep a larger bounded cache;
        # it is invalidated explicitly when the receiver snapshot changes.
        if len(self._ref_keys_cache) > 16384:
            self._ref_keys_cache.clear()
        self._ref_keys_cache[raw] = out
        return out

    def _single_owner_source_item(self, source_id):
        """Return cached source metadata used by the one-owner resolver."""
        cached = getattr(self, "_single_owner_source_items", None)
        if not isinstance(cached, dict):
            try:
                cached = dict((str(k or "").lower(), v) for k, v in (source_catalog.by_mapping_id() or {}).items())
            except Exception:
                cached = {}
            self._single_owner_source_items = cached
        return cached.get(str(source_id or "").lower()) or {}

    @staticmethod
    def _mapping_mode_class(mode):
        """Higher means a more authoritative persisted ownership decision."""
        value = str(mode or "").lower()
        if value == "manual":
            return 3
        if value in ("auto-repair", "smartmatch-iptv", "smartmatch", "auto"):
            return 2
        return 1

    def _mapping_info_rank(self, info, service=None):
        """Best-first rank for duplicate claims on one receiver ServiceRef."""
        info = info or {}
        mode = str(info.get("mode") or "").lower()
        owner_class = self._mapping_mode_class(mode)
        sid = str(info.get("source_id") or "").lower()
        item = self._single_owner_source_item(sid)
        try:
            source_rank = source_priority.ownership_rank(item, service or {})
        except Exception:
            source_rank = (-1, ())
        confidence = int(info.get("confidence") or 0)
        # Explicit MANUAL always wins. A durable AUTO choice then wins over raw
        # SRP claims. For plain SRP claims, use the same provider/country-aware
        # source ownership rank as Import All, then confidence as tie-breaker.
        if owner_class >= 2:
            return (owner_class, confidence, source_rank,
                    str(info.get("source_id") or ""), str(info.get("channel_id") or ""))
        return (owner_class, source_rank, confidence,
                str(info.get("source_id") or ""), str(info.get("channel_id") or ""))

    def _mapping_infos_for_ref(self, ref):
        rows = []
        seen = set()
        for key in self._ref_lookup_keys(ref):
            for info in self._mapped_info_by_ref.get(key) or []:
                sig = (str(info.get("source_id") or ""),
                       str(info.get("channel_id") or ""),
                       str(info.get("mode") or ""))
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append(info)
        # 8.2.1: a receiver channel can have several *candidate* SRP claims but
        # only one active owner. Keep diagnostics available while putting the
        # same deterministic primary owner first everywhere in the UI.
        service = {}
        try:
            service = (self._visible_service_by_ref or {}).get(str(ref or "")) or {}
        except Exception:
            service = {}
        rows.sort(key=lambda info: self._mapping_info_rank(info, service), reverse=True)
        return rows

    def _primary_mapping_info_for_ref(self, ref):
        rows = self._mapping_infos_for_ref(ref)
        return rows[0] if rows else None

    def _service_is_mapped(self, service_or_ref):
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        for key in self._ref_lookup_keys(ref):
            if key in self._mapped_refs or (self._mapped_info_by_ref.get(key) or []):
                return True
        return False

    def _service_has_conflict(self, service_or_ref):
        """Only unresolved explicit ownership is a conflict in 8.2.1.

        Multiple valid SRP/provider candidates are alternatives, not multiple
        active mappings. They remain visible as PROVEN/REVIEW in pane 4 while a
        single deterministic owner is GREEN/MAPPED. A conflict is therefore
        reserved for legacy data containing more than one explicit MANUAL owner
        for the same receiver service. Durable AUTO duplicates are resolved by the
        same deterministic one-owner ranking and no longer count as active conflicts.
        """
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        infos = self._mapping_infos_for_ref(ref)
        manual = [x for x in infos if str((x or {}).get("mode") or "").lower() == "manual"]
        return len(manual) > 1

    def _service_is_manual_locked(self, service_or_ref):
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        try:
            manual = set(str(x or "").strip() for x in (self.store.manual_refs() or set()) if x)
        except Exception:
            manual = set()
        if not manual:
            return False
        canon_manual = set()
        for item in manual:
            canon_manual.update(self._ref_lookup_keys(item))
        return any(key in canon_manual for key in self._ref_lookup_keys(ref))

    def _service_is_auto_repaired(self, service_or_ref):
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        return any(str((info or {}).get("mode") or "").lower() == "auto-repair"
                   for info in self._mapping_infos_for_ref(ref))

    def _merge_persisted_store_ownership(self):
        """Merge saved MANUAL/AUTO ownership into the current cached view.

        beta138 makes MappingStore the durable source for user/Auto Repair
        assignments.  The global Mapping View cache may be older than the last
        satellite repair, so reopening Mapping Repair must not rescan or repaint
        an already saved AUTO channel as unmapped.  This is a tiny JSON already
        resident in MappingStore; no XMLTV/SRP scan occurs here.
        """
        try:
            mappings = self.store.all() or {}
        except Exception:
            return 0
        active = set(self._auto_allowed_source_ids() or set())
        added = 0
        for map_key, saved in mappings.items():
            saved = saved or {}
            refs = list(saved.get("refs") or [])
            if not refs or "::" not in str(map_key):
                continue
            sid, cid = str(map_key).split("::", 1)
            mode = str(saved.get("mode") or "manual").lower()
            # Manual corrections are always authoritative. AUTO ownership only
            # counts when its source is still enabled; disabling a source makes
            # those services eligible for repair again.
            if mode != "manual" and sid not in active:
                continue
            info = {
                "source_id": sid, "source_name": self._source_label(sid),
                "channel_id": cid,
                "display_name": str(saved.get("display_name") or cid),
                "mode": mode,
                "confidence": int(saved.get("confidence") or 0),
                "reason": str(saved.get("reason") or ""),
            }
            for ref in refs:
                try:
                    if mode != "manual" and (self.store.is_blocked(ref) or self.store.is_ignored(ref)):
                        continue
                except Exception:
                    pass
                for key in self._ref_lookup_keys(ref):
                    if not key:
                        continue
                    if key not in self._mapped_refs:
                        added += 1
                    self._mapped_refs.add(key)
                    rows = self._mapped_info_by_ref.setdefault(key, [])
                    sig = (sid, cid, mode)
                    if all((str(x.get("source_id") or ""), str(x.get("channel_id") or ""), str(x.get("mode") or "").lower()) != sig for x in rows):
                        rows.append(dict(info))
            self._mapped_epg_keys.add(self.store._key(sid, cid))
        if added:
            self.mapping_revision += 1
            self.selection_cache.clear()
        return added

    def _refresh_ignored_ref_keys(self, refs=None):
        if refs is None:
            try:
                refs = self.store.ignored_refs() or []
            except Exception:
                refs = []
        keys = set()
        for ref in refs or []:
            keys.update(self._ref_lookup_keys(ref))
        self._ignored_ref_keys = keys
        return keys

    def _service_is_ignored(self, service_or_ref):
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        return any(key in self._ignored_ref_keys for key in self._ref_lookup_keys(ref))

    def _repair_exception_type(self, service_or_ref):
        ref = service_or_ref.get("ref") if isinstance(service_or_ref, dict) else service_or_ref
        for key in self._ref_lookup_keys(ref):
            kind = str((self._repair_exception_kind or {}).get(key) or "")
            if kind:
                return kind
        return ""

    def _load_repair_result_state(self, snap):
        self._repair_exception_kind = {}
        self._refresh_ignored_ref_keys((snap or {}).get("ignored_refs") or [])
        if not self._repair_mode or not self._initial_reception:
            return
        result = dict(((snap or {}).get("repair_results") or {}).get(str(self._initial_reception)) or {})
        for field, kind in (("review_refs", "REVIEW"), ("ambiguous_refs", "AMBIGUOUS"), ("no_match_refs", "NO MATCH")):
            for ref in result.get(field) or []:
                for key in self._ref_lookup_keys(ref):
                    self._repair_exception_kind[key] = kind

    def _set_repair_result_state(self, review_refs=None, ambiguous_refs=None, no_match_refs=None):
        self._repair_exception_kind = {}
        for rows, kind in ((review_refs or [], "REVIEW"), (ambiguous_refs or [], "AMBIGUOUS"), (no_match_refs or [], "NO MATCH")):
            for ref in rows:
                for key in self._ref_lookup_keys(ref):
                    self._repair_exception_kind[key] = kind

    def _mapped_display_refs(self, services):
        # ChannelPaneList only needs the exact raw refs of rows currently on
        # screen.  Build that tiny set after canonical mapping resolution.
        return set(str((svc or {}).get("ref") or "") for svc in (services or [])
                   if self._service_is_mapped(svc))


    def _light_suggestion_for_service(self, service):
        """Return a prepared Smart Mapping hint without I/O or global fuzzy work.

        This method is called while painting the receiver list, so it must remain
        bounded and cache-only.  It uses the same compact ID/name index consumed
        by GREEN Find Matches and never prepares/downloads a source.
        """
        service = service or {}
        ref = str(service.get("ref") or "")
        if not ref or self._service_is_mapped(service) or self._service_is_ignored(service):
            return False
        if not self._smart_name_index_ready or not isinstance(self._smart_name_index, dict):
            return False
        cache_key = (ref, int(self.mapping_revision or 0), id(self._smart_name_index))
        if cache_key in self._channel_status_hint_cache:
            return bool(self._channel_status_hint_cache.get(cache_key))
        try:
            # Yellow is intentionally a cheap "candidate exists" state. Full
            # source policy + Mapping V6 validation runs only on GREEN/RIGHT.
            found = manual_match_lite.has_prepared_hint_fast(service, self._smart_name_index)
        except Exception:
            found = False
        # 8.1.1: 1200 entries was too small for a full Reception List and
        # forced repeated normalize/compact work whenever the user changed SAT.
        # This cache is already cleared on mapping/index invalidation, so allow a
        # receiver-sized generation before applying the emergency bound.
        if len(self._channel_status_hint_cache) > 32768:
            self._channel_status_hint_cache.clear()
        self._channel_status_hint_cache[cache_key] = bool(found)
        return bool(found)

    def _mapping_suffix(self, ref):
        infos = self._mapping_infos_for_ref(ref)
        if not infos:
            if self._service_is_ignored(ref):
                return "  • IGNORE" if not self._repair_mode else "  • NO EPG / IGNORE"
            kind = self._repair_exception_type(ref)
            if kind:
                return "  • " + kind
            service = (self._visible_service_by_ref or {}).get(str(ref or "")) or {}
            if not self._repair_mode and service and self._light_suggestion_for_service(service):
                return "  • SUGGESTION"
            return "  • UNMAPPED" if not self._repair_mode else ""
        first = self._primary_mapping_info_for_ref(ref) or infos[0]
        # Smart Mapping Lite keeps the receiver pane intentionally compact. The
        # exact target/source is shown in the top context card when the row is
        # selected, so every channel row only needs its single active owner.
        if not self._repair_mode:
            mode = str(first.get("mode") or "").lower()
            return "  • MAPPED%s" % (" • MANUAL" if mode == "manual" else "")
        text = "%s • %s" % (first.get("display_name") or first.get("channel_id") or "channel",
                               self._source_display_label(first.get("source_id"), first.get("source_name")))
        if str(first.get("mode") or "").lower() == "auto-repair":
            text += " • AUTO"
        if self._service_has_conflict(ref):
            text += " • OWNER CONFLICT"
        return "  =>  " + text

    def _rebuild_epg_index(self):
        by_source = {}
        for item in self.epg_channels:
            by_source.setdefault(item.get("source_id"), []).append(item)
        self.epg_by_source = by_source
        self.selection_cache.clear()

    _SATELLITE_NAMES = {
        -30.0: "Hispasat 30W-5/6",
        -8.0: "Eutelsat 8 West B",
        -7.0: "Nilesat 201/301",
        -1.0: "Thor / Intelsat 10-02",
        -0.8: "Thor 5/6/7",
        4.8: "Astra 4A",
        7.0: "Eutelsat 7B/7C",
        9.0: "Eutelsat 9B",
        13.0: "Hot Bird 13F/13G",
        16.0: "Eutelsat 16A",
        19.2: "Astra 1KR/1L/1M/1N",
        21.6: "Eutelsat 21B",
        23.5: "Astra 3B",
        25.5: "Es'hail 1 / Eutelsat 25B",
        26.0: "Badr 4/5/6/7/8",
        28.2: "Astra 2E/2F/2G",
        31.5: "Astra 5B",
        39.0: "Hellas Sat 3/4",
        42.0: "Turksat 3A/4A/5B",
    }

    @classmethod
    def _satellite_friendly_name(cls, orbital):
        try:
            value = round(float(orbital), 1)
        except Exception:
            return ""
        if value in cls._SATELLITE_NAMES:
            return cls._SATELLITE_NAMES[value]
        # tolerate tiny tuner/settings rounding differences
        for pos, name in cls._SATELLITE_NAMES.items():
            if abs(value - pos) <= 0.15:
                return name
        return ""

    def _bouquet_children(self, parent_file):
        return [x for x in self.all_bouquet_records if x.get("parent_file") == parent_file]

    def _rebuild_bouquet_view(self, preserve_file=None):
        """Show satellite Reception Lists *and* the receiver's real bouquets.

        beta119 made this pane SAT-only and hid user bouquets.  beta122 keeps
        Reception Lists first (fastest DVB workflow) and restores the cached
        bouquet tree underneath.  This is a pure in-memory view rebuild: no
        lamedb parse, bouquet scan or network request is performed here.
        """
        by_parent = {}
        for item in self.all_bouquet_records or []:
            by_parent.setdefault(item.get("parent_file"), []).append(item)

        visible = []
        # 1) Reception Lists first, preserving the fast SAT-centric workflow.
        sat_groups = {}
        sat_sort_values = {}
        sat_services = {}
        for service in self.catalog or []:
            kind = service.get("service_type") or channel_mapper.classify_service_ref(service.get("ref"))
            if kind != "SAT":
                continue
            label = service.get("reception_list") or channel_registry._orbital_label(service.get("orbital"))
            sat_groups[label] = sat_groups.get(label, 0) + 1
            # Build the grouping while we are already walking the registry.
            # refresh_channels() can then select one satellite in O(1) instead of
            # scanning every SAT service on every UP/DOWN keypress.
            sat_services.setdefault(label, []).append(service)
            if label not in sat_sort_values:
                try:
                    sat_sort_values[label] = float(service.get("orbital"))
                except Exception:
                    sat_sort_values[label] = 9999.0
        self._sat_services_by_reception = sat_services
        self._sat_sorted_services_by_reception = {}
        for label in sorted(sat_groups, key=lambda x: sat_sort_values.get(x, 9999.0)):
            visible.append({
                "virtual": "reception",
                "reception_list": label,
                "bouquet_file": "__EPGMANAGER_RECEPTION_%s__" % label.replace(".", "_").replace("-", "W"),
                "bouquet_label": "%s  %s  •  %d SAT services" % (
                    label, self._satellite_friendly_name(sat_sort_values.get(label)) or "Satellite", sat_groups[label]),
                "parent_file": None,
                "depth": 0,
            })

        # 2) Real OpenATV bouquets from the already-cached receiver snapshot.
        def add_branch(parent_file=None):
            for item in by_parent.get(parent_file, []):
                visible.append(item)
                if item.get("bouquet_file") in self.expanded_bouquets:
                    add_branch(item.get("bouquet_file"))
        add_branch(None)
        seen = set(x.get("bouquet_file") for x in visible)
        for item in self.all_bouquet_records or []:
            if item.get("bouquet_file") not in seen and not item.get("parent_file"):
                visible.append(item)

        self.bouquet_records = visible
        rows = []
        target_idx = 0
        for idx, item in enumerate(visible):
            filename = item.get("bouquet_file")
            if item.get("virtual") == "reception":
                rows.append("★ " + item.get("bouquet_label", "Reception List"))
            else:
                children = by_parent.get(filename, [])
                prefix = ""
                if children:
                    prefix = "▼ " if filename in self.expanded_bouquets else "▶ "
                indent = "  " * int(item.get("depth") or 0)
                rows.append(indent + "▣ " + prefix + (item.get("bouquet_label") or filename or "Bouquet"))
            if preserve_file and filename == preserve_file:
                target_idx = idx
        self._set_list("bouquets", rows or ["No Reception Lists / bouquets found"])
        if rows:
            self._set_index("bouquets", target_idx)

    def _toggle_current_bouquet(self):
        item = self._current("bouquets", self.bouquet_records)
        if not item:
            return False
        filename = item.get("bouquet_file")
        if item.get("virtual"):
            return False
        if not self._bouquet_children(filename):
            return False
        if filename in self.expanded_bouquets:
            self.expanded_bouquets.remove(filename)
        else:
            self.expanded_bouquets.add(filename)
        self._rebuild_bouquet_view(filename)
        self.refresh_channels()
        return True

    _ARAB_COUNTRIES = set(("MA","DZ","TN","LY","EG","SA","QA","AE","PS","LB","JO","IQ","KW","BH","OM","YE","SY","MENA"))
    _REGION_ORDER = {"ARAB / MENA":0, "EUROPE":1, "AFRICA":2, "ASIA":3, "AMERICAS":4, "OCEANIA":5, "INTERNATIONAL":6}

    def _smart_region_for_country(self, code):
        code = str(code or "INT").upper()
        if code in self._ARAB_COUNTRIES:
            return "ARAB / MENA"
        if code in ("US","CA","MX","BS","GT","HN","NI","TT","JM","BB","CR","DO","PA"):
            return "AMERICAS"
        if code in ("BR","AR_COUNTRY","BO","CL","CO","EC","PE","PY","UY","VE","SV"):
            return "AMERICAS"
        cont = source_catalog._COUNTRY_CONTINENT.get(code, "INTERNATIONAL")
        if cont == "EUROPE": return "EUROPE"
        if cont == "AFRICA": return "AFRICA"
        if cont == "ASIA": return "ASIA"
        if cont == "OCEANIA": return "OCEANIA"
        return "INTERNATIONAL"

    def _source_language_rank(self, src):
        """Tie-break duplicate channel names by preferred EPG language.

        User preference: Arabic, mixed Arabic/English, French, English, then
        original/unknown. Name confidence always remains the primary safety
        signal; language never rescues a weak or wrong channel-name match.
        """
        src = src or {}
        sid = str(src.get("source_id") or src.get("id") or "")
        explicit = str(src.get("language") or "").lower().replace("_", "-")
        cache_key = (sid, explicit)
        cached_rank = getattr(self, "_source_language_rank_cache", {}).get(cache_key)
        if cached_rank is not None:
            return cached_rank
        meta = {}
        try:
            meta = source_metadata.get(src, self._epg_dir()) or {}
        except Exception:
            meta = {}
        counts = dict(meta.get("counts") or {})
        ar = int(counts.get("ar") or 0); en = int(counts.get("en") or 0); fr = int(counts.get("fr") or 0)
        known = ar + en + fr
        if known and ar > 0 and en > 0 and (100 * ar // known) >= 15 and (100 * en // known) >= 15:
            # pure Arabic is still preferred when the catalogue explicitly says ar
            # and the mixed evidence is weak; otherwise this is the bilingual tier.
            if explicit.startswith("ar") and en * 8 < ar:
                try: self._source_language_rank_cache[cache_key] = 0
                except Exception: pass
                return 0
            try: self._source_language_rank_cache[cache_key] = 1
            except Exception: pass
            return 1
        lang = explicit or str(meta.get("language") or "").lower()
        if lang.startswith("ar") or (known and ar >= max(en, fr) and ar > 0): rank = 0
        elif lang.startswith("fr") or (known and fr > max(ar, en)): rank = 2
        elif lang.startswith("en") or (known and en > max(ar, fr)): rank = 3
        else: rank = 4
        try: self._source_language_rank_cache[cache_key] = rank
        except Exception: pass
        return rank

    def _source_group_key(self, item):
        """Virtual source key used by beta71 Smart Country Sources.

        Provider feeds remain separate internally; the main Smart Mapping source
        pane exposes one logical country/region row to remove provider clutter.
        """
        try:
            code = str(source_catalog.primary_country_code(item) or "INT")
        except Exception:
            code = "INT"
        return code

    def _country_bundle_child_priority(self, child):
        sid = str((child or {}).get("source_id") or "")
        selected = sid in (self.auto_selected_source_ids or set())
        local = not bool((child or {}).get("external"))
        return (0 if selected else 1, 0 if local else 1, _alpha_text((child or {}).get("source_name") or sid))

    _DIRECT_SPECIALIST_SOURCE_IDS = set((
        "bein_sports", "ext_epgshare_al_jazeera",
    ))

    def _is_direct_specialist_source(self, item):
        """Keep specialist MENA catalogues visible instead of hiding them in MENA."""
        sid = str((item or {}).get("source_id") or "")
        return sid in self._DIRECT_SPECIALIST_SOURCE_IDS

    def _source_backend(self, src):
        return str((src or {}).get("provider") or "EPG").upper().strip() or "EPG"

    _DIRECT_SHORT_NAMES = {
        "ext_epgscrapers_morocco": "Morocco",
        "ext_epgscrapers_bein": "beIN",
        "ext_epgscrapers_elcinema": "ElCinema",
        "ext_epgscrapers_osn": "OSN",
        "ext_epgscrapers_sport24": "Sport24",
        "ext_epgscrapers_dubaiplus": "Dubai+",
        "ext_epgscrapers_starzplay": "STARZPLAY GCC",
        "ext_epgscrapers_shahid": "Shahid/MBC",
        "ext_epgscrapers_rotana": "Rotana",
        "ext_epgscrapers_stctv": "STC TV",
        "ext_epgscrapers_aljazeera": "Al Jazeera",
        "ext_epgscrapers_alkass": "Al Kass",
        "ext_epgscrapers_tunisiatv": "TunisiaTV",
        "ext_epgscrapers_tabie": "Tabie QMC",
    }

    def _compact_source_name(self, source_id=None, src=None, fallback=None):
        """Modern Smart Mapping source label: source name only, never group/backend noise."""
        src = src or {}
        sid = str(source_id or src.get("source_id") or src.get("id") or "")
        if sid in self._DIRECT_SHORT_NAMES:
            return self._DIRECT_SHORT_NAMES[sid]
        name = str(fallback or src.get("source_name") or src.get("name") or self._source_label(sid) or sid or "EPG")
        # Strip legacy group/provider decorations used by older Source Override UIs.
        name = re.sub(r"^\[[^]]+\]\s*", "", name).strip()
        name = re.sub(r"\s*[•|-]\s*EPG\s*SCRAPERS?.*$", "", name, flags=re.I).strip()
        return name or "EPG"

    def _epg_health_text(self, epg):
        marker = self._epg_programme_marker(epg)
        if "NO EPG" in marker:
            return "NO EPG"
        if "WARN" in marker:
            return "EPG WARN"
        if "ID OK" in marker:
            return "EPG OK"
        return "EPG ?"

    def _compact_epg_row(self, epg, score=None, grade=None, mapped=False):
        """Candidate row title: friendly channel name first, stable XMLTV ID second.

        rc58 intentionally painted only the XMLTV ID.  That is readable for
        semantic IDs but unusable for direct catalogues with stable opaque IDs
        such as ``stctv.bb90021b8c50``.  Direct feeds already provide a
        display-name, so keep the stable ID for mapping while exposing the human
        channel name in the list.  Name-first ordering also means a narrow
        Metrix pane clips the technical ID before it clips the useful label.
        """
        epg = epg or {}
        cid = str(epg.get("channel_id") or epg.get("display_name") or "ID").strip() or "ID"
        name = str(epg.get("display_name") or "").strip()
        # Avoid visual duplication when providers publish display-name == ID.
        if name and name.casefold() != cid.casefold():
            core_text = "%s  •  %s" % (name, cid)
        else:
            core_text = cid
        # rc74: always expose the owning provider beside every XMLTV ID.
        # This is intentionally part of the row text because the permanent
        # Source pane was removed in rc73.
        source_name = self._compact_source_name(
            epg.get("source_id"), epg, epg.get("source_name") or epg.get("provider"))
        text = "%s  •  %s" % (source_name, core_text) if source_name else core_text
        lead = ""
        if score is not None:
            try: lead = "%d%%  " % max(0, min(100, int(score)))
            except Exception: lead = ""
        return "%s%s" % (lead, text)

    # 8.2.0: OSN channels need a *catalogue* family, not only visible source
    # labels.  The first rows are the most useful country guides seen with OSN.
    # OpenEPG entries use compact channel-ID catalogues where available.
    _OSN_MENA_SOURCE_PRIORITY = {
        "ext_openepg_egypt_1": 1,
        "ext_openepg_egypt_2": 2,
        "ext_openepg_uae_6": 5,
        "ext_openepg_saudi_arabia_1": 10,
        "ext_openepg_saudi_arabia_2": 11,
        "ext_openepg_saudi_arabia_3": 12,
        "ext_openepg_saudi_arabia_4": 13,
        "ext_openepg_saudi_arabia_5": 14,
        "ext_openepg_saudi_arabia_6": 15,
        "ext_openepg_qatar_1": 20,
        "ext_openepg_qatar_2": 21,
        "ext_openepg_qatar_3": 22,
        "ext_openepg_qatar_4": 23,
        "ext_openepg_qatar_5": 24,
        "ext_openepg_qatar_6": 25,
        "ext_openepg_palestine_1": 30,
        "ext_epgshare_al_jazeera": 31,
        "bein_sports": 32,
    }

    # Missing OSN country catalogues are prepared lazily only while the virtual
    # SUGGESTION row is open.  One request runs at a time in a daemon thread, so
    # Source Override and receiver navigation never wait on HTTP/XML parsing.
    _OSN_MENA_DISCOVERY_IDS = (
        "ext_openepg_egypt_1", "ext_openepg_egypt_2",
        "ext_openepg_uae_6",
        "ext_openepg_saudi_arabia_5", "ext_openepg_saudi_arabia_6",
        "ext_openepg_saudi_arabia_1", "ext_openepg_saudi_arabia_2",
        "ext_openepg_saudi_arabia_3", "ext_openepg_saudi_arabia_4",
        "ext_openepg_qatar_4", "ext_openepg_qatar_5",
    )

    def _smart_source_display_name(self, item):
        """Short, explicit source names for the narrow Source Override pane."""
        item = item or {}
        sid = str(item.get("source_id") or item.get("id") or "")
        name = str(item.get("source_name") or item.get("name") or sid or "Source")
        labels = {
            "ext_openepg_egypt_1": "Egypt AR",
            "ext_openepg_egypt_2": "Egypt EN",
            "ext_openepg_uae_6": "UAE OpenEPG",
        }
        if sid in labels:
            return labels[sid]
        # Keep Saudi/Qatar feed number (they are distinct catalogues) but make
        # parallel programme-language shards obvious to the user.
        lang = str(item.get("language") or "").lower().split("-", 1)[0]
        if sid.startswith("ext_openepg_saudi_arabia_") or sid.startswith("ext_openepg_qatar_"):
            if lang in ("ar", "en", "fr"):
                return "%s [%s]" % (name, lang.upper())
        return name

    def _osn_mena_source_rank(self, item, receiver_name):
        if "osn" not in _alpha_text(receiver_name or ""):
            return 999
        return int(self._OSN_MENA_SOURCE_PRIORITY.get(str((item or {}).get("source_id") or ""), 999))

    def _is_osn_service(self, service):
        service = service or {}
        text = _alpha_text("%s %s" % (service.get("name") or "", service.get("provider_name") or ""))
        return "osn" in text

    def _queue_osn_mena_catalogue_prefetch(self, service):
        """Prepare missing OSN/MENA Channel-ID catalogues without blocking UI.

        8.2.0 fixes the gap between *showing* Egypt/UAE/Lebanon in Source
        Override and actually having their IDs available to ALL SOURCES.  The
        work is strictly catalogue-only: one source at a time, daemon worker,
        XMLTV channel header only (or provider tiny ID list).  No programme
        body is retained and no receiver mapping is changed.
        """
        if not self._is_osn_service(service):
            return False
        if getattr(self, "_direct_sync_busy", False):
            return False

        # Current mapped source first so a persisted owner remains visible before secondary feeds.
        ordered = []
        primary = self._primary_mapping_info_for_ref((service or {}).get("ref")) or {}
        sid = str(primary.get("source_id") or "")
        if sid:
            ordered.append(sid)
        for sid in self._OSN_MENA_DISCOVERY_IDS:
            if sid not in ordered:
                ordered.append(sid)

        for sid in ordered:
            if sid in getattr(self, "_direct_sync_attempted", set()):
                continue
            src = self._source_by_id(sid) or {}
            if not src or not src.get("external"):
                continue
            try:
                if smart_catalog_boot.has(sid) or source_channel_cache.has(sid):
                    continue
            except Exception:
                pass
            self._ensure_external_source_ready(src)
            if getattr(self, "_direct_sync_busy", False) and str(getattr(self, "_direct_sync_source_id", "") or "") == sid:
                return True
        return False

    def _rebuild_source_view(self, preserve_id=None):
        """7.0.9: SUGGESTION first, then every EPG feed as an independent row.

        Previous builds virtually merged generic providers into country bundles
        (for example Morocco / MENA / France).  That made the third Smart
        Mapping pane compact, but it hid the real provider/feed identity and
        forced extra bundle handling when browsing matches.

        Smart Mapping now consumes the canonical per-source catalogue directly:
        one source_id = one row = one RAM catalogue.  No country/source merge is
        performed here.  BootCatalogRAM already preloads the compact ID shards
        per source, so moving the cursor between rows remains cache-only.
        """
        recommended = str(getattr(self, "_smart_recommended_source_id", "") or "")
        real_rows = []
        selected_ids = self.auto_selected_source_ids or set()

        # 7.0.9: the very first Source Override row is a virtual, RAM-only
        # cross-source suggestion browser for the receiver channel under the
        # cursor.  It never owns/downloads a feed: entering it queries the
        # resident Smart-name index and shows the best matching EPG ID from
        # each prepared source in pane 4.
        service = self._current("channels", self.bouquet_services)
        receiver_name = str((service or {}).get("name") or "Receiver channel")
        primary_mapping = self._primary_mapping_info_for_ref((service or {}).get("ref")) or {}
        current_mapping_ids = set([str(primary_mapping.get("source_id") or "")]) if primary_mapping.get("source_id") else set()
        suggestion_row = {
            "source_id": "__smart_suggestions__",
            "source_name": "SUGGESTION",
            "provider": "RAM",
            "external": False,
            "suggestion_all_sources": True,
            "individual_source": True,
            "direct_source": True,
            "country_code": "ALL",
            "smart_region": "SMART",
            "receiver_name": receiver_name,
        }

        for raw in getattr(self, "_all_source_groups", []) or []:
            if not raw or raw.get("source_group"):
                continue
            item = dict(raw)
            sid = str(item.get("source_id") or "")
            if not sid:
                continue
            code = self._source_group_key(item)
            item["country_code"] = code
            item["smart_region"] = self._smart_region_for_country(code)
            item["contains_recommended"] = (sid == recommended)
            item["current_mapping"] = (sid in current_mapping_ids)
            item["selected_count"] = 1 if sid in selected_ids else 0
            item["individual_source"] = True
            # Reuse the existing direct-source rendering path: it shows the
            # actual feed/provider and never enters country-bundle code.
            item["direct_source"] = True
            real_rows.append(item)

        # 8.1.9: do not push every ACTIVE source above inactive siblings. That
        # made Egypt EN/UAE look missing when many Arabic feeds were selected.
        # BEST remains first; OSN channels then pin the useful MENA family; all
        # remaining rows stay together by region/name. This is metadata-only.
        def _individual_source_sort(item):
            osn_rank = self._osn_mena_source_rank(item, receiver_name)
            return (
                0 if item.get("current_mapping") else 1,
                0 if item.get("contains_recommended") else 1,
                0 if osn_rank < 999 else 1,
                osn_rank,
                self._REGION_ORDER.get(item.get("smart_region"), 99),
                _alpha_text(self._smart_source_display_name(item)),
                _alpha_text(self._source_backend(item)),
                0 if int(item.get("selected_count") or 0) else 1,
            )

        real_rows.sort(key=_individual_source_sort)
        rows_src = [suggestion_row] + real_rows
        self.source_groups = rows_src

        display_rows = []
        target = 0
        for i, item in enumerate(rows_src):
            if item.get("suggestion_all_sources"):
                display_rows.append("AUTO • Best Match")
            else:
                sid = str(item.get("source_id") or "")
                name = self._compact_source_name(sid, item, self._smart_source_display_name(item))
                state = (" • CURRENT" if item.get("current_mapping") else
                         (" • BEST" if item.get("contains_recommended") else
                          (" • ON" if int(item.get("selected_count") or 0) else "")))
                display_rows.append("%s%s" % (name, state))
            if preserve_id and str(preserve_id) == str(item.get("source_id") or ""):
                target = i

        self._set_list("sources", display_rows or ["No EPG sources"])
        if display_rows:
            self._set_index("sources", target)

    def _toggle_current_source_group(self):
        # beta71 country rows are the source themselves, not folders. OK/RIGHT
        # enters the merged EPG Selection directly.
        item = self._current("sources", self.source_groups)
        if item and item.get("country_bundle"):
            return False
        return False

    def _bundle_entries(self, bundle, allow_prepare=False):
        """Merge cached channel catalogues for one country without XML merging.

        Same-name rows from the same language are deduplicated. If the same TV
        identity exists in another language, EN/FR are suffixed only where that
        distinction is actually needed (Arabic/default stays visually clean).
        """
        children=list((bundle or {}).get("children") or [])
        selected=set(self.auto_selected_source_ids or [])
        # When the country contains explicitly active feeds, EPG Match should
        # represent those feeds—not every stale cache ever opened for that
        # country. This removes inactive Saudi 2/4/6 [EN] rows when 1/3/5 [AR]
        # are the active variants and keeps manual/auto behaviour consistent.
        forced_sid = str(getattr(self, "_selection_forced_source_id", "") or "")
        forced_children = [x for x in children if str(x.get("source_id") or "") == forced_sid] if forced_sid else []
        active_children = [x for x in children if str(x.get("source_id") or "") in selected]
        if forced_children:
            # Find Suggestion may deliberately expose a prepared-but-inactive
            # concrete feed.  Show exactly that feed in EPG Match.
            children = forced_children
        elif active_children:
            children = active_children

        # 7.0.1: merging/deduplicating a whole country bundle (Saudi, Turkey,
        # France...) on every receiver channel was needlessly expensive.  Cache
        # the merged catalogue until a child list or source-cache generation
        # actually changes.  No programme data is stored here.
        # RC7 incremental invalidation: bundle cache depends only on its own
        # child source shards. Refreshing an unrelated provider no longer forces
        # this country's merged list to be rebuilt.
        source_revisions = tuple((str(x.get("source_id") or ""),
                                  source_channel_cache.source_signature(str(x.get("source_id") or "")))
                                 for x in children)
        loaded_sig = tuple((str(x.get("source_id") or ""),
                            id(self.epg_by_source.get(str(x.get("source_id") or ""))),
                            len(self.epg_by_source.get(str(x.get("source_id") or "")) or []))
                           for x in children)
        bundle_key = (str((bundle or {}).get("source_id") or (bundle or {}).get("country_code") or ""),
                      forced_sid, tuple(sorted(str(x.get("source_id") or "") for x in children)),
                      source_revisions, loaded_sig)
        cached_bundle = self._bundle_entries_cache.get(bundle_key)
        if cached_bundle is not None:
            return list(cached_bundle)

        raw=[]; missing=[]
        for child in children:
            sid=str(child.get("source_id") or "")
            rows=list(self.epg_by_source.get(sid) or [])
            if not rows:
                try:
                    rows=list(smart_catalog_boot.get(sid) or [])
                except Exception:
                    rows=[]
            if not rows:
                try:
                    if child.get("external"):
                        rows=source_channel_cache.get(sid) if source_channel_cache.has(sid) else []
                    else:
                        rows=source_catalog.local_channel_hints(sid) or []
                except Exception:
                    rows=[]
            if not rows and child.get("external") and sid in selected:
                missing.append(child)
            for row in rows:
                item=dict(row or {})
                item["source_id"]=sid
                item["source_name"]=child.get("source_name") or sid
                item["_language"]=str(child.get("language") or "").lower()
                item["_source_selected"]=sid in selected
                item["_source_local"]=not bool(child.get("external"))
                raw.append(item)
        # 7.0.10 strict navigation rule: RIGHT/OK never starts a provider
        # request. Boot discovery prepares compact ID shards in the background;
        # YELLOW remains the only explicit per-source network refresh action.
        groups={}
        for item in raw:
            name=str(item.get("display_name") or item.get("channel_id") or "").strip()
            key=self._smart_norm(name, True) or name.casefold()
            groups.setdefault(key,[]).append(item)
        merged=[]
        for key, items in groups.items():
            by_lang={}
            for item in items:
                lang=str(item.get("_language") or "")
                bucket=by_lang.setdefault(lang,[])
                bucket.append(item)
            multi_lang=len([k for k,v in by_lang.items() if v]) > 1
            for lang, rows in by_lang.items():
                rows.sort(key=lambda r:(0 if r.get("_source_selected") else 1, 0 if r.get("_source_local") else 1, _alpha_text(r.get("source_name"))))
                best=dict(rows[0])
                display=str(best.get("display_name") or best.get("channel_id") or "")
                if multi_lang and lang in ("en","fr"):
                    display += " [%s]" % lang.upper()
                best["display_name"]=display
                merged.append(best)
        merged.sort(key=lambda x:_alpha_text(x.get("display_name") or x.get("channel_id")))
        if len(self._bundle_entries_cache) >= 16:
            try:
                self._bundle_entries_cache.pop(next(iter(self._bundle_entries_cache)))
            except Exception:
                self._bundle_entries_cache.clear()
        self._bundle_entries_cache[bundle_key] = list(merged)
        return merged

    def _start_mapping_view_build(self, srp_state=None):
        if self._mapping_view_busy:
            return
        state = srp_state if isinstance(srp_state, dict) else (getattr(self, '_resident_srp_state', {}) or {})
        if not state:
            return
        self._mapping_view_busy=True; self._mapping_view_result=None
        self._mapping_view_token += 1; token=self._mapping_view_token
        catalog=list(self.catalog or [])
        # beta130: Mapping Repair validates only the satellite the user chose.
        # Never run a whole-receiver PrecisionMatch sweep just to enter Repair.
        # A valid global cache is still reused when present. Partial results are
        # screen-local and are never persisted as the global ownership cache.
        self._mapping_view_partial = False
        if self._repair_mode and self._initial_reception:
            wanted = str(self._initial_reception or "")
            catalog = [svc for svc in catalog
                       if (svc.get("reception_list") or channel_registry._orbital_label(svc.get("orbital"))) == wanted]
            self._mapping_view_partial = True
        try: selected=list(self.source_preferences.selected_mapping_ids() or [])
        except Exception: selected=[]
        try: mappings=self.store.all() or {}
        except Exception: mappings={}
        try: blocked=self.store.blocked_refs() or []
        except Exception: blocked=[]
        def worker():
            try:
                data=smart_mapping_view.build(catalog,state,selected,mappings,blocked)
                self._mapping_view_result=(token,data,None)
            except Exception as exc:
                self._mapping_view_result=(token,None,str(exc))
        threading.Thread(target=worker,daemon=True).start()
        try: self._timer.start(120,False)
        except Exception: pass

    def _apply_mapping_view_data(self, data, persist=False):
        if not isinstance(data, dict):
            return False
        mapped_refs=set(data.get('mapped_refs') or set())
        mapped_info=dict(data.get('mapped_info_by_ref') or {})

        # 8.1.7 status-stability fix: the persistent Mapping View intentionally
        # compacts ownership under canonical ServiceRefs.  The ultra-fast paint
        # path, however, reads raw lamedb refs only.  8.1.6 expanded mapped_refs
        # back to raw refs but did not expand mapped_info_by_ref, so the summary
        # correctly counted a service as MAPPED while its receiver row could be
        # repainted YELLOW as SUGGESTION.  Expand the tiny ownership dictionary
        # once when the snapshot is applied; cursor/satellite navigation stays
        # O(1) and never canonicalizes thousands of rows on every repaint.
        info_by_key={}
        def merge_info(key, infos):
            key=str(key or '').strip()
            if not key or not infos:
                return
            bucket=info_by_key.setdefault(key, [])
            seen={(str(x.get('source_id') or ''), str(x.get('channel_id') or ''), str(x.get('mode') or ''))
                  for x in bucket}
            for info in infos or []:
                row=dict(info or {})
                sig=(str(row.get('source_id') or ''), str(row.get('channel_id') or ''), str(row.get('mode') or ''))
                if sig not in seen:
                    seen.add(sig); bucket.append(row)

        canon=set()
        for key, infos in list(mapped_info.items()):
            raw_key=str(key or '').strip()
            short_key=raw_key.rstrip(':')
            try: canon_key=channel_registry.canonical_service_ref(raw_key) or short_key
            except Exception: canon_key=short_key
            for lookup in (raw_key, short_key, canon_key):
                merge_info(lookup, infos)
        for ref in list(mapped_refs):
            try: canon.add(channel_registry.canonical_service_ref(ref) or str(ref or '').rstrip(':'))
            except Exception: canon.add(str(ref or '').rstrip(':'))
        for service in list(getattr(self, 'catalog', []) or []):
            raw=str((service or {}).get('ref') or '').strip()
            if not raw: continue
            short=raw.rstrip(':')
            try: c=channel_registry.canonical_service_ref(raw) or short
            except Exception: c=short
            if raw in mapped_refs or short in mapped_refs or c in canon:
                mapped_refs.add(raw); mapped_refs.add(short); mapped_refs.add(c)
                infos=info_by_key.get(raw) or info_by_key.get(short) or info_by_key.get(c) or []
                if infos:
                    for lookup in (raw, short, c):
                        merge_info(lookup, infos)

        self._mapped_epg_keys=set(data.get('mapped_epg_keys') or set())
        self._mapped_refs=mapped_refs
        self._mapped_info_by_ref=info_by_key
        self._mapping_view_cache_applied=True
        self._ref_keys_cache.clear(); self.mapping_revision += 1
        self.selection_cache.clear()

        # MappingStore is the durable owner of MANUAL and Auto-Repair choices.
        # Overlay it after every asynchronous/cached Mapping View application so
        # a background validation can never downgrade an already saved channel
        # from GREEN MAPPED to YELLOW SUGGESTION. Disabled-source AUTO mappings
        # remain excluded by _merge_persisted_store_ownership().
        try:
            self._merge_persisted_store_ownership()
        except Exception:
            pass

        if persist:
            try:
                mapping_view_cache.save(data)
                cached=mapping_view_cache.load() or data
                smart_mapping_warm_cache.update_mapping_view(cached)
            except Exception:
                pass
        return True

    def _poll_mapping_view_build(self):
        result=self._mapping_view_result
        if result is None: return
        self._mapping_view_result=None; self._mapping_view_busy=False
        token,data,err=result
        if token != self._mapping_view_token or err or not isinstance(data,dict):
            return
        _partial = bool(getattr(self, "_mapping_view_partial", False))
        self._apply_mapping_view_data(data, persist=not _partial)
        if _partial and self._initial_reception:
            try:
                smart_mapping_warm_cache.update_repair_view(self._initial_reception, data)
            except Exception:
                pass
        self.refresh_channels(); self._update_summary(); self.update_source_title()

    def _apply_receiver_snapshot(self, sat, bouquet_catalog, all_records, rebuild_mapping=True, srp_state=None, reload_preferences=False):
        self._ref_keys_cache.clear()
        sat=list(sat or []); bouquet_catalog=list(bouquet_catalog or []); all_records=list(all_records or [])
        # beta119: Smart Mapping is SAT-only by design. IPTV helpers remain in
        # the codebase for compatibility but are not mixed into this workspace.
        self.catalog=sat
        self._bouquet_catalog=bouquet_catalog
        self._sat_registry=sat
        self.all_bouquet_records=all_records
        self.bouquet_labels=self.all_bouquet_records
        if rebuild_mapping:
            self._rebuild_mapping_cache(srp_state=srp_state)
        self._rebuild_bouquet_view()
        self.refresh_channels()
        self.refresh_sources(reload_preferences=reload_preferences)

    def _start_receiver_verify(self):
        if self._receiver_verify_busy:
            return
        self._receiver_verify_busy=True
        self._receiver_verify_result=None
        def worker():
            try:
                sat, rebuilt = channel_registry.load_satellite_registry(force=False)
                # Do not deserialize every bouquet service during opening. Only
                # bouquet metadata is verified; one bouquet is parsed lazily when selected.
                bouquet_catalog = []
                all_records = channel_mapper.list_bouquets(use_cache=True, fast_cache=False)
                state = srp_master_engine.load_state() or {}
                audit = dict(getattr(self, '_startup_audit_summary', {}) or {})
                self._receiver_verify_result=(sat,bouquet_catalog,all_records,bool(rebuilt),state,audit,None)
            except Exception as exc:
                self._receiver_verify_result=([],[],[],False,{}, {}, str(exc))
        threading.Thread(target=worker,daemon=True).start()
        try:self._timer.start(250,False)
        except Exception:pass

    def _poll_receiver_verify(self):
        result=self._receiver_verify_result
        if result is None:
            return
        self._receiver_verify_busy=False
        self._receiver_verify_result=None
        sat,bouquet_catalog,all_records,rebuilt,state,audit,err=result
        if err:
            self._lamedb_state="UNKNOWN"
            return
        self._lamedb_rebuilt=bool(rebuilt)
        self._lamedb_state="UPDATED" if rebuilt else "CURRENT"
        try:
            lp=channel_registry.active_lamedb_path()
            self._lamedb_stamp=time.strftime("%d/%m %H:%M",time.localtime(os.path.getmtime(lp))) if lp and os.path.exists(lp) else ""
        except Exception:
            self._lamedb_stamp=""
        # Preserve the visible receiver channel/bouquet while applying verified
        # caches. The work above happened off the Enigma2 UI thread.
        cur_bq=(self._current("bouquets",self.bouquet_records) or {}).get("bouquet_file")
        cur_ref=(self._current("channels",self.bouquet_services) or {}).get("ref")
        self._startup_srp_stats = dict((state or {}).get("stats") or {})
        self._startup_audit_summary = dict(audit or {})
        self._resident_srp_state = state or {}
        self._apply_receiver_snapshot(sat,bouquet_catalog,all_records,rebuild_mapping=False, srp_state=state, reload_preferences=False)
        # Keep a validated persistent view if the receiver did not change. A
        # rebuilt lamedb invalidates its signature and legitimately schedules a
        # fresh background validation.
        if bool(rebuilt) or not self._mapping_view_cache_applied:
            self._start_mapping_view_build(state)
        try:
            smart_mapping_warm_cache.update_receiver(sat, bouquet_catalog, all_records, srp_state=state, audit_summary=audit)
        except Exception:
            pass
        if cur_bq:
            for i,b in enumerate(self.bouquet_records):
                if b.get("bouquet_file")==cur_bq:
                    self._set_index("bouquets",i); self.refresh_channels(); break
        if cur_ref:
            for i,ch in enumerate(self.bouquet_services):
                if str(ch.get("ref") or "")==str(cur_ref):
                    self._set_index("channels",i); break
        try:
            srp_master_engine.rebuild_async(config=self.config,force=bool(rebuilt))
        except Exception:
            pass
        self._update_summary(); self.update_source_title()

    def rescan(self):
        """Apply the resident receiver snapshot without blocking the UI.

        If Home has already pre-warmed Smart Mapping this is effectively an
        in-memory handoff.  On a cold first launch the screen remains visible
        while the same disk-only warmup runs in a daemon thread.
        """
        if self._receiver_snapshot_ready:
            return True
        try:
            snap = smart_mapping_warm_cache.snapshot()
        except Exception:
            snap = None
        if not snap:
            try:
                smart_mapping_warm_cache.ensure_async()
            except Exception:
                pass
            self["summary"].setText("Smart Mapping is open • loading the cached receiver view in background…")
            return False

        self._receiver_snapshot_ready=True
        try:
            perf_profiler.record("Smart Mapping open → receiver data", time.time() - float(getattr(self, "_perf_open_started", time.time())))
        except Exception:
            pass
        self._lamedb_state="CACHED"
        self._startup_srp_stats = dict(snap.get("srp_stats") or {})
        self._startup_audit_summary = dict(snap.get("audit_summary") or {})
        self._resident_srp_state = snap.get("srp_state") or {}
        self._load_repair_result_state(snap)
        self.epg_channels=[]; self.epg_by_source={}; self.selection_cache.clear()

        # 8.1.3: adopt the resident Smart index BEFORE painting receiver rows.
        # 8.1.2 painted the first satellite while the screen-local index was
        # still empty, cached every row as RED, then marked the index READY too
        # late. That made a fully prepared receiver appear to have no suggestions.
        _warm_index = snap.get("smart_name_index") or {}
        self._smart_name_index = _warm_index if isinstance(_warm_index, dict) else {}
        self._smart_name_index_ready = bool(self._smart_name_index)
        self._smart_name_index_building = False
        self._smart_name_index_dirty = False
        self._channel_status_hint_cache.clear()

        self._apply_receiver_snapshot(
            snap.get("sat") or [], [], snap.get("all_records") or [],
            rebuild_mapping=False, srp_state=snap.get("srp_state") or {}, reload_preferences=False)
        cached_view=snap.get("mapping_view") or {}
        if cached_view:
            self._apply_mapping_view_data(cached_view, persist=False)
            # beta138: the Mapping View cache can pre-date the last satellite
            # Auto Repair. Merge durable MappingStore ownership before painting
            # so saved AUTO/MANUAL channels are skipped on the next repair.
            self._merge_persisted_store_ownership()
            self.refresh_channels()
        elif snap.get("srp_state"):
            if self._auto_search_mode:
                # beta133 search mode must start immediately. Build only the
                # cheap ownership cache from manual + existing SRP state; do
                # NOT run the satellite PrecisionMatch health sweep before the
                # automatic search. The batch matcher itself supplies safety.
                self._rebuild_mapping_cache(srp_state=snap.get("srp_state") or {})
                self.refresh_channels()
            else:
                # Normal Smart Mapping may validate the ownership view lazily.
                self._start_mapping_view_build(snap.get("srp_state") or {})
        else:
            # Cold/partial cache: durable manual/AUTO assignments are still enough
            # to prevent a needless repeat Auto Repair scan.
            self._merge_persisted_store_ownership()
            self.refresh_channels()
        self.selection_entries=[]
        self.selection_rows=["Select a source or SUGGESTION"]
        self._set_list("selections",self.selection_rows)

        self.update_focus(); self._update_summary()
        if self._smart_name_index_ready:
            self["status_label"].setText("READY • SMART INDEX CACHED • CACHE-FIRST")
        else:
            self["status_label"].setText("READY • FIND INDEX ON DEMAND • CACHE-FIRST")
        self._schedule_idle_warmup()
        self._queue_smart_auto_repair()
        # 7.0.5 FastEntry: do NOT immediately run the legacy receiver verifier.
        # The warm cache already validates the live lamedb signature before this
        # snapshot is published. The previous verifier then called
        # list_bouquets(..., fast_cache=False), reopening every userbouquet file a
        # second time exactly when Smart Mapping became visible. On Vu+ Zero 4K
        # this is enough to create noticeable entry lag even though XMLTV itself
        # is not being parsed at this point. Explicit refresh paths remain intact.
        return True

    def _bouquet_view_key(self, bouquet=None):
        bouquet = bouquet or self._current("bouquets", self.bouquet_records) or {}
        if bouquet.get("virtual") == "reception":
            return "SAT:" + str(bouquet.get("reception_list") or "")
        return "BQ:" + str(bouquet.get("bouquet_file") or "")

    def _fast_raw_key(self, ref):
        return str(ref or "").strip()

    def _paint_is_mapped(self, ref):
        """O(1) paint-only ownership check for current receiver refs."""
        raw = self._fast_raw_key(ref)
        if not raw:
            return False
        short = raw.rstrip(":")
        return bool(raw in self._mapped_refs or short in self._mapped_refs or
                    self._mapped_info_by_ref.get(raw) or self._mapped_info_by_ref.get(short))

    def _paint_is_ignored(self, ref):
        raw = self._fast_raw_key(ref)
        if not raw:
            return False
        return raw in self._ignored_ref_keys or raw.rstrip(":") in self._ignored_ref_keys

    def _cached_mapping_visual(self, ref):
        """Return (ownership tag, colour state) from resident mapping state only.

        Smart Mapping deliberately does not repeat status words in each channel
        row.  Colour is the state; [M]/[A] is the only textual ownership marker.
        """
        raw = self._fast_raw_key(ref)
        short = raw.rstrip(":")
        infos = self._mapped_info_by_ref.get(raw) or self._mapped_info_by_ref.get(short) or []
        if infos:
            first = infos[0]
            mode = str(first.get("mode") or "").lower()
            return ("[M]" if mode == "manual" else "[A]", "mapped")
        if self._paint_is_mapped(raw):
            # A partial/LKG state can know that a ref is mapped before its detail
            # row is expanded.  Never demote its colour; default to AUTO marker.
            return ("[A]", "mapped")
        if self._paint_is_ignored(raw):
            return ("", "ignored")
        kind = str((self._repair_exception_kind or {}).get(raw) or (self._repair_exception_kind or {}).get(short) or "").upper()
        if kind in ("REVIEW", "AMBIGUOUS"):
            return ("", "suggestion")
        if kind in ("NO MATCH", "BLOCKED"):
            return ("", "unmapped")
        if not self._repair_mode and self._smart_name_index_ready:
            service = (self._visible_service_by_ref or {}).get(raw) or (self._visible_service_by_ref or {}).get(short) or {}
            if service:
                key = (raw, int(self.mapping_revision or 0), id(self._smart_name_index))
                cached = self._channel_status_hint_cache.get(key, None)
                if cached is None:
                    try:
                        cached = bool(manual_match_lite.has_prepared_hint_fast(service, self._smart_name_index))
                    except Exception:
                        cached = False
                    self._channel_status_hint_cache[key] = bool(cached)
                if cached:
                    return ("", "suggestion")
        return ("", "unmapped")

    def _current_programme_title(self, ref):
        """Current programme shown by Enigma2 for one mapped receiver service.

        This reads eEPGCache only.  It never opens source XML, downloads a URL,
        rebuilds a catalogue, or changes mapping state.  Results are minute-cached
        and bounded to keep Smart Mapping navigation light on small receivers.
        """
        raw = self._fast_raw_key(ref)
        if not raw:
            return ""
        minute = int(time.time() // 60)
        key = (minute, raw)
        cached = self._programme_title_cache.get(key)
        if cached is not None:
            return cached
        if self._programme_cache_minute != minute:
            self._programme_cache_minute = minute
            # Keep at most the current + a small previous generation.
            if len(self._programme_title_cache) > 2048:
                self._programme_title_cache.clear()
        title = ""
        try:
            from enigma import eEPGCache, eServiceReference
            epg = eEPGCache.getInstance()
            if epg is not None:
                sref = eServiceReference(str(ref or ""))
                try:
                    event = epg.lookupEventTime(sref, -1, 0)
                except TypeError:
                    event = epg.lookupEventTime(sref, -1)
                if event is not None:
                    try:
                        title = str(event.getEventName() or "").strip()
                    except Exception:
                        title = ""
        except Exception:
            title = ""
        title = " ".join(title.split())
        if len(title) > 72:
            title = title[:69].rstrip() + "…"
        self._programme_title_cache[key] = title
        return title

    def _attention_reason_for_service(self, service):
        """Return a compact, deterministic reason for the receiver-row state."""
        service = service or {}
        ref = str(service.get("ref") or "")
        if not ref:
            return "NO SERVICE REF"
        try:
            if self._service_is_ignored(service):
                return "NO FUTURE EPG / IGNORED"
        except Exception:
            pass
        try:
            if self._service_has_conflict(service):
                return "AMBIGUOUS • MULTIPLE OWNERS"
        except Exception:
            pass
        try:
            if self.store.is_blocked(ref):
                return "MANUAL UNMAPPED • AUTO BLOCKED"
        except Exception:
            pass
        raw = self._fast_raw_key(ref); short = raw.rstrip(":")
        kind = str((self._repair_exception_kind or {}).get(raw) or
                   (self._repair_exception_kind or {}).get(short) or "").upper()
        if kind == "AMBIGUOUS":
            return "AMBIGUOUS • 2+ SAFE CANDIDATES"
        if kind == "REVIEW":
            return "REVIEW • CANDIDATE NEEDS CONFIRMATION"
        if kind in ("NO MATCH", "BLOCKED"):
            return "NO SAFE MATCH / NO SRP"
        infos = self._mapped_info_by_ref.get(raw) or self._mapped_info_by_ref.get(short) or []
        if infos:
            mode = str((infos[0] or {}).get("mode") or "").lower()
            if mode == "manual":
                return "MANUAL / LOCKED"
            return "AUTO MAPPED"
        if self._paint_is_mapped(ref):
            return "MAPPED"
        if not self._smart_name_index_ready:
            return "UNMAPPED • ID INDEX PREPARING"
        try:
            if manual_match_lite.has_prepared_hint_fast(service, self._smart_name_index):
                return "CANDIDATE AVAILABLE • REVIEW"
        except Exception:
            pass
        return "NO XMLTV/SRP PROOF"

    def _paint_channel_rows_cached(self):
        """Build the visible channel pane from resident state only.

        A tiny six-view rendered-row cache makes back-and-forth satellite
        navigation nearly allocation-free. It is intentionally disabled in
        Mapping Repair because exception colours can change independently.
        """
        services = list(self.bouquet_services or [])
        view_key = self._bouquet_view_key()
        row_key = (view_key, str(getattr(self, "channel_filter_mode", "all") or "all"),
                   int(self.mapping_revision or 0), id(self._smart_name_index))
        if not self._repair_mode and view_key.startswith("SAT:"):
            cached_rows = self._sat_channel_row_cache.get(row_key)
            if cached_rows is not None:
                # Keep cached rows immutable: selected NOW EPG is painted as one
                # transient row after setList(), so cache reuse stays high.
                self._set_list("channels", list(cached_rows))
                self._paint_selected_programme_fast()
                return

        visual_by_ref = {}
        mapped_refs = set()
        for svc in services:
            ref = str((svc or {}).get("ref") or "")
            visual = self._cached_mapping_visual(ref)
            is_mapped = self._paint_is_mapped(ref)
            try:
                svc["_attention_reason"] = self._attention_reason_for_service(svc)
            except Exception:
                svc["_attention_reason"] = ""
            if is_mapped:
                mapped_refs.add(ref)
                try:
                    if self._service_has_conflict(svc):
                        visual = (visual[0] or "[!]", "wrong")
                    elif self._service_is_manual_locked(svc):
                        visual = ("[M]", "manual")
                except Exception:
                    pass
            visual_by_ref[ref] = visual
        rows = self["channels"].build_rows(
            services, mapped_refs,
            lambda ref: visual_by_ref.get(str(ref or ""), ("", "unmapped")))
        rows = rows or [[None, MultiContentEntryText(pos=(12,0), size=(390,48), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="No services")]]
        if not self._repair_mode and view_key.startswith("SAT:"):
            if len(self._sat_channel_row_cache) >= 6:
                try:
                    self._sat_channel_row_cache.pop(next(iter(self._sat_channel_row_cache)))
                except Exception:
                    self._sat_channel_row_cache.clear()
            self._sat_channel_row_cache[row_key] = list(rows)
        self._set_list("channels", list(rows))
        self._paint_selected_programme_fast()

    def _paint_selected_programme_fast(self):
        """Query Enigma2 NOW EPG for only the highlighted mapped service."""
        try:
            index = self._get_index("channels")
            if index < 0 or index >= len(self.bouquet_services or []):
                return False
            service = (self.bouquet_services or [])[index] or {}
            ref = str(service.get("ref") or "")
            if not ref or not self._paint_is_mapped(ref):
                return False
            tag, state = self._cached_mapping_visual(ref)
            programme = self._current_programme_title(ref)
            return bool(self["channels"].update_programme_row(index, service, True, tag, state, programme))
        except Exception:
            return False

    def refresh_channels(self):
        t_all = time.time()
        bouquet = self._current("bouquets", self.bouquet_records)
        if bouquet is None:
            self.bouquet_services = []
        elif bouquet.get("virtual") == "reception":
            label = str(bouquet.get("reception_list") or "")
            # 8.1.1: O(1) reception lookup. The old code scanned the complete SAT
            # registry on every cursor move. Cache the TV-only alphabetical view
            # the first time each satellite is actually selected.
            cached = self._sat_sorted_services_by_reception.get(label)
            if cached is None:
                grouped = list(self._sat_services_by_reception.get(label) or [])
                grouped = [x for x in grouped if channel_registry.is_tv_service(x)]
                grouped.sort(key=lambda x: (_alpha_text(x.get("name")), str(x.get("ref") or "")))
                self._sat_sorted_services_by_reception[label] = list(grouped)
                cached = grouped
            all_services = list(cached or [])
        else:
            bfile = bouquet.get("bouquet_file")
            cached = self._bouquet_service_cache.get(str(bfile or ""))
            if cached is None:
                cached = bouquet_lazy.load_services(
                    bfile, bouquet.get("bouquet_label") or bfile, bouquet.get("parent"))
                if len(self._bouquet_service_cache) >= 12:
                    try:
                        self._bouquet_service_cache.pop(next(iter(self._bouquet_service_cache)))
                    except Exception:
                        self._bouquet_service_cache.clear()
                self._bouquet_service_cache[str(bfile or "")] = list(cached or [])
            all_services = [x for x in list(cached or []) if channel_registry.is_tv_service(x)]
            all_services.sort(key=lambda x: (_alpha_text(x.get("name")), str(x.get("ref") or "")))

        if bouquet is None:
            all_services = []
        self._all_current_bouquet_services = list(all_services)
        mode = str(getattr(self, "channel_filter_mode", "all") or "all")
        if mode == "unresolved":
            all_services = [x for x in all_services if (not self._paint_is_mapped((x or {}).get("ref"))
                            or self._service_has_conflict(x) or self._service_is_ignored(x))]
        elif mode == "unmapped":
            all_services = [x for x in all_services if not self._paint_is_mapped((x or {}).get("ref"))]
        elif mode == "mapped":
            all_services = [x for x in all_services if self._paint_is_mapped((x or {}).get("ref"))]
        elif mode == "conflicts":
            all_services = [x for x in all_services if self._service_has_conflict(x)]
        elif mode == "locked":
            all_services = [x for x in all_services if self._service_is_manual_locked(x)]
        elif mode == "auto":
            all_services = [x for x in all_services if self._service_is_auto_repaired(x)]
        elif mode == "ignored":
            all_services = [x for x in all_services if self._service_is_ignored(x)]
        self.bouquet_services = list(all_services)
        self._visible_service_by_ref = {str((x or {}).get("ref") or ""): x for x in self.bouquet_services if (x or {}).get("ref")}

        # First paint is final: mapped ownership and prepared suggestion status
        # are both O(1) resident lookups. No delayed 2-minute hint enrichment.
        t_paint = time.time()
        self._paint_channel_rows_cached()
        try:
            perf_profiler.record("Reception Channel Paint", time.time() - t_paint)
            perf_profiler.record("Reception Switch Total", time.time() - t_all)
        except Exception:
            pass

    def refresh_sources(self, reload_preferences=True):
        current = self._current("sources", self.source_groups)
        preserve_id = current.get("source_id") if current else None
        if reload_preferences:
            try:
                self.source_preferences = SourcePreferences()
                self.auto_selected_source_ids = set(source_variant_policy.normalize_selected(
                    self.source_preferences.selected_mapping_ids()))
                self.auto_source_filter_active = bool(self.auto_selected_source_ids)
            except Exception:
                self.auto_selected_source_ids = set()
                self.auto_source_filter_active = False
        groups = []
        # v6.2: use exactly the same canonical catalogue/order as
        # Download & Monitor. No second hand-built list can drift out of sync.
        for src in source_catalog.all_sources():
            if src.get("kind") == "local":
                sid = source_catalog.mapping_source_id(src)
                filename = None
                for fn, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                    if pair[0] == sid:
                        filename = fn
                        break
                groups.append({
                    "catalogue_id": src.get("id"),
                    "source_id": sid,
                    "source_name": src.get("name") or sid,
                    "external": False,
                    "local_file": filename,
                    "provider": src.get("provider"),
                    "region": src.get("region"),
                    "countries": list(src.get("countries") or []),
                    "language": src.get("language") or "",
                    "group": src.get("group"),
                })
            else:
                item = dict(src)
                item.update({
                    "catalogue_id": src.get("id"),
                    "source_id": src.get("id"),
                    "source_name": src.get("name") or src.get("id"),
                    "external": True,
                })
                groups.append(item)
        self._all_source_groups = groups
        self._source_lookup = {}
        for _src in groups:
            _sid = str((_src or {}).get("source_id") or "")
            if _sid:
                self._source_lookup[_sid] = _src
        self._rebuild_source_view(preserve_id)

    def _prime_local_channel_hints(self):
        """Populate built-in local EPGManager IDs at zero I/O cost."""
        seen_keys = set((str(x.get("source_id") or ""), str(x.get("channel_id") or "").lower())
                        for x in (self.epg_channels or []))
        for src in getattr(self, "_all_source_groups", []) or []:
            if src.get("external") or src.get("source_group"):
                continue
            sid = str(src.get("source_id") or "")
            if not sid:
                continue
            try:
                rows = source_catalog.local_channel_hints(sid)
            except Exception:
                rows = []
            if not rows:
                continue
            source_name = str(src.get("source_name") or sid)
            clean = []
            for row in rows:
                item = dict(row or {})
                item["source_id"] = sid
                item["source_name"] = source_name
                item["epg_xml_path"] = self._source_xml_path(src) or ""
                key = (sid, str(item.get("channel_id") or "").lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                clean.append(item)
                self.epg_channels.append(item)
            if clean:
                self.epg_by_source[sid] = clean

    def _start_online_cache_refresh(self):
        """No full external XML refresh at Smart Mapping startup.

        External providers are URL-authoritative.  Compact channel catalogues
        are prepared from those URLs by bootstrap/on-demand workers; programme
        XMLTV is never downloaded into the receiver mapping directory.
        """
        if self._startup_refresh_started:
            return
        self._startup_refresh_started = True
        self._startup_refresh_busy = False
        self._startup_refresh_count = 0

    def _ensure_external_source_ready(self, src):
        """Prepare one remote Channel-ID catalogue directly from its URL.

        7.0.6 Direct Remote IDs: Smart Mapping never opens or parses a downloaded
        remote XMLTV file.  The worker reads the provider's tiny ID list when
        available, otherwise it streams only the remote <channel> header and
        closes the HTTP response as soon as <programme> begins.  Only the compact
        ID/name rows are cached; programme XML is never saved here.
        """
        if not src or not src.get("external") or src.get("source_group"):
            return True
        sid = str(src.get("source_id") or "")
        # 7.0.7: first consume the process-resident boot catalogue. No stat(),
        # JSON parse or network request is needed on source navigation.
        try:
            boot_rows = list(smart_catalog_boot.get(sid) or [])
        except Exception:
            boot_rows = []
        if boot_rows:
            if sid not in self.epg_by_source:
                self.epg_by_source[sid] = self._stabilize_source_rows(src, boot_rows)
            return True
        try:
            if smart_catalog_boot.is_loading(sid):
                return False
        except Exception:
            pass
        try:
            if source_channel_cache.has(sid):
                return True
        except Exception:
            pass

        try:
            if self.manager is not None and self.manager.is_busy():
                self["summary"].setText("EPGManager is busy — direct ID read paused")
                return False
        except Exception:
            pass
        if self._direct_sync_busy:
            return False
        if sid in self._direct_sync_attempted:
            return False

        self._direct_sync_attempted.add(sid)
        self._direct_sync_busy = True
        self._direct_sync_source_id = sid
        self._direct_sync_result = None
        self._catalog_progress_started = time.time()
        try:
            self["catalog_progress"].setValue(8)
            self["catalog_progress_text"].setText("8%")
            self["status_label"].setText("DIRECT REMOTE IDs")
        except Exception:
            pass
        item = dict(src)
        self["summary"].setText("Reading Channel IDs directly from link: %s…" % (src.get("source_name") or sid))

        def worker():
            try:
                started = time.time()
                rows, meta = remote_channel_catalog.fetch(
                    item, timeout=3, max_scan=6 * 1024 * 1024)
                meta = dict(meta or {})
                meta["elapsed_ms"] = int(max(0.0, time.time() - started) * 1000.0)
                source_channel_cache.put(
                    sid, src.get("source_name") or sid, rows,
                    provider=src.get("provider") or "",
                    region=src.get("region") or "",
                    language=src.get("language") or "")
                self._direct_sync_result = (sid, src.get("source_name") or sid, rows, None, meta)
            except Exception as exc:
                self._direct_sync_result = (sid, src.get("source_name") or sid, [], str(exc), {})

        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(100, False)
        return False

    def _poll_direct_sync(self):
        result = self._direct_sync_result
        if result is None:
            return
        self._direct_sync_busy = False
        self._direct_sync_result = None
        sid, name, rows, err, meta = result
        if rows:
            self.epg_by_source[sid] = list(rows)
            self._source_search_indexes.pop(str(sid), None)
            self._source_language_rank_cache.clear()
            self._invalidate_smart_name_index()
            try:
                smart_catalog_boot.update_source(sid, name, rows, method="direct-refresh")
            except Exception:
                pass
            self.epg_channels = [x for x in self.epg_channels if x.get("source_id") != sid] + list(rows)
            self.selection_cache.clear()
        current = self._current("sources", self.source_groups)
        current_sid = str((current or {}).get("source_id") or "")
        if err:
            self["summary"].setText("%s: channel catalogue unavailable" % name)
        else:
            q = (meta or {}).get("quality") or {}
            qo = q.get("overall") or {}
            if int(qo.get("programs") or 0) > 0:
                self["summary"].setText("%s: %d IDs • EPG quality sampled (%d programmes, %.1f days)" %
                                        (name, len(rows or []), int(qo.get("programs") or 0),
                                         float(qo.get("days") or 0.0)))
            else:
                elapsed_ms = int((meta or {}).get("elapsed_ms") or 0)
                method = str((meta or {}).get("method") or "remote-header")
                if elapsed_ms:
                    self["summary"].setText("%s: %d IDs direct from link • %d ms • %s" %
                                            (name, len(rows or []), elapsed_ms, method))
                else:
                    self["summary"].setText("%s: %d channel IDs ready" % (name, len(rows or [])))
        current_owns_sid = (current_sid == sid)
        if current and current.get("country_bundle"):
            current_owns_sid = any(str(x.get("source_id") or "") == sid for x in (current.get("children") or []))
        if current_owns_sid:
            self.refresh_selection()
            # Catalogue appears immediately when background preparation finishes.
            # If this feed contains a verified match for the current receiver
            # service, land on that exact EPG row automatically; otherwise land
            # on the first real row for manual review.
            selected_hint = None
            try:
                service = self._current("channels", self.bouquet_services)
                real_src = self._source_by_id(sid)
                if service and real_src:
                    selected_hint = self._best_name_suggestion(service, force_source=real_src)
                if selected_hint:
                    self._smart_recommended_source_id = sid
                    self._smart_recommended_channel_id = str(selected_hint["epg"].get("channel_id") or "")
                    self._select_epg_id_in_current_list(self._smart_recommended_channel_id)
            except Exception:
                selected_hint = None
            if rows and self.focus == 2:
                try:
                    self.focus = 3
                    self.update_focus()
                except Exception:
                    pass
        elif current and current.get("suggestion_all_sources"):
            # 8.2.0 OSN catalogue fill: keep the user on the virtual suggestion
            # row, repaint from the newly published RAM source, and immediately
            # queue the next missing country catalogue.  Mapping state is never
            # touched here.
            try:
                service = self._current("channels", self.bouquet_services)
                self._refresh_cross_source_suggestions(service)
            except Exception:
                pass
        try:
            self["catalog_progress"].setValue(100 if not err else 0)
            self["catalog_progress_text"].setText("READY" if not err else "ERROR")
            self._update_action_labels()
        except Exception:
            pass
        # _refresh_cross_source_suggestions may already have started the next
        # OSN/MENA direct-ID worker. Do not stop its poll timer.
        if not getattr(self, "_direct_sync_busy", False):
            try:
                self._timer.stop()
            except Exception:
                pass


    def _source_xml_path(self, src):
        if not src or src.get("source_group"):
            return None
        if src.get("external"):
            # Remote provider XMLTV is never represented by a local file.
            return None
        filename = src.get("local_file")
        if not filename:
            sid = src.get("source_id")
            for fn, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                if pair[0] == sid:
                    filename = fn
                    break
        return os.path.join(self._epg_dir(), filename) if filename else None

    def _stabilize_source_rows(self, src, rows):
        """Keep EPG channel labels stable across Update Source.

        Provider XML display-names may be generic/shifted and local generators
        intentionally use semantic IDs.  The UI must not make e.g. Almajd 11
        Rawda appear to turn into Basma just because the XML was refreshed.
        Local canonical hints win, then the previously cached display-name for
        the same channel_id, then the newly downloaded display-name.
        """
        src = src or {}
        sid = str(src.get("source_id") or "")
        stable = {}
        # Existing in-memory/cache names are stable for unchanged IDs.
        try:
            for item in self.epg_by_source.get(sid, []) or []:
                cid = str((item or {}).get("channel_id") or "").lower()
                name = str((item or {}).get("display_name") or "").strip()
                if cid and name:
                    stable[cid] = name
        except Exception:
            pass
        try:
            for item in source_channel_cache.get(sid) or []:
                cid = str((item or {}).get("channel_id") or "").lower()
                name = str((item or {}).get("display_name") or "").strip()
                if cid and name and cid not in stable:
                    stable[cid] = name
        except Exception:
            pass
        # Local EPGManager names are canonical and must always win.
        if not src.get("external"):
            try:
                for item in source_catalog.local_channel_hints(sid) or []:
                    cid = str((item or {}).get("channel_id") or "").lower()
                    name = str((item or {}).get("display_name") or "").strip()
                    if cid and name:
                        stable[cid] = name
            except Exception:
                pass
        out = []
        seen = set()
        for raw in rows or []:
            item = dict(raw or {})
            cid_raw = str(item.get("channel_id") or "")
            cid = cid_raw.lower()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if stable.get(cid):
                item["display_name"] = stable[cid]
            elif not str(item.get("display_name") or "").strip():
                item["display_name"] = cid_raw
            item["source_id"] = sid or item.get("source_id")
            item["source_name"] = src.get("source_name") or item.get("source_name") or sid
            out.append(item)
        return sorted(out, key=lambda x: (_alpha_text(x.get("display_name") or x.get("channel_id")), str(x.get("channel_id") or "").lower()))

    def _ensure_source_loaded(self, src):
        """Load one source from compact caches only; never parse XMLTV on UI navigation.

        7.0.5 FastEntry: Smart Mapping cursor movement and pane entry must stay
        cache-only. If a remote source has no compact Channel-ID cache yet, an
        explicit RIGHT/OK/GREEN/YELLOW action can start the existing background
        catalogue worker. Local EPGManager sources fall back to static zero-I/O
        channel hints.
        """
        if not src or src.get("source_group"):
            return 0
        sid = src.get("source_id")
        if sid in self.epg_by_source:
            return len(self.epg_by_source.get(sid) or [])
        fresh = []
        try:
            fresh = list(smart_catalog_boot.get(sid) or [])
        except Exception:
            fresh = []
        # 7.1.0 latency invariant: UI navigation is RAM-only. Do NOT fall back
        # to source_channel_cache.get() here because that can JSON-decode a large
        # shard synchronously on Enigma2's main thread. smart_catalog_boot owns
        # all persistent-cache reads in its daemon worker.
        # IMPORTANT: no XMLTV/file/cache open here. This function is called from
        # cursor-idle and refresh_selection paths on Enigma2's UI thread.
        if not fresh and not src.get("external"):
            try:
                fresh = source_catalog.local_channel_hints(sid) or []
                for item in fresh:
                    item["source_name"] = src.get("source_name") or sid
                    item["epg_xml_path"] = self._source_xml_path(src) or ""
            except Exception:
                fresh = []
        fresh = self._stabilize_source_rows(src, fresh)
        self.epg_by_source[sid] = fresh
        self._source_search_indexes.pop(str(sid), None)
        self._bundle_entries_cache.clear()
        if fresh:
            existing = set((str(x.get("source_id") or ""), str(x.get("channel_id") or "").lower())
                           for x in self.epg_channels)
            for item in fresh:
                key = (str(item.get("source_id") or sid), str(item.get("channel_id") or "").lower())
                if key not in existing:
                    self.epg_channels.append(item); existing.add(key)
        self.selection_cache.clear()
        return len(fresh)

    def _is_mapped(self, epg):
        try:
            key = self.store._key(epg.get("source_id"), epg.get("channel_id"))
            return key in self._mapped_epg_keys
        except Exception:
            return False

    # ------------------------------------------------------------------
    # beta63 name-first Smart Mapping
    # ------------------------------------------------------------------
    _SMART_NAME_NOISE = set(("hd", "sd", "fhd", "uhd", "4k", "hdr", "hdr10", "3840p", "2160p", "1080p",
                             "tv", "hevc", "h265", "backup", "feed", "channel", "exclus", "exclusive", "event", "only"))
    _SMART_TAIL_NOISE = set()  # beta115: programme-feed words are semantic, never UI noise.

    @classmethod
    def _smart_norm(cls, value, simplified=False):
        """Unicode-safe TV channel name normalisation.

        Unlike the legacy SAT matcher this helper preserves Arabic script. It is
        UI-only and therefore cannot alter the frozen beta1/beta12 SAT engine.
        """
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            text = unicodedata.normalize("NFKD", text)
            text = "".join(ch for ch in text if not unicodedata.combining(ch))
        except Exception:
            pass
        text = text.casefold()
        # Receiver/service names frequently use the glued branding token
        # "OSNtv" while XMLTV feeds use "OSN". Treat only that exact token as
        # presentation noise; movie genre words remain semantic.
        text = re.sub(r"\bosntv\b", "osn", text)
        text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
        words = [w for w in text.split() if w and w not in cls._SMART_NAME_NOISE]
        if simplified:
            # Decorations such as "Inter" or "National" are usually suffixes
            # (Al Aoula Inter, 2M National). Never remove them from the middle of
            # a genuine brand such as National Geographic.
            while len(words) > 1 and words[-1] in cls._SMART_TAIL_NOISE:
                words.pop()
        return " ".join(words).strip()

    @classmethod
    def _smart_name_keys(cls, value):
        out = []
        try: variants = unified_channel_identity.search_variants(value)
        except Exception: variants = [value]
        for variant in variants:
            raw = cls._smart_norm(variant, False)
            simple = cls._smart_norm(variant, True)
            for key in (raw, simple):
                if key and key not in out: out.append(key)
        return out

    def _smart_index_rows_for(self, key, limit=None):
        """Read one Smart-index bucket from beta97 compact or legacy format."""
        index = self._smart_name_index or {}
        if isinstance(index, dict) and int(index.get("_format") or 0) == 2:
            rows = index.get("rows") or []
            refs = ((index.get("buckets") or {}).get(str(key)) or [])
            if limit is not None:
                refs = refs[:max(0, int(limit))]
            out = []
            for pos in refs:
                try:
                    item = rows[int(pos)]
                except Exception:
                    continue
                if isinstance(item, dict):
                    out.append(item)
            return out
        rows = index.get(str(key), []) if isinstance(index, dict) else []
        return list(rows[:max(0, int(limit))] if limit is not None else rows or [])

    @classmethod
    def _name_only_score(cls, left, right):
        """0..100 score where the receiver/EPG channel name is authoritative."""
        from difflib import SequenceMatcher
        a = cls._smart_norm(left, False); b = cls._smart_norm(right, False)
        sa = cls._smart_norm(left, True); sb = cls._smart_norm(right, True)
        if not a or not b:
            return 0
        nums_a = re.findall(r"\d+", a); nums_b = re.findall(r"\d+", b)
        number_conflict = bool(nums_a and nums_b and nums_a != nums_b)
        if a == b:
            return 100
        if sa and sb and sa == sb:
            return 45 if number_conflict else 99
        wa, wb = set(sa.split()), set(sb.split())
        overlap = (100.0 * len(wa & wb) / len(wa | wb)) if (wa | wb) else 0.0
        ratio = 100.0 * SequenceMatcher(None, sa or a, sb or b).ratio()
        contains = bool(sa and sb and (sa in sb or sb in sa))
        score = int(round(max(ratio, overlap * 0.88 + ratio * 0.12, 94 if contains else 0)))
        if number_conflict:
            score = min(score, 45)
        return max(0, min(100, score))

    def _invalidate_smart_name_index(self):
        # beta122: invalidation is cheap and lazy.  Refreshing one source should
        # never kick off a global catalogue rebuild while the user is navigating.
        # GREEN Find Matches will rebuild on demand when no valid index exists.
        self._smart_name_index_ready = False
        self._smart_name_index_dirty = True
        self._smart_name_hint_cache.clear()
        self._channel_status_hint_cache.clear()
        self._smart_index_deferred = True
        try:
            smart_name_index_cache.mark_dirty()
        except Exception:
            pass

    def _start_smart_name_index(self):
        """Attach to the single process-global lean index builder.

        7.0.x still contained a second ~200-source index implementation inside
        this screen. GREEN could therefore launch another complete JSON/catalogue
        walk plus identity/token build even while the process-global index was
        already working. 7.1.0 has ONE index owner only: smart_boot_name_index.
        """
        if self._smart_name_index_ready:
            return
        try:
            compact = smart_boot_name_index.snapshot() or {}
        except Exception:
            compact = {}
        if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2:
            self._smart_name_index = compact
            self._smart_name_index_ready = True
            self._smart_name_index_building = False
            self._smart_index_deferred = False
            self._channel_status_hint_cache.clear()
            self._channel_status_repaint_pending = True
            return

        # No synchronous source-cache reads, XML parse, or local duplicate build.
        self._smart_name_index_building = True
        self._smart_index_deferred = False
        self._smart_index_total = 1
        self._smart_index_done = 0
        try:
            smart_boot_name_index.ensure_async(force=False)
        except Exception:
            self._smart_name_index_building = False
            return
        try:
            self["catalog_progress"].setValue(5)
            self["catalog_progress_text"].setText("5%")
            self["status_label"].setText("FAST MATCH INDEX PREPARING")
            self._timer.start(120, False)
        except Exception:
            pass

    def _source_by_id(self, source_id):
        sid = str(source_id or "")
        if sid == "__smart_suggestions__":
            return None
        cached = getattr(self, "_source_lookup", {}).get(sid)
        if cached is not None:
            return cached
        # Fallback only during the first staged frame before source view exists.
        for src in getattr(self, "_all_source_groups", []) or []:
            if str((src or {}).get("source_id") or "") == sid:
                self._source_lookup[sid] = src
                return src
        for src in self.source_groups or []:
            if str((src or {}).get("source_id") or "") == sid:
                self._source_lookup[sid] = src
                return src
        return None

    def _source_auto_eligible(self, service, src, force=False, allow_inactive=False):
        """Hard eligibility gate for automatic/forced suggestions.

        RC32 contract: an OFF Smart Source is never eligible for Force Mapping,
        Auto Repair, GREEN auto-map or any automatic write.  A cached catalogue
        is discovery evidence only; it never grants permission to map from a
        source the user switched OFF.  Manual browsing may still show inactive
        rows when explicitly requested via ``allow_inactive``.
        """
        if not src:
            return False
        sid = str(src.get("source_id") or "")
        allowed = set(self._auto_allowed_source_ids() or set())
        if force:
            return bool(sid and sid in allowed)
        if sid not in allowed and not allow_inactive:
            return False
        profile = smart_context_match.receiver_profile(service or {})
        if str(profile.get("language_hint") or "").lower() != "en" and source_variant_policy.is_english_variant(sid):
            ar = source_variant_policy.arabic_counterpart(sid)
            if ar and ar in (self.auto_selected_source_ids or set()):
                return False
        return True

    def _name_candidate_allowed(self, service, src, epg, name_score):
        """Mapping V2: only canonical/exact XMLTV identity may auto-map."""
        try:
            verdict = id_mapping_engine.evaluate(
                service, src, epg, learned=smartmatch_ai.learned_match(service, src, epg))
        except Exception as exc:
            return False, {"allowed": False, "auto": False, "reason": "Mapping V2 error: %s" % exc}
        return bool(verdict.get("auto")), verdict

    def _best_name_suggestion(self, service, force_source=None, discovery=False):
        """Mapping V2 one-click hint: first proven XMLTV identity only."""
        if not service:
            return None
        profile = smart_context_match.receiver_profile(service) or {}
        receiver_name = str(profile.get("clean_name") or service.get("name") or "")
        force_sid = str((force_source or {}).get("source_id") or "") if force_source else ""
        cache_key = ("v2", id_mapping_engine.normalize(receiver_name), force_sid,
                     bool(discovery), self.mapping_revision)
        if cache_key in self._smart_name_hint_cache:
            return self._smart_name_hint_cache.get(cache_key)

        matches = self._all_name_suggestions(service, limit=24, force_source=force_source)
        best = None
        for epg in matches:
            if not epg.get("_precision_auto"):
                continue
            sid = str(epg.get("source_id") or "")
            src = force_source if force_source and sid == force_sid else (self._source_by_id(sid) or {})
            if not src:
                continue
            if not self._source_auto_eligible(service, src, force=bool(force_source), allow_inactive=bool(discovery)):
                continue
            best = {
                "score": int(epg.get("_suggestion_name_score") or epg.get("_precision_confidence") or 0) // (10 if int(epg.get("_precision_confidence") or 0) > 100 else 1),
                "precision_confidence": int(epg.get("_precision_confidence") or 0),
                "source": src,
                "epg": epg,
                "guard": {
                    "auto": True,
                    "grade": str(epg.get("_precision_grade") or "PROVEN"),
                    "confidence": int(epg.get("_precision_confidence") or 0),
                    "reason": str(epg.get("_precision_reason") or "Mapping V2 identity"),
                    "target_key": str(epg.get("_precision_target_key") or ""),
                },
                "context": {},
                "second": -1,
            }
            break
        self._smart_name_hint_cache[cache_key] = best
        return best

    def _make_source_visible(self, source_id):
        sid = str(source_id or "")
        target = self._source_by_id(sid)
        if not target:
            return None
        # beta71: the visible source pane contains unified country rows. Locate
        # the bundle owning this real provider feed while returning the real
        # child source so download/assignment keeps original provider IDs.
        self._rebuild_source_view(sid)
        for i,bundle in enumerate(self.source_groups or []):
            if str(bundle.get("source_id") or "") == sid:
                self._set_index("sources", i)
                return target
            if any(str(x.get("source_id") or "") == sid for x in (bundle.get("children") or [])):
                self._set_index("sources", i)
                return target
        return target

    def _select_epg_id_in_current_list(self, channel_id):
        wanted = str(channel_id or "").lower()
        for i, row in enumerate(self.selection_entries or []):
            if row and not row.get("separator") and str(row.get("channel_id") or "").lower() == wanted:
                self._set_index("selections", i)
                return True
        return False

    def _preview_verified_hint(self, hint):
        """Synchronise Source/EPG Match panes from READY cache without I/O.

        Receiver scrolling must stay instant, so this method never starts a
        download. It only exposes a verified suggestion when that provider's
        channel catalogue is already in RAM/cache.
        """
        if not hint:
            return False
        src = hint.get("source") or {}
        epg = hint.get("epg") or {}
        sid = str(src.get("source_id") or "")
        rows = list(self.epg_by_source.get(sid) or [])
        if not rows:
            try:
                rows = list(smart_catalog_boot.get(sid) or [])
            except Exception:
                rows = []
        if not rows:
            try:
                if source_channel_cache.has(sid):
                    rows = list(source_channel_cache.get(sid) or [])
            except Exception:
                rows = []
        if not rows:
            return False
        if sid not in self.epg_by_source:
            clean=[]
            for row in rows:
                item=dict(row or {})
                item["source_id"] = sid
                item["source_name"] = src.get("source_name") or sid
                clean.append(item)
            self.epg_by_source[sid] = clean
        try:
            self._make_source_visible(sid)
            self.refresh_selection(allow_prepare=False)
            self._select_epg_id_in_current_list(epg.get("channel_id"))
            return True
        except Exception:
            return False

    def _bind_selection_context(self, service, src):
        self._selection_bound_ref = str((service or {}).get("ref") or "")
        if not src:
            self._selection_bound_source_ids = set()
        elif src.get("country_bundle"):
            self._selection_bound_source_ids = set(str(x.get("source_id") or "") for x in (src.get("children") or []) if x.get("source_id"))
        else:
            sid = str(src.get("source_id") or "")
            self._selection_bound_source_ids = set([sid]) if sid else set()

    def _show_current_mapping_preview(self, service):
        """Show current owner first, then alternate claims in the same compact format."""
        infos = self._mapping_infos_for_ref((service or {}).get("ref"))
        rows = []
        entries = []
        for idx, info in enumerate((infos or [])[:8]):
            rows.append(self._compact_epg_row(info, mapped=(idx == 0)))
            entries.append(info)
        if not rows:
            rows = ["No mapping"]
            entries = [{"separator": True}]
        self.selection_entries = entries
        self.selection_rows = rows
        self._set_list("selections", rows)
        self._selection_bound_ref = str((service or {}).get("ref") or "")
        primary = infos[0] if infos else {}
        self._selection_bound_source_ids = set([str(primary.get("source_id") or "")]) if primary.get("source_id") else set()

    def _clear_stale_match_preview(self, service):
        """Never show another channel's old Saudi/France list as its match."""
        if self.focus != 1:
            return
        name = str((service or {}).get("name") or "Channel")
        self.selection_entries = []
        self.selection_rows = ["Finding best EPG IDs for %s…" % name]
        self._set_list("selections", self.selection_rows)
        self._selection_bound_ref = str((service or {}).get("ref") or "")
        self._selection_bound_source_ids = set()

    def _update_instant_name_hint(self):
        """beta85 hot path: never score sources while the receiver cursor is idle.

        Smart Mapping is now explicitly human-in-the-loop.  The receiver pane
        paints immediately and only exposes the provider/satellite/IPTV context.
        Full match enumeration starts on GREEN or RIGHT, so scrolling through
        dozens of services cannot trigger fuzzy/context work.
        """
        service = self._current("channels", self.bouquet_services)
        self._smart_recommended_source_id = ""
        self._smart_recommended_channel_id = ""
        self._smart_recommended_display_name = ""
        if not service:
            return
        if self._service_is_mapped(service):
            self._show_current_mapping_preview(service)
        else:
            self._clear_stale_match_preview(service)

    def _queue_receiver_idle_preview(self):
        self._selection_forced_source_id = ""
        service = self._current("channels", self.bouquet_services)
        self._receiver_idle_ref = str((service or {}).get("ref") or "")
        # Never carry another service's recommendation while the cursor moves.
        self._smart_recommended_source_id = ""
        self._smart_recommended_channel_id = ""
        self._smart_recommended_display_name = ""
        try:
            self._receiver_nav_timer.stop()
            self._receiver_nav_timer.start(180, True)
        except Exception:
            self._receiver_nav_idle()

    def _receiver_nav_idle(self):
        service = self._current("channels", self.bouquet_services)
        if not service or str(service.get("ref") or "") != str(self._receiver_idle_ref or ""):
            return
        # rc48: held-key scrolling is still paint-only.  Once the user pauses,
        # show a tiny cross-source candidate list from the resident RAM index.
        # With the small direct-source set this is bounded and gives the old immediate
        # "EPGs appear when I stop on a channel" behaviour without provider XML
        # parsing or full-catalogue browsing.
        self._paint_selected_programme_fast()
        try:
            if not self._service_is_mapped(service):
                self._refresh_cross_source_suggestions(service)
        except Exception:
            pass
        self.update_source_title()
        self._update_receiver_context_summary()
        self._update_action_labels()

    def _queue_source_idle_preview(self):
        # Manual source navigation exits any previous Find Suggestion forced feed.
        self._selection_forced_source_id = ""
        src = self._current("sources", self.source_groups)
        self._source_idle_id = str((src or {}).get("source_id") or (src or {}).get("source_name") or "")
        try:
            self._source_nav_timer.stop()
            self._source_nav_timer.start(160, True)
        except Exception:
            self._source_nav_idle()

    def _source_nav_idle(self):
        if self.focus != 2:
            return
        src = self._current("sources", self.source_groups)
        current_id = str((src or {}).get("source_id") or (src or {}).get("source_name") or "")
        if not src or current_id != str(self._source_idle_id or ""):
            return
        if src.get("suggestion_all_sources"):
            # First row previews the cross-source recommendations directly from
            # the in-RAM index; never call _ensure_source_loaded on a virtual row.
            self.refresh_selection(allow_prepare=False)
        elif src.get("country_bundle"):
            self.refresh_selection()
        elif src.get("source_group"):
            self.selection_entries = []
            self.selection_rows = ["No channels in this source group"]
            self._set_list("selections", self.selection_rows)
        else:
            # beta80: browsing a source is cache-only.  Never start HTTP simply
            # because the remote-control cursor paused on a row.
            self._ensure_source_loaded(src)
            rows = list(self.epg_by_source.get(str(src.get("source_id") or ""), []) or [])
            if src.get("external") and not rows:
                self.selection_entries = []
                self.selection_rows = ["CHANNEL IDs NOT YET IN RAM",
                                       "Boot discovery is background / cursor stays instant",
                                       "Missing/stale cache refreshes automatically"]
                self._set_list("selections", self.selection_rows)
            else:
                self.refresh_selection(allow_prepare=False)
        self.update_source_title()

    def _suggestion_visible_name(self, epg):
        """Keep Arabic provider IDs/aliases visible in Source Override suggestions.

        RC13 rendered only ``display_name`` when present.  Some OpenEPG shards
        expose a Latin display name while the real XMLTV ID or alternate
        display-name is Arabic, which made the Arabic candidate appear to have
        disappeared even though it was still indexed.  Show the Arabic identity
        inline without changing the stored mapping target.
        """
        epg = epg or {}
        display = str(epg.get("display_name") or epg.get("channel_id") or "Channel").strip()
        values = [str(epg.get("channel_id") or "").strip()]
        values += [str(x or "").strip() for x in (epg.get("search_aliases") or [])]
        values += [str(x or "").strip() for x in (epg.get("aliases") or [])]
        arabic = ""
        for value in values:
            if value and re.search(r"[\u0600-\u06FF]", value):
                arabic = value
                break
        if arabic and arabic.casefold() != display.casefold():
            return "%s  •  AR: %s" % (display, arabic)
        return display

    def _suggestion_language_info(self, epg, src):
        """Return (rank, label) for the manual all-matches browser.

        User-visible order is Arabic first, then mixed AR/EN, French, English,
        then original/unknown.  This is presentation/ranking only; the user still
        chooses the exact feed manually and no candidate is auto-mapped.
        """
        row_lang = str(self._epg_variant_language(epg) or "").lower()
        src_lang = str(smart_context_match.source_language(src) or "").lower()
        lang = row_lang or src_lang
        if lang == "ar":
            return (0, "AR")
        if lang == "fr":
            return (2, "FR")
        if lang == "en":
            return (3, "EN")
        # Reuse the cached catalogue-language sampler only when explicit metadata
        # is missing.  Rank 1 is the existing bilingual AR/EN tier.
        try:
            rank = int(self._source_language_rank(src))
        except Exception:
            rank = 4
        if rank == 0:
            return (0, "AR")
        if rank == 1:
            return (1, "AR/EN")
        if rank == 2:
            return (2, "FR")
        if rank == 3:
            return (3, "EN")
        return (4, "ORIGINAL")

    def _parallel_language_aliases(self, source_id, row):
        """Return safe cross-language search aliases for parallel AR/EN feeds.

        Pairing is exact XMLTV-ID only; never by row position.  The returned
        aliases affect search/indexing only.  The visible candidate and final
        mapping remain on the real source row chosen by the user.
        """
        sid = str(source_id or "")
        sibling = source_variant_policy.sibling(sid)
        if not sibling:
            return []
        if sibling not in self._variant_alias_rows_cache:
            try:
                rows = list(source_channel_cache.get(sibling) or [])
                self._variant_alias_rows_cache[sibling] = {
                    str((item or {}).get("channel_id") or "").strip().casefold(): item
                    for item in rows if str((item or {}).get("channel_id") or "").strip()
                }
            except Exception:
                self._variant_alias_rows_cache[sibling] = {}
        try:
            return multilingual_channel_aliases.sibling_aliases(
                sid, row or {}, self._variant_alias_rows_cache.get(sibling) or [])
        except Exception:
            return []


    def _prepared_rows_for_find(self, src, allow_local_xml=False):
        """Return a prepared channel-ID catalogue for targeted Find Matches.

        beta131 deliberately avoids the old global Smart-name-index prerequisite.
        GREEN searches only relevant/dedicated/active sources.  Rows come from RAM,
        tiny source-channel caches or built-in local hints.  External sources
        never fall back to a downloaded XMLTV file; 7.0.6 obtains missing remote
        IDs through the direct URL reader instead.  This function never performs
        network I/O.
        """
        src = src or {}
        sid = str(src.get("source_id") or "")
        if not sid or src.get("source_group"):
            return []
        rows = list((self.epg_by_source or {}).get(sid) or [])
        if not rows and not src.get("external"):
            try:
                rows = list(source_catalog.local_channel_hints(sid) or [])
            except Exception:
                rows = []
        if not rows:
            try:
                if source_channel_cache.has(sid):
                    rows = list(source_channel_cache.get(sid) or [])
            except Exception:
                rows = []
        # Local EPGManager generators may not expose static hints (for example
        # dynamic beIN lists). They may still use their own generated local XML.
        # External providers NEVER use a local XML fallback in Smart Mapping.
        if not rows and allow_local_xml and not src.get("external"):
            try:
                path = self._source_xml_path(src)
                if path and os.path.exists(path):
                    rows = channel_mapper.read_xmltv_channel_ids_fast(
                        path, sid, src.get("source_name") or sid)
                    if rows:
                        try:
                            source_channel_cache.put(
                                sid, src.get("source_name") or sid, rows,
                                provider=src.get("provider") or "",
                                region=src.get("region") or "",
                                language=src.get("language") or "")
                        except Exception:
                            pass
            except Exception:
                rows = []
        clean = []
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item.setdefault("source_id", sid)
            item.setdefault("source_name", src.get("source_name") or sid)
            if item.get("channel_id"):
                clean.append(item)
        return clean

    def _targeted_find_sources(self, service, identity_id="", force_source=None, max_sources=18):
        """Choose a small context-first source set for one receiver channel.

        Dedicated source(s) win, then active Smart Sources in the same
        country/region, then locally prepared alternatives.  This replaces the
        expensive all-country index build that could remain at 1% on small boxes.
        """
        concrete = [dict(x) for x in (getattr(self, "_all_source_groups", []) or [])
                    if x and not x.get("source_group") and x.get("source_id")]
        by_id = {str(x.get("source_id") or ""): x for x in concrete}
        wanted = []
        seen = set()

        def add(src):
            if not src:
                return
            if src.get("country_bundle"):
                for child in src.get("children") or []:
                    add(child)
                return
            sid = str(src.get("source_id") or "")
            if not sid or sid in seen:
                return
            seen.add(sid)
            wanted.append(dict(src))

        # Explicit Force/Source Override is still permission-scoped in RC32.
        # A source switched OFF cannot be searched/mapped just because its old
        # compact cache remains on disk. Bundles expose only their ON children.
        if force_source:
            if force_source.get("country_bundle"):
                for child in force_source.get("children") or []:
                    if self._source_mapping_enabled(child or {}):
                        add(child)
            elif self._source_mapping_enabled(force_source):
                add(force_source)
            return wanted[:max(1, int(max_sources or 18))]

        profile = smart_context_match.receiver_profile(service or {}) or {}
        pure = str(profile.get("clean_name") or (service or {}).get("name") or "")
        dedicated = list(smart_context_engine.dedicated_sources(identity_id, pure) or [])
        for sid in dedicated:
            add(by_id.get(str(sid)))
        try:
            ctx0 = smart_context_engine.service_context(service or {}) or {}
            for sid in mapping_strategy.strategic_source_ids(
                    pure, (service or {}).get("provider_name") or profile.get("provider_hint") or "",
                    ctx0.get("country") or ""):
                add(by_id.get(str(sid)))
        except Exception:
            pass

        active = set(self.auto_selected_source_ids or set())

        def rank(src):
            sid = str(src.get("source_id") or "")
            try:
                ctx_rank = int(smart_context_engine.rank_source(service, src, identity_id)[0])
            except Exception:
                ctx_rank = 9
            # Active and local rows are useful, but never outrank a better
            # country/provider context.
            return (ctx_rank,
                    0 if sid in active else 1,
                    0 if not mapping_strategy.is_external_source(src) else 1,
                    _alpha_text(src.get("source_name") or sid))

        ranked = sorted(concrete, key=rank)
        # First add active rows that are context-plausible, then prepared/local
        # alternatives.  Hard cap keeps the GIL/I/O footprint bounded.
        for src in ranked:
            if len(wanted) >= max(1, int(max_sources or 18)):
                break
            sid = str(src.get("source_id") or "")
            try:
                ctx_rank = int(smart_context_engine.rank_source(service, src, identity_id)[0])
            except Exception:
                ctx_rank = 9
            if sid in active and ctx_rank <= 4:
                add(src)
        for src in ranked:
            if len(wanted) >= max(1, int(max_sources or 18)):
                break
            try:
                ctx_rank = int(smart_context_engine.rank_source(service, src, identity_id)[0])
            except Exception:
                ctx_rank = 9
            if ctx_rank <= 3 or not mapping_strategy.is_external_source(src):
                add(src)
        return wanted[:max(1, int(max_sources or 18))]

    def _targeted_find_pool(self, service, identity_id="", aliases=None, force_source=None):
        """Build a bounded candidate pool without a global Smart index."""
        aliases = list(aliases or [])
        pool = []
        seen = set()

        def add(item, src=None):
            item = dict(item or {})
            sid = str(item.get("source_id") or (src or {}).get("source_id") or "")
            cid = str(item.get("channel_id") or "")
            sig = (sid.casefold(), cid.casefold())
            if not sid or not cid or sig in seen:
                return
            item["source_id"] = sid
            item.setdefault("source_name", (src or {}).get("source_name") or sid)
            # Safe cross-language aliasing is done only for shortlisted rows.
            try:
                extra = self._parallel_language_aliases(sid, item)
            except Exception:
                extra = []
            if extra:
                base = list(item.get("search_aliases") or [])
                known = set(str(x).casefold() for x in base)
                for value in extra:
                    v = str(value or "").strip()
                    if v and v.casefold() not in known:
                        known.add(v.casefold()); base.append(v)
                item["search_aliases"] = base[:12]
            seen.add(sig)
            pool.append(item)

        target_sources = self._targeted_find_sources(
            service, identity_id=identity_id, force_source=force_source, max_sources=18)
        # Try the real receiver name plus canonical aliases/variants.  Per-source
        # indexes are tiny and reused; large catalogues never get a full fuzzy scan.
        alias_names = []
        for value in aliases:
            v = str(value or "").strip()
            if v and v.casefold() not in set(x.casefold() for x in alias_names):
                alias_names.append(v)
        if not alias_names:
            alias_names = [str((service or {}).get("name") or "")]

        for src in target_sources:
            rows = self._prepared_rows_for_find(src, allow_local_xml=False)
            if not rows:
                continue
            short = []
            short_seen = set()
            for alias in alias_names[:10]:
                svc = dict(service or {})
                svc["name"] = alias
                svc["clean_name"] = alias
                try:
                    candidates = self._candidate_pool_fast(svc, src, rows, limit=56)
                except Exception:
                    candidates = []
                for epg in candidates:
                    key = str((epg or {}).get("channel_id") or "").casefold()
                    if key and key not in short_seen:
                        short_seen.add(key); short.append(epg)
                if len(short) >= 96:
                    break
            for epg in short[:96]:
                add(epg, src)
            # Once several exact/alias candidates exist, continuing through many
            # unrelated providers only adds latency.  Dedicated/context ranking in
            # the final stage still decides the visible order.
            if len(pool) >= 140:
                break
        return pool

    def _exact_prepared_source_candidates(self, service, aliases=None, identity_id="", force_source=None, limit=96):
        """Direct exact lookup over prepared source shards, independent of global index.

        This fixes the remaining Mapping V2 blind spot: a valid XMLTV ID could be
        present in a country/source shard while the persistent Smart index had been
        built before that shard existed.  Exact receiver name / readable channel-ID /
        curated identity is cheap enough to check directly and must never depend on
        a monolithic cache being complete.
        """
        service = service or {}
        aliases = [str(x or "").strip() for x in (aliases or []) if str(x or "").strip()]
        if not aliases:
            aliases = [str(service.get("clean_name") or service.get("name") or "").strip()]
        aliases = [x for x in aliases if x]
        if not aliases:
            return []

        rnorm = set(); rcompact = set(); rids = set()
        for value in aliases[:20]:
            try:
                n = id_mapping_engine.normalize(value); c = id_mapping_engine.compact(value)
            except Exception:
                n = c = ""
            if n: rnorm.add(n)
            if c: rcompact.add(c)
            try:
                iid = unified_channel_identity.canonical_id(value) or ""
                if iid: rids.add(str(iid).casefold())
            except Exception:
                pass
        if identity_id:
            rids.add(str(identity_id).casefold())

        concrete = [dict(x or {}) for x in (getattr(self, "_all_source_groups", []) or [])
                    if x and not x.get("source_group") and x.get("source_id")]
        active = set(str(x or "") for x in (self.auto_selected_source_ids or set()) if str(x or ""))
        try:
            ctx = smart_context_engine.service_context(service) or {}
        except Exception:
            ctx = {}
        country = str(ctx.get("country") or "").upper().strip()
        region = str(ctx.get("region") or "").upper().strip()

        forced_ids = set()
        if force_source:
            if force_source.get("country_bundle"):
                forced_ids.update(str(x.get("source_id") or "") for x in (force_source.get("children") or [])
                                  if x.get("source_id") and self._source_mapping_enabled(x))
            else:
                sid = str(force_source.get("source_id") or "")
                if sid and self._source_mapping_enabled(force_source):
                    forced_ids.add(sid)
            # Force Mapping is permission-scoped: an OFF source/bundle with no
            # ON child must return no candidates instead of falling back global.
            if not forced_ids:
                return []

        dedicated = set()
        strategic = set()
        try:
            pure = str(service.get("clean_name") or service.get("name") or "")
            dedicated.update(str(x or "") for x in (smart_context_engine.dedicated_sources(identity_id, pure) or []) if str(x or ""))
            profile0 = smart_context_match.receiver_profile(service or {}) or {}
            strategic.update(str(x or "") for x in mapping_strategy.strategic_source_ids(
                pure, service.get("provider_name") or profile0.get("provider_hint") or "", country) if str(x or ""))
        except Exception:
            pass

        mena_codes = set(("MA","DZ","TN","LY","EG","SA","QA","AE","PS","LB","JO","IQ","KW","BH","OM","YE","SY","MENA"))
        def source_rank(src):
            sid = str(src.get("source_id") or "")
            countries = set(str(x or "").upper() for x in (src.get("countries") or []) if str(x or ""))
            sregion = str(src.get("region") or "").upper()
            is_mena = bool(countries & mena_codes) or "MENA" in sregion or "ARAB" in sregion
            if forced_ids:
                return (0 if sid in forced_ids else 99, 0, 0, _alpha_text(src.get("source_name") or sid))
            if sid in dedicated: tier = 0
            elif sid in strategic: tier = 0
            elif country and country in countries: tier = 1
            elif sid in active and ((region == "MENA" and is_mena) or not region): tier = 2
            elif region == "MENA" and is_mena: tier = 3
            elif sid in active: tier = 4
            elif not mapping_strategy.is_external_source(src): tier = 5
            else: tier = 8
            return (tier, 0 if sid in active else 1, 0 if not mapping_strategy.is_external_source(src) else 1,
                    _alpha_text(src.get("source_name") or sid))

        ranked_sources = sorted(concrete, key=source_rank)
        if forced_ids:
            ranked_sources = [x for x in ranked_sources if str(x.get("source_id") or "") in forced_ids]
        elif region == "MENA":
            # Never spend time opening unrelated European shards for a MENA SAT service.
            ranked_sources = [x for x in ranked_sources if source_rank(x)[0] <= 5]
        else:
            ranked_sources = [x for x in ranked_sources if source_rank(x)[0] <= 5]
        ranked_sources = ranked_sources[:48]

        out=[]; seen=set()
        for src in ranked_sources:
            rows = self._prepared_rows_for_find(src, allow_local_xml=False)
            if not rows:
                continue
            sid = str(src.get("source_id") or "")
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                cid = str(raw.get("channel_id") or "").strip()
                if not cid:
                    continue
                values = [raw.get("display_name") or cid, cid]
                try:
                    label = id_mapping_engine.source_aware_id_label(cid, src)
                    if label: values.append(label)
                    values.extend(id_mapping_engine.source_qualified_aliases(src, raw) or [])
                except Exception:
                    pass
                values.extend(list(raw.get("aliases") or []))
                values.extend(list(raw.get("search_aliases") or []))
                exact = False
                for value in values[:14]:
                    text = str(value or "").strip()
                    if not text:
                        continue
                    try:
                        n = id_mapping_engine.normalize(text); c = id_mapping_engine.compact(text)
                    except Exception:
                        n = c = ""
                    if (n and n in rnorm) or (c and c in rcompact):
                        exact = True; break
                    try:
                        iid = unified_channel_identity.canonical_id(text) or ""
                    except Exception:
                        iid = ""
                    if iid and str(iid).casefold() in rids:
                        exact = True; break
                if not exact:
                    continue
                sig=(sid.casefold(),cid.casefold())
                if sig in seen:
                    continue
                seen.add(sig)
                item=dict(raw)
                item["source_id"]=sid
                item.setdefault("source_name",src.get("source_name") or sid)
                out.append(item)
                if len(out) >= max(1,int(limit or 96)):
                    return out
        return out

    def _all_name_suggestions(self, service, limit=180, force_source=None):
        """Mapping V2: direct XMLTV-ID/name lookup, then one identity verdict.

        beta143 deliberately removes the old chain of targeted-source routing,
        feed-policy filtering, language filtering and PrecisionMatch filtering
        from interactive Mapping Repair.  Those overlapping gates were able to
        reject a candidate *after* its XMLTV ID/name had already proved the same
        channel (Sharjah HD -> Sharjah TV, MBC, Abu Dhabi TV, etc.).

        Candidate discovery is now global-cache first and bounded: exact name,
        canonical identity and token buckets only.  Country/provider metadata is
        used later only to rank already-valid IDs; it cannot create identity.
        """
        if not service:
            return []
        profile = smart_context_match.receiver_profile(service) or {}
        pure = str(profile.get("clean_name") or service.get("name") or "").strip()
        if not pure:
            return []

        aliases = [pure]
        seen_alias = {pure.casefold()}
        try:
            for value in (unified_channel_identity.aliases(pure) or []):
                text = str(value or "").strip()
                if text and text.casefold() not in seen_alias:
                    seen_alias.add(text.casefold()); aliases.append(text)
            for value in (unified_channel_identity.search_variants(pure) or []):
                text = str(value or "").strip()
                if text and text.casefold() not in seen_alias:
                    seen_alias.add(text.casefold()); aliases.append(text)
        except Exception:
            pass
        try:
            iid = unified_channel_identity.canonical_id(pure) or ""
        except Exception:
            iid = ""

        # Reuse the compact persistent index directly.  It is the authoritative
        # list of prepared XMLTV channel IDs and costs one bounded dictionary
        # lookup rather than source-by-source scans.
        compact = self._smart_name_index if (self._smart_name_index_ready and isinstance(self._smart_name_index, dict)) else {}
        if not compact:
            try:
                snap = smart_mapping_warm_cache.snapshot() or {}
                compact = snap.get("smart_name_index") or {}
            except Exception:
                compact = {}
        if not compact:
            try:
                compact = (smart_name_index_cache.load_fast_snapshot() or {}).get("index") or {}
            except Exception:
                compact = {}
        if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2 and compact.get("rows"):
            self._smart_name_index = compact
            self._smart_name_index_ready = True

        forced_ids = set()
        if force_source:
            if force_source.get("country_bundle"):
                forced_ids.update(str((x or {}).get("source_id") or "") for x in (force_source.get("children") or [])
                                  if (x or {}).get("source_id") and self._source_mapping_enabled(x or {}))
            else:
                sid0 = str(force_source.get("source_id") or "")
                if sid0 and self._source_mapping_enabled(force_source):
                    forced_ids.add(sid0)
            if not forced_ids:
                return []
        pool = []
        seen = set()
        def add_row(raw):
            if not isinstance(raw, dict):
                return
            sid = str(raw.get("source_id") or "")
            cid = str(raw.get("channel_id") or "")
            if not sid or not cid or (forced_ids and sid not in forced_ids):
                return
            sig = (sid.casefold(), cid.casefold())
            if sig in seen:
                return
            seen.add(sig); pool.append(dict(raw))

        # 8.1.0 FastMapping: interactive suggestions are INDEX-ONLY.
        # Never scan prepared source shards here. A stale/missing index returns
        # immediately and is rebuilt by the background catalogue worker.

        # 7.0.2 Smart Mapping Lite candidate discovery.  This replaces the
        # duplicated exact/token bucket walker with the same bounded index logic
        # used to paint yellow SUGGESTION rows.  It never scans the full provider
        # catalogue: canonical/ID exact keys first, then a small discriminating
        # token vote inspired by the IPTV->SAT matcher.
        if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2:
            try:
                for raw in manual_match_lite.candidate_rows(service, compact, limit=48):
                    add_row(raw)
            except Exception:
                log.exception("Smart Mapping Lite candidate discovery failed")

        # No cold fallback scan in 8.1.0. Missing prepared/indexed data must
        # stay non-blocking; YELLOW refresh prepares a source explicitly.

        ranked = []
        # Operator learning is loaded once per receiver channel, not deep-copied
        # once per EPG candidate. This was a hidden source of manual-match CPU.
        try:
            learned_target = smartmatch_ai.learned_target(service) or {}
        except Exception:
            learned_target = {}
        for epg in pool[:64]:
            sid = str(epg.get("source_id") or "")
            src = self._source_by_id(sid) or {}
            learned = bool(learned_target and
                           str(learned_target.get("source_id") or "").casefold() == sid.casefold() and
                           str(learned_target.get("channel_id") or "").casefold() == str(epg.get("channel_id") or "").casefold())
            verdict = id_mapping_engine.evaluate(service, src, epg, learned=learned)
            if not verdict.get("allowed"):
                continue
            context_rank, context_label = smart_context_engine.rank_source(service, src, iid)
            lang_rank, lang_label = self._suggestion_language_info(epg, src)
            item = dict(epg)
            item["_suggestion_tier"] = 0 if verdict.get("auto") else 2
            item["_suggestion_lang_rank"] = lang_rank
            item["_suggestion_lang_label"] = lang_label
            item["_suggestion_backend"] = self._source_backend(src) if src else "EPG"
            item["_suggestion_source_label"] = smart_context_match.source_label(src) if src else str(epg.get("source_name") or sid)
            item["_suggestion_name_score"] = int(verdict.get("score") or 0)
            item["_suggestion_context_rank"] = int(context_rank)
            item["_suggestion_context_label"] = str(context_label or "ALTERNATIVE")
            item["_suggestion_active"] = bool(sid in (self.auto_selected_source_ids or set()))
            item["_precision_auto"] = bool(verdict.get("auto"))
            item["_precision_grade"] = str(verdict.get("grade") or "REVIEW")
            item["_precision_confidence"] = int(verdict.get("confidence") or 0)
            item["_precision_target_key"] = str(verdict.get("target_key") or "")
            item["_precision_reason"] = str(verdict.get("reason") or "")
            ranked.append(item)

        ranked.sort(key=lambda e: (
            0 if e.get("_precision_auto") else 1,
            -int(e.get("_precision_confidence") or 0),
            int(e.get("_suggestion_context_rank") or 9),
            int(e.get("_suggestion_lang_rank") or 4),
            0 if e.get("_suggestion_active") else 1,
            _alpha_text(e.get("display_name") or e.get("channel_id")),
            _alpha_text(e.get("_suggestion_source_label") or ""),
        ))
        return ranked[:max(1, min(24, int(limit or 24)))]

    def _show_all_name_suggestions(self, service, force_source=None, matches=None):
        if matches is None:
            matches = self._all_name_suggestions(service, force_source=force_source)
        pure = str((smart_context_match.receiver_profile(service) or {}).get("clean_name") or service.get("name") or "Channel")
        self._suggestion_browser_active = True
        self._suggestion_browser_ref = str(service.get("ref") or "")
        self._suggestion_browser_pure_name = pure
        self._selection_forced_source_id = ""
        self._smart_recommended_source_id = ""
        self._smart_recommended_channel_id = ""
        self._smart_recommended_display_name = ""

        context = smart_context_engine.context_label(service)
        rows = ["=== AVAILABLE EPG MATCHES • %s ===" % pure.upper(), "CONTEXT • %s" % context]
        ordered = [{"separator": True}, {"separator": True}]
        if not matches:
            rows += ["No valid match in prepared catalogues",
                     "YELLOW refreshes only the feed you explicitly choose"]
            ordered += [{"separator": True}, {"separator": True}]
        else:
            context_names = {
                0: "DEDICATED / OFFICIAL SOURCE",
                1: "PROVIDER COUNTRY",
                2: "RECEIVER REGION",
                3: "INTERNATIONAL ALTERNATIVES",
                4: "OTHER REGIONS",
            }
            last_group = None
            last_lang = None
            for epg in matches:
                group = int(epg.get("_suggestion_context_rank") or 4)
                lang = str(epg.get("_suggestion_lang_label") or "ORIGINAL")
                if group != last_group:
                    rows.append("--- %s ---" % context_names.get(group, epg.get("_suggestion_context_label") or "ALTERNATIVES"))
                    ordered.append({"separator": True})
                    last_group = group
                    last_lang = None
                if lang != last_lang:
                    rows.append("[%s]" % lang)
                    ordered.append({"separator": True})
                    last_lang = lang
                identity_tag = str(epg.get("_precision_grade") or {0: "PROVEN", 1: "EXACT", 2: "REVIEW"}.get(int(epg.get("_suggestion_tier") or 0), "REVIEW"))
                active = " • ACTIVE" if epg.get("_suggestion_active") else ""
                rows.append(("%s  •  %s  •  %s  •  %s%s" % (
                    self._suggestion_visible_name(epg),
                    epg.get("_suggestion_source_label") or epg.get("source_name") or epg.get("source_id") or "EPG",
                    epg.get("_suggestion_backend") or "EPG", identity_tag, active)) +
                    self._epg_programme_marker(epg))
                ordered.append(epg)

        self.selection_entries = ordered
        self.selection_rows = rows
        self._set_list("selections", rows)
        for i, entry in enumerate(ordered):
            if entry and not entry.get("separator"):
                self._set_index("selections", i)
                break
        self.focus = 3
        self.update_focus()
        self._bind_selection_context(service, None)
        self["summary"].setText("%d safe/review match%s • PROVEN can auto-map • REVIEW requires you • YOU choose" % (
            len(matches), "es" if len(matches) != 1 else ""))
        try:
            self["source_title"].setText("%s\n%s" % (pure.upper(), context))
        except Exception:
            pass
        return bool(matches)

    def _poll_suggestion_job(self):
        """Consume an async Smart Mapping search result on the UI thread."""
        if not self._suggestion_job_busy:
            return
        elapsed = max(0.0, time.time() - float(self._suggestion_job_started or time.time()))
        result = self._suggestion_job_result
        if result is None and elapsed < float(self._suggestion_job_timeout or 8.0):
            return
        self._suggestion_job_busy = False
        self._suggestion_job_result = None
        token = int(getattr(self, "_suggestion_job_token", 0) or 0)
        if result is None:
            # The daemon worker may still finish later.  Incrementing the token
            # makes that late result stale, so it can never repaint another
            # channel after the user has moved on.
            self._suggestion_job_token = token + 1
            try:
                perf_profiler.record("Find Suggestions timeout", elapsed)
            except Exception:
                pass
            self["summary"].setText("Smart Mapping timed out safely • try a source override or BLUE → More")
            self["status_label"].setText("READY • SEARCH STOPPED SAFELY")
            return

        service = result.get("service") or {}
        current = self._current("channels", self.bouquet_services) or {}
        if str(current.get("ref") or "") != str(result.get("ref") or ""):
            # The user moved while the worker was searching.  Never display a
            # stale result for a different receiver channel.
            return
        if int(result.get("token") or -1) != token:
            return
        matches = result.get("matches") or []
        force_source = result.get("force_source") or None
        try:
            self._show_all_name_suggestions(service, force_source=force_source, matches=matches)
        finally:
            try:
                perf_profiler.record("Find Suggestions", elapsed)
            except Exception:
                pass
            try:
                self["catalog_progress"].setValue(100)
                self["catalog_progress_text"].setText("READY")
            except Exception:
                pass

    def select_best_name_suggestion(self, force_source=None):
        """Find safe same-channel candidates without ever blocking Enigma2 UI.

        beta97 keeps GREEN human-in-the-loop but moves candidate enumeration to
        a daemon worker.  The worker reads only RAM / compact channel caches;
        it never downloads XMLTV.  An 8 second watchdog discards pathological
        searches instead of letting the receiver appear frozen.
        """
        service = self._current("channels", self.bouquet_services)
        if not service:
            return False
        if self._suggestion_job_busy:
            self["summary"].setText("Smart Mapping is already finding safe matches…")
            return False
        # beta131: Find Matches is channel-scoped and starts immediately.
        # A persistent global index is consumed when already available, but GREEN
        # never builds/waits for it. This removes the 1% Smart Catalog stall seen
        # on Vu+ Zero 4K while retaining the global cache as an optional accelerator.
        if not self._smart_name_index_ready:
            try:
                snap = smart_mapping_warm_cache.snapshot() or {}
                warm_index = snap.get("smart_name_index") or {}
                if isinstance(warm_index, dict) and warm_index:
                    self._smart_name_index = warm_index
                    self._smart_name_index_ready = True
            except Exception:
                pass

        self._suggestion_job_token = int(getattr(self, "_suggestion_job_token", 0) or 0) + 1
        token = self._suggestion_job_token
        service_copy = dict(service)
        force_copy = dict(force_source) if force_source else None
        self._suggestion_job_ref = str(service_copy.get("ref") or "")
        self._suggestion_job_force_sid = str((force_copy or {}).get("source_id") or "")
        self._suggestion_job_started = time.time()
        self._suggestion_job_result = None
        self._suggestion_job_busy = True
        self["summary"].setText("Targeted safe search for %s…  •  relevant sources only • browsing stays responsive" % (service_copy.get("name") or "channel"))
        try:
            self["catalog_progress"].setValue(5)
            self["catalog_progress_text"].setText("5%")
            self["status_label"].setText("TARGETED SAFE MATCHING")
        except Exception:
            pass

        def worker():
            try:
                # 8.1.0 FastMapping: GREEN never downloads/prepares sources.
                # It consumes only the already-built compact index. If a source
                # is missing, YELLOW Refresh Source is the explicit preparation path.
                matches = self._all_name_suggestions(service_copy, limit=24, force_source=force_copy)
                result = {"token": token, "ref": self._suggestion_job_ref,
                          "service": service_copy, "force_source": force_copy,
                          "matches": matches, "error": ""}
            except Exception as exc:
                log.exception("Async Smart Mapping suggestion search failed")
                result = {"token": token, "ref": self._suggestion_job_ref,
                          "service": service_copy, "force_source": force_copy,
                          "matches": [], "error": str(exc)}
            # One atomic assignment. The UI timer is the only consumer.
            if int(getattr(self, "_suggestion_job_token", 0) or 0) == token:
                self._suggestion_job_result = result

        threading.Thread(target=worker, daemon=True).start()
        try:
            self._timer.start(100, False)
        except Exception:
            pass
        return True

    def _candidate_result(self, service, src, epg):
        """Mapping V2 single verdict used by Mapping Repair and Auto Search."""
        service = service or {}; src = src or {}; epg = epg or {}
        # An existing manual assignment is always authoritative.
        try:
            saved = self.store.get(epg.get("source_id") or src.get("source_id"), epg.get("channel_id")) or {}
            if service.get("ref") in (saved.get("refs") or []) and str(saved.get("mode") or "").lower() == "manual":
                return {"allowed": True, "auto": True, "score": 100, "confidence": 1000,
                        "grade": "MANUAL", "precision_grade": "MANUAL",
                        "reason": "Manual mapping", "reasons": [("Saved mapping", 100)],
                        "verified": True, "target_key": "manual"}
        except Exception:
            pass
        try:
            learned = bool(smartmatch_ai.learned_match(service, src, epg))
        except Exception:
            learned = False
        try:
            result = id_mapping_engine.evaluate(service, src, epg, learned=learned)
        except Exception as exc:
            return {"allowed": False, "auto": False, "score": 0, "confidence": 0,
                    "grade": "BLOCKED", "precision_grade": "BLOCKED",
                    "reason": "Mapping V2 error: %s" % exc,
                    "reasons": [("Mapping V2", -100)], "verified": False}
        out = dict(result)
        out["precision_grade"] = str(result.get("grade") or "REVIEW")
        out["verified"] = bool(result.get("auto"))
        out["reasons"] = [("ID evidence", int(result.get("confidence") or 0))] + \
                         [(str(x), 1) for x in (result.get("proofs") or [])[:5]]
        return out

    @staticmethod
    def _epg_variant_language(epg):
        """Infer a per-channel language when a mixed provider has no source language.

        This is intentionally conservative and primarily makes specialist mixed
        catalogues such as Al Jazeera readable as [AR] / [EN] in EPG Match.
        """
        epg = epg or {}
        text = (str(epg.get("display_name") or "") + " " + str(epg.get("channel_id") or "")).lower()
        if "english" in text or "_en." in text or ".en." in text or text.endswith(".en"):
            return "en"
        if "arabic" in text or "_ar." in text or ".ar." in text or text.endswith(".ar"):
            return "ar"
        return ""

    def _epg_programme_marker(self, epg):
        """Colour-ready ID health marker; cache-only and fail-open.

        Direct GitHub feeds use the daily per-ID monitor.  Only a fresh report
        with zero future programmes becomes RED/NO EPG.  Healthy IDs get a green
        ID OK badge; warnings remain orange.  Non-direct sources fall back to the
        complete local quality profile and never infer emptiness from a sample.
        """
        epg = epg or {}
        sid = str(epg.get("source_id") or "")
        cid = str(epg.get("channel_id") or "")
        if not sid or not cid:
            return ""
        try:
            health = github_direct_sync.id_health(sid, cid)
            if health == "NO_EPG":
                return "  •  NO EPG"
            if health == "OK":
                return "  •  ID OK"
            if health == "WARNING":
                return "  •  EPG WARN"
        except Exception:
            pass
        try:
            if source_quality.confirmed_no_programmes(sid, cid):
                return "  •  NO EPG"
        except Exception:
            pass
        return ""

    def _epg_row_label(self, epg, mapped=False):
        """Modern manual-browse row: XMLTV ID • source • EPG health."""
        return self._compact_epg_row(epg, mapped=mapped)

    @staticmethod
    def _candidate_tag(result):
        grade = str((result or {}).get("precision_grade") or (result or {}).get("grade") or "").upper()
        if grade in ("MANUAL", "PROVEN", "SAFE", "REVIEW", "NO EPG", "BLOCKED"):
            return grade
        reason = str((result or {}).get("reason") or "").lower()
        if "manual" in reason or "saved" in reason:
            return "MANUAL"
        return "REVIEW"

    def _auto_jump_enabled(self):
        # beta86: reverse EPG→receiver jumping is permanently disabled in the
        # interactive workspace.  It is expensive and conflicts with the user's
        # explicit choice workflow.  Keep the method for legacy callers only.
        return False

    def _next_unmapped_enabled(self):
        # beta63 removed automatic movement after Assign. Keep this compatibility
        # method only for old callers/settings files; it is intentionally OFF.
        return False

    def _protect_manual_enabled(self):
        try:
            return bool(self.config.get_protect_manual_mappings()) if self.config and hasattr(self.config, "get_protect_manual_mappings") else True
        except Exception:
            return True

    def _jump_to_best_channel_for_current_epg(self):
        """Reverse Smart Mapping: EPG selection proposes the receiver channel."""
        if not self._auto_jump_enabled():
            return
        epg = self._current("selections", self.selection_entries)
        src = self._current("sources", self.source_groups)
        if not epg or epg.get("separator") or not src or src.get("source_group") or not self.bouquet_services:
            return
        # A saved manual mapping for this XMLTV ID always wins.
        wanted_refs = []
        try:
            saved = self.store.get(epg.get("source_id") or src.get("source_id"), epg.get("channel_id")) or {}
            wanted_refs = list(saved.get("refs") or [])
        except Exception:
            wanted_refs = []
        for i, service in enumerate(self.bouquet_services):
            if service.get("ref") in wanted_refs:
                self._set_index("channels", i)
                self.update_source_title()
                return
        best_idx = None
        best_score = -1
        second = -1
        best_result = None
        for i, service in enumerate(self.bouquet_services):
            result = self._candidate_result(service, src, epg)
            if not result.get("allowed"):
                continue
            score = int(result.get("score") or 0)
            if score > best_score:
                second = best_score
                best_idx, best_score, best_result = i, score, result
            elif score > second:
                second = score
        # Require a safe candidate. Ambiguous results are proposed only when
        # confidence is strong enough; nothing is ever saved automatically.
        if best_idx is not None and best_result and best_result.get("verified") and best_score >= 96:
            self._set_index("channels", best_idx)
            service = self._current("channels", self.bouquet_services)
            self["summary"].setText("Suggested: %s ← %s • %s • %d%% %s" %
                                    (service.get("name", "Channel"), epg.get("display_name") or epg.get("channel_id"),
                                     epg.get("source_name") or self._source_label(epg.get("source_id")),
                                     best_score, self._candidate_tag(best_result)))
            self.update_source_title()

    def _cross_source_suggestion_candidates(self, service, limit=40):
        """Return one strong candidate per prepared EPG source, RAM-only.

        This is intentionally a *manual suggestion browser*, not an automatic
        mapper.  The global Smart-name index already contains compact
        channel-id/display-name rows for all boot-prepared sources.  We use that
        index directly so entering SUGGESTION never touches XMLTV/network.

        A source-region mismatch may hide a perfectly valid manual alternative
        (for example a Moroccan channel carried by a France/Egypt catalogue).
        Such rows are allowed to remain visible as REVIEW only when the channel
        identity/name evidence itself is exact; they are never promoted to
        SAFE/PROVEN automatic mapping by this browser.
        """
        if not service:
            return []
        # Prefer the process-global boot index because it is rebuilt whenever
        # background Channel-ID discovery adds a source. A screen-local index may
        # be older than the boot catalogue when Smart Mapping was opened early.
        try:
            compact = smart_boot_name_index.snapshot() or {}
        except Exception:
            compact = {}
        if not compact:
            compact = self._smart_name_index if (self._smart_name_index_ready and isinstance(self._smart_name_index, dict)) else {}
        if not compact:
            try:
                compact = (smart_mapping_warm_cache.snapshot() or {}).get("smart_name_index") or {}
            except Exception:
                compact = {}

        # 7.1.0 critical latency rule: if the compact index is not ready, return
        # immediately. 7.0.10's fallback scanned every prepared EPG row on the
        # Enigma2 UI thread; on a Zero 4K that could freeze Al Aoula for nearly a
        # minute. A temporarily empty suggestion pane is preferable to blocking
        # the receiver. The background index repaint fills it when ready.
        if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2:
            if compact is not self._smart_name_index:
                self._smart_name_index = compact
                self._smart_name_index_ready = True
                self._smart_name_index_building = False
            try:
                # Exact/identity index only; keep the candidate pool tightly
                # bounded before the more expensive safety/context evaluator.
                raw_rows = manual_match_lite.candidate_rows(
                    service, compact,
                    limit=max(24, min(80, int(limit or 40) * 2)))
            except Exception:
                raw_rows = []
        else:
            raw_rows = []

        receiver_name = str((smart_context_match.receiver_profile(service) or {}).get("clean_name") or service.get("name") or "")
        try:
            receiver_id = str(unified_channel_identity.canonical_id(receiver_name) or "")
        except Exception:
            receiver_id = ""
        receiver_norm = manual_match_lite.manual_norm(receiver_name)
        receiver_compact = manual_match_lite.manual_compact(receiver_name)

        best_by_source = {}
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            epg = dict(raw)
            sid = str(epg.get("source_id") or "")
            cid = str(epg.get("channel_id") or "")
            if not sid or not cid:
                continue
            src = self._source_by_id(sid) or {}
            if not src:
                continue
            epg.setdefault("source_name", src.get("source_name") or sid)

            try:
                verdict = id_mapping_engine.evaluate(service, src, epg, learned=False) or {}
            except Exception:
                verdict = {}

            allowed = bool(verdict.get("allowed"))
            auto = bool(verdict.get("auto")) if allowed else False
            confidence = int(verdict.get("confidence") or 0) if allowed else 0
            grade = str(verdict.get("grade") or "REVIEW").upper() if allowed else "REVIEW"
            reason = str(verdict.get("reason") or "") if allowed else ""

            # Strong manual identity proof independent of source geography.
            # This keeps valid Al Aoula/MBC/etc. alternatives visible even when
            # their catalogue is tagged France/Egypt/Europe, while sibling/feed
            # mismatches still fail closed.
            values = [str(epg.get("display_name") or cid), cid]
            try:
                label = id_mapping_engine.source_aware_id_label(cid, src)
                if label:
                    values.append(label)
            except Exception:
                pass
            identity_exact = False
            text_exact = False
            best_manual_score = 0
            for value in values[:8]:
                text = str(value or "").strip()
                if not text:
                    continue
                vn = manual_match_lite.manual_norm(text)
                vc = manual_match_lite.manual_compact(text)
                if receiver_norm and vn and receiver_norm == vn:
                    text_exact = True
                if receiver_compact and vc and receiver_compact == vc:
                    text_exact = True
                try:
                    iid = str(unified_channel_identity.canonical_id(text) or "")
                except Exception:
                    iid = ""
                if receiver_id and iid and receiver_id.casefold() == iid.casefold():
                    identity_exact = True
                # Candidate discovery already came from the compact name index.
                # Exact/canonical proof is enough for ordering here; avoid any
                # fuzzy rescoring loop in the UI thread.
                if identity_exact:
                    best_manual_score = max(best_manual_score, 100)
                elif text_exact:
                    best_manual_score = max(best_manual_score, 98)

            if not allowed:
                if not (identity_exact or text_exact):
                    continue
                # Region/provider policy conflict => visible manual REVIEW only.
                confidence = 940 if identity_exact else 920
                grade = "REVIEW"
                reason = "exact channel identity • source context requires manual confirmation"
            elif grade == "NO EPG":
                # Keep the exact ID visible for diagnosis/manual override, but
                # never present it as a SAFE/PROVEN automatic candidate.
                auto = False
            elif not auto and confidence < 820 and not (identity_exact or text_exact):
                continue

            epg["_precision_auto"] = bool(auto)
            epg["_precision_grade"] = grade
            epg["_precision_confidence"] = confidence
            epg["_precision_reason"] = reason
            epg["_suggestion_name_score"] = max(int(round(confidence / 10.0)), int(best_manual_score or 0))
            epg["_suggestion_source_label"] = smart_context_match.source_label(src)
            epg["_suggestion_backend"] = self._source_backend(src)
            epg["_suggestion_active"] = bool(sid in (self.auto_selected_source_ids or set()))

            # One best row per source, exactly as requested in the SUGGESTION
            # browser. SAFE/PROVEN wins, then confidence, then exact identity.
            rank = (
                0 if auto else 1,
                -int(confidence),
                0 if identity_exact else 1,
                0 if text_exact else 1,
                0 if epg.get("_suggestion_active") else 1,
                -int(best_manual_score or 0),
                _alpha_text(epg.get("display_name") or cid),
            )
            previous = best_by_source.get(sid)
            if previous is None or rank < previous[0]:
                best_by_source[sid] = (rank, epg)

        # 8.2.1: ONE RECEIVER = ONE ACTIVE EPG OWNER.  Several selected SRP
        # catalogues may know the same channel, but only the deterministic primary
        # owner is GREEN/MAPPED.  Other valid catalogues stay visible above as
        # PROVEN/REVIEW alternatives and can become the owner only after an
        # explicit manual change.
        info = self._primary_mapping_info_for_ref((service or {}).get("ref")) or {}
        sid = str(info.get("source_id") or "")
        cid = str(info.get("channel_id") or "")
        if sid and cid:
            src = self._source_by_id(sid) or {}
            if src:
                mapped = {
                    "source_id": sid,
                    "source_name": str(info.get("source_name") or src.get("source_name") or sid),
                    "channel_id": cid,
                    "display_name": str(info.get("display_name") or cid),
                    "_precision_auto": True,
                    "_precision_grade": "MAPPED",
                    "_precision_confidence": 1000,
                    "_precision_reason": "single active receiver owner",
                    "_suggestion_name_score": 100,
                    "_suggestion_source_label": smart_context_match.source_label(src),
                    "_suggestion_backend": self._source_backend(src),
                    "_suggestion_active": bool(sid in (self.auto_selected_source_ids or set())),
                    "_suggestion_current": True,
                }
                # The exact owner replaces any ordinary candidate from the same
                # source. It is the only row allowed to carry the MAPPED label.
                best_by_source[sid] = ((-1, -1000, 0, 0, 0, -100,
                                        _alpha_text(mapped.get("display_name") or cid)), mapped)

        rows = [pair[1] for pair in best_by_source.values()]
        rows.sort(key=lambda e: (
            0 if e.get("_suggestion_current") else 1,
            0 if e.get("_precision_auto") else 1,
            -int(e.get("_precision_confidence") or 0),
            0 if e.get("_suggestion_active") else 1,
            _alpha_text(e.get("_suggestion_source_label") or e.get("source_name") or e.get("source_id") or ""),
            _alpha_text(e.get("display_name") or e.get("channel_id") or ""),
        ))
        return rows[:max(1, int(limit or 40))]

    def _refresh_cross_source_suggestions(self, service):
        """Paint pane 4 with one suggested EPG match per prepared source."""
        name = str((service or {}).get("name") or "Receiver channel")
        try:
            boot_generation = int(smart_catalog_boot.generation())
        except Exception:
            boot_generation = 0
        try:
            index_generation = int((smart_boot_name_index.status() or {}).get("generation") or 0)
        except Exception:
            index_generation = 0
        cache_key = ("__smart_suggestions__", str((service or {}).get("ref") or ""),
                     self.mapping_revision, boot_generation, index_generation)
        cached = self.selection_cache.get(cache_key)
        if cached is not None:
            rows, ordered = cached
            self.selection_rows = list(rows)
            self.selection_entries = list(ordered)
            self._set_list("selections", self.selection_rows)
            for i, entry in enumerate(self.selection_entries):
                if entry and not entry.get("separator"):
                    self._set_index("selections", i)
                    break
            # Cached paint remains instant, then missing OSN/MENA channel-ID
            # catalogues may be filled asynchronously one source at a time.
            if self._queue_osn_mena_catalogue_prefetch(service):
                self["summary"].setText("%s • cached suggestions • preparing missing OSN/MENA IDs in background" %
                                        (str((service or {}).get("name") or "Channel")))
            return

        suggestions = self._cross_source_suggestion_candidates(service, limit=12)
        # rc46: the title is already shown by the pane/header.  Do NOT insert a
        # fake first list row here: on real OpenATV/Metrix that separator could
        # receive the blue cursor and trap navigation at index 0.  Pane 4 now
        # contains selectable EPG candidates only.
        rows = []
        ordered = []
        if not suggestions:
            try:
                boot_state = smart_catalog_boot.status() or {}
                index_state = smart_boot_name_index.status() or {}
            except Exception:
                boot_state = {}; index_state = {}
            if boot_state.get("busy") or index_state.get("busy"):
                rows.extend(["Preparing compact RAM index", "Navigation stays instant • no full scan/network"])
            else:
                rows.extend(["No SAFE/REVIEW identity found in prepared sources", "YELLOW opens Source / Details"])
            ordered.extend([{"separator": True}, {"separator": True}])
        else:
            for epg in suggestions:
                confidence = int(epg.get("_precision_confidence") or 0)
                pct = max(0, min(100, int(round(confidence / 10.0))))
                grade = str(epg.get("_precision_grade") or "REVIEW").upper()
                rows.append(self._compact_epg_row(epg, score=pct, grade=grade,
                                                  mapped=bool(epg.get("_suggestion_current"))))
                ordered.append(epg)

        self.selection_rows = rows
        self.selection_entries = ordered
        if len(self.selection_cache) >= 24:
            try:
                self.selection_cache.pop(next(iter(self.selection_cache)))
            except Exception:
                self.selection_cache.clear()
        self.selection_cache[cache_key] = (list(rows), list(ordered))
        self._set_list("selections", rows)
        for i, entry in enumerate(ordered):
            if entry and not entry.get("separator"):
                self._set_index("selections", i)
                break
        self._selection_bound_ref = str((service or {}).get("ref") or "")
        self._selection_bound_source_ids = set(str(x.get("source_id") or "") for x in suggestions if x.get("source_id"))
        osn_prefetch = self._queue_osn_mena_catalogue_prefetch(service)
        if osn_prefetch:
            self["summary"].setText("%s • %d source suggestion%s • preparing missing OSN/MENA IDs in background" % (
                name, len(suggestions), "s" if len(suggestions) != 1 else ""))
        else:
            self["summary"].setText("%s • %d source suggestion%s from RAM" % (
                name, len(suggestions), "s" if len(suggestions) != 1 else ""))
        # While the cache-only boot/index worker is finishing, quietly repaint the
        # virtual SUGGESTION row. The generation-aware cache makes this cheap: no
        # HTTP/XML and no rescoring unless new RAM data was actually published.
        try:
            busy = bool((smart_catalog_boot.status() or {}).get("busy") or
                        (smart_boot_name_index.status() or {}).get("busy"))
        except Exception:
            busy = False
        if busy and self.focus == 2:
            try:
                self._source_idle_id = "__smart_suggestions__"
                self._source_nav_timer.stop()
                self._source_nav_timer.start(650, True)
            except Exception:
                pass

    def refresh_selection(self, allow_prepare=True):
        self._suggestion_browser_active = False
        self._suggestion_browser_ref = ""
        src = self._current("sources", self.source_groups)
        service = self._current("channels", self.bouquet_services)
        self._bind_selection_context(service, src)
        if not src:
            self.selection_entries = []
            self.selection_rows = ["No EPG source selected"]
            self._set_list("selections", self.selection_rows)
            return
        if src.get("suggestion_all_sources"):
            self._refresh_cross_source_suggestions(service)
            self.update_source_title()
            return
        if src.get("country_bundle"):
            entries = self._bundle_entries(src, allow_prepare=allow_prepare)
            if not entries:
                self.selection_entries=[]
                self.selection_rows=["COUNTRY CHANNEL IDs NOT PREPARED",
                                     "Navigation is RAM-only • no HTTP on RIGHT/OK",
                                     "YELLOW opens Source / Details"]
                self._set_list("selections",self.selection_rows)
                self.update_source_title()
                return
        else:
            if src.get("source_group"):
                self.selection_entries = []
                self.selection_rows = ["No channels in this source group"]
                self._set_list("selections", self.selection_rows)
                self.update_source_title()
                return
            self._ensure_source_loaded(src)
            entries = list(self.epg_by_source.get(src.get("source_id"), []))
            # 7.0.10: entering pane 4 never starts HTTP.  The boot catalogue
            # worker owns proactive preparation; YELLOW is the explicit refresh.
        if src.get("external") and not entries:
            self.selection_entries = []
            try:
                loading = bool(smart_catalog_boot.is_loading(src.get("source_id")))
            except Exception:
                loading = False
            self.selection_rows = (["CHANNEL IDs PREPARING IN BACKGROUND",
                                    "No wait/network on this source click",
                                    "Stale source cache refreshes automatically"] if loading else
                                   ["CHANNEL IDs NOT PREPARED",
                                    "Navigation remains instant / RAM-only",
                                    "Missing/stale source cache refreshes automatically"])
            self._set_list("selections", self.selection_rows)
            self.update_source_title()
            return
        if self.only_unmapped:
            entries = [x for x in entries if not self._is_mapped(x)]

        manual_active = bool(getattr(self, "_manual_source_override_active", False))
        context_key = (service.get("ref") if service else "", src.get("source_id"), bool(self.only_unmapped))
        if context_key != getattr(self, "_manual_browse_context", None):
            self._manual_browse_context = context_key
            self._manual_browse_page = 0
        cache_key = (
            service.get("ref") if service else "",
            src.get("source_id"),
            bool(self.only_unmapped),
            manual_active,
            int(self._manual_browse_page if manual_active else 0),
            self.mapping_revision,
        )
        cached = self.selection_cache.get(cache_key)
        if cached is not None:
            rows, ordered = cached
            self.selection_entries = list(ordered)
            self.selection_rows = list(rows or ["No channels in source"])
            self._set_list("selections", self.selection_rows)
            self.update_source_title()
            return

        # Build the right-hand candidate pane from a fresh local list.
        # beta43-beta49 accidentally relied on ``rows``/``ordered`` being
        # created only in the cache-hit branch.  On a cache miss this caused
        # UnboundLocalError as soon as we tried to append "No close matches"
        # (or even the close-match header).
        rows = []
        ordered = []
        # beta60: only score a small token-indexed shortlist.  The complete
        # alphabetical list remains available below, but giant providers no
        # longer run SmartMatch on every channel every time the cursor moves.
        if getattr(self, "_manual_source_override_active", False):
            # Explicit manual browsing may use the legacy per-source shortlist.
            shortlist = self._candidate_pool_fast(service, src, entries, limit=40)
        else:
            shortlist = self._candidate_pool_global_index(service, src, limit=24)
            # rc40: if the process-global Smart index is not ready yet, do not
            # leave SAFE EPG MATCH apparently empty.  Fall back to a bounded
            # RAM-only shortlist from the already-prepared selected source.  No
            # HTTP/XMLTV download is started here, and direct feeds are small.
            if not shortlist and entries:
                try:
                    shortlist = self._candidate_pool_fast(service, src, entries, limit=24)
                except Exception:
                    shortlist = []
        scored = []
        for epg in shortlist:
            score_src = self._source_by_id(epg.get("source_id")) if src.get("country_bundle") else src
            result = self._candidate_result(service, score_src or src, epg)
            score = int(result.get("score") or 0)
            scored.append((score, epg, result))
        scored.sort(key=lambda pair: (-pair[0], _alpha_text(pair[1].get("display_name") or pair[1].get("channel_id"))))

        if not entries:
            self.selection_entries = []
            self.selection_rows = ["No channels in source"]
            self.selection_cache[cache_key] = (list(self.selection_rows), [])
            self._set_list("selections", self.selection_rows)
            self.update_source_title()
            return

        # Do not promote an unverified fuzzy 90-94% row as a Smart suggestion.
        # It remains available under ALL for manual choice.
        close = [(score, epg, result) for score, epg, result in scored
                 if result.get("allowed") and ((result.get("verified") and score >= 86) or score >= 98)]
        if close:
            for score, epg, result in close[:8]:
                rows.append(self._compact_epg_row(epg, score=score, grade=self._candidate_tag(result),
                                                  mapped=self._is_mapped(epg)))
                ordered.append(epg)

        # rc55: the direct feeds are deliberately small and authoritative.
        # Always keep every real XMLTV ID visible in normal Smart Mapping so a
        # missing safe suggestion never looks like a missing source catalogue.
        # SAFE candidates stay at the top; all remaining IDs follow alphabetically
        # with EPG OK / NO EPG / EPG ? health. No separator rows are inserted, so
        # OpenATV/Metrix native MenuList navigation remains one row == one item.
        direct_full_catalogue = str(src.get("source_id") or "") in set(github_direct_sync.DIRECT_IDS)
        close_keys = set((str((e or {}).get("source_id") or ""),
                          str((e or {}).get("channel_id") or "").casefold())
                         for _score, e, _r in close)
        if direct_full_catalogue and not getattr(self, "_manual_source_override_active", False):
            all_entries = [epg for epg in entries
                           if (str((epg or {}).get("source_id") or ""),
                               str((epg or {}).get("channel_id") or "").casefold()) not in close_keys]
            all_entries.sort(key=lambda epg: _alpha_text((epg or {}).get("display_name") or (epg or {}).get("channel_id")))
            for epg in all_entries:
                rows.append(self._compact_epg_row(epg, mapped=self._is_mapped(epg)))
                ordered.append(epg)
        elif not close and not getattr(self, "_manual_source_override_active", False):
            rows.append("No safe EPG ID for this source")
            ordered.append({"separator": True})

        # 8.1.0 FastMapping: never inject thousands of non-direct provider rows
        # during normal navigation. Full catalogue browsing remains available only
        # when the user explicitly entered BLUE -> Manual Source Override.
        if getattr(self, "_manual_source_override_active", False):
            all_entries = [epg for epg in entries
                           if (str((epg or {}).get("source_id") or ""),
                               str((epg or {}).get("channel_id") or "").casefold()) not in close_keys]
            page_size = max(40, int(getattr(self, "_manual_browse_page_size", 120) or 120))
            page_count = max(1, (len(all_entries) + page_size - 1) // page_size)
            self._manual_browse_page_count = page_count
            page = min(max(0, int(getattr(self, "_manual_browse_page", 0) or 0)), page_count - 1)
            self._manual_browse_page = page
            start = page * page_size
            stop = min(len(all_entries), start + page_size)
            rows.append("======= MANUAL BROWSE • PAGE %d/%d • %d-%d/%d =======" % (
                page + 1, page_count, (start + 1) if all_entries else 0, stop, len(all_entries)))
            ordered.append({"separator": True})
            for epg in all_entries[start:stop]:
                rows.append(self._epg_row_label(epg, mapped=self._is_mapped(epg)))
                ordered.append(epg)
            if page_count > 1:
                rows.append("CH-/CH+ = previous/next ID page")
                ordered.append({"separator": True})
        else:
            # rc49 direct sources already expose their complete ID catalogue above.
            # Non-direct providers remain candidate-only until explicit manual browse.
            pass
        self.selection_entries = ordered
        self.selection_rows = list(rows or ["No channels in source"])
        # Keep a small bounded cache. Large IPTV providers can contain thousands
        # of channels, so recomputing fuzzy scores on every LEFT/RIGHT trip is
        # wasteful. Twenty entries is enough for fluid back-and-forth mapping.
        if len(self.selection_cache) >= 20:
            try:
                self.selection_cache.pop(next(iter(self.selection_cache)))
            except Exception:
                self.selection_cache.clear()
        self.selection_cache[cache_key] = (list(self.selection_rows), list(ordered))
        self._set_list("selections", self.selection_rows)
        # Land directly on the best usable EPG candidate instead of a separator.
        for _i, _entry in enumerate(self.selection_entries):
            if _entry and not _entry.get("separator"):
                self._set_index("selections", _i)
                break
        self.update_source_title()

    def update_source_title(self):
        src = self._current("sources", self.source_groups)
        service = self._current("channels", self.bouquet_services)
        remap_refs = list(getattr(self, "_remap_scope_refs", []) or [])
        if remap_refs:
            old = dict(getattr(self, "_remap_old_info", {}) or {})
            self["source_title"].setText("CHANGE EPG • %d service%s • %s / %s" % (
                len(remap_refs), "s" if len(remap_refs) != 1 else "",
                old.get("source_name") or old.get("source_id") or "current",
                old.get("display_name") or old.get("channel_id") or "EPG"))
            return
        if service and self.focus == 3:
            epg = self._current("selections", self.selection_entries) or {}
            if isinstance(epg, dict) and not epg.get("separator") and epg.get("channel_id"):
                cid = str(epg.get("channel_id") or "ID").strip() or "ID"
                display_name = str(epg.get("display_name") or "").strip()
                identity = ("%s  •  %s" % (display_name, cid)) if (display_name and display_name.casefold() != cid.casefold()) else cid
                preview = self._safe_epg_preview_text(epg, service)
                if not preview:
                    preview = "NOW  •  loading…"
                    try:
                        self._queue_safe_preview_warm([epg], service)
                    except Exception:
                        pass
                # The source is already explicit in pane 3; pane 4 is now ID +
                # current programme only, exactly matching the receiver workflow.
                self["source_title"].setText("%s\n%s" % (identity, preview))
                return
        if service and src and src.get("suggestion_all_sources") and self.focus >= 2:
            self["source_title"].setText("%s\nAUTO • best EPG IDs from active sources" % (service.get("name") or "Channel"))
            return
        if service:
            first = self._primary_mapping_info_for_ref(service.get("ref"))
            if first:
                text = "%s\n%s • %s" % (
                    service.get("name") or "Channel",
                    first.get("display_name") or first.get("channel_id") or "channel",
                    self._source_display_label(first.get("source_id"), first.get("source_name")))
                if self._service_has_conflict(service):
                    text += " • OWNER CONFLICT"
                self["source_title"].setText(text)
                return
            context = smart_context_engine.context_label(service)
            self["source_title"].setText("%s\n%s" % (service.get("name") or "Channel", context))
            return
        if src:
            if src.get("country_bundle"):
                self["source_title"].setText("%s\nUnified country source • %d provider%s" % (
                    src.get("source_name") or "Country", len(src.get("providers") or []),
                    "s" if len(src.get("providers") or []) != 1 else ""))
            else:
                self["source_title"].setText(src.get("source_name") or "EPG Source")
        else:
            self["source_title"].setText("Mapping Assistant\nProvider-first matching")

    def _update_summary(self):
        remap_refs = list(getattr(self, "_remap_scope_refs", []) or [])
        if remap_refs:
            old = dict(getattr(self, "_remap_old_info", {}) or {})
            self["summary"].setText("CHANGE EPG • %d service%s • FROM %s / %s • BLUE More → Source → EPG ID → OK Apply" % (
                len(remap_refs), "s" if len(remap_refs) != 1 else "",
                old.get("source_name") or old.get("source_id") or "current",
                old.get("display_name") or old.get("channel_id") or "EPG"))
            return
        total_services = len(self.catalog)
        mapped_services = sum(1 for service in self.catalog if self._service_is_mapped(service))
        unmapped = max(0, total_services - mapped_services)
        conflicts = sum(1 for service in self.catalog if self._service_has_conflict(service))
        suggestions = 0  # beta85: never scan every receiver service just to paint the header.
        # beta72: summary widgets never open SRP/audit JSON files.  Home/warm
        # cache already loaded these values off-thread; verified values replace
        # them later without blocking navigation.
        try:
            sst = dict(getattr(self, "_startup_srp_stats", {}) or {})
            audit = dict(getattr(self, "_startup_audit_summary", {}) or {})
            srp_refs = int(sst.get("mapped_service_refs") or 0)
            id_linked = int(audit.get("linked_ids") or 0)
            id_total = int(audit.get("total_ids") or 0)
        except Exception:
            srp_refs = id_linked = id_total = 0
        suspicious = conflicts
        self["summary"].setText("Mapped %d/%d  •  Unmapped %d  •  Conflicts %d  •  EPG IDs %d/%d" %
                                (mapped_services, total_services, unmapped, suspicious, id_linked, id_total))
        try:
            activity_store.record_mapping(mapped=mapped_services, unmapped=unmapped,
                                          suggestions=suggestions, conflicts=conflicts,
                                          epg_ids=len(self.epg_channels))
        except Exception:
            pass

    def update_focus(self):
        # rc73: visible Smart Mapping is a clean 3-pane workflow.
        # Internal source state remains available for candidate ownership, but
        # the Source column is hidden and all source controls live in BLUE->More.
        visible_focus = 3 if self.focus in (2, 3) else self.focus
        for i in range(4):
            key = "focus%d" % (i + 1)
            try:
                active = (i == visible_focus) and i != 2
                self[key].instance.setBackgroundColor(theme.ACCENT_PRIMARY_INT if active else theme.RULE_SOFT_INT)
            except Exception:
                pass
        self["hdr_bouquet"].setText(("> " if visible_focus == 0 else "") + "RECEPTION")
        self["hdr_channel"].setText(("> " if visible_focus == 1 else "") + "RECEIVER CHANNEL")
        self["hdr_source"].setText("")
        self["hdr_selection"].setText(("> " if visible_focus == 3 else "") + "BEST EPG MATCH  •  CHANNEL / EPG ID / NOW")
        try:
            self["bouquets"].instance.setSelectionEnable(visible_focus >= 0)
            self["channels"].instance.setSelectionEnable(visible_focus >= 1)
            self["sources"].instance.setSelectionEnable(False)
            self["selections"].instance.setSelectionEnable(visible_focus >= 3)
        except Exception:
            pass
        self.update_source_title()
        self._update_action_labels()

    def focus_left(self):
        # 3-pane flow: BEST EPG -> CHANNEL -> RECEPTION. Source selection is in More.
        if self.focus in (2, 3):
            self.focus = 1
        elif self.focus == 1:
            self.focus = 0
        else:
            self.focus = 0
        self.update_focus()

    def focus_right(self):
        if self.focus == 0:
            if getattr(self, "_reception_nav_pending", False):
                try:
                    self._reception_nav_timer.stop()
                except Exception:
                    pass
                self._reception_nav_idle()
            self.focus = 1
            self.update_focus()
            return
        if self.focus == 1:
            service = self._current("channels", self.bouquet_services)
            if not service:
                return
            self._manual_source_override_active = False
            # Always use the virtual all-source suggestion owner for the normal
            # workflow. Candidate rows are already ranked safest/best first.
            try:
                self._rebuild_source_view("__smart_suggestions__")
                for i, row in enumerate(self.source_groups or []):
                    if (row or {}).get("suggestion_all_sources"):
                        self._set_index("sources", i)
                        break
                self.refresh_selection(allow_prepare=False)
            except Exception:
                self.selection_entries = []
                self.selection_rows = ["Preparing best cached EPG matches…"]
                self._set_list("selections", self.selection_rows)
            self.focus = 3
            self.update_focus()
            self._after_move()
            return
        self.focus = 3
        self.update_focus()

    @staticmethod
    def _next_safe_selection_index(entries, current, down=False):
        """Return the next non-separator SAFE-MATCH row without wrapping.

        The SAFE pane contains presentation-only separator rows.  Some Enigma2
        MenuList builds keep the visual cursor on such a row even when the
        underlying MultiContent selection moves.  Resolve the target index from
        ``selection_entries`` first, then move the widget to that exact index.
        """
        rows = list(entries or [])
        if not rows:
            return int(current or 0)
        try:
            idx = max(0, min(int(current or 0), len(rows) - 1))
        except Exception:
            idx = 0
        step = 1 if down else -1
        probe = idx + step
        while 0 <= probe < len(rows):
            row = rows[probe] or {}
            if not row.get("separator"):
                return probe
            probe += step
        # If focus initially lands on a separator, DOWN/UP must still escape it.
        if (rows[idx] or {}).get("separator"):
            order = range(idx + 1, len(rows)) if down else range(idx - 1, -1, -1)
            for probe in order:
                if not (rows[probe] or {}).get("separator"):
                    return probe
        return idx

    def _move_focused_vertical(self, down=False):
        """Move the focused pane without assuming one MenuList compatibility API.

        OpenATV images differ slightly in the methods exposed by MenuList and
        custom MultiContent lists.  Prefer our wrappers, then native up/down,
        then the eListbox instance.  Navigation must never crash Enigma2.
        """
        # rc46 SAFE EPG MATCH: move the *real* eListbox cursor instead of
        # calculating an index and calling moveToIndex().  The latter remained
        # pinned to row 0 on the user's OpenATV/Metrix build.  Candidate mode no
        # longer contains a header separator, so one native move == one row.
        if self.focus == 3 and self.selection_entries:
            try:
                widget = self["selections"]
                before = self._get_index("selections")
                fn = getattr(widget, "move_down" if down else "move_up", None)
                if callable(fn):
                    fn()
                after = self._get_index("selections")
                # Information-only rows can still exist when there is no match;
                # skip them without ever wrapping around.
                guard = 0
                while (0 <= after < len(self.selection_entries) and
                       (self.selection_entries[after] or {}).get("separator") and
                       guard < len(self.selection_entries)):
                    prev = after
                    if callable(fn):
                        fn()
                    after = self._get_index("selections")
                    guard += 1
                    if after == prev:
                        break
                # Last-resort direct eListbox target only if the native move did
                # not change index and a valid candidate exists in that direction.
                if after == before:
                    target = self._next_safe_selection_index(self.selection_entries, before, down)
                    if target != before:
                        try: widget.instance.moveSelectionTo(int(target))
                        except Exception: self._set_index("selections", target)
                return
            except Exception:
                pass

        keys = ["bouquets", "channels", "sources", "selections"]
        try:
            widget = self[keys[self.focus]]
        except Exception:
            return
        try:
            fn = getattr(widget, "move_down" if down else "move_up", None)
            if callable(fn):
                fn()
                return
        except Exception:
            pass
        try:
            fn = getattr(widget, "down" if down else "up", None)
            if callable(fn):
                fn()
                return
        except Exception:
            pass
        try:
            inst = widget.instance
            mover = getattr(inst, "moveDown" if down else "moveUp", None)
            if mover is not None:
                inst.moveSelection(mover)
        except Exception:
            pass

    def move_up(self):
        self._move_focused_vertical(False)
        self._after_move()

    def move_down(self):
        self._move_focused_vertical(True)
        self._after_move()

    def _page_move(self, down=False):
        keys = ["bouquets", "channels", "sources", "selections"]
        widget = self[keys[self.focus]]
        moved = False
        # Native MenuList paging is much faster than issuing many key moves.
        try:
            fn = getattr(widget, "pageDown" if down else "pageUp")
            fn()
            moved = True
        except Exception:
            pass
        if not moved:
            try:
                inst = widget.instance
                fn = getattr(inst, "pageDown" if down else "pageUp", None)
                if fn is not None:
                    inst.moveSelection(fn)
                    moved = True
            except Exception:
                pass
        if not moved:
            # Conservative fallback for images exposing neither paging API.
            # Use the same compatibility path as normal UP/DOWN so a custom
            # pane can never trigger an AttributeError here either.
            for _ in range(10):
                self._move_focused_vertical(down)
        self._after_move()

    def _manual_browse_page_move(self, delta):
        if self.focus != 3 or not getattr(self, "_manual_source_override_active", False):
            return False
        src = self._current("sources", self.source_groups)
        if not src or src.get("suggestion_all_sources") or src.get("source_group"):
            return False
        old = int(getattr(self, "_manual_browse_page", 0) or 0)
        last = max(0, int(getattr(self, "_manual_browse_page_count", 1) or 1) - 1)
        new = min(last, max(0, old + int(delta)))
        if new == old and delta < 0:
            return True
        self._manual_browse_page = new
        self.refresh_selection(allow_prepare=False)
        return True

    def page_up(self):
        if not self._manual_browse_page_move(-1):
            self._page_move(False)

    def page_down(self):
        if not self._manual_browse_page_move(1):
            self._page_move(True)

    def jump_top(self):
        keys = ["bouquets", "channels", "sources", "selections"]
        self._set_index(keys[self.focus], 0)
        self._after_move()

    def _update_receiver_context_summary(self):
        """O(1) receiver summary: mapping + provider-first context only."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            return
        name = service.get("name") or "Channel"
        first = self._primary_mapping_info_for_ref(service.get("ref"))
        context = smart_context_engine.context_label(service)
        if first:
            self["summary"].setText("CURRENT • %s • %s • %s" % (
                name, first.get("channel_id") or first.get("display_name") or "ID",
                self._source_display_label(first.get("source_id"), first.get("source_name"))))
            return
        state = "MATCH INDEX PREPARING" if self._smart_name_index_building else "AUTO MAPPING OPTIONAL • GREEN TO START • RIGHT → MANUAL OVERRIDE"
        self["summary"].setText("CURRENT • %s • %s • %s" % (name, context, state))

    def _queue_reception_idle_refresh(self):
        self._reception_nav_pending = True
        try:
            self._reception_nav_timer.stop()
            # 110 ms keeps single-step navigation feeling immediate while a
            # held UP/DOWN key can cross many satellites without rebuilding each.
            self._reception_nav_timer.start(110, True)
        except Exception:
            self._reception_nav_idle()

    def _reception_nav_idle(self):
        if not self._reception_nav_pending:
            return
        self._reception_nav_pending = False
        t0 = time.time()
        self.refresh_channels()
        try:
            perf_profiler.record("Reception Debounced Refresh", time.time() - t0)
        except Exception:
            pass
        self.update_source_title()
        self._update_action_labels()
        self._queue_smart_auto_repair()

    def _current_reception_label(self):
        row = self._current("bouquets", self.bouquet_records) or {}
        if row.get("virtual") != "reception":
            return ""
        return str(row.get("reception_list") or "").strip()

    def _smart_auto_repair_key(self, reception=None):
        reception = str(reception or self._current_reception_label() or "").strip()
        if not reception:
            return None
        try:
            cache_sig = str(source_channel_cache.fast_signature() or "")
        except Exception:
            cache_sig = str(source_channel_cache.generation())
        selection_sig = ",".join(sorted(self._auto_allowed_source_ids() or set()))
        return (reception, cache_sig, selection_sig)

    def _queue_smart_auto_repair(self):
        """Normal Smart Mapping is manual/optional in rc45.

        Automatic mapping starts only after an explicit GREEN press.  Dedicated
        Mapping Repair callers that open with ``auto_repair_start=True`` keep
        their explicit behaviour.
        """
        self._smart_auto_repair_pending = False
        return False

    def _start_pending_smart_auto_repair(self):
        # Compatibility hook for the existing timer; normal Smart Mapping never
        # creates a pending silent repair anymore.
        self._smart_auto_repair_pending = False
        return False

    def _after_move(self):
        if self.focus == 0:
            # 8.1.1: do not rebuild thousands of receiver rows for every
            # intermediate satellite while UP/DOWN is held.
            self._queue_reception_idle_refresh()
        elif self.focus == 1:
            # beta79: scrolling the receiver list is a paint-only hot path.
            # No fuzzy scoring, source-page rebuild or EPG list construction is
            # allowed until the user pauses for 180 ms.
            service = self._current("channels", self.bouquet_services)
            self._queue_receiver_idle_preview()
            self.update_source_title()
            self._update_receiver_context_summary()
            self._update_action_labels()
        elif self.focus == 2:
            # beta86 manual source browsing is zero-load.  Moving through source
            # rows never opens a channel shard or rebuilds EPG MATCH; RIGHT/OK is
            # the explicit load action.
            self._selection_forced_source_id = ""
            self.update_source_title()
        else:
            # Never reverse-scan the whole receiver bouquet while the user is
            # scrolling EPG IDs.  That old convenience feature was one of the
            # largest remaining latency sources on big bouquets.
            self.update_source_title()

    def _preserve_receiver_selection(self, ref):
        """Refresh one receiver row without jumping away from the user's channel."""
        self.refresh_channels()
        wanted = set(self._ref_lookup_keys(ref))
        for i, row in enumerate(self.bouquet_services or []):
            if wanted.intersection(self._ref_lookup_keys((row or {}).get("ref"))):
                self._set_index("channels", i)
                break

    def _suggestion_for_current_service(self):
        service = self._current("channels", self.bouquet_services)
        if not service or self._service_is_mapped(service):
            return None
        return self._best_name_suggestion(service)

    def map_suggested_current(self):
        """One-click GREEN mapping for a safe cached name suggestion.

        Only a suggestion that passed the name-first safety gates reaches this
        path.  No network call, programme parse or full catalogue scan occurs.
        """
        service = self._current("channels", self.bouquet_services)
        hint = self._suggestion_for_current_service()
        if not service or not hint:
            return self.select_best_name_suggestion()
        ref = str(service.get("ref") or "").strip()
        src = hint.get("source") or {}
        epg = hint.get("epg") or {}
        if not ref or not src.get("source_id") or not epg.get("channel_id"):
            return False
        try:
            learned_exact = bool(smartmatch_ai.learned_match(service, src, epg))
        except Exception:
            learned_exact = False
        precision = id_mapping_engine.evaluate(service, src, epg, learned=learned_exact)
        if not precision.get("auto"):
            self["summary"].setText("REVIEW ONLY • Mapping V2 did not prove the same XMLTV channel • %s" % (precision.get("reason") or "insufficient identity proof"))
            return False
        try:
            save_result = self.store.assign_exclusive(
                src.get("source_id"), epg.get("channel_id"), [ref],
                display_name=epg.get("display_name") or epg.get("channel_id"),
                old_source_ids=[x.get("source_id") for x in self._mapping_infos_for_ref(ref)],
                label="GREEN Map Suggested", defer_generated=True)
            self._queue_manual_generated_sync(save_result)
            info = {"source_id": src.get("source_id"),
                    "source_name": src.get("source_name") or self._source_label(src.get("source_id")),
                    "channel_id": epg.get("channel_id"),
                    "display_name": epg.get("display_name") or epg.get("channel_id"),
                    "mode": "manual"}
            self._paint_manual_mapping_fast([ref], info)
            self.mapping_revision += 1
            self.selection_cache.clear()
            self._smart_name_hint_cache.clear()
            self._smart_recommended_source_id = str(src.get("source_id") or "")
            self._smart_recommended_channel_id = str(epg.get("channel_id") or "")
            self.focus = 1
            self["summary"].setText("MAPPED • %s → %s • %s • %d%% name match" % (
                service.get("name") or "Channel", epg.get("display_name") or epg.get("channel_id"),
                src.get("source_name") or src.get("source_id"), int(hint.get("score") or 0)))
            self.update_source_title()
            self._update_action_labels()
            return True
        except Exception as exc:
            log.exception("Could not apply GREEN suggested mapping")
            self.session.open(MessageBox, "Could not map suggested EPG.\n\n%s" % exc, MessageBox.TYPE_ERROR)
            return False

    def _trusted_local_auto_source_ids(self):
        """Local EPGManager feeds are trusted mapping sources when enabled.

        Smart Sources selection is primarily for remote feeds.  Older builds
        accidentally let a non-empty remote allow-list hide built-in local
        generators such as Arryadia/SNRT/2M, which is why obvious Moroccan
        channels could stay red even though their EPG IDs were bundled locally.
        """
        out = set()
        for src in getattr(self, "_all_source_groups", []) or []:
            if not src or src.get("source_group") or src.get("external"):
                continue
            sid = str(src.get("source_id") or "")
            if not sid:
                continue
            enabled = True
            try:
                if self.config is not None and hasattr(self.config, "is_source_enabled"):
                    enabled = bool(self.config.is_source_enabled(sid))
            except Exception:
                enabled = True
            if enabled:
                out.add(sid)
        return out

    def _auto_allowed_source_ids(self):
        """Return the exact ON-set allowed to participate in automatic mapping.

        RC32 removes the historical ``empty == unrestricted`` behaviour.  If a
        remote source is OFF it cannot be used merely because its compact cache
        is still present.  Built-in local generators participate only while
        their own configuration switch is enabled.
        """
        active = set(str(x or "") for x in (self.auto_selected_source_ids or set()) if str(x or ""))
        trusted = self._trusted_local_auto_source_ids()
        return active | trusted

    def _source_mapping_enabled(self, src_or_sid):
        """Cheap permission check used before any forced candidate discovery."""
        if isinstance(src_or_sid, dict):
            src = src_or_sid or {}
            if src.get("country_bundle"):
                return any(self._source_mapping_enabled(x) for x in (src.get("children") or []))
            sid = str(src.get("source_id") or "")
        else:
            sid = str(src_or_sid or "")
        return bool(sid and sid in set(self._auto_allowed_source_ids() or set()))

    def _auto_prepare_target_catalogues(self, services, max_sources=8):
        """Fetch only missing *compact* channel-ID catalogues needed by a satellite.

        beta135 deliberately made Auto Search cache-only for speed, but that can
        turn obvious channels (for example MBC 2 on a Saudi/MENA source) into
        NO MATCH when the tiny provider catalogue has never been prepared.  In
        beta138 the explicit Auto Repair action may bootstrap a few relevant
        compact channel-ID endpoints/headers in parallel. Programme XMLTV is never
        downloaded, saved or parsed here.  Successful shards are persisted by source_channel_cache,
        so the next run is cache-only and normally completes in a few seconds.
        """
        services = list(services or [])
        if not services:
            return {"fetched": 0, "rows": 0, "attempted": 0, "errors": 0}
        concrete = [dict(x or {}) for x in (getattr(self, "_all_source_groups", []) or [])
                    if x and not x.get("source_group") and x.get("source_id")]
        by_id = {str(x.get("source_id") or ""): x for x in concrete}
        active = set(str(x or "") for x in (self.auto_selected_source_ids or set()))
        allowed_auto = set(self._auto_allowed_source_ids() or set())
        scores = {}

        # Count how often a missing source is context-relevant across a bounded
        # sample of receiver services.  This naturally puts Saudi shards first
        # for MBC, Egypt first for Egyptian channels, Morocco first for SNRT, etc.
        seen_service_profiles = set()
        for service in services[:160]:
            try:
                profile = smart_context_match.receiver_profile(service or {}) or {}
                sig = (str(profile.get("country_hint") or profile.get("country") or ""),
                       str(profile.get("provider_hint") or (service or {}).get("provider_name") or ""),
                       str(profile.get("language_hint") or ""),
                       str(profile.get("sat_zone") or ""))
                # Do not drop duplicate profiles completely: common broadcasters
                # should receive a higher source score.  The set only limits the
                # expensive identity/source expansion for pathological lists.
                pure = str(profile.get("clean_name") or (service or {}).get("name") or "")
                ident = unified_channel_identity.resolve(pure) or {}
                iid = unified_channel_identity.canonicalize_identity_id(ident.get("id") or "")
                targets = self._targeted_find_sources(service, identity_id=iid, max_sources=18)
                # Explicit broadcaster-family expansion prevents arbitrary top-N
                # source truncation. MBC must inspect all Saudi ID shards; UAE
                # broadcasters must inspect UAE before unrelated MENA sources.
                ctx = smart_context_engine.service_context(service or {}) or {}
                strategic = mapping_strategy.strategic_source_ids(
                    pure, (service or {}).get("provider_name") or profile.get("provider_hint") or "",
                    ctx.get("country") or "")
                if strategic:
                    by_target = {str((x or {}).get("source_id") or ""): x for x in (targets or [])}
                    for strategic_sid in reversed(strategic):
                        strategic_src = by_id.get(strategic_sid) or self._source_by_id(strategic_sid) or by_target.get(strategic_sid)
                        if strategic_src:
                            targets.insert(0, strategic_src)
            except Exception:
                targets = []
            target_seen = set()
            for pos, src in enumerate(targets or []):
                sid0 = str((src or {}).get("source_id") or "")
                if not sid0 or sid0 in target_seen:
                    continue
                if sid0 not in allowed_auto:
                    continue
                target_seen.add(sid0)
                sid = str((src or {}).get("source_id") or "")
                if not sid or not mapping_strategy.has_id_catalog(src):
                    continue
                try:
                    if source_channel_cache.has(sid):
                        continue
                except Exception:
                    pass
                scores[sid] = scores.get(sid, 0) + max(1, 10 - int(pos))

        # When an explicit allow-list exists, a relevant selected source can be
        # absent from the top-N target expansion only because its cache is empty.
        # Give selected same-region country shards a small bootstrap chance.
        if active:
            wanted_countries = set()
            for service in services[:160]:
                try:
                    ctx = smart_context_engine.service_context(service or {}) or {}
                    code = str(ctx.get("country") or "").upper().strip()
                    if code:
                        wanted_countries.add(code)
                    if str(ctx.get("region") or "").upper() == "MENA":
                        wanted_countries.add("MENA")
                except Exception:
                    pass
            for sid in active:
                src = by_id.get(sid) or self._source_by_id(sid) or {}
                if not src or not mapping_strategy.has_id_catalog(src):
                    continue
                try:
                    if source_channel_cache.has(sid):
                        continue
                except Exception:
                    pass
                countries = set(str(x or "").upper() for x in (src.get("countries") or []))
                if countries.intersection(wanted_countries):
                    scores[sid] = max(scores.get(sid, 0), 3)

        chosen = []
        for sid, _score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
            src = by_id.get(sid) or self._source_by_id(sid) or {}
            if src:
                chosen.append(src)
            if len(chosen) >= max(1, int(max_sources or 8)):
                break
        if not chosen:
            return {"fetched": 0, "rows": 0, "attempted": 0, "errors": 0}

        def fetch_one(src):
            sid = str((src or {}).get("source_id") or "")
            try:
                rows, _meta = remote_channel_catalog.fetch(src, timeout=3, max_scan=6 * 1024 * 1024)
                ok = bool(rows) and source_channel_cache.put(
                    sid, src.get("source_name") or src.get("name") or sid, rows,
                    provider=src.get("provider") or "", region=src.get("region") or "",
                    language=src.get("language") or "")
                return sid, len(rows or []), bool(ok), ""
            except Exception as exc:
                return sid, 0, False, str(exc)

        fetched = rows_total = errors = 0
        workers = min(4, len(chosen))
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = [pool.submit(fetch_one, src) for src in chosen]
                for fut in as_completed(futures):
                    _sid, count, ok, _err = fut.result()
                    if ok:
                        fetched += 1
                        rows_total += int(count or 0)
                    else:
                        errors += 1
        except Exception:
            # Safe sequential fallback on unusual Python builds.
            fetched = rows_total = errors = 0
            for src in chosen:
                _sid, count, ok, _err = fetch_one(src)
                if ok:
                    fetched += 1; rows_total += int(count or 0)
                else:
                    errors += 1
        if fetched:
            self._auto_batch_index = None
            self._auto_batch_index_key = None
        return {"fetched": fetched, "rows": rows_total,
                "attempted": len(chosen), "errors": errors}

    def _build_auto_batch_index(self, services):
        """Build a current, network-free Mapping Repair index.

        beta147 deliberately stops treating the monolithic Smart-name cache as
        authoritative.  That cache can be incomplete when one source was
        refreshed after it was built, producing hundreds of false NO MATCH rows.

        The repair index is rebuilt from *current cheap evidence only*:
          - every enabled built-in local EPGManager catalogue (2M/SNRT/Arryadia...);
          - selected/context-relevant external source channel caches;
          - selected/context-relevant external compact ID shards.

        There is no network request and external programme XML is never opened here.  The old
        compact index is attached only as supplemental evidence, never as the
        sole source of truth.
        """
        services = list(services or [])
        active = set(str(x or "") for x in (self.auto_selected_source_ids or set()) if str(x or ""))
        trusted_local = self._trusted_local_auto_source_ids()
        allowed = self._auto_allowed_source_ids()

        concrete = [dict(x or {}) for x in (getattr(self, "_all_source_groups", []) or [])
                    if x and not x.get("source_group") and x.get("source_id")]
        by_source = {str(x.get("source_id") or ""): x for x in concrete}

        wanted_countries = set()
        wanted_regions = set()
        wanted_langs = set()
        for service in services[:64]:
            try:
                ctx = smart_context_engine.service_context(service or {}) or {}
                cc = str(ctx.get("country") or "").upper().strip()
                rg = str(ctx.get("region") or "").upper().strip()
                lg = str(ctx.get("language") or "").lower().strip()
                if cc: wanted_countries.add(cc)
                if rg: wanted_regions.add(rg)
                if lg: wanted_langs.add(lg)
            except Exception:
                pass
            try:
                profile = smart_context_match.receiver_profile(service or {}) or {}
                cc = str(profile.get("country_hint") or profile.get("country") or "").upper().strip()
                lg = str(profile.get("language_hint") or "").lower().strip()
                if cc: wanted_countries.add(cc)
                if lg: wanted_langs.add(lg)
            except Exception:
                pass
        if not wanted_regions and wanted_countries.intersection(set(("MA","DZ","TN","LY","EG","SA","QA","AE","PS","LB","JO","IQ","KW","BH","OM","YE","SY","MENA"))):
            wanted_regions.add("MENA")

        source_ids = []
        strategic_prepared = set()
        def add_sid(sid, strategic=False):
            sid = str(sid or "")
            if not sid or sid in source_ids or sid not in by_source:
                return
            # RC32: provider/country strategy is ranking only. It may never
            # reactivate an OFF source merely because a stale cache is prepared.
            if sid not in allowed:
                return
            source_ids.append(sid)

        # Built-in local feeds are tiny and authoritative for their families.
        for sid in sorted(trusted_local):
            add_sid(sid)

        # Broadcaster-owned/dedicated sources come before generic country feeds.
        for service in services[:96]:
            try:
                profile = smart_context_match.receiver_profile(service or {}) or {}
                pure = str(profile.get("clean_name") or (service or {}).get("name") or "")
                ident = unified_channel_identity.resolve(pure) or {}
                iid = unified_channel_identity.canonicalize_identity_id(ident.get("id") or "")
                for sid in smart_context_engine.dedicated_sources(iid, pure) or []:
                    add_sid(sid, strategic=True)
                ctx = smart_context_engine.service_context(service or {}) or {}
                for sid in mapping_strategy.strategic_source_ids(
                        pure, (service or {}).get("provider_name") or profile.get("provider_hint") or "",
                        ctx.get("country") or ""):
                    add_sid(sid, strategic=True)
            except Exception:
                pass
            if len(source_ids) >= 28:
                break

        mena_codes = set(("MA","DZ","TN","LY","EG","SA","QA","AE","PS","LB","JO","IQ","KW","BH","OM","YE","SY","MENA"))
        def same_zone(src):
            countries = set(str(x or "").upper() for x in ((src or {}).get("countries") or []) if str(x or ""))
            region = str((src or {}).get("region") or "").upper()
            if countries & wanted_countries:
                return True
            if "MENA" in wanted_regions and (countries & mena_codes or "MENA" in region or "ARAB" in region):
                return True
            if "EUROPE" in wanted_regions and "EUROPE" in region:
                return True
            return not wanted_regions and not wanted_countries

        def source_sort(src):
            sid = str((src or {}).get("source_id") or "")
            countries = set(str(x or "").upper() for x in ((src or {}).get("countries") or []) if str(x or ""))
            lang = str((src or {}).get("language") or "").lower()
            return (
                0 if sid in active else 1,
                0 if sid in trusted_local else 1,
                0 if countries & wanted_countries else 1,
                0 if same_zone(src) else 1,
                0 if (not wanted_langs or not lang or lang in wanted_langs) else 1,
                0 if not mapping_strategy.is_external_source(src) else 1,
                _alpha_text((src or {}).get("source_name") or sid),
            )

        # Add ON sources in the satellite's region. OFF sources are never
        # candidates even if a compact cache from a previous session survives.
        for src in sorted(concrete, key=source_sort):
            if len(source_ids) >= 32:
                break
            sid = str(src.get("source_id") or "")
            if sid in source_ids or sid not in allowed:
                continue
            if same_zone(src) or sid in active or sid in trusted_local:
                add_sid(sid)

        # Use a cheap persistent source-cache revision so a source refresh
        # invalidates prior NO-MATCH decisions even if the monolithic name index
        # has not been rebuilt yet.
        try:
            cache_sig = str(source_channel_cache.fast_signature() or "")
        except Exception:
            cache_sig = str(source_channel_cache.generation())
        key = (tuple(source_ids), cache_sig)
        cached = getattr(self, "_auto_batch_index", None)
        if cached and key == getattr(self, "_auto_batch_index_key", None):
            return cached

        by_key = {}
        by_token = {}
        by_sig = {}
        rows_total = 0
        prepared_sources = 0

        def add_bucket(bucket, key_value, item, max_items=96):
            if not key_value:
                return
            arr = bucket.setdefault(key_value, [])
            if len(arr) < max_items:
                arr.append(item)

        for sid in source_ids:
            src = by_source.get(sid) or self._source_by_id(sid) or {}
            if not src:
                continue
            # Crucial beta147 change: if the tiny JSON shard is missing but the
            # source XML is already on disk, read only its <channel> header now.
            # This is local I/O and stops before the first <programme>.
            rows = self._prepared_rows_for_find(src, allow_local_xml=False)
            if not rows:
                continue
            prepared_sources += 1
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                cid = str(item.get("channel_id") or "").strip()
                if not cid:
                    continue
                item["source_id"] = sid
                item.setdefault("source_name", src.get("source_name") or sid)
                item["__batch_src"] = src
                by_sig[(sid.casefold(), cid.casefold())] = item
                values = [str(item.get("display_name") or cid), cid]
                try:
                    lbl = id_mapping_engine.source_aware_id_label(cid, src)
                    if lbl:
                        values.append(str(lbl))
                    values.extend(id_mapping_engine.source_qualified_aliases(src, item) or [])
                except Exception:
                    pass
                values.extend([str(x) for x in (item.get("search_aliases") or []) if x])
                values.extend([str(x) for x in (item.get("aliases") or []) if x])
                seen_keys = set()
                for value in values[:10]:
                    for nkey in (self._smart_norm(value, False), self._smart_norm(value, True)):
                        if not nkey or nkey in seen_keys:
                            continue
                        seen_keys.add(nkey)
                        add_bucket(by_key, nkey, item, 96)
                        for tok in nkey.split():
                            if len(tok) >= 3 or tok.isdigit():
                                add_bucket(by_token, tok, item, 128)
                    try:
                        vn = id_mapping_engine.normalize(value)
                        vc = id_mapping_engine.compact(value)
                    except Exception:
                        vn = vc = ""
                    if vn:
                        add_bucket(by_key, "V=" + vn, item, 96)
                        for tok in vn.split():
                            if len(tok) >= 2 or tok.isdigit():
                                add_bucket(by_token, "V#" + tok, item, 128)
                    if vc:
                        add_bucket(by_key, "V~" + vc, item, 96)
                rows_total += 1

        # Supplemental old compact index: useful for language aliases, but never
        # allowed to hide fresher source shards/local hints.
        compact = {}
        try:
            compact = self._smart_name_index if (self._smart_name_index_ready and isinstance(self._smart_name_index, dict)) else {}
            if not compact:
                compact = (smart_name_index_cache.load_fast_snapshot() or {}).get("index") or {}
            if not (isinstance(compact, dict) and int(compact.get("_format") or 0) == 2):
                compact = {}
        except Exception:
            compact = {}

        data = {
            "mode": "hybrid", "key": key, "source_ids": source_ids,
            "by_key": by_key, "by_token": by_token, "by_sig": by_sig,
            "compact": compact, "allowed_source_ids": set(allowed),
            "strategic_source_ids": set(),
            "prepared_sources": prepared_sources, "rows_total": rows_total,
            "persistent": False,
            "catalog_signature": "direct:%s:%s|mapv4" % (cache_sig, ",".join(source_ids)),
        }
        self._auto_batch_index = data
        self._auto_batch_index_key = key
        return data

    def _auto_batch_match(self, service, batch):
        """Ultra-fast SAFE/PROVEN matcher for automatic repair.

        Auto Repair is intentionally *exact/curated only*.  Fuzzy/token voting
        belongs to interactive Find Matches, not to a 200-800 channel batch.
        This keeps CPU bounded and avoids turning generic token collisions into
        dozens of expensive candidate evaluations.
        """
        if not service or not batch:
            return {"status": "nomatch", "best": None, "matches": []}
        pure = str((service or {}).get("clean_name") or (service or {}).get("name") or "").strip()
        if not pure:
            return {"status": "nomatch", "best": None, "matches": []}

        aliases = [pure]
        alias_seen = {pure.casefold()}
        identity_ids = []
        try:
            ident = unified_channel_identity.resolve(pure) or {}
            iid = unified_channel_identity.canonicalize_identity_id(ident.get("id") or "")
            if iid:
                identity_ids.append(str(iid))
            for value in (ident.get("aliases") or []):
                value = str(value or "").strip()
                if value and value.casefold() not in alias_seen:
                    alias_seen.add(value.casefold()); aliases.append(value)
            for value in unified_channel_identity.search_variants(pure) or []:
                value = str(value or "").strip()
                if value and value.casefold() not in alias_seen:
                    alias_seen.add(value.casefold()); aliases.append(value)
        except Exception:
            pass

        candidates = []
        seen = set()
        allowed_sources = set(str(x or "") for x in (batch.get("allowed_source_ids") or set()) if str(x or ""))

        def add(item):
            if not item:
                return
            sid = str((item or {}).get("source_id") or "")
            cid = str((item or {}).get("channel_id") or "")
            if allowed_sources and sid not in allowed_sources:
                return
            sig = (sid.casefold(), cid.casefold())
            if sid and cid and sig not in seen:
                seen.add(sig); candidates.append(item)

        # Receiver exact keys are computed once. No token/fuzzy search here.
        v_norms = []
        v_compacts = []
        smart_keys = []
        for value in aliases[:20]:
            try:
                vn = id_mapping_engine.normalize(value)
                vc = id_mapping_engine.compact(value)
            except Exception:
                vn = vc = ""
            if vn and vn not in v_norms:
                v_norms.append(vn)
            if vc and vc not in v_compacts:
                v_compacts.append(vc)
            for key in self._smart_name_keys(value):
                if key and key not in smart_keys:
                    smart_keys.append(key)

        by_key = batch.get("by_key") or {}
        for vn in v_norms:
            for item in by_key.get("V=" + vn, [])[:32]:
                add(item)
        for vc in v_compacts:
            for item in by_key.get("V~" + vc, [])[:32]:
                add(item)
        for key in smart_keys:
            for item in by_key.get(key, [])[:24]:
                add(item)

        # Learned manual correction is one direct O(1) lookup.
        try:
            learned = smartmatch_ai.learned_target(service) or {}
            lsig = (str(learned.get("source_id") or "").casefold(),
                    str(learned.get("channel_id") or "").casefold())
            if lsig[0] and lsig[1]:
                add((batch.get("by_sig") or {}).get(lsig))
        except Exception:
            pass

        # Supplemental compact identity/exact buckets.  No token buckets.
        compact = batch.get("compact") or {}
        if isinstance(compact, dict) and int(compact.get("_format") or 0) == 2:
            # Reuse Source Override/SUGGESTION discovery, but keep Mapping V2
            # below as the hard authority before any automatic write.
            try:
                for _row in manual_match_lite.candidate_rows(service, compact, limit=48):
                    _sid = str((_row or {}).get("source_id") or "")
                    if _sid:
                        _item = dict(_row or {})
                        _item["__batch_src"] = self._source_by_id(_sid) or {}
                        add(_item)
            except Exception:
                pass
            rows = compact.get("rows") or []
            buckets = compact.get("buckets") or {}
            def add_pos(pos):
                try:
                    raw = rows[int(pos)]
                except Exception:
                    return
                if not isinstance(raw, dict):
                    return
                sid = str(raw.get("source_id") or "")
                if allowed_sources and sid not in allowed_sources:
                    return
                item = dict(raw)
                item["__batch_src"] = self._source_by_id(sid) or {}
                add(item)
            for iid in identity_ids[:4]:
                for pos in (buckets.get("@" + str(iid)) or [])[:32]:
                    add_pos(pos)
            for vn in v_norms:
                for pos in (buckets.get("V=" + vn) or [])[:32]:
                    add_pos(pos)
            for vc in v_compacts:
                for pos in (buckets.get("V~" + vc) or [])[:32]:
                    add_pos(pos)
            for key in smart_keys:
                for pos in (buckets.get("=" + key) or [])[:24]:
                    add_pos(pos)

        if not candidates:
            return {"status": "nomatch", "best": None, "matches": []}

        allowed = []
        autos = []
        auto_allowed = set(self._auto_allowed_source_ids() or set())
        # Exact buckets should normally produce only a handful of rows. Hard cap
        # protects the GIL if a provider has pathological duplicate IDs/names.
        for epg in candidates[:20]:
            sid = str(epg.get("source_id") or "")
            src = epg.get("__batch_src") or self._source_by_id(sid) or {}
            check = self._candidate_result(service, src, epg)
            if not check.get("allowed"):
                continue
            item = dict(epg)
            item["_precision_auto"] = bool(check.get("auto"))
            item["_precision_confidence"] = int(check.get("confidence") or 0)
            item["_precision_target_key"] = str(check.get("target_key") or "")
            item["_precision_reason"] = str(check.get("reason") or "")
            try:
                _iid = unified_channel_identity.canonical_id(pure) or ""
                _ctx_rank, _ctx_label = smart_context_engine.rank_source(service, src, _iid)
            except Exception:
                _ctx_rank, _ctx_label = 9, "ALTERNATIVE"
            try:
                _lang_rank, _lang_label = self._suggestion_language_info(item, src)
            except Exception:
                _lang_rank, _lang_label = 4, "ORIGINAL"
            item["_auto_context_rank"] = int(_ctx_rank)
            item["_auto_context_label"] = str(_ctx_label or "ALTERNATIVE")
            item["_auto_lang_rank"] = int(_lang_rank)
            item["_auto_source_active"] = bool(sid in (self.auto_selected_source_ids or set()))
            allowed.append(item)
            if bool(check.get("auto")):
                if sid not in auto_allowed:
                    continue
                autos.append(item)

        proof_rows = [{"auto": True,
                       "confidence": int(epg.get("_precision_confidence") or 0),
                       "target_key": str(epg.get("_precision_target_key") or ""),
                       "source_id": str(epg.get("source_id") or ""), "_epg": epg}
                      for epg in autos]
        # Different canonical identities remain ambiguous. Once identity is
        # proven, Source Override context chooses the best provider/feed.
        best_proof = id_mapping_engine.choose_best(proof_rows)
        best = None
        if best_proof:
            target_key = str(best_proof.get("target_key") or "")
            same_target = [epg for epg in autos if str(epg.get("_precision_target_key") or "") == target_key]
            same_target.sort(key=lambda epg: (
                int(epg.get("_auto_context_rank") or 9),
                int(epg.get("_auto_lang_rank") or 4),
                0 if epg.get("_auto_source_active") else 1,
                -int(epg.get("_precision_confidence") or 0),
                str(epg.get("source_id") or ""),
                str(epg.get("channel_id") or ""),
            ))
            best = same_target[0] if same_target else (best_proof or {}).get("_epg")
        if best:
            return {"status": "mapped", "best": best, "matches": allowed}
        if autos:
            return {"status": "ambiguous", "best": None, "matches": allowed}
        if allowed:
            return {"status": "review", "best": None, "matches": allowed}
        return {"status": "nomatch", "best": None, "matches": []}

    def _start_auto_repair_batch(self):
        """Run one conservative automatic pass over the selected satellite.

        Candidate discovery is the same targeted, cache-first path used by
        GREEN Find Matches in beta131.  Automatic writes are stricter than the
        visible suggestion list: only PrecisionMatch auto=True rows from an
        enabled Smart Source are eligible, and competing identities inside the
        precision margin are left for REVIEW.
        """
        if self._auto_repair_started or self._auto_repair_busy:
            return False
        self._auto_repair_started = True
        self._auto_repair_requested = False

        scope_services = list(getattr(self, "_all_current_bouquet_services", None) or self.bouquet_services or [])
        services = [dict(x or {}) for x in scope_services
                    if x and not self._service_is_mapped(x)]
        # Respect explicit GREEN-Unmap tombstones even when an old/stale mapping
        # view classified the service as unmapped.
        try:
            services = [svc for svc in services if not self.store.is_blocked(svc.get("ref"))]
        except Exception:
            pass

        # beta142 incremental repair: if the exact same clean compact catalogue
        # has already evaluated an unmapped service in this beta, do not burn CPU
        # repeating the same NO MATCH / REVIEW decision. A source refresh marks
        # the catalogue dirty and changes the signature, so those rows become
        # eligible automatically. Manual mappings are already excluded above.
        try:
            _source_cache_sig = str(source_channel_cache.fast_signature() or "")
        except Exception:
            _source_cache_sig = str(source_channel_cache.generation())
        _selection_sig = ",".join(sorted(self._auto_allowed_source_ids() or set()))
        current_catalog_sig = "repairv7|%s|%s" % (_source_cache_sig, _selection_sig)
        reception = str(getattr(self, "_auto_repair_current_reception", "") or self._initial_reception or
                        ((self._current("bouquets", self.bouquet_records) or {}).get("reception_list") or "Satellite"))
        previous_skipped = 0
        previous_review_refs = []
        previous_ambiguous_refs = []
        previous_no_match_refs = []
        previous_scanned_refs = []
        try:
            snap = smart_mapping_warm_cache.snapshot() or {}
            prev = dict(((snap.get("repair_results") or {}).get(reception) or {}))
            prev_sig = str(prev.get("catalog_signature") or "")
            if current_catalog_sig and prev_sig == current_catalog_sig:
                eligible_refs = set(str((svc or {}).get("ref") or "") for svc in services if str((svc or {}).get("ref") or ""))
                old_refs = set(str(x or "") for x in (prev.get("scanned_refs") or []) if str(x or "")) & eligible_refs
                previous_scanned_refs = list(old_refs)
                previous_review_refs = [str(x) for x in (prev.get("review_refs") or []) if str(x or "") in eligible_refs]
                previous_ambiguous_refs = [str(x) for x in (prev.get("ambiguous_refs") or []) if str(x or "") in eligible_refs]
                previous_no_match_refs = [str(x) for x in (prev.get("no_match_refs") or []) if str(x or "") in eligible_refs]
                if old_refs:
                    keep = []
                    for svc in services:
                        ref = str((svc or {}).get("ref") or "")
                        if ref and ref in old_refs:
                            previous_skipped += 1
                        else:
                            keep.append(svc)
                    services = keep
        except Exception:
            previous_skipped = 0
            previous_review_refs = []
            previous_ambiguous_refs = []
            previous_no_match_refs = []
            previous_scanned_refs = []
        total = len(services)
        self._auto_repair_progress = {"done": 0, "total": total, "mapped": 0, "review": 0, "nomatch": 0}
        self._auto_repair_result_rows = []
        self._auto_results_painted_count = -1
        if not total:
            cached_exceptions = len(previous_review_refs) + len(previous_ambiguous_refs) + len(previous_no_match_refs)
            self._set_repair_result_state(previous_review_refs, previous_ambiguous_refs, previous_no_match_refs)
            self["summary"].setText("AUTO REPAIR • 0 new scan • %d unchanged checked • %d exception(s) kept" % (previous_skipped, cached_exceptions))
            self["status_label"].setText("AUTO REPAIR • COMPLETE • CACHE HIT")
            self._auto_search_done = True
            self._auto_repair_progress = {"done": 0, "total": 0, "mapped": 0,
                                          "review": len(previous_review_refs) + len(previous_ambiguous_refs),
                                          "nomatch": len(previous_no_match_refs)}
            self._auto_repair_live = {"channel": "No new/changed unmapped channel", "phase": "CACHE HIT",
                                      "match": "%d previous result(s) reused • no matching CPU work" % previous_skipped,
                                      "index": 0, "total": 0}
            self._update_auto_search_overlay()
            try:
                self["catalog_progress"].setValue(100); self["catalog_progress_text"].setText("100%")
            except Exception:
                pass
            return False

        self._auto_repair_token = int(getattr(self, "_auto_repair_token", 0) or 0) + 1
        token = self._auto_repair_token
        self._auto_repair_result = None
        self._auto_repair_busy = True
        self["summary"].setText("AUTO REPAIR %s • %d new/unmapped • %d unchanged skipped • SAFE/PROVEN only" % (reception, total, previous_skipped))
        self["status_label"].setText("AUTO REPAIR • STARTING")
        self._auto_repair_live = {"channel": "Starting search", "phase": "PREPARING",
                                  "match": "%d queued • %d unchanged results skipped" % (total, previous_skipped),
                                  "index": 0, "total": total}
        self._update_auto_search_overlay()
        try:
            self["catalog_progress"].setValue(1); self["catalog_progress_text"].setText("1%")
        except Exception:
            pass

        def worker():
            records = []
            review_refs = []
            no_match_refs = []
            ambiguous_refs = []
            error = ""
            try:
                batch_started = time.time()
                # beta141: Auto Repair is strictly CACHE-FIRST. Never wait at
                # 0/NN for network/catalogue refresh. The persistent Smart name
                # index is reused directly; a cold-cache fallback indexes only a
                # bounded set of relevant prepared source shards.
                bootstrap = {"fetched": 0, "rows": 0, "attempted": 0, "errors": 0}
                self._auto_repair_live = {"channel": "Current EPG channel IDs", "phase": "LOCAL ID INDEX",
                                          "match": "Local hints + compact Channel-ID caches only…",
                                          "index": 0, "total": total}
                batch = self._build_auto_batch_index(services)
                self._auto_repair_live = {"channel": "%d current EPG IDs • %d source(s)" % (
                                              int(batch.get("rows_total") or 0), int(batch.get("prepared_sources") or 0)),
                                          "phase": "READY",
                                          "match": "Current source snapshot ready • starting receiver scan",
                                          "index": 0, "total": total}

                for idx, service in enumerate(services):
                    if int(getattr(self, "_auto_repair_token", 0) or 0) != token:
                        return
                    self._auto_repair_live = {
                        "channel": str(service.get("name") or "Unnamed channel"),
                        "phase": "SEARCHING", "match": "Fast lookup in prepared EPG index…",
                        "index": idx + 1, "total": total,
                    }
                    verdict = self._auto_batch_match(service, batch)
                    status = str((verdict or {}).get("status") or "nomatch")
                    best = (verdict or {}).get("best")
                    matches = list((verdict or {}).get("matches") or [])

                    if best:
                        records.append({
                            "source_id": str(best.get("source_id") or ""),
                            "channel_id": str(best.get("channel_id") or ""),
                            "display_name": str(best.get("display_name") or best.get("channel_id") or ""),
                            "refs": [str(service.get("ref") or "")],
                            "confidence": int(best.get("_precision_confidence") or 0),
                            "reason": str(best.get("_precision_reason") or ""),
                        })
                    elif status == "ambiguous":
                        ambiguous_refs.append(str(service.get("ref") or ""))
                    elif status == "review" or matches:
                        review_refs.append(str(service.get("ref") or ""))
                    else:
                        no_match_refs.append(str(service.get("ref") or ""))

                    row_name = str(service.get("name") or "Channel")
                    if best:
                        target = str(best.get("display_name") or best.get("channel_id") or "EPG")
                        _line = "SAFE  %s  →  %s  •  %s" % (
                            row_name, target, str(best.get("source_name") or best.get("source_id") or "source"))
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "SAFE MATCH", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name, "status": "SAFE", "target": target}
                    elif status == "ambiguous":
                        _line = "AMBIGUOUS  %s  • manual review kept" % row_name
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "REVIEW", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name, "status": "AMBIGUOUS", "target": ""}
                    elif status == "review" or matches:
                        _line = "REVIEW  %s  • manual mapping required" % row_name
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "REVIEW", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name, "status": "REVIEW", "target": ""}
                    else:
                        _line = "NO MATCH  %s" % row_name
                        self._auto_repair_live = {"channel": row_name,
                            "phase": "NO MATCH", "match": _line, "index": idx + 1, "total": total}
                        row = {"ref": str(service.get("ref") or ""), "name": row_name, "status": "NO MATCH", "target": ""}

                    # Append in-place: the previous ``list + [row]`` copied the
                    # whole result list for every channel (O(n²) churn on 300+
                    # service satellites). Keep only six recent text rows.
                    self._auto_repair_recent.append(_line)
                    if len(self._auto_repair_recent) > 6:
                        del self._auto_repair_recent[:-6]
                    self._auto_repair_result_rows.append(row)
                    self._auto_repair_progress = {
                        "done": idx + 1, "total": total,
                        "mapped": len(records),
                        "review": len(review_refs) + len(ambiguous_refs),
                        "nomatch": len(no_match_refs),
                    }
                    # Yield in tiny micro-batches so the Zero 4K never sees a
                    # long CPU burst. The total added delay is only a few dozen
                    # milliseconds even on 300-800 channel satellites.
                    if (idx + 1) % 32 == 0:
                        time.sleep(0.002)

                # beta147: Auto Repair is strictly OFFLINE once it starts.
                # Never enter the old 96%% "ID CACHE FILL" network phase.
                # Missing remote catalogues are prepared by direct-URL Smart Mapping
                # actions/background maintenance, while Auto Repair itself uses only
                # local hints and compact cache shards. This guarantees
                # bounded run time and low CPU/network use on Vu+ Zero 4K.
                bootstrap = {"fetched": 0, "rows": 0, "attempted": 0, "errors": 0}

                batch_elapsed = max(0.0, time.time() - batch_started)
                if int(getattr(self, "_auto_repair_token", 0) or 0) != token:
                    return
                # rc47 DRY-RUN FIRST: discovery never changes persisted mappings.
                # The UI shows the result summary and asks the user to APPLY the
                # SAFE/PROVEN winners explicitly. This removes silent mapping
                # changes and makes Auto Mapping fully user-controlled.
                save_result = {"dry_run": True, "changed": 0, "records": len(records),
                               "refs": sum(len((x or {}).get("refs") or []) for x in records)}
                # Preserve unchanged exception results from the previous run.
                # They were skipped only because the source-index signature is
                # identical; dropping them here would make incremental repair lose
                # its persistent unresolved-state cache.
                merged_review = list(dict.fromkeys(list(previous_review_refs) + list(review_refs)))
                merged_ambiguous = list(dict.fromkeys(list(previous_ambiguous_refs) + list(ambiguous_refs)))
                merged_nomatch = list(dict.fromkeys(list(previous_no_match_refs) + list(no_match_refs)))
                scanned_now = [str((svc or {}).get("ref") or "") for svc in services if str((svc or {}).get("ref") or "")]
                merged_scanned = list(dict.fromkeys(list(previous_scanned_refs) + scanned_now))
                result = {
                    "token": token, "reception": reception, "records": records,
                    "mapped": len(records), "review": len(merged_review),
                    "ambiguous": len(merged_ambiguous), "nomatch": len(merged_nomatch),
                    "review_refs": merged_review, "ambiguous_refs": merged_ambiguous,
                    "no_match_refs": merged_nomatch,
                    "save": save_result, "error": "",
                    "elapsed": float(batch_elapsed),
                    "prepared_sources": int(batch.get("prepared_sources") or 0),
                    "indexed_rows": int(batch.get("rows_total") or 0),
                    "scanned_refs": merged_scanned,
                    "catalogs_fetched": int((bootstrap or {}).get("fetched") or 0),
                    "catalog_signature": ("repairv7|%s|%s" % (
                        str(source_channel_cache.fast_signature() or ""),
                        ",".join(sorted(self._auto_allowed_source_ids() or set())))),
                    "previous_skipped": int(previous_skipped),
                    "dry_run": True, "confirmed": False,
                }
            except Exception as exc:
                log.exception("Automatic satellite Mapping Repair failed")
                result = {"token": token, "reception": reception, "records": records,
                          "mapped": 0, "review": len(review_refs),
                          "ambiguous": len(ambiguous_refs), "nomatch": len(no_match_refs),
                          "review_refs": list(review_refs), "ambiguous_refs": list(ambiguous_refs),
                          "no_match_refs": list(no_match_refs),
                          "save": {}, "error": str(exc)}
            if int(getattr(self, "_auto_repair_token", 0) or 0) == token:
                self._auto_repair_result = result

        threading.Thread(target=worker, daemon=True).start()
        try:
            self._timer.start(120, False)
        except Exception:
            pass
        return True

    def _auto_dryrun_decision(self, yes):
        result = getattr(self, "_pending_auto_dryrun", None) or {}
        self._pending_auto_dryrun = None
        if not result:
            return
        if not yes:
            self._auto_search_done = True
            self["status_label"].setText("DRY RUN COMPLETE • NOTHING CHANGED")
            self["summary"].setText("DRY RUN only • %d safe • %d review • %d ambiguous • %d no match • mappings unchanged" % (
                int(result.get("mapped") or 0), int(result.get("review") or 0),
                int(result.get("ambiguous") or 0), int(result.get("nomatch") or 0)))
            self._auto_repair_live = {"channel": "Dry-run complete", "phase": "NOT APPLIED",
                                      "match": "No mapping changed • GREEN can search again",
                                      "index": int((self._auto_repair_progress or {}).get("total") or 0),
                                      "total": int((self._auto_repair_progress or {}).get("total") or 0)}
            self._update_action_labels(); self._update_auto_search_overlay()
            return
        try:
            records = list(result.get("records") or [])
            result["save"] = self.store.bulk_assign_auto_exclusive(
                records, label="Auto Repair %s" % str(result.get("reception") or "Satellite")) if records else {"changed":0,"records":0,"refs":0}
            result["confirmed"] = True
            result["dry_run"] = True
            # Reuse the normal completion painter on the UI thread. No worker
            # restart and no second candidate scan is required.
            self._auto_repair_result = result
            self._poll_auto_repair_batch()
        except Exception as exc:
            self["status_label"].setText("APPLY FAILED • MAPPINGS PRESERVED")
            self["summary"].setText("Dry-run was safe; apply failed: %s" % str(exc)[:100])
            self.session.open(MessageBox, "Auto Mapping apply failed safely:\n%s" % exc, MessageBox.TYPE_ERROR)

    def _poll_auto_repair_batch(self):
        result = self._auto_repair_result
        if result is None:
            return
        self._auto_repair_result = None
        self._auto_repair_busy = False
        if int((result or {}).get("token") or -1) != int(getattr(self, "_auto_repair_token", 0) or 0):
            return
        error = str((result or {}).get("error") or "")
        if error:
            self["status_label"].setText("AUTO REPAIR • ERROR")
            self["summary"].setText("Auto Repair failed safely • no unsafe fallback was applied")
            if not self._smart_auto_repair_silent:
                self.session.open(MessageBox, "Auto Repair failed.\n\n%s" % error, MessageBox.TYPE_ERROR)
            try:
                if self._smart_auto_repair_active_key is not None:
                    self._smart_auto_repair_seen.discard(self._smart_auto_repair_active_key)
            except Exception:
                pass
            self._smart_auto_repair_silent = False
            self._smart_auto_repair_active_key = None
            self._auto_repair_current_reception = ""
            if self._smart_auto_repair_pending:
                try: self._timer.start(40, True)
                except Exception: pass
            return

        # DRY-RUN result: stop here until the user explicitly confirms APPLY.
        if bool((result or {}).get("dry_run")) and not bool((result or {}).get("confirmed")):
            self._pending_auto_dryrun = result
            mapped = int((result or {}).get("mapped") or 0)
            review = int((result or {}).get("review") or 0)
            ambiguous = int((result or {}).get("ambiguous") or 0)
            nomatch = int((result or {}).get("nomatch") or 0)
            self["status_label"].setText("DRY RUN COMPLETE • REVIEW BEFORE APPLY")
            self["summary"].setText("DRY RUN • %d safe • %d review • %d ambiguous • %d no match • 0 changes" % (
                mapped, review, ambiguous, nomatch))
            self._auto_repair_live = {"channel": "Dry-run complete", "phase": "WAITING FOR APPLY",
                                      "match": "%d SAFE/PROVEN mappings ready • no change yet" % mapped,
                                      "index": int((self._auto_repair_progress or {}).get("total") or 0),
                                      "total": int((self._auto_repair_progress or {}).get("total") or 0)}
            self._update_auto_search_overlay()
            self.session.openWithCallback(self._auto_dryrun_decision, MessageBox,
                "AUTO MAPPING DRY-RUN COMPLETE\n\nSAFE / PROVEN: %d\nManual review: %d\nAmbiguous: %d\nNo match: %d\n\nNo mapping has been changed.\n\nApply the %d SAFE / PROVEN mappings now?" % (
                    mapped, review, ambiguous, nomatch, mapped),
                MessageBox.TYPE_YESNO, default=False)
            return

        records = list((result or {}).get("records") or [])
        # If Auto Repair used a prepared strategic sibling feed that was not
        # selected yet, enable only the concrete feeds that actually won a SAFE
        # mapping. This prevents a saved mapping pointing at an OFF source.
        try:
            for _sid in sorted(set(str((x or {}).get("source_id") or "") for x in records if str((x or {}).get("source_id") or ""))):
                self._activate_mapping_source_if_needed(_sid)
        except Exception:
            log.exception("Could not activate one or more automatic mapping sources")
        # Paint the new ownership immediately without a full receiver-wide
        # validation. A satellite-scoped validation is then scheduled in the
        # background to refresh Mapping Health / warm cache.
        for row in records:
            sid = str((row or {}).get("source_id") or "")
            cid = str((row or {}).get("channel_id") or "")
            info = {"source_id": sid, "channel_id": cid,
                    "display_name": str((row or {}).get("display_name") or cid),
                    "mode": "auto-repair",
                    "confidence": int((row or {}).get("confidence") or 0),
                    "reason": str((row or {}).get("reason") or "")}
            if sid and cid:
                self._mapped_epg_keys.add(self.store._key(sid, cid))
            for ref in (row or {}).get("refs") or []:
                for key in self._ref_lookup_keys(ref):
                    self._mapped_refs.add(key)
                    current = [x for x in (self._mapped_info_by_ref.get(key) or [])
                               if str((x or {}).get("mode") or "").lower() == "manual"]
                    current.append(dict(info))
                    self._mapped_info_by_ref[key] = current
        self.mapping_revision += 1
        self.selection_cache.clear()
        self._smart_name_hint_cache.clear()
        self._set_repair_result_state((result or {}).get("review_refs") or [],
                                      (result or {}).get("ambiguous_refs") or [],
                                      (result or {}).get("no_match_refs") or [])
        _keep_ref = ""
        if self._smart_auto_repair_silent:
            try:
                _keep_ref = str((self._current("channels", self.bouquet_services) or {}).get("ref") or "")
            except Exception:
                _keep_ref = ""
        self.refresh_channels()
        if _keep_ref:
            _wanted = set(self._ref_lookup_keys(_keep_ref))
            for _i, _svc in enumerate(self.bouquet_services or []):
                if _wanted.intersection(self._ref_lookup_keys((_svc or {}).get("ref"))):
                    self._set_index("channels", _i)
                    break
        elif not self._smart_auto_repair_silent:
            self._set_index("channels", 0)
        try:
            self["catalog_progress"].setValue(100); self["catalog_progress_text"].setText("100%")
        except Exception:
            pass

        mapped = int((result or {}).get("mapped") or 0)
        review = int((result or {}).get("review") or 0)
        ambiguous = int((result or {}).get("ambiguous") or 0)
        nomatch = int((result or {}).get("nomatch") or 0)
        elapsed = float((result or {}).get("elapsed") or 0.0)
        self["status_label"].setText("AUTO REPAIR COMPLETE • %d MAPPED • %.1fs" % (mapped, elapsed))
        self["summary"].setText("AUTO REPAIR COMPLETE • %.1fs • %d mapped • %d manual • %d ambiguous • %d no match" % (
            elapsed, mapped, review, ambiguous, nomatch))
        self._auto_search_done = True
        self._auto_repair_live = {
            "channel": "Search complete", "phase": "COMPLETE",
            "match": "%.1fs • %d SAFE mapped • %d manual • %d ambiguous • %d no match" % (elapsed, mapped, review, ambiguous, nomatch),
            "index": int((self._auto_repair_progress or {}).get("total") or 0),
            "total": int((self._auto_repair_progress or {}).get("total") or 0),
        }
        self._update_action_labels()
        self._update_auto_search_overlay()

        # beta133: publish the selected satellite's current ownership directly
        # from the already-updated in-memory map. Do not launch another
        # PrecisionMatch sweep immediately after the search; that used to make
        # small Vu+ boxes feel stuck just when Auto Repair had finished.
        if (self._repair_mode or self._smart_auto_repair_silent) and str((result or {}).get("reception") or ""):
            try:
                wanted = str((result or {}).get("reception") or self._initial_reception or "")
                mapped_refs = []
                mapped_info = {}
                for svc in self.catalog or []:
                    label = svc.get("reception_list") or channel_registry._orbital_label(svc.get("orbital"))
                    if str(label or "") != wanted:
                        continue
                    ref = str(svc.get("ref") or "").strip()
                    if ref and self._service_is_mapped(svc):
                        mapped_refs.append(ref)
                        infos = self._mapping_infos_for_ref(ref)
                        if infos:
                            mapped_info[ref] = [dict(x or {}) for x in infos]
                        try:
                            canon = channel_registry.canonical_service_ref(ref) or ref.rstrip(":")
                            if canon and canon != ref:
                                mapped_info[canon] = [dict(x or {}) for x in infos]
                        except Exception:
                            pass
                smart_mapping_warm_cache.update_repair_view(wanted, {
                    "mapped_refs": mapped_refs,
                    "mapped_info_by_ref": mapped_info,
                    "mapped_epg_keys": list(self._mapped_epg_keys or []),
                })
                smart_mapping_warm_cache.update_repair_result(wanted, {
                    "review_refs": list((result or {}).get("review_refs") or []),
                    "ambiguous_refs": list((result or {}).get("ambiguous_refs") or []),
                    "no_match_refs": list((result or {}).get("no_match_refs") or []),
                    "scanned_refs": list((result or {}).get("scanned_refs") or []),
                    "catalog_signature": str((result or {}).get("catalog_signature") or ""),
                    "previous_skipped": int((result or {}).get("previous_skipped") or 0),
                    "mapped": mapped, "review": review, "ambiguous": ambiguous, "nomatch": nomatch,
                    "timestamp": int(time.time()),
                })
                smart_mapping_warm_cache.update_ignored_refs(self.store.ignored_refs())
            except Exception:
                pass
        if not self._auto_search_mode and not self._smart_auto_repair_silent:
            self.session.open(MessageBox,
                "Automatic repair finished for %s.\n\n%d channel(s) mapped automatically.\n%d candidate(s) need review.\n%d ambiguous automatic candidate(s) were left untouched.\n%d channel(s) have no prepared match.\n\nThe channel list now shows only what still needs manual work." % (
                    str((result or {}).get("reception") or "satellite"), mapped, review, ambiguous, nomatch),
                MessageBox.TYPE_INFO)

        self._smart_auto_repair_silent = False
        self._smart_auto_repair_active_key = None
        self._auto_repair_current_reception = ""
        if self._smart_auto_repair_pending:
            try:
                self._timer.start(40, True)
            except Exception:
                pass

    def _set_auto_search_visible(self, visible):
        names = ("auto_search_bg", "auto_search_caption", "auto_search_current",
                 "auto_search_match", "auto_search_stats", "auto_search_recent",
                 "auto_search_results_bg", "auto_search_results_title",
                 "auto_search_results_legend", "auto_search_results",
                 "auto_search_results_count")
        normal = ("column_bar", "hdr_bouquet", "hdr_channel", "hdr_source", "hdr_selection",
                  "column_rule", "pane1_bg", "pane2_bg", "pane3_bg", "pane4_bg",
                  "bouquets", "channels", "sources", "selections",
                  "focus1", "focus2", "focus3", "focus4")
        for name in names:
            try:
                (self[name].instance.show if visible else self[name].instance.hide)()
            except Exception:
                pass
        for name in normal:
            try:
                (self[name].instance.hide if visible else self[name].instance.show)()
            except Exception:
                pass

    def _apply_smart_auto_repair_layout(self):
        if self._repair_mode or self._auto_search_mode:
            return
        self["key_green"].setText("Auto Mapping")
        try:
            self["key_green"].instance.show()
            self["key_green_bar"].instance.show()
        except Exception:
            pass

    def _apply_auto_search_layout(self):
        self._set_auto_search_visible(bool(self._auto_search_mode))
        if self._auto_search_mode:
            self["title"].setText("AUTO MAPPING SEARCH")
            self["subtitle"].setText("DRY-RUN search • selected satellite • no mapping changes until APPLY")
            self["status_caption"].setText("AUTO SEARCH")
            self["match_caption"].setText("SATELLITE / CURRENT CHANNEL")
            self._update_auto_search_overlay()
            self._update_action_labels()

    def _update_auto_search_overlay(self):
        if not self._auto_search_mode:
            return
        live = dict(getattr(self, "_auto_repair_live", {}) or {})
        progress = dict(getattr(self, "_auto_repair_progress", {}) or {})
        reception = str(self._initial_reception or "Selected satellite")
        done = int(progress.get("done") or live.get("index") or 0)
        total = int(progress.get("total") or live.get("total") or 0)
        mapped = int(progress.get("mapped") or 0)
        review = int(progress.get("review") or 0)
        nomatch = int(progress.get("nomatch") or 0)
        if self._auto_search_done:
            caption = "%s  •  SEARCH COMPLETE" % reception
        elif self._auto_repair_busy:
            caption = "%s  •  SEARCHING CHANNEL %d / %d" % (reception, done, total)
        else:
            caption = "%s  •  PREPARING AUTOMATIC SEARCH" % reception
        self["auto_search_caption"].setText(caption)
        channel = str(live.get("channel") or "Preparing receiver/source cache…")
        phase = str(live.get("phase") or "PREPARING")
        self["auto_search_current"].setText("%s\n%s" % (phase, channel))
        self["auto_search_match"].setText(str(live.get("match") or "Each unmapped channel is searched against compatible prepared EPG sources. No unsafe fuzzy match is applied automatically."))
        self["auto_search_stats"].setText("%d / %d scanned   •   %d SAFE mapped   •   %d manual   •   %d no match" % (done, total, mapped, review, nomatch))
        self["auto_search_recent"].setText("\n".join(list(getattr(self, "_auto_repair_recent", []) or [])[-4:]))
        try:
            rows = list(getattr(self, "_auto_repair_result_rows", []) or [])
            painted = int(getattr(self, "_auto_results_painted_count", -1))
            # Rebuilding a MultiContent list for every single fast match creates
            # more CPU work than the matcher itself. Paint in small chunks while
            # keeping the live counter exact; always paint the final chunk.
            if painted < 0 or len(rows) >= painted + 8 or (total and len(rows) >= total):
                self["auto_search_results"].update_rows(rows)
                self._auto_results_painted_count = len(rows)
            self["auto_search_results_count"].setText("%d / %d scanned" % (len(rows), total))
        except Exception:
            pass

    def _leave_auto_search_for_filter(self, filter_mode):
        if self._auto_repair_busy:
            self["summary"].setText("Automatic search is still running • RED cancels safely")
            return
        self._auto_search_mode = False
        self._set_auto_search_visible(False)
        self["title"].setText("MAPPING REPAIR")
        self["subtitle"].setText("Satellite → Channel → Source → Safe EPG Match  •  manual correction workspace")
        try:
            self.set_channel_filter(filter_mode)
        except Exception:
            pass
        self.focus = 1
        self.refresh_channels()
        self.update_focus()
        self._update_action_labels()

    def yellow_pressed(self):
        """YELLOW is the fast Source / Details entry point."""
        if getattr(self, "_auto_repair_busy", False):
            self["summary"].setText("AUTO Mapping is running • source details remain cache-only")
        return self.open_manual_source_override()

    def open_reset_ids_menu(self):
        """Protected Reset IDs menu moved to BLUE -> More in rc74."""
        if getattr(self, "_sid_reset_running", False):
            self.session.open(MessageBox, "SID reset is already running.", MessageBox.TYPE_INFO, timeout=4)
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = [
                ("Reset AUTO mappings only • keep MANUAL / LOCKED (recommended)", "auto_only"),
                ("FULL RESET all IDs • backup + fresh rebuild/import", "full"),
                ("Cancel", "cancel"),
            ]
            self.session.openWithCallback(self._yellow_reset_selected, ChoiceBox,
                                          title="RESET EPG IDs", list=choices)
        except Exception:
            return self.reset_all_sids_and_rebuild()

    def _yellow_reset_selected(self, answer):
        if not answer:
            return
        key = answer[1]
        if key == "auto_only":
            self.session.openWithCallback(self._confirm_reset_auto_only, MessageBox,
                "Reset only automatic mappings?\n\nMANUAL / LOCKED mappings are preserved.\nAuto Repair ownership and generated AUTO refs will be removed.\nYou can run Auto Mapping again afterwards.",
                MessageBox.TYPE_YESNO, default=False)
        elif key == "full":
            self.reset_all_sids_and_rebuild()

    def _confirm_reset_auto_only(self, yes):
        if not yes:
            return
        try:
            stats = self.store.reset_auto_mappings()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self._smart_name_hint_cache.clear()
            try:
                smart_mapping_warm_cache.clear_repair_result()
                smart_mapping_warm_cache.invalidate()
            except Exception:
                pass
            self._rebuild_mapping_cache()
            self.refresh_channels()
            self._set_index("channels", 0)
            self.update_focus()
            self._update_action_labels()
            self["summary"].setText("AUTO IDs reset • %d removed • %d manual/locked preserved" % (
                int(stats.get("removed_mappings") or 0), int(stats.get("manual_preserved") or 0)))
            self.session.open(MessageBox,
                "AUTO mappings reset completed.\n\nRemoved: %d\nManual / locked preserved: %d\n\nNo bouquet, lamedb or tuner setting was changed." % (
                    int(stats.get("removed_mappings") or 0), int(stats.get("manual_preserved") or 0)),
                MessageBox.TYPE_INFO, timeout=6)
        except Exception as exc:
            self.session.open(MessageBox, "AUTO reset failed safely:\n%s" % exc, MessageBox.TYPE_ERROR)

    def blue_pressed(self):
        if self._auto_search_mode:
            return self._leave_auto_search_for_filter("auto")
        return self.open_mapping_options()

    def _start_optional_auto_mapping(self):
        """Enter Auto Mapping only after the user explicitly presses GREEN."""
        if self._auto_repair_busy:
            self["summary"].setText("AUTO Mapping is already running")
            return False
        reception = self._current_reception_label() or str(self._initial_reception or "").strip()
        if not reception:
            self["summary"].setText("Select a satellite / reception first, then press GREEN Auto Mapping")
            return False
        self._auto_search_mode = True
        self._auto_search_done = False
        self._auto_repair_started = False
        # rc46: entering the Auto Mapping screen does NOT launch work by itself.
        # GREEN is now a genuine Start Search button.
        self._auto_repair_requested = False
        self._auto_repair_current_reception = reception
        self._initial_reception = reception
        # The reception came from the CURRENT highlighted row, so there is no
        # need to wait for the initial-reception timer to select it again.
        self._initial_reception_applied = True
        self._auto_repair_recent = []
        self._auto_repair_result_rows = []
        self._auto_results_painted_count = -1
        self._auto_repair_live = {
            "phase": "PREPARING",
            "channel": "Selected reception: %s" % reception,
            "match": "SAFE / PROVEN only • manual/locked mappings are preserved",
        }
        self._set_auto_search_visible(True)
        self["title"].setText("AUTO MAPPING SEARCH")
        self["subtitle"].setText("Optional Auto Mapping • DRY-RUN first • explicit APPLY only")
        self["status_caption"].setText("AUTO SEARCH")
        self["match_caption"].setText("SATELLITE / CURRENT CHANNEL")
        self._update_auto_search_overlay()
        self._update_action_labels()
        try:
            self._timer.start(30, True)
        except Exception:
            pass
        return True

    def green_pressed(self):
        """Stable GREEN semantics: find/apply, never delete.

        Destructive Unmap moved to BLUE → More.  This keeps the primary remote
        button predictable across the whole Smart Mapping workflow.
        """
        if self._auto_search_mode:
            if getattr(self, "_auto_repair_busy", False):
                self["summary"].setText("AUTO SEARCH is running • RED cancels safely")
                return
            if self._auto_search_done:
                # GREEN repeats the search only on what remains unmapped.
                self._auto_search_done = False
                self._auto_repair_started = False
                self._auto_repair_requested = True
                self._auto_repair_recent = []
                self._auto_repair_result_rows = []
                self._auto_results_painted_count = -1
                self._auto_repair_live = {"phase": "PREPARING", "channel": "Remaining unmapped channels", "match": "Restarting SAFE search…"}
                try: self._timer.start(30, True)
                except Exception: pass
                self._update_auto_search_overlay()
                return
            # rc46: START SEARCH must perform a real action.  If the receiver
            # snapshot is ready, launch the batch immediately; otherwise queue it
            # and the warm-up timer will start it as soon as the snapshot arrives.
            self._auto_repair_requested = True
            if self._receiver_snapshot_ready:
                if not self._initial_reception or self._initial_reception_applied:
                    if not self._initial_filter or self._initial_filter_applied:
                        if self._start_auto_repair_batch():
                            self["summary"].setText("AUTO SEARCH started • SAFE / PROVEN only")
                            self._update_action_labels()
                            return
            self["summary"].setText("START SEARCH queued • preparing receiver snapshot…")
            try: self._timer.start(30, True)
            except Exception: pass
            return
        if getattr(self, "_auto_repair_busy", False):
            self["summary"].setText("AUTO Mapping Repair is running in background • manual mappings stay locked")
            return
        # GREEN applies a manually selected SAFE row when pane 4 is focused;
        # everywhere else it starts the OPTIONAL Auto Mapping workflow.
        if self.focus == 3:
            epg = self._current("selections", self.selection_entries)
            if epg and not epg.get("separator"):
                return self.assign_current()
        return self._start_optional_auto_mapping()

    def _update_action_labels(self):
        if self._auto_search_mode:
            try:
                if self._auto_repair_busy:
                    self["key_green"].setText("Searching…")
                    self["key_yellow"].setText("Source / Details")
                    self["key_blue"].setText("Review Auto")
                elif self._auto_search_done:
                    self["key_green"].setText("Search Remaining Again")
                    self["key_yellow"].setText("Source / Details")
                    self["key_blue"].setText("Review Auto")
                else:
                    self["key_green"].setText("Start Search")
                    self["key_yellow"].setText("Source / Details")
                    self["key_blue"].setText("Review Auto")
                self["key_red"].setText("Cancel / Back")
            except Exception:
                pass
            return
        # rc45: Auto Mapping is explicitly optional. GREEN starts it from the
        # normal workspace, while pane 4 GREEN/OK still applies the selected row.
        service = self._current("channels", self.bouquet_services)
        mapped = bool(service and self._service_is_mapped(service))
        remap_count = len(getattr(self, "_remap_scope_refs", []) or [])
        green = "Auto Mapping"
        if remap_count:
            state = "MANUAL CHANGE MODE"
        elif self.focus == 3 and self._current("selections", self.selection_entries) and not (self._current("selections", self.selection_entries) or {}).get("separator"):
            state = "MATCH READY • OK TO APPLY"
        elif mapped:
            state = "READY • AUTO MAPPING OPTIONAL"
        else:
            state = "READY • AUTO MAPPING OPTIONAL"
        try:
            lstate = str(getattr(self, "_lamedb_state", "") or "").upper()
            if lstate in ("CURRENT", "UPDATED"):
                state += " • LAMEDB %s" % lstate
            elif lstate == "UNKNOWN":
                state += " • LAMEDB ?"
            self["key_green"].setText(green)
            self["key_yellow"].setText("Source / Details")
            self["key_blue"].setText("More")
            self["status_label"].setText(state)
        except Exception:
            pass

    def ok_pressed(self):
        if self.focus == 0 and self._toggle_current_bouquet():
            return
        if self.focus in (0, 1, 2):
            self.focus_right()
            return
        self.assign_current()

    def _activate_mapping_source_if_needed(self, source_id):
        """Ensure a manually chosen source is also enabled for import/mapping.

        A mapping to an OFF source is functionally useless because the source may
        never be imported.  beta80 activates only the exact concrete feed the
        user just confirmed, applying the existing AR/EN sibling policy.
        """
        sid = str(source_id or "")
        if not sid:
            return False
        try:
            item = source_catalog.by_mapping_id().get(sid) or source_catalog.by_catalogue_id().get(sid)
            if not item:
                return False
            cid = source_catalog.catalogue_source_id(item)
            mid = source_catalog.mapping_source_id(item)
            selected = list(self.source_preferences.selected_catalogue_ids() or [])
            changed = False
            if cid and cid not in selected:
                selected.append(cid)
                self.source_preferences.set_selected_catalogue_ids(selected, save=True, explicit_choice=cid)
                changed = True
            # Re-read the one canonical selection after activation.
            self.auto_selected_source_ids = set(source_variant_policy.normalize_selected(
                self.source_preferences.selected_mapping_ids()))
            self.auto_source_filter_active = bool(self.auto_selected_source_ids)
            return changed
        except Exception:
            log.exception("Could not activate mapping source %s", sid)
            return False

    def _queue_manual_generated_sync(self, result):
        """Serialize slow derived channels.xml writes away from the UI thread."""
        job = dict((result or {}).get("sync_job") or {})
        if not job:
            return
        try:
            with self._manual_sync_lock:
                self._manual_sync_queue.append(job)
                if self._manual_sync_busy:
                    return
                self._manual_sync_busy = True
        except Exception:
            self._manual_sync_queue.append(job)
            if self._manual_sync_busy:
                return
            self._manual_sync_busy = True

        def worker():
            while True:
                try:
                    with self._manual_sync_lock:
                        if not self._manual_sync_queue:
                            self._manual_sync_busy = False
                            break
                        current = self._manual_sync_queue.pop(0)
                except Exception:
                    if not self._manual_sync_queue:
                        self._manual_sync_busy = False
                        break
                    current = self._manual_sync_queue.pop(0)
                backups = []
                try:
                    backups = self.store.sync_generated_assignment(current) or []
                except Exception:
                    log.exception("Deferred manual channels.xml sync failed")
                try:
                    from twisted.internet import reactor
                    reactor.callFromThread(self._finish_manual_generated_sync, current.get("history_id"), backups)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_manual_generated_sync(self, history_id, backups):
        try:
            if history_id and backups:
                self.store.attach_generated_backups(history_id, backups)
        except Exception:
            log.exception("Could not attach deferred mapping backups")

    def _paint_manual_mapping_fast(self, refs, info):
        """Update mapping ownership and visible receiver rows without a global rebuild."""
        refs = [str(x or "").strip() for x in (refs or []) if str(x or "").strip()]
        # Remove stale EPG-key markers only when their old record no longer owns refs.
        for ref in refs:
            for old in self._mapping_infos_for_ref(ref):
                try:
                    saved = self.store.get(old.get("source_id"), old.get("channel_id")) or {}
                    if not (saved.get("refs") or []):
                        self._mapped_epg_keys.discard(self.store._key(old.get("source_id"), old.get("channel_id")))
                except Exception:
                    pass
        self._mapped_epg_keys.add(self.store._key(info.get("source_id"), info.get("channel_id")))
        for ref in refs:
            for key in self._ref_lookup_keys(ref):
                self._mapped_refs.add(key)
                self._mapped_info_by_ref[key] = [dict(info)]
        idx = self._get_index("channels")
        self._sat_channel_row_cache.clear()
        self._paint_channel_rows_cached()
        self._set_index("channels", idx)
        self._paint_selected_programme_fast()

    def assign_current(self):
        service = self._current("channels", self.bouquet_services)
        epg = self._current("selections", self.selection_entries)
        if not service:
            self.session.open(MessageBox, "Select a bouquet channel first.", MessageBox.TYPE_INFO)
            return
        if not epg or epg.get("separator"):
            return
        try:
            remap_refs = list(getattr(self, "_remap_scope_refs", []) or [])
            refs = remap_refs or [service.get("ref")]
            refs = [str(x or "").strip() for x in refs if str(x or "").strip()]
            if not refs:
                return

            # Exact explicit replacement: every receiver service in the scope is
            # moved to the exact source/channel ID highlighted by the user.
            previous_source_ids = []
            for ref in refs:
                for row in self._mapping_infos_for_ref(ref):
                    sid = str((row or {}).get("source_id") or "")
                    if sid and sid not in previous_source_ids:
                        previous_source_ids.append(sid)
            action_label = ("Change EPG ID for %d linked services" % len(refs)) if remap_refs else \
                           ("Assign %s -> %s" % (service.get("name") or "channel", epg.get("display_name") or epg.get("channel_id") or "EPG"))
            _save_started = time.time()
            save_result = self.store.assign_exclusive(epg.get("source_id"), epg.get("channel_id"), refs,
                                        display_name=epg.get("display_name"),
                                        old_source_ids=previous_source_ids,
                                        label=action_label, defer_generated=True)
            try: perf_profiler.record("Manual Map Save", time.time() - _save_started)
            except Exception: pass
            self._queue_manual_generated_sync(save_result)
            try:
                _ignored = self.store.ignored_refs()
                self._refresh_ignored_ref_keys(_ignored)
                smart_mapping_warm_cache.update_ignored_refs(_ignored)
            except Exception:
                pass
            # If Find Suggestion exposed a prepared-but-OFF feed, confirming
            # the mapping also enables that exact feed.  Otherwise the user would
            # get a saved mapping with no programme source behind it.
            self._activate_mapping_source_if_needed(epg.get("source_id"))
            try:
                src = self._source_by_id(epg.get("source_id")) or self._current("sources", self.source_groups) or {}
                # Learn against the real provider feed even when the visible
                # source row is a beta71 Unified Country Source.
                smartmatch_ai.learn(service, src, epg)
            except Exception:
                pass

            info = {"source_id": epg.get("source_id"),
                    "source_name": self._source_label(epg.get("source_id")),
                    "channel_id": epg.get("channel_id"),
                    "display_name": epg.get("display_name") or epg.get("channel_id"),
                    "mode": "manual"}
            self._paint_manual_mapping_fast(refs, info)

            self.mapping_revision += 1
            self._suggestion_browser_active = False
            self._suggestion_browser_ref = ""
            count = len(refs)
            if remap_refs:
                self._remap_scope_refs = []
                self._remap_scope_names = []
                self._remap_scope_mode = ""
                self._remap_old_info = {}
                self["summary"].setText("EPG changed: %d service%s  →  %s / %s" % (
                    count, "s" if count != 1 else "",
                    info.get("source_name") or "EPG", info.get("display_name") or info.get("channel_id")))
            else:
                self["summary"].setText("Mapped: %s  →  %s" %
                                        (service.get("name", "Channel"), epg.get("display_name", "EPG")))

            self.selection_cache.clear()
            # Update the current EPG list only once; no full source rescan.
            if self.only_unmapped:
                self.refresh_selection()
            else:
                idx = self._get_index("selections")
                if 0 <= idx < len(self.selection_rows):
                    text = self.selection_rows[idx]
                    if "[M]" not in text and not epg.get("separator"):
                        self.selection_rows[idx] = text + " [M]"
                        self._set_list("selections", self.selection_rows)
                        self._set_index("selections", idx)
            self.update_focus()
            if remap_refs:
                try:
                    self.session.open(MessageBox,
                                      "%d service%s now use%s:\n%s / %s\n\nThe previous EPG ID/source links were removed from those services." % (
                                          count, "s" if count != 1 else "",
                                          "" if count != 1 else "s",
                                          info.get("source_name") or "EPG",
                                          info.get("display_name") or info.get("channel_id")),
                                      MessageBox.TYPE_INFO, timeout=6)
                except Exception:
                    pass
            else:
                # beta63: never jump/zap to another receiver channel after Assign.
                # Keep the user on the service they just corrected so the result is
                # visible immediately and another correction is always deliberate.
                self.focus = 1
                self.update_focus()
        except Exception as exc:
            log.exception("Could not save mapping")
            self.session.open(MessageBox, "Could not save mapping.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _advance_to_next_channel(self):
        """Compatibility no-op: beta64 never moves away after Map/Unmap.

        Keeping the method avoids breaking older internal callers while making
        the UX deterministic: every action remains on the receiver service the
        user is currently reviewing.
        """
        self.focus = 1
        self.update_focus()
        self.update_source_title()
        return

    def auto_map_current_channel(self):
        """Auto-map one receiver service using selected sources and safety guards.

        Manual mapping remains unrestricted. Automatic mapping prefers the exact
        country/source choices from EPG Sources and refuses ambiguous same-name
        candidates across providers/countries.
        """
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select a channel first.", MessageBox.TYPE_INFO)
            return
        kind = str(service.get("service_type") or channel_mapper.classify_service_ref(service.get("ref")) or "").upper()
        if kind != "IPTV":
            # SAT maps are generated deterministically from the live lamedb and
            # cached Channel IDs. GREEN only opens the manual override panes if
            # the automatic SRP resolver left this service unlinked.
            if self._service_is_mapped(service):
                self["summary"].setText("Already mapped from SRP database: %s" % service.get("name", "channel"))
                self._advance_to_next_channel()
                return
            self._manual_source_override_active = False
            self._rebuild_source_view("__smart_suggestions__")
            for _i, _row in enumerate(self.source_groups or []):
                if (_row or {}).get("suggestion_all_sources"):
                    self._set_index("sources", _i); break
            self.refresh_selection(allow_prepare=False)
            self.focus = 3
            self.update_focus()
            self["summary"].setText("No deterministic SRP match yet for %s • best cached EPG candidate selected automatically" %
                                    service.get("name", "channel"))
            return
        if self._service_is_mapped(service):
            self["summary"].setText("Already mapped: %s" % service.get("name", "channel"))
            self._advance_to_next_channel()
            return
        channel_name = service.get("name", "")
        candidates = []
        blocked = 0
        allowed_auto_sources = set(self._auto_allowed_source_ids() or set())
        # IPTV manual auto-match is a local scoring helper. SAT mapping is
        # handled separately by the live-lamedb deterministic SRP engine.
        for src in self._all_source_groups:
            if not src or src.get("source_group"):
                continue
            if str(src.get("source_id") or "") not in allowed_auto_sources:
                continue
            if src.get("external"):
                try:
                    if not source_channel_cache.has(src.get("source_id")):
                        continue
                except Exception:
                    continue
            else:
                path = self._source_xml_path(src)
                if not path or not os.path.exists(path):
                    continue
            affinity = smartmatch_ai.source_affinity(service, src)
            if affinity <= -100:
                blocked += 1
                continue
            candidates.append((affinity, src))
        candidates.sort(key=lambda pair: -pair[0])
        ranked = []
        # Parse only the best candidate feeds. Identity/country affinity means
        # i24News/Al Alam no longer waste time opening SNRT just due to text.
        for source_score, src in candidates[:18]:
            self._ensure_source_loaded(src)
            for epg in self.epg_by_source.get(src.get("source_id"), []):
                try:
                    result = precision_match_engine.evaluate(
                        service, src, epg,
                        text_score=channel_mapper._token_score(channel_name, epg.get("display_name") or epg.get("channel_id") or ""),
                        learned=smartmatch_ai.learned_match(service, src, epg), balanced=True)
                except Exception:
                    blocked += 1
                    continue
                if not result.get("auto"):
                    blocked += 1
                    continue
                ranked.append((int(result.get("confidence") or 0), src, epg, result))
        ranked.sort(key=lambda x: (-x[0], str(x[1].get("source_name") or "").lower(), str(x[2].get("display_name") or "").lower()))
        best = ranked[0] if ranked else None
        second_score = ranked[1][0] if len(ranked) > 1 else 0
        protected_best = False
        # Require both confidence and a useful margin. Learned corrections can
        # legitimately score 100 and win directly.
        if best and best[0] >= precision_match_engine.AUTO_MIN and (len(ranked) == 1 or best[0] - second_score >= precision_match_engine.AUTO_MARGIN):
            score, src, epg, match_result = best
            # Never let automatic IPTV SmartMatch reuse/replace an XMLTV ID or
            # service reference that the user has manually locked. In that
            # case fall through to manual proposal mode below.
            protected = False
            if self._protect_manual_enabled():
                try:
                    existing = self.store.get(epg.get("source_id"), epg.get("channel_id")) or {}
                    protected = (str(existing.get("mode") or "").lower() == "manual" or
                                 self._service_is_manual_locked(service))
                except Exception:
                    protected = False
            if not protected:
                try:
                    _ref = str(service.get("ref") or "")
                    _old_sids = [x.get("source_id") for x in self._mapping_infos_for_ref(_ref) if x.get("source_id")]
                    _saved = self.store.assign_exclusive(
                        epg.get("source_id"), epg.get("channel_id"), [_ref],
                        display_name=epg.get("display_name"), old_source_ids=_old_sids,
                        label="SmartMatch IPTV", defer_generated=True, mode="smartmatch-iptv")
                    self._queue_manual_generated_sync(_saved)
                    self._last_smartmatch = {"service": service, "source": src, "epg": epg, "result": match_result}
                    self._rebuild_mapping_cache()
                    self.mapping_revision += 1
                    self.selection_cache.clear()
                    idx = self._get_index("channels")
                    self._sat_channel_row_cache.clear()
                    self._paint_channel_rows_cached()
                    self._set_index("channels", idx)
                    self._paint_selected_programme_fast()
                    region = ",".join(src.get("countries") or []) or src.get("region") or "-"
                    self["summary"].setText("SmartMatch %s  →  %s [%s] / %s  (%d%%)" %
                                            (channel_name, src.get("source_name", "EPG"), region,
                                             epg.get("display_name", "channel"), int(round(float(score) / 10.0))))
                    self._advance_to_next_channel()
                    return
                except Exception as exc:
                    log.exception("SmartMatch Channel failed")
                    self.session.open(MessageBox, "SmartMatch failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)
                    return
            else:
                protected_best = True
                self["summary"].setText("Protected manual mapping detected • suggestion shown, nothing overwritten")
        self._last_smartmatch = None
        if best:
            self._last_smartmatch = {"service": service, "source": best[1], "epg": best[2], "result": best[3]}
        self._manual_source_override_active = False
        self._rebuild_source_view("__smart_suggestions__")
        for _i, _row in enumerate(self.source_groups or []):
            if (_row or {}).get("suggestion_all_sources"):
                self._set_index("sources", _i); break
        self.refresh_selection(allow_prepare=False)
        self.focus = 3
        self.update_focus()
        if protected_best and best:
            self["summary"].setText("Protected manual mapping • suggested %s / %s (%d%%), nothing overwritten" %
                                    (best[1].get("source_name", "EPG"), best[2].get("display_name", "channel"), best[0]))
        elif best and len(ranked) > 1 and best[0] - second_score < 6:
            self["summary"].setText("Ambiguous EPG for %s — choose the correct source manually (top %d%% / %d%%)." %
                                    (channel_name, best[0], second_score))
        elif best:
            self["summary"].setText("Suggested EPG for %s: %s / %s (%d%%) • press OK to confirm" %
                                    (channel_name, best[1].get("source_name", "EPG"), best[2].get("display_name", "channel"), best[0]))
        else:
            self["summary"].setText("No safe auto-match for %s — %d unsafe candidate(s) blocked; review manually." %
                                    (channel_name, blocked))

    def _reload_one_source_channels(self, src, path=None):
        """Refresh one XMLTV source in memory without reparsing every cached feed."""
        sid = src.get("source_id")
        name = src.get("source_name") or sid
        fresh = []
        if src.get("external"):
            try:
                fresh = list(source_channel_cache.get(sid) or [])
            except Exception:
                fresh = []
        else:
            if path is None:
                for filename, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                    if pair[0] == sid:
                        path = os.path.join(self._epg_dir(), filename)
                        break
            try:
                if path and os.path.exists(path):
                    fresh = channel_mapper.read_xmltv_channel_ids_fast(path, sid, name)
            except Exception:
                log.exception("Could not parse updated EPGManager XMLTV source %s", sid)
        self.epg_channels = [x for x in self.epg_channels if x.get("source_id") != sid] + fresh
        self._rebuild_epg_index()
        return len(fresh)

    def update_selected_source(self):
        """Refresh the real Channel-ID catalogue behind the visible source.

        beta71 introduced virtual country rows; the old YELLOW handler treated
        them as folders and therefore did nothing. beta74 resolves the concrete
        provider feed (current EPG row -> current suggestion -> preferred child)
        and refreshes that one catalogue in background.
        """
        if getattr(self, "_channel_refresh_busy", False):
            return
        visible = self._current("sources", self.source_groups)
        if not visible:
            return
        if visible.get("suggestion_all_sources"):
            service = self._current("channels", self.bouquet_services)
            self.selection_cache.pop(("__smart_suggestions__", str((service or {}).get("ref") or ""), self.mapping_revision), None)
            self._refresh_cross_source_suggestions(service)
            self["summary"].setText("Suggestion view refreshed from RAM • no source download")
            return

        src = visible
        # beta82 all-matches browser may show a concrete provider unrelated to
        # the source row currently highlighted in column 3.  YELLOW must refresh
        # the exact feed selected in EPG MATCH, never an unrelated visible row.
        current_epg = self._current("selections", self.selection_entries) or {}
        if getattr(self, "_suggestion_browser_active", False) and current_epg and not current_epg.get("separator"):
            concrete = self._source_by_id(current_epg.get("source_id"))
            if concrete:
                src = concrete
        if src is visible and visible.get("country_bundle"):
            children = list(visible.get("children") or [])
            child_by_id = {str(x.get("source_id") or ""): x for x in children}
            # 1) Trust an EPG row only when the pane is bound to the receiver
            # service currently under the cursor *and* that feed belongs to this
            # visible country. Otherwise it is a stale preview and must not drive
            # a network refresh.
            service = self._current("channels", self.bouquet_services) or {}
            current_epg = self._current("selections", self.selection_entries) or {}
            wanted = ""
            bound_ok = (str(getattr(self, "_selection_bound_ref", "") or "") == str(service.get("ref") or ""))
            if bound_ok and not current_epg.get("separator"):
                candidate_sid = str(current_epg.get("source_id") or "")
                if candidate_sid in child_by_id and candidate_sid in (getattr(self, "_selection_bound_source_ids", set()) or set()):
                    wanted = candidate_sid
            # 2) Otherwise refresh the actual verified suggestion provider.
            if not wanted:
                candidate_sid = str(getattr(self, "_smart_recommended_source_id", "") or "")
                if candidate_sid in child_by_id:
                    wanted = candidate_sid
            src = child_by_id.get(wanted)
            # 3) Fall back to the best active/local child, never the whole country.
            if not src and children:
                children.sort(key=self._country_bundle_child_priority)
                src = children[0]
        if not src or src.get("source_group"):
            return

        sid = str(src.get("source_id") or "")
        if not sid:
            return
        name = src.get("source_name") or sid
        current_epg = self._current("selections", self.selection_entries) or {}
        service = self._current("channels", self.bouquet_services) or {}
        bound_ok = (str(getattr(self, "_selection_bound_ref", "") or "") == str(service.get("ref") or ""))
        preserve_cid = str(current_epg.get("channel_id") or "") if bound_ok and not current_epg.get("separator") else ""
        self["summary"].setText("Refreshing channel catalogue for %s…" % smart_context_match.source_label(src))
        self["key_yellow"].setText("Source / Details")
        self._channel_refresh_busy = True
        self._channel_refresh_result = None

        # YELLOW means a real source refresh when the Manager is available, not
        # merely a re-read of an old channel list. The operation is still fully
        # asynchronous; Smart Mapping remains responsive. After the source update
        # completes we rebuild only this source's lightweight channel header.
        catalogue_id = str(src.get("catalogue_id") or (src.get("source_id") if src.get("external") else "") or "")
        if self.manager is not None:
            try:
                if self.manager.is_busy():
                    self._channel_refresh_busy = False
                    self["key_yellow"].setText("Source / Details")
                    self["summary"].setText("EPGManager is busy • source refresh postponed")
                    return
            except Exception:
                pass
        if (self.manager is not None and catalogue_id and not src.get("external") and
                hasattr(self.manager, "run_catalogue_update_only_async")):
            def manager_done(result):
                rows = []; err = None
                try:
                    ok = bool((result or {}).get("ok"))
                    if not ok:
                        err = str((result or {}).get("message") or "Source refresh failed")
                    path = self._source_xml_path(src)
                    if path and os.path.exists(path):
                        rows = channel_mapper.read_xmltv_channel_ids_fast(path, sid, name)
                    if not rows:
                        try:
                            rows = source_channel_cache.get(sid) if source_channel_cache.has(sid) else []
                        except Exception:
                            rows = []
                    rows = self._stabilize_source_rows(src, rows)
                    if rows:
                        source_channel_cache.put(sid, name, rows,
                                                 provider=src.get("provider") or "",
                                                 region=src.get("region") or "",
                                                 language=src.get("language") or "")
                except Exception as exc:
                    err = str(exc)
                self._channel_refresh_result = (src, rows, err, preserve_cid)
            try:
                started = self.manager.run_catalogue_update_only_async(
                    catalogue_id, on_complete=manager_done, force_download=True)
            except Exception:
                started = False
            if started:
                self._timer.start(400, False)
                return

        def worker():
            rows = []
            meta = {}
            err = None
            try:
                if src.get("external"):
                    # External source truth is its configured XML URL. The
                    # reader streams only the channel header/bounded sample.
                    rows, meta = remote_channel_catalog.fetch(src, timeout=8, max_scan=8 * 1024 * 1024)
                    # No local XML fallback for external providers.
                else:
                    path = self._source_xml_path(src)
                    if path and os.path.exists(path):
                        rows = channel_mapper.read_xmltv_channel_ids_fast(path, sid, name)
                    if not rows:
                        rows = source_catalog.local_channel_hints(sid) or []
                rows = self._stabilize_source_rows(src, rows)
                source_channel_cache.put(sid, name, rows,
                                         provider=src.get("provider") or "",
                                         region=src.get("region") or "",
                                         language=src.get("language") or "")
                # If the explicit refresh sampled programme data, retain that
                # tiny quality profile. Partial samples can prove presence but
                # are never allowed to prove absence, so violet stays safe.
                try:
                    quality = dict((meta or {}).get("quality") or {})
                    if quality:
                        quality["source_name"] = name
                        source_quality.save(sid, quality)
                except Exception:
                    pass
                if not rows:
                    err = "Provider returned no channel IDs"
            except Exception as exc:
                err = str(exc)
            self._channel_refresh_result = (src, rows, err, preserve_cid)

        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(400, False)

    def _poll_download(self):
        if getattr(self, "_channel_refresh_busy", False):
            result = getattr(self, "_channel_refresh_result", None)
            if result is None:
                return
            self._channel_refresh_busy = False
            self._channel_refresh_result = None
            try:
                self._timer.stop()
            except Exception:
                pass
            src, rows, err, preserve_cid = result
            sid = src.get("source_id")
            if rows:
                self.epg_by_source[sid] = list(rows)
                self.epg_channels = [x for x in self.epg_channels if x.get("source_id") != sid] + list(rows)
                self.selection_cache.clear()
                self._source_language_rank_cache.clear()
                self._invalidate_smart_name_index()
                try:
                    smart_catalog_boot.update_source(sid, src.get("source_name") or sid, rows, method="yellow-refresh")
                except Exception:
                    pass
            self["key_yellow"].setText("Source / Details")
            if err:
                # Never erase a healthy cached catalogue because a manual refresh
                # hit a transient provider/network error.
                try:
                    cached_count = len(self.epg_by_source.get(sid) or smart_catalog_boot.get(sid) or [])
                except Exception:
                    cached_count = len(self.epg_by_source.get(sid) or [])
                self["summary"].setText("%s: refresh failed • keeping %d cached IDs" %
                                        (smart_context_match.source_label(src), cached_count))
                self.session.open(MessageBox, "Could not refresh channel catalogue.\n\n%s\n\nExisting cache was kept." % err, MessageBox.TYPE_ERROR)
            else:
                self["summary"].setText("%s: %d channel IDs refreshed" % (smart_context_match.source_label(src), len(rows or [])))
                self.refresh_selection()
                if preserve_cid:
                    for idx, item in enumerate(self.selection_entries):
                        if str((item or {}).get("channel_id") or "") == preserve_cid:
                            self._set_index("selections", idx)
                            break
            return
        if not self._download_busy:
            return
        if self._download_result is None and self._download_error is None:
            return
        self._download_busy = False
        try:
            self._timer.stop()
        except Exception:
            pass
        self["key_yellow"].setText("Source / Details")
        if self._download_error:
            err = self._download_error
            self._download_error = None
            self["summary"].setText("EPG source update failed")
            self.session.open(MessageBox, "Could not update EPG source.\n\n%s" % err, MessageBox.TYPE_ERROR)
            return
        path = self._download_result
        self._download_result = None
        self["summary"].setText("Source updated: %s" % os.path.basename(path))
        src = self._current("sources", self.source_groups)
        if src:
            self._reload_one_source_channels(src, path)
        self.refresh_sources()
        if self.focus == 3:
            self.refresh_selection()
        else:
            self._after_move()
        self._update_summary()

    def _poll_local_update(self):
        sid = self._local_update_sid
        if not sid or self.manager is None:
            self._local_update_sid = None
            return
        try:
            if self.manager.is_busy():
                return
        except Exception:
            pass
        self._local_update_sid = None
        try:
            self._timer.stop()
        except Exception:
            pass
        self["key_yellow"].setText("Source / Details")
        status = None
        try:
            status = self.manager.get_status(sid)
        except Exception:
            status = None
        try:
            src = next((x for x in self._all_source_groups if x.get("source_id") == sid),
                       {"source_id": sid, "source_name": sid, "external": False})
            self._reload_one_source_channels(src)
            self.refresh_sources()
            if self.focus == 3:
                self.refresh_selection()
            else:
                self._after_move()
        except Exception:
            log.exception("Could not refresh local source after update")
        if status and status.get("status") == "SUCCESS":
            self["summary"].setText("Local source updated: %s (%s programmes)" %
                                    (status.get("name", sid), status.get("program_count", 0)))
            self._ask_restart_after_script()
        elif status and status.get("status") == "COOLDOWN":
            self["summary"].setText("Local source is in safety cooldown")
        elif status and status.get("status") == "FAILED":
            self["summary"].setText("Local source update failed: %s" % (status.get("error_message") or sid))
        else:
            self["summary"].setText("Local source update finished")

    def _ask_restart_after_script(self):
        try:
            self.session.openWithCallback(
                self._restart_after_script_answer,
                MessageBox,
                "Source script finished successfully.\n\nRestart Enigma2 now?",
                MessageBox.TYPE_YESNO,
                default=False,
            )
        except Exception:
            pass

    def _restart_after_script_answer(self, answer):
        if not answer:
            return
        try:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        except Exception as exc:
            log.exception("Could not restart Enigma2: %s", exc)

    def open_main_settings(self):
        try:
            from .settings import EPGManagerSettingsScreen
            self.session.open(EPGManagerSettingsScreen, self.config)
        except Exception:
            log.exception("Unable to open Main Settings from Smart Mapping")

    def open_mapping_options(self):
        """beta63: five user-facing actions only; no technical menu maze."""
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = []
            if getattr(self, "_remap_scope_refs", None):
                choices.append(("Cancel pending EPG change", "cancel_remap"))
            service = self._current("channels", self.bouquet_services) or {}
            if service and self._service_is_mapped(service):
                choices.append(("Remove current mapping", "unmap"))
            if service and self._service_is_ignored(service):
                choices.append(("Restore channel from NO EPG / Ignore", "unignore"))
            elif service and not self._service_is_mapped(service):
                choices.append(("Ignore / No EPG expected", "ignore"))
            choices += [
                ("1  Change EPG for this service", "change_one"),
                ("2  Change EPG ID for ALL linked services", "change_all_id"),
                ("3  UNRESOLVED ONLY • yellow / orange / red", "filter_unresolved"),
                ("Show unmapped channels", "filter_unmapped"),
                ("Show ignored / No EPG", "filter_ignored"),
                ("4  Smart Check", "smart_audit"),
                ("5  Undo last mapping change", "undo"),
                ("6  Advanced Tools  •  lamedb / SRP / audit", "advanced"),
                ("Reset IDs…", "reset_ids"),
            ]
            self.session.openWithCallback(self._mapping_option_selected, ChoiceBox,
                                          title="Smart Mapping", list=choices)
        except Exception:
            pass

    def _mapping_option_selected(self, answer):
        if not answer:
            return
        key = answer[1]
        if key == "unmap":
            self.unmap_current_channel()
        elif key == "ignore":
            self.ignore_current_channel()
        elif key == "unignore":
            self.unignore_current_channel()
        elif key == "manual_source":
            self.open_manual_source_override()
        elif key == "change_one":
            self.start_explicit_remap_one()
        elif key == "change_all_id":
            self.start_explicit_remap_all_linked()
        elif key == "filter_unresolved":
            self.set_channel_filter("unresolved")
        elif key == "filter_unmapped":
            self.set_channel_filter("unmapped")
        elif key == "filter_ignored":
            self.set_channel_filter("ignored")
        elif key == "smart_audit":
            self.smart_audit_current_bouquet()
        elif key == "undo":
            self.undo_last_mapping()
        elif key == "advanced":
            self.open_advanced_mapping_tools()
        elif key == "reset_ids":
            self.open_reset_ids_menu()
        elif key == "cancel_remap":
            self._clear_explicit_remap("EPG change cancelled")

    def _source_cache_age_label(self, source_id):
        try:
            info = source_channel_cache.info(source_id) or {}
            ts = int(info.get("updated_at") or 0)
            count = int(info.get("channels") or 0)
            if not ts:
                return count, "not cached"
            age = max(0, int(time.time()) - ts)
            if age < 3600:
                label = "%dm" % max(0, age // 60)
            elif age < 86400:
                label = "%dh" % (age // 3600)
            else:
                label = "%dd" % (age // 86400)
            return count, label
        except Exception:
            return 0, "cache ?"

    def open_manual_source_override(self):
        """YELLOW source details/override without a permanent source pane."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select a receiver channel first.", MessageBox.TYPE_INFO)
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = []
            selected = set(self.auto_selected_source_ids or [])
            concrete = []
            for src in getattr(self, "_all_source_groups", []) or []:
                if not src or src.get("source_group") or src.get("country_bundle"):
                    continue
                sid = str(src.get("source_id") or "")
                if not sid:
                    continue
                concrete.append(src)
            concrete.sort(key=lambda x: (0 if str(x.get("source_id") or "") in selected else 1,
                                         _alpha_text(x.get("source_name") or x.get("source_id"))))
            for src in concrete:
                sid = str(src.get("source_id") or "")
                count, age = self._source_cache_age_label(sid)
                state = "ON" if sid in selected else "OFF"
                label = "%s  •  %s  •  %d IDs  •  cache %s" % (
                    self._smart_source_display_name(src), state, count, age)
                choices.append((label, sid))
            if not choices:
                self.session.open(MessageBox, "No EPG sources are available.", MessageBox.TYPE_INFO)
                return
            self.session.openWithCallback(self._manual_source_choice, ChoiceBox,
                                          title="Smart Mapping — Sources / details", list=choices)
        except Exception as exc:
            self.session.open(MessageBox, "Source details unavailable:\n%s" % exc, MessageBox.TYPE_ERROR)

    def _manual_source_choice(self, answer):
        if not answer:
            return
        sid = str(answer[1] or "")
        src = self._source_by_id(sid)
        if not src:
            return
        self._manual_source_override_active = True
        self._selection_forced_source_id = sid
        self._make_source_visible(sid)
        # Fresh cache is reused instantly. Missing/stale cache is refreshed in
        # background automatically; no separate manual Refresh Source step.
        try:
            smart_source_cache_refresh.refresh_source_async(sid, force=False)
        except Exception:
            pass
        try:
            self.refresh_selection(allow_prepare=False)
        except Exception:
            self.selection_entries = []
            self.selection_rows = ["Preparing %s from cache…" % self._smart_source_display_name(src)]
            self._set_list("selections", self.selection_rows)
        self.focus = 3
        self.update_focus()
        count, age = self._source_cache_age_label(sid)
        self["summary"].setText("SOURCE OVERRIDE • %s • %d cached IDs • age %s • stale refresh is automatic" % (
            self._smart_source_display_name(src), count, age))

    def _clear_explicit_remap(self, summary=None):
        self._remap_scope_refs = []
        self._remap_scope_names = []
        self._remap_scope_mode = ""
        self._remap_old_info = {}
        self.update_focus()
        if summary:
            self["summary"].setText(summary)
        else:
            self._update_summary()

    def _activate_explicit_remap(self, service, old_info, refs, names, mode):
        clean = []
        for ref in refs or []:
            ref = str(ref or "").strip()
            if ref and not any(set(self._ref_lookup_keys(ref)).intersection(self._ref_lookup_keys(existing)) for existing in clean):
                clean.append(ref)
        if not clean and service and service.get("ref"):
            clean = [str(service.get("ref"))]
        self._remap_scope_refs = clean
        self._remap_scope_names = list(names or [])
        self._remap_scope_mode = str(mode or "one")
        self._remap_old_info = dict(old_info or {})
        # rc73: source choice lives in BLUE -> More; the main workspace stays 3-pane.
        self.focus = 1
        self.update_focus()
        self._update_summary()
        try:
            count = len(clean)
            self.session.open(
                MessageBox,
                ("Change EPG for %d service%s.\n\n"
                 "1. YELLOW -> Source / Details\n"
                 "2. Choose the source, then the exact EPG ID\n"
                 "3. Press OK to apply\n\n"
                 "Fresh source cache is reused automatically; stale cache refreshes in background.\n"
                 "No automatic fuzzy replacement will be applied.") %
                (count, "s" if count != 1 else ""),
                MessageBox.TYPE_INFO, timeout=8)
        except Exception:
            pass

    def start_explicit_remap_one(self):
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select a receiver channel first.", MessageBox.TYPE_INFO)
            return
        def chosen(info):
            self._activate_explicit_remap(service, info, [service.get("ref")],
                                          [service.get("name") or "channel"], "one")
        self._choose_old_mapping_for_service(service, chosen)

    def _services_using_exact_epg(self, old_info, selected_service):
        sid = str((old_info or {}).get("source_id") or "").lower()
        cid = str((old_info or {}).get("channel_id") or "").lower()
        if not sid or not cid:
            return [selected_service] if selected_service else []
        rows = []
        seen = set()
        candidates = list(getattr(self, "catalog", []) or [])
        if selected_service:
            candidates.append(selected_service)
        for svc in candidates:
            ref = str((svc or {}).get("ref") or "").strip()
            if not ref:
                continue
            try:
                canon = str(channel_registry.canonical_service_ref(ref) or ref).rstrip(":").lower()
            except Exception:
                canon = ref.rstrip(":").lower()
            if canon in seen:
                continue
            info = self._primary_mapping_info_for_ref(ref) or {}
            if not (str(info.get("source_id") or "").lower() == sid and
                    str(info.get("channel_id") or "").lower() == cid):
                continue
            seen.add(canon)
            rows.append(svc)
        return rows or ([selected_service] if selected_service else [])

    def start_explicit_remap_all_linked(self):
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select one receiver channel using the EPG ID you want to replace.", MessageBox.TYPE_INFO)
            return
        def chosen(info):
            if not (info or {}).get("source_id") or not (info or {}).get("channel_id"):
                self.session.open(MessageBox, "This service has no EPG ID to replace. Use 'Change EPG for this service'.", MessageBox.TYPE_INFO)
                return
            services = self._services_using_exact_epg(info, service)
            refs = [x.get("ref") for x in services if x.get("ref")]
            names = [x.get("name") or "channel" for x in services]
            self._activate_explicit_remap(service, info, refs, names, "all_linked")
        self._choose_old_mapping_for_service(service, chosen)

    def open_attention_menu(self):
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = [
                ("UNRESOLVED ONLY (yellow / orange / red)", "filter_unresolved"),
                ("Unmapped channels", "filter_unmapped"),
                ("Mapping conflicts", "filter_conflicts"),
                ("Review next Smart suggestion", "suggestion"),
                ("Review next conflict", "conflict"),
                ("Show all channels", "filter_all"),
            ]
            self.session.openWithCallback(self._attention_selected, ChoiceBox,
                                          title="Channels needing attention", list=choices)
        except Exception:
            self.set_channel_filter("unmapped")

    def _attention_selected(self, answer):
        if not answer:
            return
        key = answer[1]
        if key == "filter_unresolved": self.set_channel_filter("unresolved")
        elif key == "filter_unmapped": self.set_channel_filter("unmapped")
        elif key == "filter_conflicts": self.set_channel_filter("conflicts")
        elif key == "filter_all": self.set_channel_filter("all")
        elif key == "suggestion": self.review_next_suggestion()
        elif key == "conflict": self.review_next_conflict()

    def open_advanced_mapping_tools(self):
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = [
                ("UNRESOLVED ONLY • yellow / orange / red", "filter_unresolved"),
                ("Show all receiver channels", "filter_all"),
                ("Show only mapped channels", "filter_mapped"),
                ("Show manually locked channels", "filter_locked"),
                ("Search channel", "search"),
                ("Remove mapping from current channel", "unmap"),
                ("Preview next programmes", "preview"),
                ("Verify lamedb update", "verify_lamedb"),
                ("Refresh SRP maps from live lamedb", "rebuild_srp"),
                ("RESET ALL SIDs + rebuild/import fresh EPG", "reset_all_sids"),
                ("Direct ID health / repair", "direct_id_health"),
                ("Show Channel-ID coverage audit", "id_audit"),
                ("Inspect selected-source ID collisions", "collision_audit"),
                ("Run Smart Mapping regression self-test", "regression_test"),
                ("Export diagnostic report", "export_diagnostic"),
                ("Show performance profile", "perf_profile"),
            ]
            self.session.openWithCallback(self._advanced_mapping_selected, ChoiceBox,
                                          title="Smart Mapping — Advanced Tools", list=choices)
        except Exception:
            pass

    def _advanced_mapping_selected(self, answer):
        if not answer:
            return
        key = answer[1]
        if key == "filter_unresolved": self.set_channel_filter("unresolved")
        elif key == "filter_all": self.set_channel_filter("all")
        elif key == "filter_mapped": self.set_channel_filter("mapped")
        elif key == "filter_locked": self.set_channel_filter("locked")
        elif key == "search": self.search_channel()
        elif key == "unmap": self.unmap_current_channel()
        elif key == "preview": self.preview_three_programmes()
        elif key == "toggle_auto_source": self.toggle_current_auto_source()
        elif key == "reset_auto_sources": self.reset_auto_sources()
        elif key == "verify_lamedb": self.verify_lamedb_update()
        elif key == "rebuild_srp": self.rebuild_global_srp_database()
        elif key == "reset_all_sids": self.reset_all_sids_and_rebuild()
        elif key == "direct_id_health":
            from .id_monitor import DirectIdMonitorScreen
            self.session.open(DirectIdMonitorScreen, self.config)
        elif key == "id_audit": self.show_id_coverage_audit()
        elif key == "collision_audit": self.show_source_collision_audit()
        elif key == "regression_test": self.run_mapping_regression_selftest()
        elif key == "export_diagnostic": self.export_diagnostic_report()
        elif key == "perf_profile": self.show_performance_profile()

    def show_performance_profile(self):
        try:
            report = perf_profiler.report()
            lines = ["PERFORMANCE PROFILE", "", "Measured on this Enigma2 session:"]
            if not report:
                lines += ["", "No timings recorded yet.", "Open Smart Mapping and run Find Suggestions first."]
            else:
                for name, row in report:
                    lines.append("%s: %.0f ms  (last %.0f / max %.0f)" % (
                        name, float(row.get("avg_ms") or 0), float(row.get("last_ms") or 0), float(row.get("max_ms") or 0)))
            try:
                st = smart_name_index_cache.stats() or {}
                lines += ["", "Smart index: %d rows • %d buckets • %.1f KB" % (
                    int(st.get("rows") or 0), int(st.get("keys") or 0), float(st.get("bytes") or 0) / 1024.0)]
            except Exception:
                pass
            lines += ["", "Diagnostic only • no network • no mapping changes"]
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)
        except Exception as exc:
            self.session.open(MessageBox, "Performance profile unavailable.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def run_mapping_regression_selftest(self):
        try:
            report = mapping_regression.run() or {}
        except Exception as exc:
            self.session.open(MessageBox, "Smart Mapping self-test failed to run.\n\n%s" % exc, MessageBox.TYPE_ERROR)
            return
        lines = [
            "SMART MAPPING REGRESSION SELF-TEST",
            "",
            "%d/%d passed" % (int(report.get("passed") or 0), int(report.get("total") or 0)),
        ]
        for row in report.get("cases") or []:
            lines.append("%s  %s" % ("PASS" if row.get("ok") else "FAIL", row.get("name") or "case"))
            if not row.get("ok"):
                lines.append("    %s" % (row.get("detail") or ""))
        lines += ["", "Offline test only • no network • no mapping changes"]
        self.session.open(MessageBox, "\n".join(lines),
                          MessageBox.TYPE_INFO if not report.get("failed") else MessageBox.TYPE_ERROR)

    def show_source_collision_audit(self):
        """Cache-only duplicate XMLTV-ID inspection for active mapping feeds."""
        source_ids = list(source_variant_policy.normalize_selected(self.auto_selected_source_ids or []))
        if not source_ids:
            self.session.open(MessageBox, "No active Smart Mapping sources are selected.", MessageBox.TYPE_INFO)
            return
        self["summary"].setText("Checking selected source IDs for collisions in background…")

        def done(report):
            report = report or {}
            lines = [
                "SOURCE ID COLLISION INSPECTOR",
                "",
                "Selected feeds: %d" % int(report.get("selected") or 0),
                "Cached feeds scanned: %d" % int(report.get("scanned") or 0),
                "Duplicate Channel IDs: %d" % int(report.get("collision_count") or 0),
                "High-risk overlaps: %d" % int(report.get("high_risk") or 0),
                "Known AR/EN siblings: %d" % int(report.get("known_parallel") or 0),
            ]
            rows = list(report.get("collisions") or [])
            if rows:
                lines += ["", "TOP COLLISIONS"]
                for row in rows[:14]:
                    owners = []
                    for owner in row.get("owners") or []:
                        label = str(owner.get("name") or owner.get("source_id") or "Source")
                        lang = str(owner.get("language") or "").split("-", 1)[0].upper()
                        provider = str(owner.get("provider") or "")
                        if lang:
                            label += " [%s]" % lang
                        if provider:
                            label += " / %s" % provider
                        owners.append(label)
                    marker = "KNOWN AR/EN" if row.get("known_parallel_language") else "REVIEW"
                    lines.append("%s  •  %s  →  %s" % (marker, row.get("channel_id") or "?", " | ".join(owners)))
            else:
                lines += ["", "No duplicate XMLTV Channel IDs found in cached active feeds."]
            missing = list(report.get("missing") or [])
            if missing:
                lines += ["", "%d active feed catalogue(s) are not cached yet; no network request was started." % len(missing)]
            lines += ["", "This inspector never changes mappings or source selection."]
            self["summary"].setText("Collision audit: %d duplicate ID(s) • %d high-risk" %
                                    (int(report.get("collision_count") or 0), int(report.get("high_risk") or 0)))
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)

        def worker():
            try:
                report = source_collision_audit.scan(source_ids, max_groups=100)
            except Exception as exc:
                report = {"selected": len(source_ids), "error": str(exc), "collisions": []}
            try:
                from twisted.internet import reactor
                reactor.callFromThread(done, report)
            except Exception:
                try:
                    done(report)
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True).start()

    def open_system_check(self):
        try:
            from .system_check import SystemCheckScreen
            self.session.open(SystemCheckScreen, self.config)
        except Exception as exc:
            self.session.open(MessageBox, "System Check unavailable:\n%s" % exc, MessageBox.TYPE_ERROR)

    def export_diagnostic_report(self):
        self["summary"].setText("Building diagnostic • local XML / mappings / timezone…")
        def worker():
            try:
                report = system_check.run(self.config)
                path = system_check.export_report(report)
                error = ""
            except Exception as exc:
                path = ""; error = str(exc)
            def done():
                if error:
                    self.session.open(MessageBox, "Diagnostic export failed:\n%s" % error, MessageBox.TYPE_ERROR)
                else:
                    self["summary"].setText("Diagnostic ready • %s" % path)
                    self.session.open(MessageBox, "Diagnostic exported:\n%s\n\nPersistent copy: /etc/enigma2/epgmanager_last_diagnostic.txt" % path,
                                      MessageBox.TYPE_INFO, timeout=7)
            try:
                from twisted.internet import reactor
                reactor.callFromThread(done)
            except Exception:
                done()
        threading.Thread(target=worker, name="EPGM-Diagnostic", daemon=True).start()

    def set_channel_filter(self, mode):
        """Filter the receiver pane without rescanning bouquets or XMLTV data."""
        mode = str(mode or "all")
        if mode not in ("all", "unresolved", "unmapped", "mapped", "conflicts", "locked", "auto", "ignored"):
            mode = "all"
        self.channel_filter_mode = mode
        self.refresh_channels()
        self._set_index("channels", 0)
        # A filter changes the receiver context. Clear only the tiny candidate
        # cache and refresh the right pane from already-cached source IDs; no
        # network or bouquet scan is triggered here.
        self.selection_cache.clear()
        if self.bouquet_services:
            try:
                self.refresh_selection()
            except Exception:
                pass
        else:
            self.selection_entries = []
            self.selection_rows = ["No %s channels in this bouquet" % mode.upper()]
            self._set_list("selections", self.selection_rows)
        labels = {
            "all": "ALL receiver channels",
            "unresolved": "UNRESOLVED ONLY • yellow/orange/red",
            "unmapped": "UNMAPPED receiver channels",
            "mapped": "MAPPED receiver channels",
            "conflicts": "CONFLICTS only",
            "locked": "MANUAL LOCKS only",
            "auto": "AUTO REPAIR mappings only",
            "ignored": "NO EPG / IGNORED channels",
        }
        self["summary"].setText("Filter: %s  •  %d channel(s)" %
                                (labels.get(mode, mode), len(self.bouquet_services)))
        self.focus = 1
        self.update_focus()
        self.update_source_title()
        self._update_action_labels()

    def _cached_entries_for_source(self, src):
        """Read only lightweight channel headers/caches; never programme data."""
        if not src or src.get("source_group"):
            return []
        sid = str(src.get("source_id") or "")
        if not sid:
            return []
        if src.get("external"):
            try:
                rows = source_channel_cache.get(sid) or []
                return [dict(x) for x in rows if isinstance(x, dict)]
            except Exception:
                return []
        path = self._source_xml_path(src)
        if path and os.path.isfile(path):
            try:
                return channel_mapper.read_xmltv_channel_ids_fast(path, sid, src.get("source_name") or sid)
            except Exception:
                pass
        try:
            return list(source_catalog.local_channel_hints(sid) or [])
        except Exception:
            return []

    def _candidate_pool_global_index(self, service, src, limit=24):
        """Return source-specific candidates from the one global RAM index.

        Normal Smart Mapping must never build a per-source token/identity index
        on first access. If the global index is unavailable, return immediately.
        """
        if not service or not src:
            return []
        try:
            compact = smart_boot_name_index.snapshot() or {}
        except Exception:
            compact = {}
        if not compact:
            compact = self._smart_name_index if (self._smart_name_index_ready and isinstance(self._smart_name_index, dict)) else {}
        if not compact:
            try:
                compact = (smart_mapping_warm_cache.snapshot() or {}).get("smart_name_index") or {}
            except Exception:
                compact = {}
        if not isinstance(compact, dict) or int(compact.get("_format") or 0) != 2:
            return []

        allowed = set()
        if src.get("country_bundle"):
            for child in src.get("children") or []:
                sid = str((child or {}).get("source_id") or "")
                if sid:
                    allowed.add(sid)
        else:
            sid = str(src.get("source_id") or "")
            if sid:
                allowed.add(sid)
        if not allowed:
            return []

        try:
            raw_rows = manual_match_lite.candidate_rows(service, compact, limit=96)
        except Exception:
            raw_rows = []
        out = []
        seen = set()
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("source_id") or "")
            cid = str(raw.get("channel_id") or "")
            if sid not in allowed or not cid:
                continue
            sig = (sid.casefold(), cid.casefold())
            if sig in seen:
                continue
            seen.add(sig)
            out.append(dict(raw))
            if len(out) >= max(1, int(limit or 24)):
                break
        return out

    def _source_entry_index(self, src, entries):
        """Build/reuse a tiny lookup index for one EPG channel catalogue.

        The index contains only normalized display names and significant tokens;
        no programme XML is parsed.  A signature makes it self-invalidating when
        Update Source changes the channel catalogue.
        """
        sid = str((src or {}).get("source_id") or "")
        entries = list(entries or [])
        first = str((entries[0] if entries else {}).get("channel_id") or "")
        last = str((entries[-1] if entries else {}).get("channel_id") or "")
        sig = (len(entries), first, last)
        cached = self._source_search_indexes.get(sid)
        if cached and cached.get("sig") == sig:
            return cached
        by_norm = {}
        by_token = {}
        by_identity = {}
        normalized = []
        for epg in entries:
            name = str(epg.get("display_name") or epg.get("channel_id") or "")
            norm = channel_mapper.normalize_name(name)
            normalized.append((norm, epg))
            if norm:
                by_norm.setdefault(norm, []).append(epg)
                for token in set(x for x in norm.split() if len(x) >= 3):
                    by_token.setdefault(token, []).append(epg)
            # Identity lookup is cheap (signature-cached JSON) and prevents
            # short broadcaster names such as 2M from disappearing from the
            # turbo shortlist simply because token indexing starts at 3 chars.
            ident = channel_identity_resolver.resolve(name)
            if not ident:
                ident = channel_identity_resolver.resolve(str(epg.get("channel_id") or ""))
            iid = str((ident or {}).get("id") or "")
            if iid:
                by_identity.setdefault(iid, []).append(epg)
        data = {"sig": sig, "by_norm": by_norm, "by_token": by_token,
                "by_identity": by_identity, "normalized": normalized}
        self._source_search_indexes[sid] = data
        # keep memory bounded if users browse many providers
        if len(self._source_search_indexes) > 12:
            for key in list(self._source_search_indexes.keys())[:-12]:
                self._source_search_indexes.pop(key, None)
        return data

    def _candidate_pool_fast(self, service, src, entries, old_info=None, limit=96):
        """Return a small candidate pool without expensive full-catalog scoring."""
        entries = list(entries or [])
        if not entries:
            return []
        index = self._source_entry_index(src, entries)
        profile = smart_context_match.receiver_profile(service or {})
        names = [str(profile.get("clean_name") or (service or {}).get("name") or "")]
        if old_info:
            names.extend([str(old_info.get("display_name") or ""), str(old_info.get("channel_id") or "")])
        wanted_norms = []
        wanted_identities = []
        tokens = []
        for name in names:
            norm = channel_mapper.normalize_name(name)
            if norm and norm not in wanted_norms:
                wanted_norms.append(norm)
                for token in norm.split():
                    if len(token) >= 3 and token not in tokens:
                        tokens.append(token)
            ident = channel_identity_resolver.resolve(name)
            iid = str((ident or {}).get("id") or "")
            if iid and iid not in wanted_identities:
                wanted_identities.append(iid)
        pool = []
        seen = set()
        def add(epg):
            key = (str(epg.get("source_id") or ""), str(epg.get("channel_id") or "").lower())
            if key not in seen:
                seen.add(key); pool.append(epg)
        # Curated identity aliases are first-class candidates.  This is both
        # faster and more reliable than fuzzy text for renamed/short services
        # (2M National -> 2M, Al Aoula Inter -> Al Aoula, Sharjah HD -> TV).
        for iid in wanted_identities:
            for epg in index.get("by_identity", {}).get(iid, []):
                add(epg)
        for norm in wanted_norms:
            for epg in index.get("by_norm", {}).get(norm, []):
                add(epg)
        # Token intersection narrows huge providers from thousands of channels to
        # a few dozen likely candidates. Channel numbers remain part of scoring.
        for token in tokens[:5]:
            for epg in index.get("by_token", {}).get(token, [])[:160]:
                add(epg)
                if len(pool) >= 320:
                    break
            if len(pool) >= 320:
                break
        # Tiny providers can safely use the whole list; for huge providers we do
        # not fall back to a full fuzzy scan because that causes Enigma2 spinner.
        if not pool and len(entries) <= 240:
            pool = list(entries)
        ranked = []
        service_name = str((service or {}).get("name") or "")
        old_name = str((old_info or {}).get("display_name") or "")
        for epg in pool:
            name = str(epg.get("display_name") or epg.get("channel_id") or "")
            score = channel_mapper._token_score(service_name, name)
            if old_name:
                score = max(score, channel_mapper._token_score(old_name, name))
            ranked.append((int(score or 0), epg))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [epg for _score, epg in ranked[:max(12, int(limit or 96))]]

    def _best_epg_candidate_fast(self, service, src, entries, old_info=None, entry_index=None):
        """Return the safest best/runner-up candidate using a bounded shortlist."""
        if not service or not entries:
            return None, 0, 0, None
        shortlist = self._candidate_pool_fast(service, src, entries, old_info=old_info, limit=40)
        if not shortlist:
            return None, 0, 0, None
        best = None; best_score = -1; second = -1; best_result = None
        old_norm = channel_mapper.normalize_name(str((old_info or {}).get("display_name") or ""))
        service_norm = channel_mapper.normalize_name(str((service or {}).get("name") or ""))
        for epg in shortlist:
            try:
                result = id_mapping_engine.evaluate(
                    service, src, epg, learned=smartmatch_ai.learned_match(service, src, epg))
            except Exception:
                continue
            if not result.get("allowed"):
                continue
            score = int(result.get("score") or 0)
            epg_norm = channel_mapper.normalize_name(str(epg.get("display_name") or epg.get("channel_id") or ""))
            # Source replacement should preserve channel identity. A matching old
            # label (e.g. Al Arabiya) is a strong continuity signal, but cannot
            # turn an otherwise unsafe candidate into an automatic mapping.
            if old_norm and epg_norm == old_norm:
                score = min(100, score + 5)
            if service_norm and epg_norm == service_norm:
                score = max(score, 98)
            if score > best_score:
                second = best_score
                best, best_score, best_result = epg, score, result
            elif score > second:
                second = score
        return best, max(0, best_score), max(0, second), best_result

    def smart_audit_current_bouquet(self):
        """Audit current bouquet in a worker thread so navigation never blocks."""
        services = list(getattr(self, "_all_current_bouquet_services", None) or self.bouquet_services or [])
        if not services:
            self.session.open(MessageBox, "No receiver channels in the current bouquet.", MessageBox.TYPE_INFO)
            return
        mapped = {str(ref): [dict(x) for x in (infos or [])]
                  for ref, infos in (self._mapped_info_by_ref or {}).items()}
        sources = {str(x.get("source_id") or ""): dict(x) for x in self._all_source_groups
                   if x and not x.get("source_group") and x.get("source_id")}
        self["summary"].setText("SMART AUDIT  •  checking %d channel(s) in background..." % len(services))

        def worker():
            report = {"total": len(services), "healthy": 0, "unmapped": 0,
                      "conflicts": 0, "locked": 0, "suspicious": [], "cache_missing": set()}
            cache = {}
            for service in services:
                ref = str(service.get("ref") or "")
                infos = []
                for _key in self._ref_lookup_keys(ref):
                    infos.extend(mapped.get(_key) or [])
                if infos:
                    _seen = set(); _uniq = []
                    for _row in infos:
                        _sig = (str(_row.get("source_id") or ""), str(_row.get("channel_id") or ""), str(_row.get("mode") or ""))
                        if _sig not in _seen:
                            _seen.add(_sig); _uniq.append(_row)
                    infos = _uniq
                if not infos:
                    report["unmapped"] += 1
                    continue
                # Several SRP/provider candidates are normal alternatives. Only
                # legacy data with >1 explicit MANUAL owner is an ownership conflict.
                if sum(1 for x in infos if str((x or {}).get("mode") or "").lower() == "manual") > 1:
                    report["conflicts"] += 1
                if any(str(x.get("mode") or "").lower() == "manual" for x in infos):
                    report["locked"] += 1
                # Manual is authoritative; audit it only for presence, not to
                # second-guess a deliberate user correction.
                check = next((x for x in infos if str(x.get("mode") or "").lower() != "manual"), None)
                if check is None:
                    report["healthy"] += 1
                    continue
                sid = str(check.get("source_id") or "")
                src = sources.get(sid)
                if not src:
                    report["healthy"] += 1
                    continue
                if sid not in cache:
                    cache[sid] = self._cached_entries_for_source(src)
                entries = cache.get(sid) or []
                if not entries:
                    report["cache_missing"].add(sid)
                    report["healthy"] += 1
                    continue
                current = next((e for e in entries if str(e.get("channel_id") or "").lower() == str(check.get("channel_id") or "").lower()), None)
                current_score = -1
                if current is not None:
                    try:
                        rr = id_mapping_engine.evaluate(
                            service, src, current, learned=smartmatch_ai.learned_match(service, src, current))
                        current_score = int(rr.get("score") or 0) if rr.get("allowed") else 0
                    except Exception:
                        current_score = 0
                best, best_score, second, _res = self._best_epg_candidate_fast(service, src, entries)
                if current_score < 70 or (best is not None and best_score >= 92 and best_score >= current_score + 12):
                    report["suspicious"].append({
                        "service": service, "current": check, "current_score": current_score,
                        "best": best, "best_score": best_score, "second": second, "source": src})
                else:
                    report["healthy"] += 1
            report["cache_missing"] = sorted(report["cache_missing"])
            return report

        def done(report):
            suspicious = list((report or {}).get("suspicious") or [])
            lines = [
                "SMART MAPPING AUDIT", "",
                "Channels: %d" % int((report or {}).get("total") or 0),
                "Healthy / accepted: %d" % int((report or {}).get("healthy") or 0),
                "Unmapped: %d" % int((report or {}).get("unmapped") or 0),
                "Conflicts: %d" % int((report or {}).get("conflicts") or 0),
                "Manual locks: %d" % int((report or {}).get("locked") or 0),
                "Suspicious: %d" % len(suspicious),
            ]
            if suspicious:
                lines += ["", "First suspicious mappings:"]
                for row in suspicious[:7]:
                    lines.append("- %s: %s (%d%%) → %s (%d%%)" % (
                        row["service"].get("name", "channel"),
                        row["current"].get("display_name") or row["current"].get("channel_id"),
                        max(0, int(row.get("current_score") or 0)),
                        (row.get("best") or {}).get("display_name") or (row.get("best") or {}).get("channel_id") or "?",
                        int(row.get("best_score") or 0)))
            if (report or {}).get("cache_missing"):
                lines += ["", "%d source catalogue(s) were not cached; audit stayed offline." % len(report.get("cache_missing") or [])]
            self["summary"].setText("Smart Audit: %d suspicious • %d conflicts • %d unmapped" %
                                    (len(suspicious), int((report or {}).get("conflicts") or 0), int((report or {}).get("unmapped") or 0)))
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)

        def run_and_return():
            try:
                report = worker()
            except Exception as exc:
                report = {"total": len(services), "healthy": 0, "unmapped": 0, "conflicts": 0,
                          "locked": 0, "suspicious": [], "error": str(exc)}
            try:
                from twisted.internet import reactor
                reactor.callFromThread(done, report)
            except Exception:
                pass
        threading.Thread(target=run_and_return, daemon=True).start()

    def _mapping_source_choices_for_info(self, info):
        old_sid = str((info or {}).get("source_id") or "")
        choices = []
        for src in self._all_source_groups:
            if not src or src.get("source_group"):
                continue
            sid = str(src.get("source_id") or "")
            if not sid or sid == old_sid:
                continue
            choices.append((str(src.get("source_name") or sid), dict(src)))
        choices.sort(key=lambda x: _alpha_text(x[0]))
        return choices

    def _choose_old_mapping_for_service(self, service, callback):
        infos = list(self._mapping_infos_for_ref((service or {}).get("ref")) or [])
        if not infos:
            callback({})
            return
        # Manual ownership first, then deterministic maps.
        infos.sort(key=lambda x: (0 if str(x.get("mode") or "").lower() == "manual" else 1,
                                  _alpha_text(x.get("source_name") or x.get("source_id"))))
        if len(infos) == 1:
            callback(dict(infos[0])); return
        try:
            from Screens.ChoiceBox import ChoiceBox
            choices = []
            for info in infos:
                label = "%s / %s%s" % (info.get("source_name") or info.get("source_id") or "EPG",
                                         info.get("display_name") or info.get("channel_id") or "channel",
                                         "  [manual]" if str(info.get("mode") or "").lower()=="manual" else "")
                choices.append((label, dict(info)))
            self.session.openWithCallback(lambda ans: callback(dict(ans[1])) if ans else None,
                                          ChoiceBox, title="Which current mapping do you want to replace?", list=choices)
        except Exception:
            callback(dict(infos[0]))

    def _replacement_entries_for_source(self, src):
        """Load one target catalogue in a worker, fetching IDs only when needed."""
        entries = self._cached_entries_for_source(src)
        if entries:
            return self._stabilize_source_rows(src, entries)
        if not src.get("external"):
            return self._stabilize_source_rows(src, entries)
        item = dict(src)
        try:
            rows, _meta = remote_channel_catalog.fetch(item, timeout=8, max_scan=6 * 1024 * 1024)
        except Exception:
            rows = []
        rows = self._stabilize_source_rows(src, rows)
        if rows:
            try:
                source_channel_cache.put(str(src.get("source_id") or ""), src.get("source_name") or src.get("source_id"), rows,
                                         provider=src.get("provider") or "", region=src.get("region") or "",
                                         language=src.get("language") or "")
            except Exception:
                pass
        return rows

    def _begin_source_change(self, service, old_info, affected):
        old_info = dict(old_info or {})
        choices = self._mapping_source_choices_for_info(old_info)
        if not choices:
            self.session.open(MessageBox, "No alternative EPG source is available.", MessageBox.TYPE_INFO)
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
            self._bulk_old_info = old_info
            self._bulk_affected_services = list(affected or [service])
            self.session.openWithCallback(self._bulk_target_selected, ChoiceBox,
                                          title="Choose the new EPG source for %s" % (service.get("name") or "channel"),
                                          list=choices)
        except Exception as exc:
            self.session.open(MessageBox, "Could not open source selector.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def change_source_for_current_channel(self):
        """One-channel source correction; works from the receiver pane, not column 3."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select the receiver channel you want to correct first.", MessageBox.TYPE_INFO)
            return
        def chosen(old_info):
            self._begin_source_change(service, old_info, [service])
        self._choose_old_mapping_for_service(service, chosen)

    def bulk_replace_current_source(self):
        """Replace the current receiver channel's source for all affected services.

        beta60 deliberately does not depend on which provider happens to be
        highlighted in column 3.  The old source is read from the selected
        receiver channel's real mapping, which makes the workflow predictable.
        """
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select one receiver channel using the wrong source first.", MessageBox.TYPE_INFO)
            return
        def chosen(old_info):
            old_sid = str((old_info or {}).get("source_id") or "")
            if not old_sid:
                self.session.open(MessageBox, "This channel has no existing EPG source to replace.\nUse 'Fix source for selected channel' instead.", MessageBox.TYPE_INFO)
                return
            services = list(getattr(self, "_all_current_bouquet_services", None) or self.bouquet_services or [])
            affected = []
            for svc in services:
                info = self._primary_mapping_info_for_ref(svc.get("ref")) or {}
                if str(info.get("source_id") or "") == old_sid:
                    affected.append(svc)
            if not affected:
                affected = [service]
            self._begin_source_change(service, old_info, affected)
        self._choose_old_mapping_for_service(service, chosen)

    def _bulk_target_selected(self, answer):
        if not answer:
            return
        target = dict(answer[1] or {})
        affected = list(getattr(self, "_bulk_affected_services", []) or [])
        old = dict(getattr(self, "_bulk_old_info", {}) or {})
        if not target or not affected:
            return
        self["summary"].setText("SMART SOURCE CHANGE  •  preparing %s in background..." % (target.get("source_name") or target.get("source_id")))

        # For a single explicitly selected receiver service, the user has
        # already chosen the target provider.  Show the best channel and let the
        # user confirm it even when SmartMatch is below the conservative 96%%
        # bulk threshold.  Bulk operations remain SAFE-only.
        if len(affected) == 1:
            service = affected[0]
            def single_worker():
                entries = self._replacement_entries_for_source(target)
                if not entries:
                    return {"error": "The target source did not expose a channel catalogue. Nothing was changed."}
                best, score, second, result = self._best_epg_candidate_fast(service, target, entries, old_info=old)
                if best is None:
                    return {"error": "No matching EPG channel was found in %s." % (target.get("source_name") or target.get("source_id"))}
                return {"service": service, "epg": best, "score": int(score or 0), "second": int(second or 0),
                        "result": result, "target": target, "old": old}
            def single_done(payload):
                if payload.get("error"):
                    self.session.open(MessageBox, payload["error"], MessageBox.TYPE_INFO); return
                epg = payload.get("epg") or {}; score = int(payload.get("score") or 0)
                text = "FIX SOURCE FOR CHANNEL\n\n%s\n\n%s  →  %s / %s\nMatch confidence: %d%%%%\n\nApply this mapping?" % (
                    service.get("name") or "Receiver channel",
                    old.get("source_name") or "Current source",
                    target.get("source_name") or target.get("source_id") or "New source",
                    epg.get("display_name") or epg.get("channel_id") or "EPG channel", score)
                self._single_source_change_payload = payload
                self.session.openWithCallback(self._single_source_change_apply, MessageBox, text, MessageBox.TYPE_YESNO, default=True)
            def single_run():
                try: payload = single_worker()
                except Exception as exc: payload = {"error": str(exc)}
                try:
                    from twisted.internet import reactor
                    reactor.callFromThread(single_done, payload)
                except Exception:
                    pass
            threading.Thread(target=single_run, daemon=True).start()
            return

        def worker():
            entries = self._replacement_entries_for_source(target)
            if not entries:
                return {"error": "The target source did not expose a channel catalogue. Nothing was changed.",
                        "safe": [], "review": [], "unresolved": affected}
            safe=[]; review=[]; unresolved=[]
            old_sid = str(old.get("source_id") or "")
            for service in affected:
                old_for_service = next((x for x in self._mapping_infos_for_ref(service.get("ref"))
                                        if str((x or {}).get("source_id") or "") == old_sid), old)
                best, score, second, result = self._best_epg_candidate_fast(service, target, entries, old_info=old_for_service)
                if best is None:
                    unresolved.append(service); continue
                row={"service": service, "epg": best, "score": score, "second": second,
                     "result": result, "old_info": dict(old_for_service or {})}
                # beta124: bulk changes may auto-apply only PrecisionMatch
                # PROVEN rows. A high legacy/name score is never enough.
                if (result or {}).get("auto"):
                    safe.append(row)
                elif (result or {}).get("allowed"):
                    review.append(row)
                else:
                    unresolved.append(service)
            return {"safe":safe,"review":review,"unresolved":unresolved,"target":target,"old":old}

        def done(report):
            if report.get("error"):
                self.session.open(MessageBox, report["error"], MessageBox.TYPE_INFO)
                return
            safe=report.get("safe") or []; review=report.get("review") or []; unresolved=report.get("unresolved") or []
            lines=["SMART SOURCE CHANGE", "", "%s  →  %s" % (old.get("source_name","Current source"), target.get("source_name","New source")),
                   "", "Channels selected: %d" % len(affected), "Safe matches: %d" % len(safe),
                   "Need review: %d" % len(review), "Unresolved: %d" % len(unresolved)]
            for row in safe[:6]:
                lines.append("✓ %s  →  %s  %d%%" % (row["service"].get("name","channel"),
                                                      row["epg"].get("display_name") or row["epg"].get("channel_id"), row["score"]))
            lines += ["", "Apply SAFE matches now?", "Other channels remain unchanged."]
            self._bulk_preview_report = report
            self.session.openWithCallback(self._bulk_apply_answer, MessageBox, "\n".join(lines), MessageBox.TYPE_YESNO, default=False)

        def run_and_return():
            try: report=worker()
            except Exception as exc: report={"error":str(exc),"safe":[],"review":[],"unresolved":affected}
            try:
                from twisted.internet import reactor
                reactor.callFromThread(done, report)
            except Exception:
                pass
        threading.Thread(target=run_and_return, daemon=True).start()

    def _single_source_change_apply(self, answer):
        if not answer:
            return
        payload = dict(getattr(self, "_single_source_change_payload", None) or {})
        service = payload.get("service") or {}
        epg = payload.get("epg") or {}
        target = payload.get("target") or {}
        old = payload.get("old") or {}
        ref = service.get("ref")
        if not ref or not epg.get("channel_id") or not target.get("source_id"):
            return
        try:
            old_source_ids = [str((x or {}).get("source_id") or "") for x in self._mapping_infos_for_ref(ref) if (x or {}).get("source_id")]
            save_result = self.store.assign_exclusive(target.get("source_id"), epg.get("channel_id"), [ref],
                                        display_name=epg.get("display_name") or epg.get("channel_id"),
                                        old_source_ids=old_source_ids,
                                        label="Fix source %s -> %s" % (service.get("name") or "channel", target.get("source_name") or target.get("source_id")),
                                        defer_generated=True)
            self._queue_manual_generated_sync(save_result)
            try: smartmatch_ai.learn(service, target, epg)
            except Exception: pass
            info = {"source_id": target.get("source_id"),
                    "source_name": target.get("source_name") or self._source_label(target.get("source_id")),
                    "channel_id": epg.get("channel_id"),
                    "display_name": epg.get("display_name") or epg.get("channel_id"),
                    "mode": "manual"}
            self._paint_manual_mapping_fast([ref], info)
            self.mapping_revision += 1
            self.selection_cache.clear()
            self["summary"].setText("Mapped: %s  →  %s / %s" % (service.get("name") or "Channel",
                                    target.get("source_name") or target.get("source_id"),
                                    epg.get("display_name") or epg.get("channel_id")))
            self.session.open(MessageBox, "Source corrected immediately.\n\n%s now uses:\n%s / %s\n\nThe previous EPG source was removed from this receiver service. Native Import will use the new owner." % (
                              service.get("name") or "Channel", target.get("source_name") or target.get("source_id"),
                              epg.get("display_name") or epg.get("channel_id")), MessageBox.TYPE_INFO, timeout=6)
        except Exception as exc:
            log.exception("Single source correction failed")
            self.session.open(MessageBox, "Source correction failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _bulk_apply_answer(self, answer):
        if not answer:
            return
        report = getattr(self, "_bulk_preview_report", None) or {}
        safe = list(report.get("safe") or [])
        target = report.get("target") or {}
        old = report.get("old") or {}
        if not safe:
            return
        records=[]
        sid=str(target.get("source_id") or "")
        for row in safe:
            epg=row.get("epg") or {}; service=row.get("service") or {}
            records.append({"source_id":sid,"channel_id":epg.get("channel_id"),
                            "display_name":epg.get("display_name") or epg.get("channel_id"),
                            "refs":[service.get("ref")]})
            try: smartmatch_ai.learn(service, target, epg)
            except Exception: pass
        try:
            old_source_ids = set()
            for row in safe:
                service = row.get("service") or {}
                info = self._primary_mapping_info_for_ref(service.get("ref")) or {}
                sid_old = str(info.get("source_id") or "")
                if sid_old and sid_old != sid:
                    old_source_ids.add(sid_old)
            old_source_ids.add(str(old.get("source_id") or ""))
            result=self.store.bulk_assign(records, label="Replace source %s -> %s" %
                                          (old.get("source_name","source"), target.get("source_name","source")),
                                          remove_source_id=str(old.get("source_id") or ""),
                                          remove_source_ids=sorted(x for x in old_source_ids if x))
            self._source_search_indexes.pop(str(target.get("source_id") or ""), None)
            self._rebuild_mapping_cache(); self.mapping_revision += 1; self.selection_cache.clear(); self.refresh_channels(); self._update_summary()
            self.session.open(MessageBox, "Source change applied.\n\n%d safe channel(s) updated.\n%d mapping item(s) changed.\n\nOld source links for those receiver services were removed from the generated channels map.\nUse Undo to restore the complete previous state." %
                              (len(safe), int((result or {}).get("changed") or 0)), MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception("Source replacement failed")
            self.session.open(MessageBox, "Source replacement failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def verify_lamedb_update(self):
        """Verify the live receiver lamedb without forcing expensive work.

        load_satellite_registry() compares only file signatures when unchanged.
        If lamedb/lamedb5 really changed, it rebuilds the small receiver registry
        and then refreshes derived SRP maps once in the background.
        """
        try:
            services, rebuilt = channel_registry.load_satellite_registry(force=False)
            self._lamedb_rebuilt = bool(rebuilt)
            self._lamedb_state = "UPDATED" if rebuilt else "CURRENT"
            path = channel_registry.active_lamedb_path()
            try:
                self._lamedb_stamp = time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(path)))
            except Exception:
                self._lamedb_stamp = ""
            if rebuilt:
                # Refresh the in-memory SAT registry immediately; SRP derivation
                # stays background/low-impact.
                self._sat_registry = list(services or [])
                self.catalog = list(self._sat_registry)
                self.refresh_channels()
                try:
                    srp_master_engine.rebuild_async(config=self.config, force=True)
                except Exception:
                    pass
            self._update_action_labels()
            msg = "LAMEDB %s" % self._lamedb_state
            if self._lamedb_stamp:
                msg += "\nLast receiver database update: %s" % self._lamedb_stamp
            msg += "\n\n" + ("SRP refresh started in background." if rebuilt else "No receiver database change detected. Cached SRP maps remain valid.")
            self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=6)
        except Exception as exc:
            self._lamedb_state = "UNKNOWN"
            self._update_action_labels()
            self.session.open(MessageBox, "Could not verify lamedb.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def rebuild_global_srp_database(self):
        """Refresh deterministic SRP knowledge from current receiver data.

        This action never deletes manual mappings and never relies on Import All
        to rebuild anything.  It refreshes the current lamedb-backed registry,
        asks the full OpenEPG Channel-ID harvester to verify catalogues, and
        atomically rebuilds derived *.channels.xml maps in background.
        """
        try:
            self["summary"].setText("Refreshing live lamedb SRP maps + full Channel-ID catalogues in background...")
            try:
                source_id_harvester.refresh_async(config=self.config, force=True)
            except Exception:
                log.exception("Could not start full Channel-ID refresh")
            try:
                srp_master_engine.rebuild_async(config=self.config, force=True)
            except Exception:
                log.exception("Could not start deterministic SRP rebuild")
            self.session.open(
                MessageBox,
                "SRP refresh started in background.\n\n"
                "Source of truth: /etc/enigma2/lamedb (or lamedb5 when active).\n"
                "OpenEPG Channel IDs are verified without importing programme data.\n\n"
                "Manual mappings are preserved. Import All does not rebuild mappings.",
                MessageBox.TYPE_INFO, timeout=7)
        except Exception as exc:
            log.exception("Could not refresh SRP maps")
            self.session.open(MessageBox, "Could not start SRP refresh.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def reset_all_sids_and_rebuild(self):
        """Destructive EPGManager-only SID reset with automatic fresh rebuild.

        Receiver lamedb, bouquets and favourites are explicitly outside the
        reset scope.  The operation creates one rollback archive, clears old
        mapping/SRP/cache state, downloads current direct XMLTV feeds, rebuilds
        canonical IDs, then launches Import All when a Manager is available.
        """
        if getattr(self, "_sid_reset_running", False):
            self.session.open(MessageBox, "SID reset is already running.", MessageBox.TYPE_INFO, timeout=4)
            return
        text = (
            "RESET ALL EPG SIDs?\n\n"
            "This removes ALL old EPGManager mappings, blocked/ignored SID state, "
            "generated channels.xml maps and their pre-bulk backups.\n\n"
            "It will NOT delete lamedb, bouquets, favourites or tuner services.\n"
            "A rollback archive is created first.\n\n"
            "Then the 7 current GitHub feeds are downloaded, SIDs rebuilt from "
            "their canonical XMLTV IDs, and Import All starts automatically."
        )
        self.session.openWithCallback(self._confirm_reset_all_sids, MessageBox, text, MessageBox.TYPE_YESNO)

    def _confirm_reset_all_sids(self, answer):
        if not answer:
            return
        self._sid_reset_running = True
        try:
            self["summary"].setText("Resetting all SIDs • backup → purge → fresh GitHub rebuild…")
        except Exception:
            pass

        def worker():
            try:
                result = sid_reset.reset_and_rebuild(config=self.config, store=self.store)
            except Exception as exc:
                log.exception("Full SID reset failed")
                result = {"ok": False, "error": str(exc), "sources_ok": 0, "sources_total": 5}
            try:
                from twisted.internet import reactor
                reactor.callFromThread(self._sid_reset_rebuild_done, result)
            except Exception:
                self._sid_reset_rebuild_done(result)

        threading.Thread(target=worker, name="EPGM-SID-Reset", daemon=True).start()

    def _sid_reset_rebuild_done(self, result):
        result = dict(result or {})
        if not result.get("ok"):
            self._sid_reset_running = False
            msg = "SID reset/rebuild failed.\n\n%s" % str(result.get("error") or (result.get("master") or {}).get("reason") or "Unknown error")
            self.session.open(MessageBox, msg, MessageBox.TYPE_ERROR)
            return
        # Refresh the visible Smart Mapping state from the brand-new SRP master.
        try:
            self.refresh_sources(reload_preferences=True)
            self.refresh_channels()
        except Exception:
            log.exception("Could not repaint Smart Mapping after SID reset")

        # Complete the user's requested one-action workflow: replace old SIDs,
        # then inject the freshly mapped programmes. rc37's Direct runner has an
        # eEPGCache fallback when the EPGImport plugin is not installed.
        if self.manager is not None:
            try:
                started = self.manager.run_fast_import_async(
                    on_complete=lambda imported: self._sid_reset_import_done(result, imported))
            except Exception:
                started = False
                log.exception("Could not launch Import All after SID reset")
            if started:
                try:
                    self["summary"].setText("SIDs rebuilt • importing all fresh EPG now…")
                except Exception:
                    pass
                return

        self._sid_reset_running = False
        lines = [
            "ALL SIDs RESET + REBUILT",
            "",
            "Sources rebuilt: %d/%d" % (int(result.get("sources_ok") or 0), int(result.get("sources_total") or 0)),
            "Old mapping rows removed: %d" % int(result.get("removed_mappings") or 0),
            "Safety: lamedb/bouquets untouched",
        ]
        if result.get("backup"):
            lines.append("Backup: %s" % result.get("backup"))
        lines += ["", "Mapping is fresh. Run Import All to inject programmes."]
        self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)

    def _sid_reset_import_done(self, reset_result, imported):
        def show():
            self._sid_reset_running = False
            imported = dict(imported or {}) if isinstance(imported, dict) else {}
            ok = bool(imported.get("ok", True))
            native = imported.get("native_import") or imported.get("import") or imported
            if not isinstance(native, dict):
                native = {}
            events = int(native.get("imported_events") or imported.get("imported_events") or 0)
            engine = str(native.get("engine") or imported.get("engine") or "Native Import")
            try:
                self.refresh_sources(reload_preferences=True)
                self.refresh_channels()
            except Exception:
                pass
            lines = [
                "RESET + REMAP + IMPORT COMPLETE" if ok else "RESET/REMAP COMPLETE • IMPORT WARNING",
                "",
                "Sources rebuilt: %d/%d" % (int(reset_result.get("sources_ok") or 0), int(reset_result.get("sources_total") or 0)),
                "Imported events: %d" % events,
                "Engine: %s" % engine,
                "Safety: lamedb/bouquets untouched",
            ]
            if reset_result.get("backup"):
                lines.append("Rollback: %s" % reset_result.get("backup"))
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO if ok else MessageBox.TYPE_ERROR)
        # Manager completion can originate from a worker on some images.
        try:
            from twisted.internet import reactor
            reactor.callFromThread(show)
        except Exception:
            show()

    def show_id_coverage_audit(self):
        """Show the latest source->SRP and receiver->EPG coverage counters."""
        try:
            data = id_coverage_audit.load() or {}
            summary = data.get("summary") or {}
            if not summary:
                try:
                    data = id_coverage_audit.build(config=self.config) or {}
                    summary = data.get("summary") or {}
                except Exception:
                    summary = {}
            sats = data.get("satellites") or {}
            lines = [
                "Channel-ID Coverage Audit",
                "",
                "IDs linked: %d / %d" % (int(summary.get("linked_ids") or 0), int(summary.get("total_ids") or 0)),
                "Real match failures: %d" % int(summary.get("match_failed") or 0),
                "Not on receiver: %d" % int(summary.get("not_present") or 0),
                "Non-TV / virtual / bad ID: %d" % (
                    int(summary.get("non_tv") or 0) + int(summary.get("online_virtual") or 0) + int(summary.get("bad_source_ids") or 0)),
                "SRP links: %d" % int(summary.get("srp_links") or 0),
                "Conflicts: %d" % int(summary.get("conflicts") or 0),
                "",
                "Priority satellites:",
            ]
            for wanted in ("26.0E", "25.5E", "7.0W", "8.0W"):
                row = sats.get(wanted) or {}
                lines.append("%s  %d/%d mapped (%.1f%%) • match-failed %d • no-ID %d" % (
                    wanted, int(row.get("mapped") or 0), int(row.get("services") or 0),
                    float(row.get("coverage") or 0.0), int(row.get("match_failed") or 0),
                    int(row.get("no_epg_id") or 0)))
            lines.extend(["", "Full report:", id_coverage_audit.TEXT_PATH])
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception("Could not display ID coverage audit")
            self.session.open(MessageBox, "Coverage audit unavailable.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def toggle_current_auto_source(self):
        # Compatibility hook retained for old callers. beta81 has one source
        # policy, so toggling here is exactly the same as toggling Smart Sources.
        src = self._current("sources", self.source_groups)
        if not src or src.get("source_group"):
            self.session.open(MessageBox, "Select an individual EPG source first.", MessageBox.TYPE_INFO)
            return
        sid = str(src.get("source_id") or "")
        try:
            item = source_catalog.by_mapping_id().get(sid) or source_catalog.by_catalogue_id().get(sid)
            cid = source_catalog.catalogue_source_id(item) if item else ""
            if not cid:
                return
            enabled = self.source_preferences.toggle_catalogue(cid, save=True)
            self.auto_selected_source_ids = set(source_variant_policy.normalize_selected(
                self.source_preferences.selected_mapping_ids()))
            self.auto_source_filter_active = bool(self.auto_selected_source_ids)
            self._rebuild_source_view(preserve_id=sid)
            self["summary"].setText("%s %s in Smart Sources." %
                                    (src.get("source_name", sid), "ENABLED" if enabled else "DISABLED"))
        except Exception as exc:
            log.exception("Could not change source selection")
            self.session.open(MessageBox, "Could not save source selection.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def reset_auto_sources(self):
        # Legacy compatibility: there is no secondary Auto Mapping allow-list
        # to reset in beta81. Refresh the canonical Smart Sources selection.
        try:
            self.source_preferences = SourcePreferences()
            self.auto_selected_source_ids = set(source_variant_policy.normalize_selected(
                self.source_preferences.selected_mapping_ids()))
            self.auto_source_filter_active = bool(self.auto_selected_source_ids)
            current = self._current("sources", self.source_groups)
            self._rebuild_source_view(preserve_id=(current or {}).get("source_id"))
            self["summary"].setText("Smart Mapping uses the current Smart Sources selection.")
        except Exception as exc:
            log.exception("Could not reload source selection")
            self.session.open(MessageBox, "Could not reload source selection.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def ignore_current_channel(self):
        """Mark an unmapped receiver service as intentionally having no EPG."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            return
        ref = str(service.get("ref") or "").strip()
        if not ref:
            return
        try:
            if self._service_is_mapped(service):
                self.session.open(MessageBox, "Remove the current mapping first, then mark the channel as No EPG / Ignore.", MessageBox.TYPE_INFO, timeout=5)
                return
            changed = self.store.ignore_ref(ref)
            try:
                _ignored = self.store.ignored_refs()
                self._refresh_ignored_ref_keys(_ignored)
                smart_mapping_warm_cache.update_ignored_refs(_ignored)
            except Exception:
                pass
            # It is no longer an actionable exception.
            for key in self._ref_lookup_keys(ref):
                self._repair_exception_kind.pop(key, None)
            self.refresh_channels()
            self._set_index("channels", 0)
            self["summary"].setText("NO EPG / IGNORE • %s • excluded from Auto Repair and Mapping Health" % (service.get("name") or "channel"))
            self._update_action_labels()
            if not changed:
                self["summary"].setText("Already ignored • %s" % (service.get("name") or "channel"))
        except Exception as exc:
            log.exception("Could not ignore service")
            self.session.open(MessageBox, "Could not ignore channel.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def unignore_current_channel(self):
        """Return a NO EPG service to the normal repair queue."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            return
        ref = str(service.get("ref") or "").strip()
        if not ref:
            return
        try:
            changed = self.store.unignore_ref(ref)
            try:
                _ignored = self.store.ignored_refs()
                self._refresh_ignored_ref_keys(_ignored)
                smart_mapping_warm_cache.update_ignored_refs(_ignored)
            except Exception:
                pass
            self.refresh_channels()
            self._set_index("channels", 0)
            self["summary"].setText("RESTORED TO REPAIR QUEUE • %s" % (service.get("name") or "channel"))
            self._update_action_labels()
            if not changed:
                self["summary"].setText("Channel was not ignored • %s" % (service.get("name") or "channel"))
        except Exception as exc:
            log.exception("Could not restore ignored service")
            self.session.open(MessageBox, "Could not restore channel.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def unmap_current_channel(self):
        """Instant, persistent GREEN Unmap for the highlighted receiver service.

        Removes manual ownership, prunes generated Native EPGImport maps and
        stores a tiny tombstone so frozen deterministic SRP does not immediately
        re-claim the service.  A later manual/Smart assignment clears the tombstone.
        """
        service = self._current("channels", self.bouquet_services)
        if not service:
            return
        ref = str(service.get("ref") or "").strip()
        if not ref:
            return
        removed = 0
        infos_before = list(self._mapping_infos_for_ref(ref) or [])
        source_ids = sorted(set(str(x.get("source_id") or "").strip().lower() for x in infos_before if x.get("source_id")))
        try:
            all_maps = self.store.all()
            target_keys = set(self._ref_lookup_keys(ref))
            for key, saved in list(all_maps.items()):
                refs = list((saved or {}).get("refs") or [])
                if not any(target_keys.intersection(self._ref_lookup_keys(x)) for x in refs):
                    continue
                refs = [x for x in refs if not target_keys.intersection(self._ref_lookup_keys(x))]
                if "::" not in key:
                    continue
                source_id, channel_id = key.split("::", 1)
                if refs:
                    self.store.set(source_id, channel_id, refs, mode=(saved or {}).get("mode", "manual"),
                                   display_name=(saved or {}).get("display_name"))
                else:
                    self.store.remove(source_id, channel_id)
                removed += 1

            # Remove this receiver service from every generated source currently
            # claiming it. This keeps Native EPGImport routing in sync immediately.
            pruned = 0
            for sid in source_ids:
                try:
                    meta = MappingStore._prune_generated_source_refs(sid, [ref])
                    pruned += int((meta or {}).get("changed") or 0)
                except Exception:
                    pass

            # Persist explicit UNMAPPED ownership so the immutable SRP engine can
            # remain frozen while respecting the user's manual choice in the UI.
            try:
                self.store.block_ref(ref)
            except Exception:
                pass
            self._rebuild_mapping_cache()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self._smart_name_hint_cache.clear()
            self._smart_recommended_source_id = ""
            self._smart_recommended_channel_id = ""
            self._preserve_receiver_selection(ref)
            self._update_instant_name_hint()
            # Rebuild only the small source menu so ★ BEST follows the newly
            # unmapped receiver channel. No source XML/network work is started.
            current_src = self._current("sources", self.source_groups) or {}
            self._rebuild_source_view(preserve_id=current_src.get("source_id"))
            self.update_source_title()
            self["summary"].setText("○ UNMAPPED INSTANTLY • %s • %d mapping record%s removed%s" % (
                service.get("name", "channel"), removed, "" if removed == 1 else "s",
                (" • %d native link%s removed" % (pruned, "" if pruned == 1 else "s")) if pruned else ""))
            self._update_action_labels()
        except Exception as exc:
            log.exception("Could not unmap service")
            self.session.open(MessageBox, "Could not unmap channel.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def show_match_reason(self):
        """INFO -> Why this match? using the same hard gates as assignment."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select a SAT receiver channel first.", MessageBox.TYPE_INFO)
            return

        epg = self._current("selections", self.selection_entries)
        src = self._current("sources", self.source_groups)
        if epg and epg.get("separator"):
            epg = None
        if epg and epg.get("source_id"):
            concrete = self._source_by_id(epg.get("source_id"))
            if concrete:
                src = concrete
        if not epg or not src or src.get("source_group"):
            try:
                hint = self._best_name_suggestion(service) if self._smart_name_index_ready else None
            except Exception:
                hint = None
            if hint:
                epg = hint.get("epg") or epg
                src = hint.get("source") or src

        sat_ctx = smart_context_engine.service_context(service)
        orbital = sat_ctx.get("orbital")
        sat_label = "-"
        if orbital is not None:
            sat_label = ("%.1fE" % orbital) if orbital >= 0 else ("%.1fW" % abs(orbital))
            friendly = self._satellite_friendly_name(orbital)
            if friendly:
                sat_label += "  " + friendly
        provider = str(sat_ctx.get("provider") or service.get("provider_name") or "-")
        country = str(sat_ctx.get("country") or "-")
        language = str(sat_ctx.get("language") or "-").upper()
        ref = str(service.get("ref") or "-")
        try:
            canonical_ref = str(channel_registry.canonical_service_ref(ref) or ref)
        except Exception:
            canonical_ref = ref
        try:
            owner_rows = list(self._mapping_infos_for_ref(ref) or [])
        except Exception:
            owner_rows = []
        try:
            durable_owner = dict(self._primary_mapping_info_for_ref(ref) or {})
        except Exception:
            durable_owner = {}
        owner_text = "UNMAPPED"
        if durable_owner:
            owner_text = "%s / %s [%s]" % (
                durable_owner.get("source_name") or durable_owner.get("source_id") or "source",
                durable_owner.get("display_name") or durable_owner.get("channel_id") or "id",
                str(durable_owner.get("mode") or "mapped").upper())
        alternatives = max(0, len(owner_rows) - (1 if durable_owner else 0))

        if not epg or not src or src.get("source_group"):
            lines = [
                "WHY THIS MATCH?", "",
                "SAT channel: %s" % (service.get("name") or "Channel"),
                "Satellite: %s" % sat_label,
                "DVB provider: %s" % provider,
                "Country / language: %s / %s" % (country, language),
                "ServiceRef: %s" % ref,
                "CanonicalRef: %s" % canonical_ref,
                "Active owner: %s" % owner_text,
                "Stored alternatives: %d" % alternatives,
                "", "No concrete safe EPG candidate is selected.",
                "Auto Mapping uses prepared candidates only after GREEN; fuzzy text alone is never auto-written.",
            ]
            self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)
            return

        try:
            visible_score = self._name_only_score(
                (smart_context_match.receiver_profile(service) or {}).get("clean_name") or service.get("name") or "",
                epg.get("display_name") or epg.get("channel_id") or "")
        except Exception:
            visible_score = 0
        try:
            ctx = smart_context_match.evaluate(service, src, epg, visible_score) or {}
        except Exception:
            ctx = {}
        try:
            feed_ok, feed_reason = service_variant_guard.check_epg_row(service, src, epg)
        except Exception as exc:
            feed_ok, feed_reason = False, "feed guard error: %s" % exc
        try:
            route_ok, route_score, route_reason = programme_feed_policy.route(service, src, epg)
        except Exception as exc:
            route_ok, route_score, route_reason = False, -999, "route guard error: %s" % exc
        try:
            lang_ok, lang_reason = multilingual_guard.check(service, src, epg, receiver_context=sat_ctx)
        except Exception as exc:
            lang_ok, lang_reason = False, "language guard error: %s" % exc
        source_id = str(src.get("source_id") or src.get("id") or "-")
        source_name = str(src.get("source_name") or src.get("name") or source_id)
        source_lang = str(smart_context_match.source_language(src) or "-").upper()
        epg_name = str(epg.get("display_name") or epg.get("channel_id") or "-")
        epg_id = str(epg.get("channel_id") or "-")
        try:
            qmetrics = source_quality.get_channel_cached(source_id, epg_id)
        except Exception:
            qmetrics = {}
        no_programmes = bool(qmetrics.get("specific") and qmetrics.get("profile_complete") and
                             int(qmetrics.get("programs") or 0) <= 0)
        if no_programmes:
            programme_line = "EPG programmes: NONE • confirmed full source profile"
        elif qmetrics.get("specific") and int(qmetrics.get("programs") or 0) > 0:
            programme_line = "EPG programmes: %d%s" % (
                int(qmetrics.get("programs") or 0),
                " • sampled" if not qmetrics.get("profile_complete") else "")
        else:
            programme_line = "EPG programmes: status not fully verified"
        all_ok = bool(feed_ok and route_ok and lang_ok and ctx.get("allowed", False) and not no_programmes)
        verified = bool(ctx.get("verified")) and not no_programmes
        status = "NO EPG" if no_programmes else ("VERIFIED" if all_ok and verified else ("STRONG" if all_ok else "BLOCKED"))
        reason = (ctx.get("reason") or route_reason or feed_reason or lang_reason or "-") if all_ok else (
            feed_reason if not feed_ok else (route_reason if not route_ok else (lang_reason if not lang_ok else ctx.get("reason") or "candidate rejected")))

        lines = [
            "WHY THIS MATCH?  •  %s" % status, "",
            "SAT channel: %s" % (service.get("name") or "Channel"),
            "Satellite: %s" % sat_label,
            "DVB provider: %s" % provider,
            "Country / language: %s / %s" % (country, language),
            "ServiceRef: %s" % ref,
            "CanonicalRef: %s" % canonical_ref,
            "Active owner: %s" % owner_text,
            "Stored alternatives: %d" % alternatives,
            "", "Candidate: %s" % epg_name,
            "EPG ID: %s" % epg_id,
            "Source: %s  [%s]" % (source_name, source_lang),
            "", "Identity/name: %d%%  •  %s" % (int(ctx.get("score") or visible_score or 0), "VERIFIED" if verified else "REVIEW"),
            "Feed guard: %s  •  %s" % ("PASS" if feed_ok else "BLOCK", feed_reason),
            "Provider/SAT/lang: %s  •  %s" % ("PASS" if route_ok else "BLOCK", route_reason),
            "Language guard: %s  •  %s" % ("PASS" if lang_ok else "BLOCK", lang_reason),
            programme_line,
            "Context rank: %s" % str(smart_context_engine.rank_source(service, src, unified_channel_identity.canonical_id(service.get("name") or ""))),
            "", "FINAL: %s" % ("SAFE TO MAP" if all_ok else "DO NOT MAP"),
            "Reason: %s" % str(reason),
        ]
        self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)

    def _source_score_for_channel(self, channel_name, src):
        service = self._current("channels", self.bouquet_services) or {"name": channel_name}
        try:
            return smartmatch_ai.source_affinity(service, src)
        except Exception:
            return channel_mapper._token_score(channel_name, src.get("source_name", ""))

    def _jump_to_best_source_for_current_channel(self):
        """Preselect the smartest EPG source for the current receiver channel.

        Priority: existing mapping -> real cached channel-name match.
        No network request and no programme parsing are ever started here.
        """
        service = self._current("channels", self.bouquet_services)
        if not service or not self.source_groups:
            return
        primary = self._primary_mapping_info_for_ref(service.get("ref")) or {}
        wanted = primary.get("source_id") or None
        hint = None
        if not wanted and self._smart_name_index_ready:
            hint = self._best_name_suggestion(service)
            if hint:
                wanted = hint["source"].get("source_id")
                self._smart_recommended_source_id = str(wanted or "")
                self._smart_recommended_channel_id = str(hint["epg"].get("channel_id") or "")
        if wanted:
            src = self._make_source_visible(wanted)
            if src:
                self.update_source_title()
                self._ensure_source_loaded(src)
                self.refresh_selection()
                if hint:
                    self._select_epg_id_in_current_list(hint["epg"].get("channel_id"))
                return True

        # beta69: never invent a Suggested Source from provider/source-name
        # affinity.  That legacy fallback produced misleading entries such as
        # US / UK-Ireland FreeSat/FTA for unrelated receiver channels while the
        # name index was still warming.  A suggestion now exists only when an
        # actual cached EPG channel ID/name matches the receiver service.
        self._smart_recommended_source_id = ""
        self._smart_recommended_channel_id = ""
        self.update_source_title()
        return False

    def review_next_suggestion(self):
        epg_norm_to_source = {}
        for epg in self.epg_channels:
            norm = channel_mapper.normalize_name(epg.get("display_name", ""))
            if norm and norm not in epg_norm_to_source:
                epg_norm_to_source[norm] = epg
        candidates = self.bouquet_services if self.bouquet_services else self.catalog
        for i, service in enumerate(candidates):
            if self._service_is_mapped(service):
                continue
            norm = channel_mapper.normalize_name(service.get("name", ""))
            epg = epg_norm_to_source.get(norm)
            if epg:
                if self.bouquet_services:
                    self._set_index("channels", i)
                    self.focus = 1
                    self.update_focus()
                    self._jump_to_best_source_for_current_channel()
                self["summary"].setText("Suggestion: %s  →  %s" %
                                        (service.get("name", "channel"), epg.get("display_name", "EPG")))
                return
        self.session.open(MessageBox, "No exact-name suggestion is currently loaded.\n\nOpen/refresh the relevant EPG source IDs first.", MessageBox.TYPE_INFO)

    def review_next_conflict(self):
        conflict_refs = [ref for ref, infos in self._mapped_info_by_ref.items() if len(infos or []) > 1]
        if not conflict_refs:
            self.session.open(MessageBox, "No mapping conflict found.", MessageBox.TYPE_INFO)
            return
        target = conflict_refs[0]
        for i, service in enumerate(self.bouquet_services):
            if service.get("ref") == target:
                self._set_index("channels", i)
                self.focus = 1
                self.update_focus()
                infos = self._mapped_info_by_ref.get(target) or []
                self["summary"].setText("Conflict: %s has %d EPG mappings  •  BLUE → Remove mapping" %
                                        (service.get("name", "channel"), len(infos)))
                return
        self.session.open(MessageBox, "A mapping conflict exists outside the current bouquet.\nOpen the affected bouquet to review it.", MessageBox.TYPE_INFO)

    def search_channel(self):
        if not self.bouquet_services:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(self._search_channel_answer, VirtualKeyBoard, title="Search channel", text="")
        except Exception:
            try:
                from Screens.InputBox import InputBox
                self.session.openWithCallback(self._search_channel_answer, InputBox, title="Search channel", text="")
            except Exception:
                self.session.open(MessageBox, "Search keyboard is not available on this image.", MessageBox.TYPE_INFO)

    def _search_channel_answer(self, text):
        text = channel_mapper.normalize_name(text or "")
        if not text:
            return
        best = None
        for i, item in enumerate(self.bouquet_services):
            n = channel_mapper.normalize_name(item.get("name", ""))
            if text in n:
                best = i
                break
        if best is None:
            scores = [(channel_mapper._token_score(text, x.get("name", "")), i) for i, x in enumerate(self.bouquet_services)]
            if scores:
                score, idx = max(scores)
                if score >= 65:
                    best = idx
        if best is None:
            self.session.open(MessageBox, "No matching channel found in this bouquet.", MessageBox.TYPE_INFO)
            return
        self._set_index("channels", best)
        self.focus = 1
        self.update_focus()
        self.update_source_title()

    def _preview_rows(self, epg, limit=3):
        path = epg.get("epg_xml_path") or self._source_xml_path(self._current("sources", self.source_groups))
        if not path or not os.path.exists(path):
            return []
        import time as _time
        from xml.etree import ElementTree as ET
        out = []
        now = int(_time.time())
        try:
            for _event, elem in ET.iterparse(path, events=("end",)):
                if elem.tag != "programme" or elem.get("channel") != epg.get("channel_id"):
                    elem.clear(); continue
                start = parse_xmltv_time(elem.get("start"))
                stop = parse_xmltv_time(elem.get("stop"))
                if stop and stop < now:
                    elem.clear(); continue
                title_el = elem.find("title")
                title = (title_el.text or "Programme") if title_el is not None else "Programme"
                when = ""
                if start:
                    try: when = _time.strftime("%H:%M", _time.localtime(start)) + "  "
                    except Exception: pass
                out.append(when + title.strip())
                elem.clear()
                if len(out) >= limit:
                    break
        except Exception:
            log.exception("EPG preview failed")
        return out

    def preview_three_programmes(self):
        epg = self._current("selections", self.selection_entries)
        if not epg or epg.get("separator"):
            self.session.open(MessageBox, "Select an EPG channel first.", MessageBox.TYPE_INFO)
            return
        rows = self._preview_rows(epg, 3)
        text = "Next programmes for %s:\n\n%s" % (epg.get("display_name", "EPG"), "\n".join(rows or ["No upcoming programmes found."]))
        self.session.open(MessageBox, text, MessageBox.TYPE_INFO)

    def undo_last_mapping(self):
        try:
            if not self.store.undo_last():
                self.session.open(MessageBox, "No mapping change to undo.", MessageBox.TYPE_INFO)
                return
            self._rebuild_mapping_cache()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self.refresh_channels()
            if self.focus == 3:
                self.refresh_selection()
            self._update_summary()
            self.session.open(MessageBox, "Last mapping change restored.", MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception("Undo mapping failed")
            self.session.open(MessageBox, "Could not undo the last mapping.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def refresh_manual_view(self):
        """Cheap UI refresh; never downloads, imports, or reparses all feeds."""
        self._rebuild_mapping_cache()
        self.refresh_channels()
        if self.focus == 3:
            src = self._current("sources", self.source_groups)
            self._ensure_source_loaded(src)
            self.refresh_selection()
        self._update_summary()

    def _save_beta1_style_matches(self, src, entries, threshold):
        """SmartMatch Auto Mapping is IPTV-only and single-owner.

        8.2.1 batches proven matches through MappingStore's exclusive AUTO path,
        so one receiver ServiceRef cannot be written to several source/ID rows in
        the same pass.
        """
        iptv_catalog = [x for x in self.catalog
                        if str((x or {}).get("service_type") or channel_mapper.classify_service_ref((x or {}).get("ref")) or "").upper() == "IPTV"]
        results = channel_mapper.smart_match_channels(entries, iptv_catalog, min_score=threshold, store=None)
        saved = skipped = 0
        manual_refs = self.store.manual_refs() if self._protect_manual_enabled() else set()
        records = []
        for entry in results:
            matches = entry.get("matches") or []
            if len(matches) != 1:
                skipped += 1; continue
            match = matches[0]
            if self._protect_manual_enabled():
                existing = self.store.get(entry.get("source_id"), entry.get("channel_id")) or {}
                if str(existing.get("mode") or "").lower() == "manual" or match.get("ref") in manual_refs:
                    skipped += 1; continue
            try:
                result = precision_match_engine.evaluate(
                    match, src, entry,
                    text_score=channel_mapper._token_score(str((match or {}).get("name") or ""), entry.get("display_name") or entry.get("channel_id") or ""),
                    learned=smartmatch_ai.learned_match(match, src, entry), balanced=True)
            except Exception:
                skipped += 1; continue
            if not result.get("auto"):
                skipped += 1; continue
            records.append({
                "source_id": entry.get("source_id"),
                "channel_id": entry.get("channel_id"),
                "refs": [match.get("ref")],
                "display_name": entry.get("display_name"),
                "confidence": int(result.get("confidence") or 0),
                "reason": str(result.get("reason") or "SmartMatch IPTV"),
            })
        if records:
            result = self.store.bulk_assign_auto_exclusive(records, label="SmartMatch IPTV Source")
            saved = int((result or {}).get("refs") or 0)
            # A same-ref duplicate candidate intentionally loses the one-owner
            # arbitration; count it as review/skipped rather than as a save.
            skipped += max(0, len(records) - saved)
        return saved, skipped

    def auto_map_source(self):
        src = self._current("sources", self.source_groups)
        if not src or src.get("source_group"):
            self.session.open(MessageBox, "Choose one EPG source first.", MessageBox.TYPE_INFO); return
        if not self._source_mapping_enabled(src):
            self.session.open(MessageBox, "This source is OFF. Force/Auto Mapping uses ON sources only. Enable it from Smart Sources first.", MessageBox.TYPE_INFO); return
        try:
            self._ensure_source_loaded(src)
            target=list(self.epg_by_source.get(src.get("source_id"), []))
            threshold=self.config.get_safe_auto_map_threshold() if self.config and hasattr(self.config,"get_safe_auto_map_threshold") else 95
            saved, skipped=self._save_beta1_style_matches(src, target, threshold)
            self._rebuild_mapping_cache(); self.mapping_revision += 1; self.selection_cache.clear(); self.refresh_channels(); self._update_summary()
            self.session.open(MessageBox, "IPTV Auto Map completed: %d saved, %d left for review in %s." % (saved, skipped, src.get("source_name","source")), MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception("Auto map failed"); self.session.open(MessageBox, "Auto Map failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def auto_map_all(self):
        """Compatibility hook: rebuild deterministic SRP maps, never AI-map."""
        return self.rebuild_global_srp_database()

    def repair_unmapped(self):
        """Focus the workflow on services that still have no mapping."""
        self.only_unmapped = True
        self["key_blue"].setText("Show All")
        unmapped = [x for x in self.catalog if not self._service_is_mapped(x)]
        cov = channel_mapper.mapping_coverage(self.catalog, set(str((x or {}).get('ref') or '') for x in self.catalog if self._service_is_mapped(x)))
        self["summary"].setText('Repair mode: %d unmapped service(s) • coverage %d%%' % (len(unmapped), cov.get('percent', 0)))
        if self.focus == 3:
            self.refresh_selection()

    def toggle_unmapped(self):
        self.only_unmapped = not self.only_unmapped
        self["key_blue"].setText("Show All" if self.only_unmapped else "Hide Mapped")
        self.refresh_selection()

    def apply(self):
        """Compatibility helper using the same beta124 PROVEN-only contract.

        Older callers used channel_mapper.smart_match_channels(..., store=self.store),
        which could write name-only matches behind the new precision firewall.
        Keep the export hook, but never let that compatibility path mutate the
        MappingStore without a PrecisionMatch proof.
        """
        try:
            iptv_catalog = [x for x in self.catalog
                            if str((x or {}).get("service_type") or channel_mapper.classify_service_ref((x or {}).get("ref")) or "").upper() == "IPTV"]
            results = channel_mapper.smart_match_channels(self.epg_channels, iptv_catalog, min_score=82, store=None)
            safe_results = []
            source_lookup = {str((x or {}).get("source_id") or ""): x for x in (self._all_source_groups or []) if x and not x.get("source_group")}
            for entry in results or []:
                matches = list((entry or {}).get("matches") or [])
                if len(matches) != 1:
                    continue
                match = matches[0]
                sid = str((entry or {}).get("source_id") or "")
                src = source_lookup.get(sid) or self._source_by_id(sid) or {}
                verdict = precision_match_engine.evaluate(
                    match, src, entry,
                    text_score=channel_mapper._token_score(str((match or {}).get("name") or ""),
                                                           str((entry or {}).get("display_name") or (entry or {}).get("channel_id") or "")),
                    learned=smartmatch_ai.learned_match(match, src, entry), balanced=True)
                if not verdict.get("auto"):
                    continue
                safe_results.append(entry)
            return save_sourcexml(safe_results)
        except Exception:
            log.exception("Failed to save compatibility XML")
            return None


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (ChannelMappingScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
