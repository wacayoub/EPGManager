# -*- coding: utf-8 -*-
"""Unified EPG source browser with persistent source selection."""
from __future__ import print_function

_SOURCE_NAVIGATION = {}
import threading
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ProgressBar import ProgressBar
from Components.MenuList import MenuList
from .compat import MultiContentEntryText
from enigma import eTimer, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER

from ..core import external_sources, source_catalog, source_index_cache, activity_store, channel_mapper, srp_channel_map, source_readiness, source_channel_cache, source_quality, source_fast_status, id_coverage_audit, source_id_harvester, github_direct_sync
from ..core.validation import validate_xmltv_file
from ..core.source_preferences import SourcePreferences
from ..core import source_metadata, source_state, source_intelligence, auto_readiness, source_variant_policy
from ..core.mapping_store import MappingStore
from . import theme
from .compat import adapt_skin, scale_y, ui_gFont



class _PreferenceSelectionAdapter(object):
    """Selection facade used by the existing fast source list widget."""
    def __init__(self, preferences): self.preferences = preferences
    def is_selected(self, source_id): return self.preferences.is_catalogue_selected(source_id)
    def toggle(self, source_id): return self.preferences.toggle_catalogue(source_id, save=True)
    def set_selected(self, ids): return set(self.preferences.set_selected_catalogue_ids(list(ids or []), save=True))
    def get_selected(self): return set(self.preferences.selected_catalogue_ids())



class NativeSourceList(MenuList):
    """Fast monitor list: source, live status, channels, programmes, time, last update, mappings."""
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 24))
        self.l.setFont(1, ui_gFont("Regular", 20))
        self.l.setItemHeight(scale_y(48))

    @staticmethod
    def _status_color(state):
        state = str(state or "--").upper()
        if state in ("OK", "DONE", "SUCCESS", "READY", "EPG READY", "LIVE", "ONLINE", "LOCAL"):
            return theme.STATUS_GREEN_INT
        if state in ("NOK", "FAILED", "ERROR", "OFFLINE"):
            return theme.STATUS_RED_INT
        if state in ("DOWNLOADING", "PARSING", "RUNNING", "SYNCING", "PREPARING", "PROBING"):
            return theme.STATUS_YELLOW_INT
        if state in ("PARTIAL", "DEGRADED", "WARNING"):
            return theme.STATUS_YELLOW_INT
        if state in ("NO EPG", "NOK", "FAILED", "ERROR"):
            return theme.STATUS_RED_INT
        if state in ("WEAK", "MAP NEEDED", "TO PREPARE"):
            return theme.STATUS_ORANGE_INT
        if state in ("PENDING", "QUEUED", "COOLDOWN", "WAIT", "WAITING", "STALE"):
            return theme.STATUS_ORANGE_INT
        return theme.MUTED_TEXT_INT

    def set_rows(self, items, selected_store, mapped_counts, schedule_map):
        """Render source health with EPG coverage, programme count and refresh age.

        rc72 keeps the lightweight receiver controls (USE / SELECT / MAPPED)
        but makes the live EPG-Scrapers health visible in the main table.
        """
        rows = []
        active_runtime = ("DOWNLOADING", "STREAMING", "PARSING", "RUNNING",
                          "SYNCING", "PREPARING", "PROBING", "PENDING")
        for index, item in enumerate(items):
            selected = selected_store.is_selected(item.get("id"))
            runtime = str(item.get("_runtime_state") or "").upper()
            health = str(item.get("_health") or "--").upper()
            # A terminal import state such as DONE must not hide source health.
            state = runtime if runtime in active_runtime else health
            if state == "DEGRADED":
                state = "PARTIAL"
            mapped = int(mapped_counts.get(item.get("id"), 0) or mapped_counts.get(item.get("manager_id"), 0) or 0)
            bg = theme.PANEL_ROW_HEX_INT if index % 2 == 0 else theme.PANEL_ROW_ALT_HEX_INT
            state_color = self._status_color(state)
            channels = item.get("_channels")
            programmes = item.get("_programmes")
            epg_ids_text = str(item.get("_epg_ids_text") or "")
            if not epg_ids_text:
                epg_ids_text = str(channels) if channels not in (None, "") else "-"
            programmes_text = str(programmes) if programmes not in (None, "") else "-"
            use_text = "ON" if selected else "OFF"
            use_color = theme.STATUS_GREEN_INT if selected else theme.MUTED_TEXT_INT
            batch_marked = bool(item.get("_batch_marked"))
            select_text = "YES" if batch_marked else "--"
            select_color = theme.ACCENT_PRIMARY_INT if batch_marked else theme.MUTED_TEXT_INT
            refresh_text = item.get("_source_refresh_text") or item.get("_source_updated_text") or item.get("_last_update_text") or "-"
            display_name = item.get("_display_name") or item.get("name", item.get("id", "Source"))
            rows.append([
                item,
                MultiContentEntryText(pos=(8,0), size=(58,48), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=use_text, color=use_color, color_sel=use_color,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(72,0), size=(86,48), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=select_text, color=select_color, color_sel=select_color,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(166,0), size=(500,48), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=display_name, color=theme.TEXT_INT, color_sel=theme.WHITE_INT,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(676,0), size=(155,48), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=state, color=state_color, color_sel=state_color,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(841,0), size=(165,48), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=epg_ids_text, color=theme.ACCENT_SECONDARY_INT if epg_ids_text != "-" else theme.MUTED_TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1016,0), size=(165,48), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=programmes_text, color=theme.TEXT_INT if programmes_text != "-" else theme.MUTED_TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1191,0), size=(120,48), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text="NATIVE" if str(item.get("provider") or "").upper() == "RYTEC" else str(mapped), color=theme.TEXT_INT if mapped else theme.MUTED_TEXT_INT, color_sel=theme.WHITE_INT,
                    backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1321,0), size=(477,48), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=str(refresh_text),
                    color=theme.ACCENT_PRIMARY_INT if refresh_text not in (None, "-", "Unknown", "Pending") else theme.MUTED_TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
            ])
        self.setList(rows)

    def current_item(self):
        cur = self.getCurrent()
        return cur[0] if cur else None

class SmartSourcesScreen(Screen):
    _KEY_BARS = (
        theme.key_bar_skin("key_red", 30, 1008, 330, 54, theme.BTN_RED) +
        theme.key_bar_skin("key_green", 390, 1008, 500, 54, theme.BTN_GREEN) +
        theme.key_bar_skin("key_yellow", 920, 1008, 470, 54, theme.BTN_YELLOW) +
        theme.key_bar_skin("key_blue", 1420, 1008, 470, 54, theme.BTN_BLUE)
    )
    skin = ("""
    <screen name="NativeSourcesScreen" position="0,0" size="1920,1080" title="EPG Sources" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,110" backgroundColor="%(PANEL)s" />
      <widget name="title" position="55,25" size="1000,60" font="Regular;40" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="health" position="1080,31" size="220,46" font="Regular;22" halign="right" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="1320,30" size="540,50" font="Regular;23" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="55,103" size="1825,2" backgroundColor="%(RULE)s" />
      <widget name="hero_bg" position="55,122" size="1810,78" backgroundColor="%(PANEL_AI)s" />
      <widget name="group" position="78,134" size="420,36" font="Regular;25" foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL_AI)s" transparent="1" />
      <widget name="category_stats" position="515,133" size="1330,38" font="Regular;21" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_AI)s" transparent="1" />
      <widget name="hint" position="78,168" size="1767,26" font="Regular;18" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL_AI)s" transparent="1" />
      <widget name="h_use" position="63,218" size="58,38" font="Regular;18" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_select" position="127,218" size="86,38" font="Regular;18" foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_source" position="221,218" size="500,38" font="Regular;18" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_status" position="731,218" size="155,38" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_channels" position="896,218" size="165,38" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_programmes" position="1071,218" size="165,38" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_mapped" position="1246,218" size="120,38" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_updated" position="1376,218" size="477,38" font="Regular;18" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="list" position="55,258" size="1810,560" font="Regular;25" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
      <widget name="detail" position="55,826" size="1810,78" font="Regular;20" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="progress_label" position="55,912" size="1510,30" font="Regular;20" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="progress" position="55,950" size="1680,14" borderWidth="1" borderColor="%(RULE_SOFT)s" backgroundColor="%(PANEL)s" foregroundColor="%(ACCENT_PRIMARY)s" />
      <widget name="progress_pct" position="1750,936" size="115,36" font="Regular;23" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="footer_bg" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_strip_rule" position="30,990" size="1860,2" backgroundColor="%(RULE)s" />
      """ + _KEY_BARS + """
    </screen>""") % theme.__dict__

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session, self.manager, self.config = session, manager, config
        self.preferences = SourcePreferences()
        self.store = _PreferenceSelectionAdapter(self.preferences)
        self.mapping_store = MappingStore()
        for n in ("header_bg", "rule", "hero_bg", "footer_bg", "key_strip_rule"):
            self[n] = Label("")
        self["title"] = Label("SOURCES  •  LIVE FEED HUB")
        self["health"] = Label("HEALTH")
        self["summary"] = Label("")
        self["group"] = Label("")
        self["category_stats"] = Label("")
        self["hint"] = Label("OK enable  •  LEFT/RIGHT provider  •  INFO multi-select  •  GREEN refresh  •  YELLOW import")
        self["h_use"] = Label("USE")
        self["h_select"] = Label("SELECT")
        self["h_source"] = Label("SOURCE")
        self["h_status"] = Label("STATUS")
        self["h_channels"] = Label("EPG IDS/TOTAL")
        self["h_programmes"] = Label("PROGRAMMES")
        self["h_mapped"] = Label("MAPPED")
        self["h_last"] = Label("")
        self["h_updated"] = Label("PUBLISHED / CHECKED GMT+0")
        self["h_schedule"] = Label("")
        self["list"] = NativeSourceList()
        try:
            self["list"].onSelectionChanged.append(self._queue_detail)
        except Exception:
            pass
        self["detail"] = Label("")
        self["progress_label"] = Label("")
        self["progress_pct"] = Label("")
        self["progress"] = ProgressBar(); self["progress"].setValue(0)
        for key, text in (("red", "Back"), ("green", "Refresh Source"), ("yellow", "Import Source"), ("blue", "More")):
            self["key_%s_bar" % key] = Label(""); self["key_%s" % key] = Label(text)
        self._all_items, self._items = [], []
        self._initial_selected = self.store.get_selected()
        self._health_overrides = {}
        self._show_selected_only = False
        self._show_useful_only = False
        self._show_pinned_only = False
        # beta67: Smart Sources uses real pages instead of moving the cursor
        # through one giant grouped list. LEFT/RIGHT always cycles this fixed
        # order, so filters or empty providers can no longer make navigation
        # jump unpredictably. MY RECEIVER is a cache-only intelligence view.
        self._provider_pages = ["MY RECEIVER", "EPG GROUPS", "EPG SCRAPERS", "EPGSHARE", "OPENEPG", "RYTEC"]
        self._active_page = _SOURCE_NAVIGATION.get("page", "MY RECEIVER")
        self._page_selection = dict(_SOURCE_NAVIGATION.get("selections", {}))
        self._show_selected_only = bool(_SOURCE_NAVIGATION.get("selected"))
        self._show_useful_only = bool(_SOURCE_NAVIGATION.get("useful"))
        self._show_pinned_only = bool(_SOURCE_NAVIGATION.get("pinned"))
        self._busy = False
        self._pending_locals = []
        self._errors = []
        self._runtime = {}
        self._job_started_at = None
        self._job_total = 0
        self._job_done = 0
        self._job_failed = 0
        self._last_monitor_render = 0.0
        self._last_progress_signature = None
        self._single_operation = ""
        self._single_label = ""
        # Source validation can be expensive on large XMLTV files. Never parse
        # XML on the Enigma2 UI thread: keep a lightweight stat/cache here and
        # validate stale files in a daemon worker.
        self._health_cache = source_index_cache.load_all()
        self._health_scan_running = False
        self._health_scan_results = None
        self._prepare_result = None
        self._prepare_notify = False
        self._batch_selected = set()
        self._fast_status_refresh_running = False
        self._fast_status_refresh_results = None
        self["actions"] = ActionMap(["OkCancelActions", "NumberActions", "ColorActions", "DirectionActions", "ChannelSelectBaseActions", "MenuActions", "InfoActions"], {
            "cancel": self.save_and_close, "red": self.save_and_close, "ok": self.toggle_current,
            "green": self.download_current, "yellow": self.import_current_source, "blue": self.open_options, "menu": self.open_settings,
            "0": self.open_options,
            "7": self.toggle_batch_mark,
            "info": self.toggle_batch_mark,
            "1": lambda: self._set_page("MY RECEIVER"),
            "2": lambda: self._set_page("EPG GROUPS"),
            "3": lambda: self._set_page("EPG SCRAPERS"),
            "4": lambda: self._set_page("EPGSHARE"),
            "5": lambda: self._set_page("OPENEPG"),
            "6": lambda: self._set_page("RYTEC"),
            "8": self.open_mapping, "9": self.open_settings,
            "up": self._up, "down": self._down, "left": self._page_up, "right": self._page_down,
            "pageUp": self._next_category, "pageDown": self._previous_category,
            "channelUp": self._next_category, "channelDown": self._previous_category,
            "prevBouquet": self._next_category, "nextBouquet": self._previous_category,
            "bouquetUp": self._next_category, "bouquetDown": self._previous_category,
        }, -1)
        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get(); cb.append(self._poll)
        # Keep Smart Sources visually in sync with the session-start readiness
        # worker. This timer only redraws cached metadata; it never performs
        # network or XML parsing on the UI thread.
        self._auto_refresh_timer = eTimer()
        rcb = self._auto_refresh_timer.callback if hasattr(self._auto_refresh_timer, "callback") else self._auto_refresh_timer.timeout.get()
        rcb.append(self._auto_refresh_cached)
        try: self._auto_refresh_timer.start(60000, False)
        except Exception: pass
        self.onClose.append(self._stop_auto_refresh)
        # beta79: expensive metadata decoration is a snapshot, not a page-change
        # operation. Provider navigation now filters already-decorated RAM rows.
        self._metadata_dirty = True
        self._metadata_refreshed_at = 0.0
        self._mapped_counts_cache = {}
        self._page_count_cache = {}
        # Refresh the tiny GitHub JSON control plane in background.  No XMLTV
        # body is downloaded by this status refresh.
        try:
            github_direct_sync.sync_async(force=False, min_age=60)
        except Exception:
            pass
        self._navigation_visible = True
        self._detail_timer = eTimer()
        cb = self._detail_timer.callback if hasattr(self._detail_timer, "callback") else self._detail_timer.timeout.get()
        cb.append(self._paint_detail)
        self.onHide.append(self._suspend_navigation)
        self.onShow.append(self._resume_navigation)
        self.onClose.append(self._remember_navigation)
        self.reload_catalogue()
        self._refresh_selected_fast_status_async()

    def _set_runtime(self, source_id, state=None, started=None, message=None):
        if not source_id:
            return
        row = dict(self._runtime.get(source_id) or {})
        if state is not None:
            row["state"] = str(state)
        if started is not None:
            row["started"] = float(started)
        if message is not None:
            row["message"] = str(message)
        self._runtime[source_id] = row
        # Keep the already-rendered RAM catalogue row in sync immediately.
        # _apply_filter() is intentionally cache-only and may not re-run the
        # expensive metadata decorator, so without this the STATUS cell can
        # remain stuck on DOWNLOADING after a completed/failed refresh.
        try:
            for item in self._all_items:
                if str(item.get("id") or "") == str(source_id):
                    item["_runtime_state"] = str(row.get("state") or "").upper()
                    if item["_runtime_state"] in ("DOWNLOADING", "STREAMING", "PARSING", "RUNNING", "PENDING", "PREPARING", "PROBING"):
                        item["_runtime_elapsed"] = max(0, int(time.time() - float(row.get("started") or time.time())))
                    else:
                        item["_runtime_elapsed"] = None
                    break
        except Exception:
            pass

    def _runtime_counts(self):
        running = done = failed = 0
        for row in self._runtime.values():
            state = str((row or {}).get("state") or "").upper()
            if state in ("DOWNLOADING", "STREAMING", "PARSING", "RUNNING", "PENDING", "PREPARING", "PROBING"):
                running += 1
            elif state in ("DONE", "OK", "SUCCESS", "COOLDOWN"):
                done += 1
            elif state in ("NOK", "FAILED", "ERROR"):
                failed += 1
        return running, done, failed

    @staticmethod
    def _age_text(epoch):
        try:
            age = max(0, int(time.time() - float(epoch)))
        except Exception:
            return "-"
        if age < 60:
            return "now"
        if age < 3600:
            return "%dm ago" % (age // 60)
        if age < 86400:
            return "%dh ago" % (age // 3600)
        return "%dd ago" % (age // 86400)

    def _epg_dir(self):
        try: return self.config.get_epg_output_dir()
        except Exception: return "/etc/epgmanager_epg"

    def reload_catalogue(self):
        old_id = (self._current() or {}).get("id")
        self._metadata_dirty = True
        self._all_items = source_catalog.all_sources()
        if self._active_page not in self._provider_pages:
            self._active_page = "MY RECEIVER"
        # Remove selections belonging to the old/dead catalogue. This keeps
        # Native Import lean after source-list upgrades.
        valid = set(x.get("id") for x in self._all_items if x.get("id"))
        selected = self.store.get_selected()
        cleaned = selected.intersection(valid)
        if cleaned != selected:
            self.store.set_selected(cleaned)
        self._apply_filter(old_id or self._page_selection.get(self._active_page), refresh_metadata=True)

    def _mapped_counts(self):
        counts={}
        # First count the persistent beta12/beta1 generated maps. This is the
        # most useful recommendation signal for Smart Sources.
        try:
            index = srp_channel_map.load_index() or {}
            for sid, row in (index.get("sources") or {}).items():
                count = int((row or {}).get("channels") or 0)
                if count:
                    counts[str(sid)] = max(counts.get(str(sid), 0), count)
        except Exception:
            pass
        # Overlay manual mappings as well.
        try:
            for key, value in self.mapping_store.all().items():
                sid = key.split("::", 1)[0] if "::" in key else ""
                refs=(value or {}).get("refs") or []
                if sid and refs:
                    counts[sid]=counts.get(sid,0)+1
        except Exception:
            pass
        return counts

    def _source_path(self, item):
        try:
            if item.get("kind") == "local":
                # Do not import scraper modules just to render Smart Sources.
                filename = str(item.get("output_file") or "")
                if filename:
                    return os.path.join(self._epg_dir(), filename)
                src = getattr(self.manager, "_sources", {}).get(item.get("manager_id"))
                if src is not None:
                    return src.get_output_path(self.config)
                return None
            if item.get("grouped_feed"):
                return external_sources.local_xml_path(item, self._epg_dir())
            # Ordinary remote Smart Sources remain direct/streamed.
            return None
        except Exception:
            return None

    @staticmethod
    def _direct_refresh_text(meta):
        from ..core.sync_timing import source_refresh_text
        return source_refresh_text(meta)

    def _apply_direct_scraper_status(self, item, direct_rows, direct_monitor=None):
        """Overlay current EPG-Scrapers feed + per-ID monitor metadata.

        feeds/<source>.json supplies the published feed size/programme count and
        generated_utc timestamp.  The monitoring CSV is newer and tells us how
        many IDs still have FUTURE EPG *right now*.  When its ID count matches
        the published feed we use it for the EPG IDS/TOTAL column, matching the
        repository monitoring table rather than showing an older generation-time
        active_channels value.
        """
        sid = str(item.get("id") or "")
        meta = dict((direct_rows or {}).get(sid) or {})
        if not meta:
            return
        total = int(meta.get("channels") or 0)
        active = int(meta.get("active_channels") or 0)
        programmes = int(meta.get("programmes") or 0)
        zero = int(meta.get("zero_epg_channels") or max(0, total - active))

        stem = github_direct_sync.source_stem(sid)
        monitor_rows = list(((direct_monitor or {}).get(stem) or [])) if stem else []
        # De-duplicate defensively by XMLTV id before counting future EPG.
        monitor_by_id = {}
        for row in monitor_rows:
            cid = str((row or {}).get("id") or "").strip().casefold()
            if cid:
                monitor_by_id[cid] = row or {}
        if monitor_by_id and (not total or len(monitor_by_id) == total):
            if not total:
                total = len(monitor_by_id)
            active = sum(1 for row in monitor_by_id.values() if bool(row.get("has_epg")))
            zero = max(0, total - active)

        coverage = (100.0 * active / total) if total else 0.0
        if not meta.get("ok"):
            state = "OFFLINE"
        elif meta.get("stale"):
            state = "STALE"
        elif zero > 0 or coverage < 99.95:
            state = "PARTIAL"
        else:
            state = "OK"

        item["_health"] = state
        item["_channels"] = total
        item["_programmes"] = programmes
        item["_epg_active_ids"] = active
        item["_epg_total_ids"] = total
        item["_epg_ids_text"] = "%d/%d" % (active, total) if total else "0/0"
        item["_coverage_pct"] = coverage
        item["_zero_epg_channels"] = zero
        item["_zero_epg_ids"] = [str((row or {}).get("id") or "") for row in monitor_by_id.values() if not bool((row or {}).get("has_epg"))] if monitor_by_id else list(meta.get("zero_epg_ids") or [])
        item["_source_refresh_text"] = self._direct_refresh_text(meta)
        item["_source_updated_text"] = item["_source_refresh_text"]
        item["_health_message"] = (
            "EPG-Scrapers: %d/%d IDs with future EPG • %.1f%% coverage • %d programmes" %
            (active, total, coverage, programmes)
        )
        if zero:
            item["_health_message"] += " • %d zero-EPG ID(s)" % zero

    def _refresh_health(self, item, audit_sources=None, readiness_rows=None, fast_rows=None, state_rows=None):
        """Use cached readiness/metrics only; never parse/download on the UI thread."""
        sid = item.get("id")
        override = self._health_overrides.get(sid)
        cached = override or self._health_cache.get(sid) or {}
        prepared = dict((readiness_rows or {}).get(str(sid)) or {}) if readiness_rows is not None else (source_readiness.get(sid) or {})
        fast = dict((fast_rows or {}).get(str(sid)) or {}) if fast_rows is not None else (source_fast_status.get(sid) or {})
        path = self._source_path(item)
        current_sig = source_index_cache.signature(path) if path and os.path.exists(path) else None
        cache_valid = bool(cached and source_index_cache.same_signature(cached.get("sig"), current_sig))
        bg = dict((state_rows or {}).get(str(sid)) or {}) if state_rows is not None else source_state.get(sid)
        file_stamp = None
        try:
            if path and os.path.exists(path) and os.path.getsize(path) > 32:
                file_stamp = os.path.getmtime(path)
        except Exception:
            file_stamp = None

        if item.get("kind") != "local":
            # Detailed readiness from an explicit Smart Mapping / Deep Check wins.
            # Otherwise the dashboard uses the beta30 HEAD-only availability cache.
            detailed = bool(prepared and str(prepared.get("mode") or "") != "fast-status" and
                            (prepared.get("channels") or prepared.get("programmes") or
                             str(prepared.get("state") or "").upper() in ("READY", "EPG READY", "NO EPG", "WEAK", "MAP NEEDED")))
            if detailed:
                state = str(prepared.get("state") or "CHECKED").upper()
                item["_health"] = state
                item["_channels"] = int(prepared.get("channels") or 0)
                p = int(prepared.get("programmes") or 0)
                item["_programmes"] = (("%d+" % p) if prepared.get("programmes_partial") and p else p)
                desc = int(round(float(prepared.get("desc_ratio") or 0.0) * 100))
                days = float(prepared.get("days") or 0.0)
                map_text = "map ready" if prepared.get("map_ready") else "map missing"
                item["_health_message"] = "Deep check: %d IDs • %s programmes • %d%% descriptions • %.1f day(s) • %s" % (
                    int(prepared.get("channels") or 0), item["_programmes"], desc, days, map_text)
                item["_duration"] = None
                item["_last_update_text"] = self._age_text(prepared.get("checked"))
            elif fast:
                item["_health"] = str(fast.get("state") or "OFFLINE").upper()
                item["_channels"] = str(fast.get("channel_status") or "-")
                item["_programmes"] = str(fast.get("programme_status") or "-")
                try:
                    cinfo = source_channel_cache.info(sid) or {}
                    cage = max(0, int(time.time() - int(cinfo.get("updated_at") or 0))) if cinfo.get("updated_at") else 0
                    ctext = (" • %d cached IDs • cache %s" % (int(cinfo.get("channels") or 0), self._age_text(int(cinfo.get("updated_at") or 0)))) if cinfo.get("channels") else ""
                except Exception:
                    ctext = ""
                latency = int(fast.get("latency_ms") or 0)
                failures = int(fast.get("consecutive_failures") or 0)
                net = (" • %d ms" % latency) if latency else ""
                fail = (" • %d consecutive failure(s)" % failures) if failures else ""
                item["_health_message"] = "Fast availability check; no XMLTV body downloaded. %s%s%s%s" % (
                    str(fast.get("message") or ""), net, ctext, fail)
                item["_duration"] = None
                item["_last_update_text"] = self._age_text(fast.get("checked"))
            else:
                item["_health"] = "CHECKING"
                item["_channels"] = "-"
                item["_programmes"] = "-"
                item["_health_message"] = "HEAD-only status check queued; no XMLTV download"
                item["_duration"] = None
                item["_last_update_text"] = "Pending"
        else:
            if prepared:
                item["_health"] = str(prepared.get("state") or "READY").upper()
                item["_channels"] = int(prepared.get("channels") or 0)
                item["_programmes"] = int(prepared.get("programmes") or 0)
                item["_health_message"] = "Local XMLTV verified; %s" % ("map ready" if prepared.get("map_ready") else "map missing")
                item["_duration"] = None
                item["_last_update_text"] = self._age_text(file_stamp or prepared.get("checked"))
            elif override or cache_valid:
                raw_health = str(cached.get("health") or "--").upper()
                item["_health"] = "READY" if raw_health in ("OK", "SUCCESS", "DONE") else raw_health
                item["_channels"] = cached.get("channels")
                item["_programmes"] = cached.get("programmes")
                item["_health_message"] = cached.get("message") or ""
                item["_duration"] = cached.get("duration")
                item["_last_update_text"] = self._age_text(file_stamp or cached.get("downloaded_at") or cached.get("updated_at"))
            elif current_sig:
                item["_health"] = "TO PREPARE" if self.store.is_selected(sid) else "AVAILABLE"
                item["_channels"] = None
                item["_programmes"] = None
                item["_health_message"] = "Local XMLTV cache exists but has not been verified; press 1 to count channels/programmes"
                item["_duration"] = None
                item["_last_update_text"] = self._age_text(file_stamp)
            else:
                item["_health"] = "WAITING" if self.store.is_selected(sid) else "--"
                item["_channels"] = None
                item["_programmes"] = None
                item["_health_message"] = "Legacy local source (disabled)"
                item["_duration"] = None
                item["_last_update_text"] = "-"
            # One-day sources can explicitly cap how old a local XMLTV may
            # be before it stops being importable. Keep the file on disk for
            # diagnostics/recovery, but label it STALE instead of READY.
            try:
                max_age = int(item.get("max_cache_age_seconds") or 0)
                age = max(0, int(time.time() - float(file_stamp or 0))) if file_stamp else 0
            except Exception:
                max_age = age = 0
            if max_age and file_stamp and age > max_age:
                item["_health"] = "STALE"
                item["_health_message"] = "Local XMLTV is %.1f day(s) old and is preserved only as backup; refresh before import." % (float(age) / 86400.0)
            bg_status = str((bg or {}).get("status") or "").upper()
            if bg_status in ("SYNCING", "ERROR", "WAITING", "STALE"):
                item["_health"] = bg_status
                if (bg or {}).get("error"):
                    item["_health_message"] = str(bg.get("error"))

        _native_rytec_display(item, prepared)

        # CHECKED is the newest observation time, independent of which cached
        # layer supplied channel/programme metrics.  Older builds displayed a
        # 9-12 day old deep-check timestamp even after a fresh HEAD check.
        try:
            checked_stamp = max(int((prepared or {}).get("checked") or 0),
                                int((fast or {}).get("checked") or 0),
                                int(file_stamp or 0) if item.get("kind") == "local" else 0)
        except Exception:
            checked_stamp = 0
        if checked_stamp:
            item["_last_update_text"] = self._age_text(checked_stamp)

        # SOURCE UPDATED is provider/file freshness, not the time EPGManager
        # last checked it. Remote Last-Modified comes from the HEAD-only worker.
        remote_updated = int((fast or {}).get("remote_updated") or 0)
        if item.get("kind") == "local":
            remote_updated = int(file_stamp or remote_updated or 0)
        item["_source_updated_text"] = self._age_text(remote_updated) if remote_updated else "Unknown"

        try:
            mid = source_catalog.mapping_source_id(item)
            audit = dict(((audit_sources or {}).get(str(mid))) or ((audit_sources or {}).get(str(mid).lower())) or {}) if audit_sources is not None else (id_coverage_audit.source_stats(mid) or {})
            item["_id_total"] = int(audit.get("total_ids") or 0)
            item["_id_linked"] = int(audit.get("linked_ids") or 0)
            item["_id_unlinked"] = int(audit.get("unlinked_ids") or 0)
            item["_id_conflicts"] = int(audit.get("conflicts") or 0)
            item["_id_status"] = str(audit.get("status") or "")
            item["_id_catalog_complete"] = bool(audit.get("catalog_complete", True))
            if item["_id_total"]:
                catalog_tag = "FULL IDs" if item["_id_catalog_complete"] else "PARTIAL IDs"
                item["_health_message"] = (str(item.get("_health_message") or "") +
                    " • ID map: %d/%d linked, %d unlinked • %s" %
                    (item["_id_linked"], item["_id_total"], item["_id_unlinked"], catalog_tag)).strip(" •")
        except Exception:
            item["_id_total"] = item["_id_linked"] = item["_id_unlinked"] = item["_id_conflicts"] = 0
            item["_id_status"] = ""
            item["_id_catalog_complete"] = False

        runtime = self._runtime.get(sid) or {}
        state = str(runtime.get("state") or "").upper()
        item["_runtime_state"] = state
        if state in ("DOWNLOADING", "STREAMING", "PARSING", "RUNNING", "PENDING", "PREPARING", "PROBING"):
            item["_runtime_elapsed"] = max(0, int(time.time() - float(runtime.get("started") or time.time())))
        else:
            item["_runtime_elapsed"] = None
    def _start_health_scan(self, force=False):
        if self._health_scan_running:
            return
        jobs = []
        for item in self._all_items:
            sid = item.get("id")
            if sid in self._health_overrides:
                continue
            path = self._source_path(item)
            if not path or not os.path.exists(path):
                continue
            try:
                sig = source_index_cache.signature(path)
            except Exception:
                sig = None
            cached = self._health_cache.get(sid)
            if force or not cached or not source_index_cache.same_signature(cached.get("sig"), sig):
                jobs.append((sid, path, sig))
        if not jobs:
            return
        self._health_scan_running = True
        self._health_scan_results = None
        def worker():
            results = {}
            for sid, path, sig in jobs:
                try:
                    vr = validate_xmltv_file(path, min_programs=1)
                    try:
                        item = next((x for x in self._all_items if x.get("id") == sid), None)
                        if item:
                            source_metadata.audit_source(item, path, force=force)
                    except Exception:
                        pass
                    results[sid] = {
                        "sig": sig, "health": "OK" if vr.ok else "NOK",
                        "channels": vr.channel_count, "programmes": vr.program_count,
                        "message": "XMLTV valid" if vr.ok else "; ".join(vr.errors[:2])
                    }
                except Exception as exc:
                    results[sid] = {"sig": sig, "health": "NOK", "channels": 0,
                                    "programmes": 0, "message": str(exc)}
            source_index_cache.update_many(results)
            self._health_scan_results = results
        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(250, False)

    def _download_schedule_text(self, item):
        try:
            sid = item.get("id")
            if item.get("kind") == "local":
                if self.store.is_selected(sid):
                    return "DISABLED"
                return "DISABLED"
            # Readiness probing is automatic for selected and non-selected
            # sources. DIRECT still describes Import All, AUTO the probe cache.
            return "AUTO"
        except Exception:
            return "AUTO"

    def _set_fg(self, name, color):
        try:
            from enigma import gRGB
            self[name].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _compute_source_intelligence(self, mapped_counts):
        """Decorate rows from caches only; never scan receiver/XML/network here."""
        pinned = set(self.preferences.pinned_catalogue_ids())
        best_by_category = {}
        for item in self._all_items:
            aliases = [item.get("id"), item.get("manager_id")]
            mapped = max([int(mapped_counts.get(a, 0) or 0) for a in aliases if a] or [0])
            item["_mapped_count"] = mapped
            item["_pinned"] = item.get("id") in pinned
            item["_source_score"] = source_intelligence.score(item, mapped)
            item["_useful"] = source_intelligence.is_useful(item)
            cat = source_catalog.source_category(item)
            current = best_by_category.get(cat)
            if item["_useful"] and (current is None or item["_source_score"] > current[0]):
                best_by_category[cat] = (item["_source_score"], item.get("id"))
        for item in self._all_items:
            best = best_by_category.get(source_catalog.source_category(item))
            item["_best_source"] = bool(best and best[1] == item.get("id") and int(best[0]) >= 35)

    def toggle_useful_filter(self):
        self._show_useful_only = not self._show_useful_only
        if self._show_useful_only:
            self._show_selected_only = False; self._show_pinned_only = False
        self._apply_filter((self._current() or {}).get("id"))

    def toggle_pinned_filter(self):
        self._show_pinned_only = not self._show_pinned_only
        if self._show_pinned_only:
            self._show_selected_only = False; self._show_useful_only = False
        self._apply_filter((self._current() or {}).get("id"))

    def toggle_current_pin(self):
        item = self._current()
        if not item:
            return
        state = self.preferences.toggle_pin(item.get("id"), save=True)
        item["_pinned"] = bool(state)
        self._metadata_dirty = True
        self._apply_filter(item.get("id"))
        try:
            self["detail"].setText(("Pinned. " if state else "Unpinned. ") + self["detail"].getText())
        except Exception:
            pass

    def open_unified_countries(self):
        """Open the cache-only virtual country merger.

        This never joins huge XMLTV files. It groups the real provider feeds
        logically by country and shows the best cached provider/coverage.
        """
        try:
            from .unified_countries import UnifiedCountrySourcesScreen
            self.session.open(UnifiedCountrySourcesScreen, self.manager, self.config)
        except Exception as exc:
            self.session.open(MessageBox, "Unified Country Sources unavailable.\n\n%s" % exc, MessageBox.TYPE_ERROR, timeout=5)

    def open_mapping_conflicts(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.config, True, self.manager, "conflicts")

    def _refresh_metadata_snapshot(self, force=False):
        now = time.time()
        if not force and float(self._metadata_refreshed_at or 0.0) > 0.0:
            return
        mapped_counts = self._mapped_counts()
        self._mapped_counts_cache = dict(mapped_counts or {})
        schedule_map = {}
        # Read the ID-audit JSON once per render. beta67 called source_stats()
        # once per source, reparsing the same JSON 100+ times after catalog growth.
        try:
            audit_sources = (id_coverage_audit.load().get("sources") or {})
        except Exception:
            audit_sources = {}
        # Snapshot the three persistent state files once. Older builds called
        # get() per source, turning one table render into hundreds of stat/load
        # operations on receiver flash.
        try: readiness_rows = source_readiness.all_rows()
        except Exception: readiness_rows = {}
        try: fast_rows = source_fast_status.all_rows()
        except Exception: fast_rows = {}
        try: state_rows = source_state.all_rows()
        except Exception: state_rows = {}
        try:
            direct_payload = github_direct_sync.load() or {}
            direct_rows = direct_payload.get("sources") or {}
            direct_monitor = direct_payload.get("id_monitor") or {}
        except Exception:
            direct_rows = {}
            direct_monitor = {}
        # Health/audit metadata are cache reads only. Compute source intelligence
        # after those fields are populated so no network/XML work reaches UI.
        for item in self._all_items:
            self._refresh_health(item, audit_sources=audit_sources, readiness_rows=readiness_rows, fast_rows=fast_rows, state_rows=state_rows)
            self._apply_direct_scraper_status(item, direct_rows, direct_monitor)
        self._compute_source_intelligence(mapped_counts)
        for item in self._all_items:
            provider = str(item.get("provider") or "")
            lang_suffix = source_metadata.suffix(item, self._epg_dir())
            suffix = ("  " + lang_suffix) if lang_suffix else ""
            # beta73 professional table: no checkmarks/stars/diamonds in the
            # source name. USE has its own ON/OFF column and recommendation is
            # explained in the detail card. This keeps every row predictable.
            if lang_suffix:
                suffix = "  [%s]" % lang_suffix
            else:
                suffix = ""
            item["_display_name"] = (item.get("name") or item.get("id")) + suffix
            if item.get("kind") != "local":
                item["_display_status"] = external_sources.source_status(item, self._epg_dir())
            item["group"] = source_catalog.source_category(item)
            schedule_map[item.get("id")] = self._download_schedule_text(item)

        self._schedule_map_cache = schedule_map
        self._metadata_refreshed_at = now
        self._metadata_dirty = False
        # Precompute page counts once so the SaaS header does not rescan 196 rows
        # several times for every LEFT/RIGHT/page action.
        counts = {p: 0 for p in self._provider_pages}
        for _x in self._all_items:
            _cat = source_catalog.source_category(_x)
            if _cat in counts:
                counts[_cat] += 1
            if _x.get("_useful") or _x.get("_pinned") or self.store.is_selected(_x.get("id")):
                counts["MY RECEIVER"] = counts.get("MY RECEIVER", 0) + 1
        self._page_count_cache = counts

    def _apply_filter(self, keep_id=None, refresh_metadata=False):
        self._refresh_metadata_snapshot(force=bool(refresh_metadata))
        mapped_counts = getattr(self, "_mapped_counts_cache", {}) or {}
        schedule_map = getattr(self, "_schedule_map_cache", {}) or {}
        # Provider pages are now just RAM filters over the metadata snapshot.
        if self._active_page == "MY RECEIVER":
            source_order = [x for x in self._all_items
                            if x.get("_useful") or x.get("_pinned") or self.store.is_selected(x.get("id"))]
            source_order.sort(key=lambda x: (-int(x.get("_source_score") or 0),
                                             str(x.get("name") or x.get("id") or "").casefold()))
        else:
            source_order = [x for x in self._all_items
                            if source_catalog.source_category(x) == self._active_page]
            source_order.sort(key=source_catalog.source_sort_key)
        self._items = []
        for x in source_order:
            if self._show_selected_only and not self.store.is_selected(x.get("id")):
                continue
            if self._show_useful_only and not x.get("_useful"):
                continue
            if self._show_pinned_only and not x.get("_pinned"):
                continue
            self._items.append(x)
        for _row in self._all_items:
            _row["_batch_marked"] = str(_row.get("id") or "") in self._batch_selected
        self["list"].set_rows(self._items, self.store, mapped_counts, schedule_map)
        self._update_batch_action_labels()

        if keep_id:
            for i, it in enumerate(self._items):
                if it.get("id") == keep_id:
                    try: self["list"].moveToIndex(i)
                    except Exception:
                        try: self["list"].instance.moveSelectionTo(i)
                        except Exception: pass
                    break

        selected_items = [x for x in self._all_items if self.store.is_selected(x.get("id"))]
        sel_count = len(selected_items)
        running, done, failed = self._runtime_counts()
        if self._busy or running:
            self["summary"].setText("%d active  •  %d preparing  •  %d done  •  %d failed" % (sel_count, running, done, failed))
            self["health"].setText("PREPARING")
            self._set_fg("health", theme.STATUS_YELLOW)
        else:
            ready_count = sum(1 for x in selected_items if str(x.get("_health") or "").upper() in ("READY", "EPG READY", "ONLINE", "LOCAL", "OK"))
            bad_count = sum(1 for x in selected_items if str(x.get("_health") or "").upper() in ("OFFLINE", "NO EPG", "NOK", "ERROR", "FAILED", "WEAK"))
            pending_count = max(0, sel_count - ready_count - bad_count)
            checked_states = ("READY", "EPG READY", "ONLINE", "CATALOG OK", "OFFLINE", "LOCAL", "OK", "PARTIAL", "STALE", "NO EPG", "NOK", "ERROR", "FAILED", "WEAK", "MAP NEEDED")
            catalog_checked = sum(1 for x in self._all_items if str(x.get("_health") or "").upper() in checked_states)
            catalog_total = len(self._all_items)
            try:
                audit = id_coverage_audit.summary() or {}
                id_linked = int(audit.get("linked_ids") or 0)
                id_total = int(audit.get("total_ids") or 0)
            except Exception:
                id_linked = id_total = 0
            self["summary"].setText("%d active • %d ready • IDs %d/%d • catalog %d/%d" % (sel_count, ready_count, id_linked, id_total, catalog_checked, catalog_total))
            if pending_count or catalog_checked < catalog_total:
                self["health"].setText("AUTO %d/%d" % (catalog_checked, catalog_total))
                self._set_fg("health", theme.STATUS_YELLOW)
            elif bad_count:
                self["health"].setText("CHECK SOURCES")
                self._set_fg("health", theme.STATUS_ORANGE)
            elif sel_count and ready_count == sel_count:
                self["health"].setText("FEEDS ONLINE")
                self._set_fg("health", theme.STATUS_GREEN)
            elif sel_count:
                self["health"].setText("AUTO PREPARING")
                self._set_fg("health", theme.STATUS_YELLOW)
            else:
                self["health"].setText("CATALOG READY")
                self._set_fg("health", theme.STATUS_GREEN)
        self._detail()
        # Cache-only opening: no XMLTV validation is launched automatically.

    def _auto_refresh_cached(self):
        if self._busy:
            return
        try:
            # Redraw cached rows every minute.  The helper itself only starts a
            # HEAD-only status pass when selected remote feeds are older than
            # 15 minutes, so CHECKED/SOURCE UPDATED stay live without XMLTV I/O.
            self._refresh_selected_fast_status_async()
            try:
                github_direct_sync.sync_async(force=False, min_age=300)
            except Exception:
                pass
            self._metadata_dirty = True
            self._apply_filter((self._current() or {}).get("id"), refresh_metadata=True)
        except Exception:
            pass

    def _stop_auto_refresh(self):
        try: self._auto_refresh_timer.stop()
        except Exception: pass

    def _delayed_health_scan(self):
        self._health_scan_scheduled = False
        self._start_health_scan()

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

    def _page_count(self, page):
        cached = getattr(self, "_page_count_cache", {}) or {}
        if page in cached:
            return int(cached.get(page) or 0)
        if page == "MY RECEIVER":
            return sum(1 for x in self._all_items if x.get("_useful") or x.get("_pinned") or self.store.is_selected(x.get("id")))
        return sum(1 for x in self._all_items if source_catalog.source_category(x) == page)

    def _page_visible_count(self, page):
        if page == "MY RECEIVER":
            rows=[x for x in self._all_items
                  if x.get("_useful") or x.get("_pinned") or self.store.is_selected(x.get("id"))]
        else:
            rows=[x for x in self._all_items if source_catalog.source_category(x) == page]
        count=0
        for x in rows:
            if self._show_selected_only and not self.store.is_selected(x.get("id")):
                continue
            if self._show_useful_only and not x.get("_useful"):
                continue
            if self._show_pinned_only and not x.get("_pinned"):
                continue
            count += 1
        return count

    def _category_summary(self):
        rows=[]
        page_no={p:i+1 for i,p in enumerate(self._provider_pages)}
        for page in self._provider_pages:
            count=self._page_count(page)
            if page != "MY RECEIVER" and not count:
                continue
            prefix=str(page_no.get(page, ""))
            if page == "MY RECEIVER":
                text="%s MY %d" % (prefix, count)
            else:
                total = self._page_count(page)
                selected=sum(1 for x in self._all_items if x.get("group") == page and self.store.is_selected(x.get("id")))
                short={"EPG SCRAPERS":"DIRECT", "EPG GROUPS":"GROUPS"}.get(page,page)
                text="%s %s %d/%d" % (prefix, short, selected, total)
            rows.append("[%s]" % text if page == self._active_page else text)
        summary = "   •   ".join(rows)
        if self._batch_selected:
            summary += "   •   SELECT %d" % len(self._batch_selected)
        return summary

    def _set_page(self, page):
        if page not in self._provider_pages:
            return
        current=self._current() or {}
        if current.get("id"):
            self._page_selection[self._active_page]=current.get("id")
        self._active_page=page
        keep=self._page_selection.get(page)
        self._apply_filter(keep)
        if self._items and not keep:
            try: self["list"].moveToIndex(0)
            except Exception:
                try: self["list"].instance.moveSelectionTo(0)
                except Exception: pass
            self._detail()

    def _jump_category(self, step):
        # Fixed page order, independent of active filters / empty source rows.
        # Always cycle the fixed provider order. A filter may legitimately
        # make a page empty; showing that empty page is less confusing than
        # silently skipping to another provider.
        pages=[p for p in self._provider_pages if p == "MY RECEIVER" or self._page_count(p) > 0]
        if not pages:
            return
        try: idx=pages.index(self._active_page)
        except Exception: idx=0
        self._set_page(pages[(idx + int(step)) % len(pages)])

    def _previous_category(self): self._jump_category(-1)
    def _next_category(self): self._jump_category(1)

    def _queue_detail(self):
        if not getattr(self, "_navigation_visible", False): return
        self._detail_timer.start(100, True)

    def _paint_detail(self):
        if self._navigation_visible:
            self._detail_now()

    def _detail(self):
        # Explicit actions can paint immediately; held-key movement is coalesced.
        if hasattr(self, "_detail_timer"): self._detail_timer.stop()
        self._detail_now()

    def _suspend_navigation(self):
        self._navigation_visible = False
        self._detail_timer.stop()
        self._auto_refresh_timer.stop()

    def _resume_navigation(self):
        self._navigation_visible = True
        if hasattr(self, "_detail_timer"):
            self._auto_refresh_timer.start(60000, False)
            self._queue_detail()

    def _remember_navigation(self):
        current = self._current() or {}
        if current.get("id"): self._page_selection[self._active_page] = current["id"]
        _SOURCE_NAVIGATION.update(page=self._active_page, selections=dict(self._page_selection),
            selected=self._show_selected_only, useful=self._show_useful_only, pinned=self._show_pinned_only)
        self._navigation_visible = False
        self._detail_timer.stop()

    def _detail_now(self):
        item=self._current()
        if not item:
            hero=self._active_page
            if hero == "MY RECEIVER":
                self["group"].setText("MY RECEIVER  /  RECOMMENDED SOURCES")
            else:
                self["group"].setText("%s  /  SOURCES A-Z" % hero)
            self["category_stats"].setText(self._category_summary())
            self["detail"].setText("No source matches this page/filter.  1-6 opens provider pages • DIRECT = EPG-Scrapers • OK toggles USE.")
            return
        hero = self._active_page
        if hero == "MY RECEIVER":
            self["group"].setText("MY RECEIVER  /  RECOMMENDED SOURCES")
        else:
            self["group"].setText("%s  /  SOURCES A-Z" % hero)
        self["category_stats"].setText(self._category_summary())
        active = "ACTIVE" if self.store.is_selected(item.get("id")) else "NOT ACTIVE"
        mapped=int(item.get("_mapped_count",0) or 0)
        health=str(item.get("_runtime_state") or item.get("_health") or "--")
        channels=item.get("_channels")
        programmes=item.get("_programmes")
        score=int(item.get("_source_score") or 0)
        if score >= 85:
            fit="Excellent"
        elif score >= 65:
            fit="Good"
        elif score >= 40:
            fit="Fair"
        else:
            fit="Low"
        if str(item.get("provider") or "").upper() == "RYTEC":
            role="Native EPGImport source"
        elif item.get("_best_source"):
            role="Recommended for this receiver"
        elif item.get("_useful"):
            role="Useful for this receiver"
        else:
            role="Available source"
        linked=int(item.get("_id_linked") or 0)
        total=int(item.get("_id_total") or 0)
        ids_text=("%d/%d linked" % (linked,total)) if total else ("%d linked" % linked)
        native_rytec = str(item.get("provider") or "").upper() == "RYTEC"
        if native_rytec: ids_text="Shared provider file"
        mapped_text="Native SRP links" if native_rytec else "%d mapped" % mapped
        epg_ids=item.get("_epg_ids_text") or (str(channels) if channels is not None else "-")
        coverage=item.get("_coverage_pct")
        coverage_text=("%.1f%%" % float(coverage)) if coverage is not None else "-"
        schedule=self._download_schedule_text(item)
        schedule_line=("Automatic refresh %s" % schedule) if item.get("kind") == "local" else "Direct provider import"
        favorite=" • Favorite" if item.get("_pinned") else ""
        batch=" • SELECTED" if str(item.get("id") or "") in self._batch_selected else ""
        source_updated=item.get("_source_refresh_text") or item.get("_source_updated_text") or "Unknown"
        self["detail"].setText(
            "%s  •  %s  •  %s%s\n"
            "STATUS %s  •  EPG IDs %s  •  Programmes %s  •  Coverage %s\n"
            "%s  •  ID map %s  •  Published / checked GMT+0: %s  •  %s" % (
                item.get("_display_name") or item.get("name","Source"), active, role, favorite + batch,
                health, epg_ids, str(programmes) if programmes is not None else "-", coverage_text,
                mapped_text, ids_text, source_updated, schedule_line))
    def _up(self):
        try:self["list"].up()
        except Exception:pass
        self._queue_detail()
    def _down(self):
        try:self["list"].down()
        except Exception:pass
        self._queue_detail()
    def _page_up(self):
        moved = False
        try:
            self["list"].pageUp(); moved = True
        except Exception:
            pass
        if not moved:
            for _ in range(12):
                try: self["list"].up()
                except Exception: break
        self._queue_detail()
    def _page_down(self):
        moved = False
        try:
            self["list"].pageDown(); moved = True
        except Exception:
            pass
        if not moved:
            for _ in range(12):
                try: self["list"].down()
                except Exception: break
        self._queue_detail()

    def _update_batch_action_labels(self):
        count=len(self._batch_selected)
        try:
            self["key_green"].setText(("Refresh %d" % count) if count else "Refresh Source")
            self["key_yellow"].setText(("Multi Import %d" % count) if count else "Import Source")
        except Exception: pass

    def toggle_batch_mark(self):
        if self._busy: return
        item=self._current() or {}; sid=str(item.get("id") or "")
        if not sid: return
        if sid in self._batch_selected: self._batch_selected.remove(sid)
        else: self._batch_selected.add(sid)
        self._apply_filter(sid)

    def mark_active_for_batch(self):
        self._batch_selected=set(str(x.get("id")) for x in self._all_items if x.get("id") and self.store.is_selected(x.get("id")))
        self._apply_filter((self._current() or {}).get("id"))

    def toggle_page_batch_selection(self):
        """Select/unselect every source currently visible in the SELECT column."""
        ids=set(str(x.get("id")) for x in self._items if x.get("id"))
        if not ids:
            return
        if ids.issubset(self._batch_selected):
            self._batch_selected.difference_update(ids)
        else:
            self._batch_selected.update(ids)
        self._apply_filter((self._current() or {}).get("id"))

    def clear_batch_selection(self):
        self._batch_selected.clear(); self._apply_filter((self._current() or {}).get("id"))

    def _refresh_selected_fast_status_async(self, force=False):
        """Update CHECKED/SOURCE UPDATED for active feeds with HEAD only.

        This is deliberately independent from the global readiness daemon, which
        pauses while the EPGManager UI is active.  No XMLTV body is downloaded.
        """
        if self._fast_status_refresh_running: return
        ids=[str(x.get("id")) for x in self._all_items if x.get("id") and self.store.is_selected(x.get("id")) and x.get("kind") != "local"]
        stale=[]
        for sid in ids:
            try:
                if force or not source_fast_status.fresh(sid, 15*60): stale.append(sid)
            except Exception: stale.append(sid)
        if not stale: return
        self._fast_status_refresh_running=True; self._fast_status_refresh_results=None
        def done(rows): self._fast_status_refresh_results=rows or {}
        try:
            auto_readiness.refresh_sources_async(self.config, stale, on_complete=done, max_workers=2)
        except Exception:
            self._fast_status_refresh_running=False

    def refresh_batch_selected(self):
        if self._busy or not self._batch_selected: return
        ids=[sid for sid in self._batch_selected if source_catalog.by_catalogue_id().get(sid)]
        ids=source_variant_policy.normalize_selected(ids)
        if not ids: return
        self._busy=True; self._job_started_at=time.time(); self._job_total=len(ids); self._job_done=0; self._job_failed=0
        self._single_operation="batch_refresh"; self._single_label="%d marked source(s)" % len(ids)
        for sid in ids: self._set_runtime(sid,"PENDING",self._job_started_at,"Batch refresh queued")
        self._set_source_progress(1,"BATCH REFRESH","%d feed(s)" % len(ids)); self._apply_filter((self._current() or {}).get("id"))
        def done(result): self._batch_refresh_result=result or {}
        started=self.manager.run_catalogue_batch_update_only_async(ids,on_complete=done,force_download=True)
        if not started:
            self._busy=False; self._single_operation=""; self._set_source_progress(0,"FAILED","EPG Manager is busy"); return
        self._timer.start(900,False)

    def import_batch_selected(self):
        if self._busy or not self._batch_selected: return
        ids=[sid for sid in self._batch_selected if source_catalog.by_catalogue_id().get(sid)]
        ids=source_variant_policy.normalize_selected(ids)
        if not ids: return
        self._busy=True; self._job_started_at=time.time(); self._job_total=len(ids); self._job_done=0; self._job_failed=0
        self._single_operation="batch_import"; self._single_label="%d marked source(s)" % len(ids)
        for sid in ids:
            self._set_runtime(sid, "PREPARING", self._job_started_at, "Multi Import queued")
        self._set_source_progress(0,"MULTI IMPORT","parallel source prepare • one safe EPG write • %d feed(s)" % len(ids)); self._apply_filter((self._current() or {}).get("id"))
        def done(result): self._batch_import_result=result or {}
        started=self.manager.run_catalogue_batch_import_async(ids,on_complete=done)
        if not started:
            self._busy=False; self._single_operation=""; self._set_source_progress(0,"FAILED","EPG Manager is busy"); return
        self._timer.start(900,False)

    def toggle_current(self):
        item=self._current()
        if not item:return
        sid=item.get("id")
        was_selected=self.store.is_selected(sid)
        self.store.toggle(sid); self._metadata_dirty = True; self._apply_filter(sid)
        # beta28: readiness is automatic/direct-stream for every source. Selection only
        # changes queue priority; it never starts a heavy probe on the UI path.
        if (not was_selected) and self.store.is_selected(sid):
            try:
                self["detail"].setText("Selected. Automatic readiness queued with high priority.\nNo manual Prepare is required; key 1 only forces an immediate check.")
            except Exception:
                pass

    def _prepare_items_async(self, items, notify=True):
        if self._busy:
            return False
        rows=[dict(x) for x in (items or []) if x and x.get("id")]
        if not rows:
            return False
        # Keep already-fresh readiness rows out of the job unless they are local
        # and their XML changed; pressing full Refresh (3) remains the force path.
        todo=[]
        for item in rows:
            sid=item.get("id")
            if item.get("kind") != "local" and source_readiness.fresh(sid):
                continue
            todo.append(item)
        if not todo:
            self._apply_filter((self._current() or {}).get("id"))
            if notify:
                self.session.open(MessageBox,"Selected sources are already prepared.\nChannel IDs and programme samples are cached.",MessageBox.TYPE_INFO,timeout=4)
            return True
        self._busy=True
        self._prepare_notify=bool(notify)
        self._prepare_result=None
        self._job_started_at=time.time(); self._job_total=len(todo); self._job_done=0; self._job_failed=0
        for item in todo:
            self._set_runtime(item.get("id"),"PENDING",self._job_started_at,"Waiting for readiness probe")
        self._apply_filter((self._current() or {}).get("id"))
        def worker():
            result={"ok":0,"bad":0,"rows":[]}
            for item in todo:
                sid=item.get("id"); name=item.get("name") or sid
                try:
                    self._set_runtime(sid,"PROBING",time.time(),"Reading channel IDs + programme sample")
                    row=source_readiness.probe(item,self._epg_dir(),timeout=8,prepare_map=True)
                    good=bool((row or {}).get("ready"))
                    self._set_runtime(sid,"DONE" if good else "NOK",time.time(),str((row or {}).get("state") or "DONE"))
                    result["ok" if good else "bad"] += 1
                    result["rows"].append((sid,name,row))
                except source_channel_sync.SyncDeferred as exc:
                    self._set_runtime(sid,"WAIT",time.time(),str(exc))
                    result["bad"] += 1; result["rows"].append((sid,name,{"state":"WAIT","error":str(exc)}))
                    break
                except Exception as exc:
                    self._set_runtime(sid,"NOK",time.time(),str(exc))
                    result["bad"] += 1; result["rows"].append((sid,name,{"state":"NOK","error":str(exc)}))
                self._job_done += 1
                time.sleep(0.10)
            self._prepare_result=result
        threading.Thread(target=worker,daemon=True).start()
        self._timer.start(250,False)
        return True

    def prepare_selected(self):
        selected=[x for x in self._all_items if self.store.is_selected(x.get("id"))]
        if not selected:
            self.session.open(MessageBox,"No EPG sources selected.\nUse OK to select sources first.",MessageBox.TYPE_INFO)
            return
        self._prepare_items_async(selected,notify=True)

    def toggle_selected_filter(self):
        self._show_selected_only = not self._show_selected_only
        if self._show_selected_only:
            self._show_useful_only = False; self._show_pinned_only = False
        self._apply_filter()
    def show_all(self):
        self._show_selected_only = False; self._show_useful_only = False; self._show_pinned_only = False
        self._apply_filter()

    def toggle_all(self):
        ids = [x.get("id") for x in self._all_items if x.get("id")]
        selected = self.store.get_selected()
        if ids and all(x in selected for x in ids):
            self.store.set_selected(set())
        else:
            self.store.set_selected(set(ids))
        self._apply_filter((self._current() or {}).get("id"))

    def run_health_check(self):
        self._health_manual = True
        self["detail"].setText("HEALTH CHECK running in background...\nValidating downloaded XMLTV files without blocking navigation.")
        self._start_health_scan(force=True)

    def open_options(self):
        from Screens.ChoiceBox import ChoiceBox
        self.session.openWithCallback(self._simple_option_selected, ChoiceBox, title="Sources",
            list=[("Filter sources", "filter"), ("Multiple selection", "batch"),
                  ("Mapping", "mapping"), ("Advanced tools", "advanced")])

    def _simple_option_selected(self, answer):
        if not answer: return
        from Screens.ChoiceBox import ChoiceBox
        key = answer[1]
        if key == "mapping": return self.open_mapping()
        choices = {
            "filter": [("All sources", "all"), ("Active sources", "selected"),
                       ("Useful for this receiver", "useful"), ("My receiver", "myreceiver")],
            "batch": [("Select / unselect this page", "batch_page"),
                      ("Select active sources", "batch_active"), ("Clear selection", "batch_clear")],
            "advanced": [("Health check", "health"), ("Reload catalogue", "catalogue"),
                         ("Settings", "settings")],
        }.get(key)
        if choices:
            self.session.openWithCallback(self._option_selected, ChoiceBox, title="Sources / "+key, list=choices)

    def _option_selected(self, answer):
        if not answer:
            return
        key = answer[1]
        if key == "batch_page": self.toggle_page_batch_selection()
        elif key == "batch_active": self.mark_active_for_batch()
        elif key == "batch_clear": self.clear_batch_selection()
        elif key == "settings": self.open_settings()
        elif key == "myreceiver": self._set_page("MY RECEIVER")
        elif key == "countries": self.open_unified_countries()
        elif key == "useful": self.toggle_useful_filter() if not self._show_useful_only else self.show_all()
        elif key == "pin": self.toggle_current_pin()
        elif key == "pinned": self.toggle_pinned_filter() if not self._show_pinned_only else self.show_all()
        elif key == "conflicts": self.open_mapping_conflicts()
        elif key == "health":
            self._health_manual = True
            self["detail"].setText("HEALTH CHECK running in background...\nValidating downloaded XMLTV files without blocking navigation.")
            self._start_health_scan(force=True)
        elif key == "mapping": self.open_mapping()
        elif key == "catalogue": self.reload_catalogue()
        elif key == "selected":
            self._show_selected_only = True; self._show_useful_only = False; self._show_pinned_only = False; self._apply_filter()
        elif key == "all": self.show_all()

    def _launch_current_source_import(self, sid, name):
        self._set_runtime(sid, "RUNNING", time.time(), "Direct source import")
        self._apply_filter(sid)
        self["detail"].setText("%s  •  IMPORT SOURCE  •  this source only" % name)
        self._set_source_progress(3, "NATIVE IMPORT", "Starting selected source")
        def done(result):
            self._single_import_result = (sid, name, result or {})
        started = self.manager.run_catalogue_import_async(sid, on_complete=done)
        if not started:
            self._busy = False
            self["key_yellow"].setText("Import Source")
            self._set_source_progress(0, "FAILED", "EPG Manager is busy")
            self._single_operation = ""
            self._single_label = ""
            self._set_runtime(sid, "NOK", time.time(), "EPG Manager is busy")
            self._apply_filter(sid)
            self.session.open(MessageBox, "EPG Manager is already busy.", MessageBox.TYPE_INFO, timeout=3)
            return False
        self._timer.start(1200, False)
        return True

    def refresh_full_ids(self):
        if self._busy:
            self.session.open(MessageBox, "EPG Manager is busy.", MessageBox.TYPE_INFO, timeout=3)
            return
        self["detail"].setText("Refreshing complete OpenEPG MENA Channel IDs in background...\nOnly XMLTV channel headers are read; programme data is skipped.")
        started = source_id_harvester.refresh_async(config=self.config, force=True)
        if not started:
            self.session.open(MessageBox, "Channel-ID refresh is already running.", MessageBox.TYPE_INFO, timeout=3)
        else:
            self.session.open(MessageBox, "Full Channel-ID refresh started in background.\n\nThe SRP maps and ID audit will rebuild automatically when finished.", MessageBox.TYPE_INFO, timeout=5)

    def show_id_audit(self):
        item = self._current() or {}
        mid = source_catalog.mapping_source_id(item)
        row = id_coverage_audit.source_stats(mid) or {}
        if not row:
            self.session.open(MessageBox, "ID audit is not ready yet.\n\nPath: /etc/epgimport/epgmanager-id-audit.txt", MessageBox.TYPE_INFO)
            return
        details = row.get("details") or {}
        match_failed = list(details.get("MATCH FAILED") or [])
        lines = [
            str(row.get("name") or mid),
            "Status: %s" % (row.get("status") or "-"),
            "IDs: %d (%s catalogue)" % (int(row.get("total_ids") or 0), "FULL" if row.get("catalog_complete", True) else "PARTIAL"),
            "Linked: %d" % int(row.get("linked_ids") or 0),
            "Match failed: %d" % int(row.get("match_failed") or 0),
            "Not on receiver: %d" % int(row.get("not_present") or 0),
            "Non-TV / virtual / bad ID: %d" % (
                int(row.get("non_tv") or 0) + int(row.get("online_virtual") or 0) + int(row.get("bad_source_ids") or 0)),
            "Receiver-applicable coverage: %.1f%%" % float(row.get("applicable_coverage") or 0.0),
            "SRP links: %d" % int(row.get("srp_links") or 0),
            "Conflicts: %d" % int(row.get("conflicts") or 0),
            "",
            "Audit file: /etc/epgimport/epgmanager-id-audit.txt",
        ]
        if match_failed:
            lines += ["", "First real matching failures:"] + [
                "- %s" % str((x or {}).get("channel_id") or "") for x in match_failed[:12]
            ]
        self.session.open(MessageBox, "\n".join(lines), MessageBox.TYPE_INFO)

    def import_current_source(self):
        if self._batch_selected:
            return self.import_batch_selected()
        """Import the highlighted source with RC9 auto-prepare semantics.

        The UI itself stays zero-I/O. The background EPGCycle performs one cheap
        readiness check and automatically Refreshes only when first-use artifacts
        are missing. Already-prepared sources keep the direct fast-import path.
        """
        if self._busy:
            return
        item = self._current()
        if not item or not item.get("id"):
            return
        sid = item.get("id")
        name = item.get("name") or sid
        self._busy = True
        self._job_started_at = time.time()
        self._job_total = 1
        self._job_done = 0
        self._job_failed = 0
        self._single_operation = "import"
        self._single_label = name
        self["key_yellow"].setText("Importing...")
        self._set_source_progress(0, "START", "Opening selected source")
        self._launch_current_source_import(sid, name)

    def import_all_selected(self):
        if self._busy:
            return
        self.preferences.set_selected_catalogue_ids(self.store.get_selected(), save=True)
        self._schedule_background_coverage()
        selected = list(self.preferences.selected_catalogue_ids() or [])
        if not selected:
            self.session.open(MessageBox, "No EPG sources selected.\nUse OK to tick the sources first.", MessageBox.TYPE_INFO)
            return
        self._busy = True
        self._job_started_at = time.time()
        self._job_total = len(selected)
        self._job_done = 0
        self._job_failed = 0
        self._single_operation = "import_all"
        self._single_label = "%d selected source(s)" % len(selected)
        self["key_blue"].setText("Working...")
        # Always reset stale progress from a previous Refresh/Import Source.
        self._set_source_progress(0, "START", "Preparing Import All")
        self["detail"].setText("IMPORT ALL  •  %d selected source(s)" % len(selected))
        def done(result):
            self._import_all_result = result or {}
        started = self.manager.run_fast_import_async(on_complete=done)
        if not started:
            self._busy = False
            self["key_blue"].setText("More")
            self._single_operation = ""
            self._single_label = ""
            self._set_source_progress(0, "FAILED", "EPG Manager is busy")
            self.session.open(MessageBox, "EPG Manager is already busy.", MessageBox.TYPE_INFO, timeout=3)
            return
        self._timer.start(1200, False)

    def update_and_import_selected(self):
        # Compatibility alias: beta15 no longer requires a separate receiver-
        # side update phase for remote feeds.
        return self.import_all_selected()

    @staticmethod
    def _schedule_background_coverage():
        # beta21 performance hotfix: source selection must never arm a global
        # receiver-wide mapping/coverage job.
        return

    def cancel_changes(self):
        self.store.set_selected(self._initial_selected)
        self.close()

    def save_and_close(self):
        self.preferences.set_selected_catalogue_ids(self.store.get_selected(), save=True)
        self._schedule_background_coverage()
        self.close()

    def _set_source_progress(self, percent=0, stage="", detail=""):
        """Low-redraw progress update for small Enigma2 receivers.

        Progress widgets are surprisingly expensive on some skins.  Skip
        identical frames and let the 0.9s monitor timer drive visible updates
        instead of repainting several times per second.
        """
        pct = max(0, min(100, int(percent or 0)))
        text = str(stage or "")
        if detail:
            text += ("  •  " if text else "") + str(detail)
        signature = (pct, text, bool(self._busy))
        if signature == getattr(self, "_last_progress_signature", None):
            return
        self._last_progress_signature = signature
        try: self["progress"].setValue(pct)
        except Exception: pass
        self["progress_pct"].setText(("%d%%" % pct) if (pct or self._busy) else "")
        self["progress_label"].setText(text)

    def _reset_source_progress(self):
        self._single_operation = ""
        self._single_label = ""
        self._set_source_progress(0, "", "")

    def download_current(self):
        if self._batch_selected:
            return self.refresh_batch_selected()
        """Refresh exactly the highlighted source and show real cycle progress.

        beta53 uses the same one-source EPGCycleRunner as the country/source
        screen, instead of a metadata probe.  For local generators this
        rebuilds the XMLTV cache; for remote feeds it refreshes that feed only.
        No mapping or Native Import is run by GREEN.
        """
        if self._busy:
            return
        item = self._current()
        if not item or not item.get("id"):
            return
        sid = item.get("id")
        name = item.get("name") or sid
        started = time.time()
        self._busy = True
        self._errors = []
        self._job_started_at = started
        self._job_total = 1
        self._job_done = 0
        self._job_failed = 0
        self._single_operation = "refresh"
        self._single_label = name
        self._set_runtime(sid, "DOWNLOADING", started, "Refreshing source")
        self._apply_filter(sid)
        self["key_green"].setText("Refreshing...")
        self["detail"].setText("%s  •  Refresh Source  •  this source only" % name)
        self._set_source_progress(1, "CONNECT", "Starting source refresh")
        def done(result):
            result = result or {"ok": False, "message": "Unknown result"}
            # Keep Smart Mapping's Channel-ID catalogue in sync with the actual
            # refreshed feed.  beta73 refreshed programme XML but could leave the
            # lightweight ID shard stale, which made GREEN look like it did nothing.
            if result.get("ok"):
                try:
                    downloads = result.get("downloads") or {}
                    one = downloads.get(sid) or {} if isinstance(downloads, dict) else {}
                    path = str((one or {}).get("path") or "")
                    rows = []
                    if path and os.path.exists(path):
                        rows = channel_mapper.read_xmltv_channel_ids_fast(path, sid, name)
                    if rows:
                        source_channel_cache.put(sid, name, rows,
                                                 provider=item.get("provider") or "",
                                                 region=item.get("region") or "",
                                                 language=item.get("language") or "")
                        result["channel_catalog_ids"] = len(rows)
                except Exception:
                    pass
            self._smart_refresh_result = (sid, name, result)
        launched = self.manager.run_catalogue_update_only_async(sid, on_complete=done, force_download=True)
        if not launched:
            self._busy = False
            self["key_green"].setText("Refresh Source")
            self._set_runtime(sid, "NOK", started, "EPG Manager is busy")
            self._set_source_progress(0, "", "")
            self.session.open(MessageBox, "EPG Manager is already busy.", MessageBox.TYPE_INFO, timeout=3)
            return
        self._timer.start(1200, False)

    def update_selected(self):
        if self._busy:
            return
        selected = [x for x in self._all_items if self.store.is_selected(x.get("id"))]
        if not selected:
            self.session.open(MessageBox, "No EPG sources selected.\nUse OK to select sources first.", MessageBox.TYPE_INFO)
            return
        started = time.time()
        self._busy = True
        self._errors = []
        self._job_started_at = started
        self._job_total = len(selected)
        self._job_done = 0
        self._job_failed = 0
        self._pending_locals = [x.get("manager_id") for x in selected if x.get("kind") == "local" and x.get("manager_id")]
        external = [x for x in selected if x.get("kind") != "local"]
        for item in selected:
            self._set_runtime(item.get("id"), "PENDING", started, "Queued")
        self._apply_filter((self._current() or {}).get("id"))
        self["detail"].setText("Source metadata refresh started in background\n%d source(s) queued  •  remote feeds are streamed directly; no full XMLTV files are saved." % len(selected))

        def fetch_external(item):
            sid = item.get("id")
            one_started = time.time()
            self._set_runtime(sid, "STREAMING", one_started, "Reading provider stream")
            if item.get("dynamic"):
                return sid, item, False, 0, 0, "Dynamic EPG-Importer URL", 0.0, None
            try:
                row = source_readiness.probe(
                    item, self._epg_dir(), timeout=10, prepare_map=True,
                    quality_scan=5 * 1024 * 1024, max_programmes=600)
                ok = bool((row or {}).get("programmes") and not (row or {}).get("placeholder"))
                message = "%s • direct stream" % str((row or {}).get("state") or "CHECKED")
                return sid, item, ok, int((row or {}).get("channels") or 0), int((row or {}).get("programmes") or 0), message, max(0.0, time.time()-one_started), None
            except Exception as exc:
                return sid, item, False, 0, 0, str(exc), max(0.0, time.time()-one_started), None

        def worker():
            # Deliberately sequential on Vu+ receivers. One provider stream at
            # a time avoids RAM spikes and prevents three decompressors from
            # competing with Enigma2.
            for item in external:
                sid, item, ok, channels, programmes, message, elapsed, path = fetch_external(item)
                row = {"health": "OK" if ok else "NOK", "channels": channels, "programmes": programmes,
                       "message": message, "duration": elapsed, "downloaded_at": int(time.time())}
                source_index_cache.update(sid, row)
                self._health_cache[sid] = dict(row)
                self._health_overrides[sid] = dict(row)
                self._set_runtime(sid, "DONE" if ok else "NOK", time.time(), message)
                self._job_done += 1
                if not ok:
                    self._job_failed += 1
                    self._errors.append("%s: %s" % (item.get("name"), message))
                time.sleep(0.10)
            self._external_done = True
        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(1200, False)
    def _finish_single_result(self, source_id, name, ok, channels, programmes, message, duration=None):
        row = {"health": "OK" if ok else "NOK", "channels": int(channels or 0), "programmes": int(programmes or 0),
               "message": message, "duration": float(duration or 0.0), "downloaded_at": int(time.time())}
        try:
            item = next((x for x in self._all_items if x.get("id") == source_id), None)
            path = self._source_path(item) if item else None
            if path and os.path.exists(path):
                row["sig"] = source_index_cache.signature(path)
        except Exception:
            pass
        self._health_overrides[source_id] = row
        self._health_cache[source_id] = row
        source_index_cache.update(source_id, row)
        self._set_runtime(source_id, "DONE" if ok else "NOK", time.time(), message)
        self._busy = False
        activity_store.record_download(sources=1, ok=1 if ok else 0, failed=0 if ok else 1,
                                       channels=int(channels or 0), programmes=int(programmes or 0),
                                       elapsed=float(duration or 0.0), source=name)
        self.reload_catalogue()
        if ok:
            self.session.open(MessageBox, "%s\n\nStatus: OK\nChannels: %d\nProgrammes: %d\nTime: %.1f sec" %
                              (name, channels or 0, programmes or 0, float(duration or 0.0)), MessageBox.TYPE_INFO)
        else:
            self.session.open(MessageBox, "%s\n\nStatus: NOK\n%s" % (name, message or "Source update failed"), MessageBox.TYPE_ERROR)
    def _poll(self):
        now = time.time()
        if self._fast_status_refresh_running and self._fast_status_refresh_results is not None:
            self._fast_status_refresh_running=False
            self._fast_status_refresh_results=None
            self._metadata_dirty=True
            self._metadata_refreshed_at=0.0
            self._apply_filter((self._current() or {}).get("id"),refresh_metadata=True)

        if hasattr(self,"_batch_refresh_result"):
            result=self._batch_refresh_result; del self._batch_refresh_result
            self._busy=False; self._single_operation=""
            downloads=(result or {}).get("downloads") or {}
            ok=sum(1 for r in downloads.values() if (r or {}).get("ok")); total=len(downloads) or len(self._batch_selected)
            for sid in list(self._batch_selected):
                row=(downloads or {}).get(sid) or {}
                self._set_runtime(sid, "DONE" if row.get("ok") else "NOK", time.time(),
                                  "Batch refresh completed" if row.get("ok") else str(row.get("error") or "Refresh failed"))
            stage="DONE" if ok == total and total else ("PARTIAL" if ok else "FAILED")
            self._set_source_progress(100 if ok else 0,stage,"%d/%d feed(s) refreshed" % (ok,total))
            self._update_batch_action_labels()
            self._metadata_dirty=True; self._metadata_refreshed_at=0.0; self._apply_filter((self._current() or {}).get("id"),refresh_metadata=True)
            self._refresh_selected_fast_status_async(force=True)
            self.session.open(MessageBox,"Batch Refresh finished\n\n%d/%d source(s) refreshed.\nMarked feeds stay selected so YELLOW can import the same batch." % (ok,total),MessageBox.TYPE_INFO,timeout=6)
            return

        if hasattr(self,"_batch_import_result"):
            result=self._batch_import_result; del self._batch_import_result
            self._busy=False; self._single_operation=""
            imp=(result or {}).get("import") or {}; imported=int(imp.get("imported_events") or 0); services=int(imp.get("written_services") or 0); elapsed=float((result or {}).get("elapsed") or 0.0)
            for sid in list(self._batch_selected):
                self._set_runtime(sid, "DONE" if (result or {}).get("ok") else "NOK", time.time(), "Batch import completed")
            self._set_source_progress(100 if (result or {}).get("ok") else 0,"DONE" if (result or {}).get("ok") else "FAILED","%d events • %d services • %.1fs" % (imported,services,elapsed))
            self._update_batch_action_labels(); self._apply_filter((self._current() or {}).get("id"))
            self.session.open(MessageBox,"Multi Import finished\n\nSources: %d\nEvents: %d\nServices: %d\nTime: %.1f sec" % (len(self._batch_selected),imported,services,elapsed),MessageBox.TYPE_INFO,timeout=6)
            return
        if self._busy and now - self._last_monitor_render >= 1.80:
            self._last_monitor_render = now
            elapsed = max(0, int(now - (self._job_started_at or now)))
            if self._single_operation in ("refresh", "import", "import_all", "batch_refresh", "batch_import"):
                live = self.manager.get_cycle_status() if self.manager else {}
                if live.get("running"):
                    pct = max(0, min(99, int(live.get("percent") or 0)))
                    # Refresh/scraper sources cannot report byte-level progress.
                    # Keep movement gentle; aggressive redraws caused CPU spikes.
                    if self._single_operation == "refresh" and pct <= 4:
                        pct = min(25, max(pct, 1 + elapsed))
                    stage = str(live.get("stage") or self._single_operation.upper())
                    detail = str(live.get("detail") or "Working")
                    self._set_source_progress(pct, stage, "%s  •  %ds" % (detail, elapsed))
                self["detail"].setText("%s  •  %s in progress" % (self._single_label or "EPG", self._single_operation.upper()))
            else:
                running, done, failed = self._runtime_counts()
                self["detail"].setText("ONGOING  •  %d running  •  %d done  •  %d failed\nElapsed: %ds  •  background processing" % (running, done, failed, elapsed))

        if self._health_scan_running and self._health_scan_results is not None:
            results = self._health_scan_results
            self._health_scan_results = None
            self._health_scan_running = False
            self._health_cache.update(results)
            self._apply_filter((self._current() or {}).get("id"))
            if getattr(self, "_health_manual", False):
                self._health_manual = False
                ok = sum(1 for row in results.values() if str((row or {}).get("health") or "").upper() == "OK")
                nok = sum(1 for row in results.values() if str((row or {}).get("health") or "").upper() == "NOK")
                self["detail"].setText("HEALTH CHECK DONE  •  %d OK  •  %d NOK\nResults are cached; reopening this screen stays fast." % (ok, nok))
            if not self._busy:
                self._timer.stop()
                return

        if hasattr(self, "_import_all_result"):
            self._timer.stop()
            result = self._import_all_result
            del self._import_all_result
            self._busy = False
            self["key_blue"].setText("More")
            ok = bool((result or {}).get("ok"))
            imported = int(((result or {}).get("import") or {}).get("imported_events") or 0)
            elapsed = float((result or {}).get("elapsed") or max(0.0, time.time() - (self._job_started_at or time.time())))
            message = str((result or {}).get("message") or ("EPG import completed" if ok else "Import All failed"))
            self._set_source_progress(100 if ok else 0, "DONE" if ok else "FAILED", "%s  •  %.1fs" % (message, elapsed))
            self._single_operation = ""
            self._single_label = ""
            # One full redraw only after completion, not every 350 ms.
            self._apply_filter((self._current() or {}).get("id"))
            if ok:
                self.session.open(MessageBox, "Import All: DONE\n\nEvents imported: %d\nTime: %.1f sec" % (imported, elapsed), MessageBox.TYPE_INFO, timeout=6)
            else:
                self.session.open(MessageBox, "Import All: FAILED\n\n%s" % message, MessageBox.TYPE_ERROR)
            return

        if hasattr(self, "_smart_refresh_result"):
            self._timer.stop()
            sid, name, result = self._smart_refresh_result
            del self._smart_refresh_result
            self._busy = False
            self["key_green"].setText("Refresh Source")
            ok = bool((result or {}).get("ok"))
            elapsed = float((result or {}).get("elapsed") or max(0.0, time.time() - (self._job_started_at or time.time())))
            message = str((result or {}).get("message") or ("Source refreshed" if ok else "Source refresh failed"))
            downloads = (result or {}).get("downloads") or {}
            one = downloads.get(sid) or {} if isinstance(downloads, dict) else {}
            channels = int((one or {}).get("channels") or 0)
            programmes = int((one or {}).get("programmes") or 0)
            stale_cache = bool((one or {}).get("stale_cache"))
            runtime_state = "DONE" if ok else "NOK"
            progress_state = "DONE" if ok else "FAILED"
            self._set_runtime(sid, runtime_state, time.time(), message)
            self._set_source_progress(100 if ok else 0, progress_state, "%s  •  %.1fs" % (message, elapsed))
            self._apply_filter(sid)
            if ok:
                self._refresh_selected_fast_status_async(force=True)
            activity_store.record_download(sources=1, ok=1 if ok else 0, failed=0 if ok else 1,
                                           channels=channels, programmes=programmes, elapsed=elapsed, source=name)
            kept_cache = bool((one or {}).get("kept_cache") or (one or {}).get("fallback"))
            if ok and kept_cache:
                warning = str((one or {}).get("warning") or message or "Last Known Good kept")
                self.session.open(MessageBox, "%s\n\nRefresh Source: CACHE KEPT\n%s" % (name, warning), MessageBox.TYPE_INFO, timeout=7)
            elif ok:
                self.session.open(MessageBox, "%s\n\nRefresh Source: DONE\nTime: %.1f sec" % (name, elapsed), MessageBox.TYPE_INFO, timeout=5)
            else:
                self.session.open(MessageBox, "%s\n\nRefresh Source: FAILED\n%s" % (name, message), MessageBox.TYPE_ERROR)
            self._single_operation = ""
            self._single_label = ""
            return

        if self._prepare_result is not None:
            result=self._prepare_result; self._prepare_result=None; self._busy=False
            notify=self._prepare_notify; self._prepare_notify=False
            self._apply_filter((self._current() or {}).get("id"))
            try:self._timer.stop()
            except Exception:pass
            if notify:
                self.session.open(MessageBox,
                    "Source preparation completed.\n\n%d READY\n%d need attention\n\nREADY means: channel IDs found + programme data verified + SRP map available." %
                    (int(result.get("ok") or 0),int(result.get("bad") or 0)),
                    MessageBox.TYPE_INFO,timeout=7)
            return

        if hasattr(self, "_prepare_then_import_result"):
            sid, name, row, error = self._prepare_then_import_result
            del self._prepare_then_import_result
            if error or not bool((row or {}).get("map_ready")) or int((row or {}).get("programmes") or 0) <= 0:
                self._timer.stop()
                self._busy = False
                self["key_yellow"].setText("Import Source")
                message = error or str((row or {}).get("state") or "Source is not importable")
                self._set_source_progress(0, "FAILED", message)
                self._single_operation = ""
                self._single_label = ""
                self._set_runtime(sid, "NOK", time.time(), message)
                self._apply_filter(sid)
                self.session.open(MessageBox, "%s\n\nImport Source: FAILED\n%s" % (name, message), MessageBox.TYPE_ERROR)
                return
            self._apply_filter(sid)
            self._launch_current_source_import(sid, name)
            return

        if hasattr(self, "_single_import_result"):
            self._timer.stop()
            sid, name, result = self._single_import_result
            del self._single_import_result
            self._busy = False
            self["key_yellow"].setText("Import Source")
            ok = bool((result or {}).get("ok"))
            imported = int(((result or {}).get("import") or {}).get("imported_events") or 0)
            elapsed = float((result or {}).get("elapsed") or 0.0)
            message = str((result or {}).get("message") or ("EPG import completed" if ok else "Source import failed"))
            self._set_runtime(sid, "DONE" if ok else "NOK", time.time(), message)
            self._set_source_progress(100 if ok else 0, "DONE" if ok else "FAILED", "%s  •  %.1fs" % (message, elapsed))
            self._apply_filter(sid)
            self._single_operation = ""
            self._single_label = ""
            if ok:
                self.session.open(MessageBox, "%s\n\nImport Source: DONE\nEvents imported: %d\nTime: %.1f sec" %
                                  (name, imported, elapsed), MessageBox.TYPE_INFO, timeout=6)
            else:
                self.session.open(MessageBox, "%s\n\nImport Source: FAILED\n%s" % (name, message), MessageBox.TYPE_ERROR)
            return

        if hasattr(self, "_single_external_result"):
            self._timer.stop()
            sid, name, res = self._single_external_result
            del self._single_external_result
            self._finish_single_result(sid, name, res.get("ok", False), res.get("channels", 0),
                                       res.get("programmes", 0), res.get("message", ""), res.get("elapsed", 0.0))
            return

        if hasattr(self, "_single_local_result"):
            result = self._single_local_result
            del self._single_local_result
            context = getattr(self, "_single_local_context", None) or {}
            try:
                del self._single_local_context
            except Exception:
                pass
            sid = context.get("sid")
            name = context.get("name", "Source")
            manager_id = context.get("manager_id")
            st = self.manager.get_status(manager_id) or {}
            path = context.get("path")
            started = context.get("started") or (self._runtime.get(sid) or {}).get("started") or self._job_started_at or time.time()
            def local_metrics_worker():
                channels = 0
                try:
                    if path and os.path.exists(path):
                        channels = len(channel_mapper.read_xmltv_channel_ids_fast(path, sid or manager_id, name))
                except Exception:
                    channels = 0
                programmes = int(st.get("program_count") or 0)
                update_ok = bool((result or {}).get(manager_id))
                status = str(st.get("status") or "").upper()
                try:
                    existing_ok = bool(path and os.path.exists(path) and os.path.getsize(path) > 32)
                except Exception:
                    existing_ok = False
                ok = bool(update_ok or (status in ("SUCCESS", "COOLDOWN") and existing_ok))
                message = st.get("error_message") or ("XMLTV ready" if ok else "Source update failed")
                self._single_local_metrics = (sid, name, ok, channels, programmes, message, max(0.0, time.time()-started))
            threading.Thread(target=local_metrics_worker, daemon=True).start()
            return

        if hasattr(self, "_single_local_metrics"):
            self._timer.stop()
            data = self._single_local_metrics
            del self._single_local_metrics
            self._finish_single_result(*data)
            return

        if getattr(self, "_external_done", False):
            del self._external_done
            if self._pending_locals:
                ids = list(self._pending_locals)
                self._pending_locals = []
                manager_to_item = {x.get("manager_id"): x for x in self._all_items if x.get("manager_id") in ids}
                for manager_id in ids:
                    item = manager_to_item.get(manager_id) or {}
                    self._set_runtime(item.get("id"), "DOWNLOADING", time.time(), "Generating XMLTV")
                ok = self.manager.update_selected_async(ids, on_complete=lambda result: setattr(self, "_local_result", result), auto_import=False)
                if not ok:
                    self._errors.append("Local EPG Manager update could not start")
                    self._local_result = {}
            else:
                self._local_result = {}

        if hasattr(self, "_local_result") and not getattr(self, "_local_metrics_running", False):
            result = self._local_result
            del self._local_result
            self._local_metrics_running = True
            locals_now = [x for x in self._all_items if x.get("kind") == "local" and x.get("manager_id")]
            def finish_locals():
                for item in locals_now:
                    manager_id = item.get("manager_id")
                    sid = item.get("id")
                    state = str((self._runtime.get(sid) or {}).get("state") or "")
                    if state not in ("DOWNLOADING", "PENDING", "RUNNING"):
                        continue
                    st = self.manager.get_status(manager_id) or {}
                    path = self._source_path(item)
                    channels = 0
                    try:
                        if path and os.path.exists(path):
                            channels = len(channel_mapper.read_xmltv_channel_ids_fast(path, sid, item.get("name") or sid))
                    except Exception:
                        channels = 0
                    programmes = int(st.get("program_count") or 0)
                    try:
                        existing_ok = bool(path and os.path.exists(path) and os.path.getsize(path) > 32)
                    except Exception:
                        existing_ok = False
                    status = str(st.get("status") or "").upper()
                    ok = bool((result or {}).get(manager_id) or (status in ("SUCCESS", "COOLDOWN") and existing_ok))
                    message = st.get("error_message") or ("XMLTV ready" if ok else "Source update failed")
                    duration = float(st.get("duration_seconds") or 0.0)
                    row = {"health": "OK" if ok else "NOK", "channels": channels, "programmes": programmes,
                           "message": message, "duration": duration, "downloaded_at": int(time.time()),
                           "sig": source_index_cache.signature(path) if path else None}
                    source_index_cache.update(sid, row)
                    self._health_cache[sid] = dict(row)
                    self._health_overrides[sid] = dict(row)
                    self._set_runtime(sid, "DONE" if ok else "NOK", time.time(), message)
                    self._job_done += 1
                    if not ok:
                        self._job_failed += 1
                        self._errors.append("%s: %s" % (item.get("name"), message))
                self._local_metrics_done = True
            threading.Thread(target=finish_locals, daemon=True).start()
            return

        if getattr(self, "_local_metrics_done", False):
            self._local_metrics_done = False
            self._local_metrics_running = False
            self._timer.stop()
            self._busy = False
            self.reload_catalogue()
            elapsed = max(0.0, time.time() - (self._job_started_at or time.time()))
            rows = [self._health_cache.get(x.get("id")) or {} for x in self._all_items if self.store.is_selected(x.get("id"))]
            ok_count = sum(1 for r in rows if str(r.get("health") or "").upper() == "OK")
            failed_count = sum(1 for r in rows if str(r.get("health") or "").upper() == "NOK")
            channels = sum(int(r.get("channels") or 0) for r in rows)
            programmes = sum(int(r.get("programmes") or 0) for r in rows)
            activity_store.record_download(sources=len(rows), ok=ok_count, failed=failed_count,
                                           channels=channels, programmes=programmes, elapsed=elapsed)
            if self._errors:
                shown = "\n".join(self._errors[:8])
                more = len(self._errors) - 8
                if more > 0:
                    shown += "\n... and %d more" % more
                self.session.open(MessageBox, "Daily EPG finished with warnings:\n\n%s" % shown, MessageBox.TYPE_WARNING)
            else:
                self.session.open(MessageBox, "Daily EPG DONE\n\n%d sources\n%d channels\n%d programmes\n%.1f sec" %
                                  (len(rows), channels, programmes, elapsed), MessageBox.TYPE_INFO, timeout=5)
    def open_settings(self):
        try:
            from .settings import EPGManagerSettingsScreen
            self.session.openWithCallback(lambda *args: self._apply_filter((self._current() or {}).get("id")), EPGManagerSettingsScreen, self.config)
        except Exception:
            pass

    def open_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen,self.config,True,self.manager)


# Backward compatibility: old callers/imports still use NativeSourcesScreen.
NativeSourcesScreen = SmartSourcesScreen


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (SmartSourcesScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass


def _native_rytec_display(item, prepared):
    if str(item.get("provider") or "").upper() != "RYTEC":
        return
    # A shared channels file is a native mapping mechanism, not the channel
    # count of this country's programme feed or evidence of future schedules.
    item["_epg_ids_text"] = "Shared SRP"
    item["_channels"] = None
    state = str(item.get("_health") or "").upper()
    if state == "MAP NEEDED" or (state in ("NO EPG", "CHECKING", "CATALOG OK") and
                                  not bool((prepared or {}).get("complete"))):
        item["_health"] = "NATIVE MAP"
        item["_programmes"] = None
        item["_health_message"] = "Native EPGImport map; future programme availability not verified"
