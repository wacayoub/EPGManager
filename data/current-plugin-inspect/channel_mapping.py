# -*- coding: utf-8 -*-
"""Jedi-style four-pane Smart Mapping UI for EPG Manager standalone mode.

Layout:
    Bouquet | Channel | EPG Source | EPG Selection

The screen is intentionally self-contained and does not depend on JediEPGXtream.
It reads Enigma2 bouquets and EPG Manager's generated XMLTV files, lets the user
walk left/right between panes, and stores manual mappings in
/etc/enigma2/epgmanager_mappings.json.
"""

from enigma import eTimer, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_VALIGN_CENTER
import threading
import os
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
from Tools.LoadPixmap import LoadPixmap

from ..core import channel_mapper, external_sources
from ..core.mapping_store import MappingStore
from ..core.epgimport_export import save_sourcexml
from ..core.logger import get_logger
from ..core.native_importer import parse_xmltv_time
from . import theme

log = get_logger(__name__)


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
    """Channel pane with a real mapped icon instead of text tags."""
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", 25))
        self.l.setItemHeight(48)
        icon_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "icons", "mapped.png"))
        try:
            self.mapped_icon = LoadPixmap(path=icon_path, cached=True)
        except Exception:
            self.mapped_icon = None

    def build_rows(self, services, mapped_refs, mapping_suffix_cb):
        rows = []
        for item in services:
            ref = item.get("ref")
            mapped = ref in mapped_refs
            name = item.get("name", "Unnamed service")
            suffix = mapping_suffix_cb(ref) if mapped else ""
            row = [item]
            x = 12
            if mapped:
                if self.mapped_icon is not None:
                    row.append(MultiContentEntryPixmapAlphaTest(pos=(10,10), size=(28,28), png=self.mapped_icon))
                else:
                    row.append(MultiContentEntryText(pos=(8,0), size=(32,48), font=0, flags=RT_VALIGN_CENTER, text="*"))
                x = 50
            row.append(MultiContentEntryText(pos=(x,0), size=(390-x,48), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=name+suffix))
            rows.append(row)
        return rows

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


class ChannelMappingScreen(Screen):
    """Four-column mapping workflow inspired by Jedi EPG Xtream.

    Pane 0: receiver bouquets
    Pane 1: channels/services inside selected bouquet
    Pane 2: EPG Manager XMLTV source
    Pane 3: XMLTV channels from that source, best matches first
    """

    skin = """
    <screen name="ChannelMappingScreen" position="0,0" size="1920,1080" title="EPG Manager - Smart Mapping" backgroundColor="%(BG)s">
        <widget name="header_bg" position="0,0" size="1920,205" backgroundColor="%(PANEL)s" />
        <widget name="title" position="32,20" size="560,58" font="Regular;36" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="source_title" position="515,24" size="1335,44" font="Regular;31" foregroundColor="%(ACCENT_PRIMARY)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="subtitle" position="515,72" size="1335,50" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="summary" position="32,135" size="1815,40" font="Regular;22" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
        <widget name="header_rule" position="0,202" size="1920,2" backgroundColor="%(RULE)s" />

        <widget name="column_bar" position="0,205" size="1920,62" backgroundColor="%(PANEL_ALT)s" />
        <widget name="hdr_bouquet" position="45,216" size="420,42" font="Regular;27" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
        <widget name="hdr_channel" position="510,216" size="440,42" font="Regular;27" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
        <widget name="hdr_source" position="990,216" size="400,42" font="Regular;27" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
        <widget name="hdr_selection" position="1430,216" size="445,42" font="Regular;27" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" transparent="1" />
        <widget name="column_rule" position="0,265" size="1920,2" backgroundColor="%(ACCENT_PRIMARY)s" />

        <widget name="pane1_bg" position="28,300" size="445,650" backgroundColor="%(PANEL)s" />
        <widget name="pane2_bg" position="493,300" size="455,650" backgroundColor="%(PANEL)s" />
        <widget name="pane3_bg" position="968,300" size="420,650" backgroundColor="%(PANEL)s" />
        <widget name="pane4_bg" position="1408,300" size="484,650" backgroundColor="%(PANEL)s" />

        <widget name="bouquets" position="30,302" size="441,646" font="Regular;27" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
        <widget name="channels" position="495,302" size="451,646" font="Regular;25" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
        <widget name="sources" position="970,302" size="416,646" font="Regular;26" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />
        <widget name="selections" position="1410,302" size="480,646" font="Regular;25" itemHeight="48" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(PANEL_SELECTED)s" scrollbarMode="showOnDemand" />

        <widget name="focus1" position="28,292" size="445,6" backgroundColor="%(ACCENT_PRIMARY)s" />
        <widget name="focus2" position="493,292" size="455,6" backgroundColor="%(RULE_SOFT)s" />
        <widget name="focus3" position="968,292" size="420,6" backgroundColor="%(RULE_SOFT)s" />
        <widget name="focus4" position="1408,292" size="484,6" backgroundColor="%(RULE_SOFT)s" />

        <widget name="footer_bg" position="0,976" size="1920,104" backgroundColor="%(FOOTER_PANEL)s" />
        <widget name="footer_rule" position="0,976" size="1920,2" backgroundColor="%(RULE_SOFT)s" />
        <widget name="key_red_bar" position="30,994" size="9,60" backgroundColor="%(BTN_RED)s" />
        <widget name="key_red" position="52,994" size="330,60" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
        <widget name="key_green_bar" position="500,994" size="9,60" backgroundColor="%(BTN_GREEN)s" />
        <widget name="key_green" position="522,994" size="390,60" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
        <widget name="key_yellow_bar" position="980,994" size="9,60" backgroundColor="%(BTN_YELLOW)s" />
        <widget name="key_yellow" position="1002,994" size="390,60" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
        <widget name="key_blue_bar" position="1440,994" size="9,60" backgroundColor="%(BTN_BLUE)s" />
        <widget name="key_blue" position="1462,994" size="410,60" font="Regular;25" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
    </screen>
    """ % theme.__dict__

    def __init__(self, session, config=None, standalone=True, manager=None):
        Screen.__init__(self, session)
        self.session = session
        self.config = config
        self.standalone = standalone
        self.manager = manager
        self.store = MappingStore()
        self.focus = 0
        # Panes already entered keep their selected row highlighted.  This
        # mirrors Jedi's workflow: the bouquet remains visibly chosen while
        # the user moves on to Channel, EPG Source and EPG Selection.
        self.only_unmapped = False
        self.catalog = []
        self.epg_channels = []
        self.epg_by_source = {}
        self.selection_cache = {}
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

        for name in ("header_bg", "column_bar", "pane1_bg", "pane2_bg", "pane3_bg", "pane4_bg",
                     "header_rule", "column_rule", "footer_bg", "footer_rule",
                     "focus1", "focus2", "focus3", "focus4",
                     "key_red_bar", "key_green_bar", "key_yellow_bar", "key_blue_bar"):
            self[name] = Label("")

        self["title"] = Label("EPG MANAGER  SMART MAPPING")
        self["source_title"] = Label("Select a bouquet and channel")
        self["subtitle"] = Label("MANUAL MODE • GREEN Auto Map Channel • 6 Unmap Channel • MENU Search • 9 Preview • BLUE Undo")
        self["summary"] = Label("Opening Smart Mapping...")
        self["hdr_bouquet"] = Label("Bouquet")
        self["hdr_channel"] = Label("Channel")
        self["hdr_source"] = Label("EPG Source")
        self["hdr_selection"] = Label("EPG Selection")

        self["bouquets"] = JediPaneList([])
        self["channels"] = ChannelPaneList()
        self["sources"] = JediPaneList([])
        self["selections"] = JediPaneList([])

        self["key_red"] = Label("Exit")
        self["key_green"] = Label("Auto Map Channel")
        self["key_yellow"] = Label("Sync Source")
        self["key_blue"] = Label("Undo Last Mapping")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions", "ChannelSelectBaseActions", "NumberActions", "MenuActions"],
            {
                "cancel": self.close,
                "red": self.close,
                "green": self.auto_map_current_channel,
                "yellow": self.update_selected_source,
                "blue": self.undo_last_mapping,
                "ok": self.ok_pressed,
                "left": self.focus_left,
                "right": self.focus_right,
                "up": self.move_up,
                "down": self.move_down,
                "pageUp": self.page_up,
                "pageDown": self.page_down,
                "channelUp": self.page_up,
                "channelDown": self.page_down,
                "prevBouquet": self.page_up,
                "nextBouquet": self.page_down,
                "8": self.page_up,
                "2": self.page_down,
                "0": self.jump_top,
                "4": self.search_channel,
                "6": self.unmap_current_channel,
                "9": self.preview_three_programmes,
                "menu": self.search_channel,
            }, -1)

        self._timer = eTimer()
        if hasattr(self._timer, "callback"):
            self._timer.callback.append(self._timer_tick)
        else:
            self._timer.timeout.get().append(self._timer_tick)
        self.onLayoutFinish.append(self.update_focus)
        self._timer.start(80, True)

    def _timer_tick(self):
        if self._download_busy:
            self._poll_download()
        elif self._local_update_sid:
            self._poll_local_update()
        elif not self.catalog and not self.epg_channels:
            self.rescan()

    def _epg_dir(self):
        if self.config is not None and hasattr(self.config, "get_epg_output_dir"):
            try:
                return self.config.get_epg_output_dir()
            except Exception:
                pass
        return channel_mapper.EPG_DIR

    def _set_list(self, key, rows):
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

    def _rebuild_mapping_cache(self):
        self._mapped_epg_keys = set()
        self._mapped_refs = set()
        self._mapped_info_by_ref = {}
        try:
            all_maps = self.store.all()
            for key, saved in all_maps.items():
                if not saved or not saved.get("refs"):
                    continue
                self._mapped_epg_keys.add(key)
                refs = saved.get("refs") or []
                self._mapped_refs.update(refs)
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
                for ref in refs:
                    self._mapped_info_by_ref.setdefault(ref, []).append(info)
        except Exception:
            pass

    def _mapping_suffix(self, ref):
        infos = self._mapped_info_by_ref.get(ref) or []
        if not infos:
            return ""
        first = infos[0]
        text = "%s / %s" % (first.get("source_name") or "EPG", first.get("display_name") or first.get("channel_id") or "channel")
        if len(infos) > 1:
            text += " +%d" % (len(infos) - 1)
        return "  =>  " + text

    def _rebuild_epg_index(self):
        by_source = {}
        for item in self.epg_channels:
            by_source.setdefault(item.get("source_id"), []).append(item)
        self.epg_by_source = by_source
        self.selection_cache.clear()

    def _bouquet_children(self, parent_file):
        return [x for x in self.all_bouquet_records if x.get("parent_file") == parent_file]

    def _rebuild_bouquet_view(self, preserve_file=None):
        by_parent = {}
        for item in self.all_bouquet_records:
            by_parent.setdefault(item.get("parent_file"), []).append(item)
        visible = []
        def add_branch(parent_file=None):
            for item in by_parent.get(parent_file, []):
                visible.append(item)
                if item.get("bouquet_file") in self.expanded_bouquets:
                    add_branch(item.get("bouquet_file"))
        add_branch(None)
        seen = set(x.get("bouquet_file") for x in visible)
        for item in self.all_bouquet_records:
            if item.get("bouquet_file") not in seen and not item.get("parent_file"):
                visible.append(item)
        # Best UX: keep the four-pane Jedi layout and expose satellite services
        # as a virtual bouquet after the real bouquets. A fifth pane would make
        # every column too narrow on 1920x1080 and slow navigation.
        sat_count = len({x.get("ref") for x in self.catalog if (x.get("service_type") or channel_mapper.classify_service_ref(x.get("ref"))) == "SAT"})
        visible.append({
            "virtual": "satellite",
            "bouquet_file": "__EPGMANAGER_SATELLITE__",
            "bouquet_label": "SATELLITE CHANNELS (%d)" % sat_count,
            "parent_file": None,
            "depth": 0,
        })
        self.bouquet_records = visible
        rows = []
        target_idx = 0
        for idx, item in enumerate(visible):
            filename = item.get("bouquet_file")
            if item.get("virtual") == "satellite":
                rows.append("★ " + item.get("bouquet_label", "SATELLITE CHANNELS"))
            else:
                children = by_parent.get(filename, [])
                prefix = ""
                if children:
                    prefix = "▼ " if filename in self.expanded_bouquets else "▶ "
                indent = "  " * int(item.get("depth") or 0)
                rows.append(indent + prefix + (item.get("bouquet_label") or filename or "Other"))
            if preserve_file and filename == preserve_file:
                target_idx = idx
        self._set_list("bouquets", rows or ["No bouquets found"])
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

    def _source_group_key(self, item):
        return item.get("region") or "Other"

    def _rebuild_source_view(self, preserve_id=None):
        # Built-in sources stay immediately visible. External providers are
        # grouped into collapsible folders so a catalogue with 100+ feeds does
        # not make navigation sluggish.
        builtins = [x for x in self._all_source_groups if not x.get("external")]
        externals = [x for x in self._all_source_groups if x.get("external")]
        regions = []
        region_map = {}
        for item in externals:
            region = self._source_group_key(item)
            if region not in region_map:
                region_map[region] = []
                regions.append(region)
            region_map[region].append(item)
        visible = list(builtins)
        rows = []
        for item in builtins:
            has_channels = bool(self.epg_by_source.get(item.get("source_id")))
            rows.append(item["source_name"])
        for region in regions:
            children = region_map[region]
            expanded = region in self.expanded_source_groups
            visible.append({"source_group": True, "region": region, "source_name": region})
            rows.append(("▼ " if expanded else "▶ ") + "%s (%d)" % (region, len(children)))
            if expanded:
                for item in children:
                    visible.append(item)
                    status = external_sources.source_status(item, self._epg_dir())
                    rows.append("  %s  [%s]" % (item["source_name"], status))
        self.source_groups = visible
        self._set_list("sources", rows or ["No EPG sources"])
        if preserve_id:
            for i, item in enumerate(visible):
                if item.get("source_id") == preserve_id:
                    self._set_index("sources", i)
                    break

    def _toggle_current_source_group(self):
        item = self._current("sources", self.source_groups)
        if not item or not item.get("source_group"):
            return False
        region = item.get("region")
        if region in self.expanded_source_groups:
            self.expanded_source_groups.remove(region)
        else:
            self.expanded_source_groups.add(region)
        self._rebuild_source_view()
        self.refresh_selection()
        return True

    def rescan(self):
        """Fast manual-mode startup.

        Only bouquet metadata/services are loaded here. XMLTV files are parsed
        lazily when the user actually enters EPG Selection for one source.
        This avoids parsing every cached OpenEPG/EPGShare/Rytec feed just to
        open Smart Mapping. No source update and no native import is triggered.
        """
        self["summary"].setText("Fast loading bouquets from cache...")
        try:
            self.catalog = channel_mapper.scan_bouquets(use_cache=True, fast_cache=True)
        except Exception:
            log.exception("Smart mapping bouquet scan failed")
            self.catalog = []
            self.session.open(MessageBox, "Smart Mapping bouquet scan failed. See EPG Manager logs.", MessageBox.TYPE_ERROR)

        # Deliberately do NOT call list_epg_channels() here: some cached feeds
        # contain thousands of channels and made screen startup unnecessarily slow.
        self.epg_channels = []
        self.epg_by_source = {}
        self.selection_cache.clear()
        try:
            self.all_bouquet_records = channel_mapper.list_bouquets(use_cache=True, fast_cache=True)
        except Exception:
            self.all_bouquet_records = []
        self.bouquet_labels = self.all_bouquet_records
        self._rebuild_mapping_cache()
        self._rebuild_bouquet_view()
        self.refresh_channels()
        self.refresh_sources()
        # v5.5 Fast Start: never refresh remote feeds at screen startup.
        # Network refresh is lazy per selected source so opening Smart Mapping
        # remains instant even with many cached OpenEPG/Rytec/EPGShare feeds.
        self.selection_entries = []
        self.selection_rows = ["Select a source, then RIGHT to load its EPG channels"]
        self._set_list("selections", self.selection_rows)
        self.update_focus()
        self._update_summary()

    def refresh_channels(self):
        bouquet = self._current("bouquets", self.bouquet_records)
        if bouquet is None:
            self.bouquet_services = []
        elif bouquet.get("virtual") == "satellite":
            # Aggregate all SAT services while removing duplicates that appear
            # in several bouquets. Keep the first human-readable occurrence.
            seen = set()
            sat = []
            for item in self.catalog:
                kind = item.get("service_type") or channel_mapper.classify_service_ref(item.get("ref"))
                ref = item.get("ref")
                if kind != "SAT" or not ref or ref in seen:
                    continue
                seen.add(ref)
                sat.append(item)
            self.bouquet_services = sat
        else:
            bfile = bouquet.get("bouquet_file")
            self.bouquet_services = [x for x in self.catalog if x.get("bouquet_file") == bfile]
        rows = self["channels"].build_rows(self.bouquet_services, self._mapped_refs, self._mapping_suffix)
        self._set_list("channels", rows or [[None, MultiContentEntryText(pos=(12,0), size=(390,48), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="No services")]])

    def refresh_sources(self):
        current = self._current("sources", self.source_groups)
        preserve_id = current.get("source_id") if current else None
        groups = []
        seen = set()

        # Direct/online XMLTV only. Legacy receiver-side generators are no
        # longer exposed because production feeds are built on GitHub.
        for ch in self.epg_channels:
            sid = ch.get("source_id")
            if not sid or sid in seen or sid not in external_sources.BY_ID:
                continue
            item = dict(external_sources.BY_ID[sid])
            item.update({"source_id": sid, "source_name": item["name"], "external": True})
            groups.append(item)
            seen.add(sid)

        for src in external_sources.SOURCES:
            if src["id"] in seen:
                continue
            seen.add(src["id"])
            item = dict(src)
            item.update({"source_id": src["id"], "source_name": src["name"], "external": True})
            groups.append(item)

        groups.sort(key=lambda x: (
            -int(x.get("tier") == "direct"),
            -int(x.get("priority", 0) or 0),
            (x.get("source_name") or "").casefold(),
        ))
        self._all_source_groups = groups
        self._rebuild_source_view(preserve_id)

    def _start_online_cache_refresh(self):
        """Refresh only online sources already used/cached, in background.

        Downloading every remote feed at startup would make Smart Mapping very
        slow. Cached feeds are the sources the user has actually used, so they
        are refreshed automatically while the UI remains responsive. Remote
        uncached feeds are downloaded automatically when opened.
        """
        if self._startup_refresh_started:
            return
        self._startup_refresh_started = True
        epg_dir = self._epg_dir()
        targets = []
        try:
            for src in external_sources.SOURCES:
                if os.path.exists(external_sources.local_xml_path(src, epg_dir)):
                    targets.append(dict(src))
        except Exception:
            targets = []
        if not targets:
            return
        self._startup_refresh_busy = True

        def worker():
            count = 0
            for src in targets:
                try:
                    external_sources.download_source(src, epg_dir)
                    count += 1
                except Exception:
                    log.warning("Startup online refresh failed for %s", src.get("name"), exc_info=True)
            self._startup_refresh_count = count
            self._startup_refresh_busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _ensure_external_source_ready(self, src):
        """Auto-download an uncached online source when the user opens it."""
        if not src or not src.get("external") or src.get("source_group"):
            return True
        path = external_sources.local_xml_path(src, self._epg_dir())
        if os.path.exists(path):
            # Never block navigation for an already cached feed. If it is stale,
            # refresh only this selected source in the background while the
            # existing XML remains immediately usable.
            try:
                if external_sources.source_status(src, self._epg_dir()) == "STALE":
                    token = src.get("source_id") or src.get("id")
                    refreshed = getattr(self, "_lazy_refresh_started", set())
                    if token not in refreshed:
                        refreshed.add(token)
                        self._lazy_refresh_started = refreshed
                        def stale_worker():
                            try:
                                external_sources.download_source(src, self._epg_dir())
                            except Exception:
                                log.warning("Lazy stale-source refresh failed for %s", src.get("source_name"), exc_info=True)
                        threading.Thread(target=stale_worker, daemon=True).start()
            except Exception:
                pass
            return True
        if self._download_busy:
            return False
        self._download_busy = True
        self._download_result = None
        self._download_error = None
        self["summary"].setText("Downloading %s automatically..." % src.get("source_name", "online source"))
        self["key_yellow"].setText("Downloading...")

        def worker():
            try:
                self._download_result = external_sources.download_source(src, self._epg_dir())
            except Exception as exc:
                log.exception("Automatic external EPG source download failed")
                self._download_error = str(exc)
        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(250, False)
        return False

    def _source_xml_path(self, src):
        if not src or src.get("source_group"):
            return None
        if src.get("external"):
            try:
                return external_sources.local_xml_path(src, self._epg_dir())
            except Exception:
                return None
        filename = src.get("local_file")
        if not filename:
            sid = src.get("source_id")
            for fn, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                if pair[0] == sid:
                    filename = fn
                    break
        return os.path.join(self._epg_dir(), filename) if filename else None

    def _ensure_source_loaded(self, src):
        """Parse only the selected source, once, on demand."""
        if not src or src.get("source_group"):
            return 0
        sid = src.get("source_id")
        if sid in self.epg_by_source:
            return len(self.epg_by_source.get(sid) or [])
        path = self._source_xml_path(src)
        fresh = []
        if path and os.path.exists(path):
            try:
                fresh = channel_mapper._read_xmltv_channels(path, sid, src.get("source_name") or sid)
            except Exception:
                log.exception("Could not lazily parse XMLTV source %s", sid)
        self.epg_by_source[sid] = fresh
        if fresh:
            self.epg_channels.extend(fresh)
        self.selection_cache.clear()
        return len(fresh)

    def _is_mapped(self, epg):
        try:
            key = self.store._key(epg.get("source_id"), epg.get("channel_id"))
            return key in self._mapped_epg_keys
        except Exception:
            return False

    def refresh_selection(self):
        src = self._current("sources", self.source_groups)
        service = self._current("channels", self.bouquet_services)
        if not src:
            self.selection_entries = []
            self.selection_rows = ["No EPG source selected"]
            self._set_list("selections", self.selection_rows)
            return
        if src.get("source_group"):
            self.selection_entries = []
            self.selection_rows = ["Press OK to open %s sources" % src.get("region", "group")]
            self._set_list("selections", self.selection_rows)
            self.update_source_title()
            return
        self._ensure_source_loaded(src)
        entries = list(self.epg_by_source.get(src.get("source_id"), []))
        if src.get("external") and not entries:
            self.selection_entries = []
            self.selection_rows = ["Press YELLOW to download/update", src.get("region", "External XMLTV source")]
            self._set_list("selections", self.selection_rows)
            self.update_source_title()
            return
        if self.only_unmapped:
            entries = [x for x in entries if not self._is_mapped(x)]

        cache_key = (
            service.get("ref") if service else "",
            src.get("source_id"),
            bool(self.only_unmapped),
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

        scored = []
        service_name = service.get("name", "") if service else ""
        for epg in entries:
            score = channel_mapper._token_score(service_name, epg.get("display_name", ""))
            scored.append((score, epg))
        scored.sort(key=lambda pair: (-pair[0], (pair[1].get("display_name") or "").lower()))

        close = [(s, e) for s, e in scored if s >= 82]
        ordered = []
        rows = []
        if close:
            rows.append("=== CLOSE MATCHES ===")
            ordered.append({"separator": True})
            for score, epg in close:
                mapped = " [M]" if self._is_mapped(epg) else ""
                rows.append("%d%%  %s%s" % (score, epg.get("display_name", epg.get("channel_id", "")), mapped))
                ordered.append(epg)
        else:
            rows.append("No close matches")
            ordered.append({"separator": True})

        rows.append("=========== ALL ===========")
        ordered.append({"separator": True})
        close_ids = set(id(e) for _, e in close)
        for score, epg in scored:
            if id(epg) in close_ids:
                continue
            mapped = " [M]" if self._is_mapped(epg) else ""
            rows.append("%s%s" % (epg.get("display_name", epg.get("channel_id", "")), mapped))
            ordered.append(epg)
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
        mapped_suffix = ""
        if service:
            infos = self._mapped_info_by_ref.get(service.get("ref")) or []
            if infos:
                first = infos[0]
                mapped_suffix = "   |   MAPPED: %s / %s" % (first.get("source_name") or "EPG", first.get("display_name") or first.get("channel_id") or "channel")
                if len(infos) > 1:
                    mapped_suffix += " (+%d)" % (len(infos) - 1)
        if src:
            title = src.get("source_name", "EPG Source")
            if src.get("source_group"):
                title = "%s sources" % src.get("region", title)
            elif src.get("external"):
                title += "  [%s]" % external_sources.source_status(src, self._epg_dir())
            self["source_title"].setText(title + mapped_suffix)
        elif service:
            self["source_title"].setText(service.get("name", "Channel") + mapped_suffix)
        else:
            self["source_title"].setText("Smart Mapping")

    def _update_summary(self):
        total = len(self.epg_channels)
        mapped = 0
        for epg in self.epg_channels:
            if self._is_mapped(epg):
                mapped += 1
        try:
            manual_count = len(self.store.all())
        except Exception:
            manual_count = mapped
        self["summary"].setText("%d services cached  •  FAST MANUAL MODE  •  %d EPG channels loaded  •  %d mappings" %
                                (len(self.catalog), total, manual_count))

    def update_focus(self):
        for i in range(4):
            key = "focus%d" % (i + 1)
            try:
                self[key].instance.setBackgroundColor(theme.ACCENT_PRIMARY_INT if i == self.focus else theme.RULE_SOFT_INT)
            except Exception:
                # Some images do not expose runtime backgroundColor; labels still work.
                pass
        names = ["Bouquet", "Channel", "EPG Source", "EPG Selection"]
        hdrs = ["hdr_bouquet", "hdr_channel", "hdr_source", "hdr_selection"]
        list_keys = ["bouquets", "channels", "sources", "selections"]
        for i, key in enumerate(hdrs):
            self[key].setText(("> " if i == self.focus else "") + names[i])
        # Breadcrumb selection: when moving forward, previously chosen rows
        # remain highlighted. Moving LEFT automatically clears columns to the
        # right because only panes up to the current focus stay enabled.
        for i, key in enumerate(list_keys):
            try:
                self[key].instance.setSelectionEnable(i <= self.focus)
            except Exception:
                pass
        self.update_source_title()

    def focus_left(self):
        self.focus = max(0, self.focus - 1)
        self.update_focus()

    def focus_right(self):
        # A collapsed source group must be opened before EPG Selection can be
        # entered; this prevents an empty fourth pane and keeps navigation fast.
        if self.focus == 2:
            src = self._current("sources", self.source_groups)
            if src and src.get("source_group"):
                self._toggle_current_source_group()
                return
        new_focus = min(3, self.focus + 1)
        if self.focus == 1 and new_focus == 2:
            self._jump_to_best_source_for_current_channel()
        if self.focus == 2 and new_focus == 3:
            src = self._current("sources", self.source_groups)
            if src and src.get("external") and not self._ensure_external_source_ready(src):
                return
            self._ensure_source_loaded(src)
            self.refresh_selection()
        self.focus = new_focus
        self.update_focus()

    def move_up(self):
        keys = ["bouquets", "channels", "sources", "selections"]
        self[keys[self.focus]].move_up()
        self._after_move()

    def move_down(self):
        keys = ["bouquets", "channels", "sources", "selections"]
        self[keys[self.focus]].move_down()
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
            # Conservative fallback for images exposing neither API.
            for _ in range(10):
                widget.move_down() if down else widget.move_up()
        self._after_move()

    def page_up(self):
        self._page_move(False)

    def page_down(self):
        self._page_move(True)

    def jump_top(self):
        keys = ["bouquets", "channels", "sources", "selections"]
        self._set_index(keys[self.focus], 0)
        self._after_move()

    def _after_move(self):
        if self.focus == 0:
            self.refresh_channels()
        elif self.focus == 1:
            # Channel movement must stay instant even with huge XMLTV feeds.
            # Fuzzy matching is deferred until EPG Selection is entered.
            self.update_source_title()
        elif self.focus == 2:
            # Keep source browsing lightweight. A source is parsed only after
            # OK/RIGHT (or when the suggested source was preloaded once).
            src = self._current("sources", self.source_groups)
            if src and src.get("source_group"):
                self.selection_entries = []
                self.selection_rows = ["Press OK to open %s sources" % src.get("region", "group")]
                self._set_list("selections", self.selection_rows)
            elif src and src.get("source_id") in self.epg_by_source:
                self.refresh_selection()
            elif src and src.get("external"):
                self.selection_entries = []
                self.selection_rows = ["OK/RIGHT to load • YELLOW to update"]
                self._set_list("selections", self.selection_rows)
            else:
                self.selection_entries = []
                self.selection_rows = ["OK/RIGHT to load EPG Selection"]
                self._set_list("selections", self.selection_rows)
            self.update_source_title()
        else:
            self.update_source_title()

    def ok_pressed(self):
        if self.focus == 0 and self._toggle_current_bouquet():
            return
        if self.focus == 2 and self._toggle_current_source_group():
            return
        if self.focus < 3:
            self.focus_right()
            return
        self.assign_current()

    def assign_current(self):
        service = self._current("channels", self.bouquet_services)
        epg = self._current("selections", self.selection_entries)
        if not service:
            self.session.open(MessageBox, "Select a bouquet channel first.", MessageBox.TYPE_INFO)
            return
        if not epg or epg.get("separator"):
            return
        try:
            refs = [service.get("ref")]
            self.store.set(epg.get("source_id"), epg.get("channel_id"), [service.get("ref")],
                           mode="manual", display_name=epg.get("display_name"))
            # Update the in-memory indexes immediately instead of rebuilding
            # every pane twice.  This makes repeated manual assignments feel
            # instant even with very large IPTV bouquets/source catalogues.
            map_key = self.store._key(epg.get("source_id"), epg.get("channel_id"))
            self._mapped_epg_keys.add(map_key)
            self._mapped_refs.update(refs)
            info = {"source_id": epg.get("source_id"),
                    "source_name": self._source_label(epg.get("source_id")),
                    "channel_id": epg.get("channel_id"),
                    "display_name": epg.get("display_name") or epg.get("channel_id"),
                    "mode": "manual"}
            for ref in refs:
                lst = [x for x in self._mapped_info_by_ref.get(ref, [])
                       if not (x.get("source_id") == info["source_id"] and x.get("channel_id") == info["channel_id"])]
                lst.append(info)
                self._mapped_info_by_ref[ref] = lst
            self.mapping_revision += 1
            self["summary"].setText("Mapped: %s  →  %s" %
                                    (service.get("name", "Channel"), epg.get("display_name", "EPG")))
            # Update only the currently affected rows. Avoid rescoring/sorting
            # the complete XMLTV source after every OK press.
            channel_idx = self._get_index("channels")
            rows = self["channels"].build_rows(self.bouquet_services, self._mapped_refs, self._mapping_suffix)
            self._set_list("channels", rows)
            self._set_index("channels", channel_idx)
            if self.only_unmapped:
                self.selection_cache.clear()
                self.refresh_selection()
            else:
                idx = self._get_index("selections")
                if 0 <= idx < len(self.selection_rows):
                    text = self.selection_rows[idx]
                    if "[M]" not in text and not epg.get("separator"):
                        self.selection_rows[idx] = text + " [M]"
                        self._set_list("selections", self.selection_rows)
                        self._set_index("selections", idx)
            self._advance_to_next_channel()
        except Exception as exc:
            log.exception("Could not save mapping")
            self.session.open(MessageBox, "Could not save mapping.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _advance_to_next_channel(self):
        """After a successful mapping, continue with the next channel."""
        if not self.bouquet_services:
            return
        current = self._get_index("channels")
        nxt = current + 1
        if nxt >= len(self.bouquet_services):
            nxt = current
            try:
                current_text = self["summary"].getText()
            except Exception:
                current_text = "Mapping saved"
            self["summary"].setText(current_text + "  •  End of bouquet")
        self.focus = 1
        self._set_index("channels", nxt)
        self.selection_entries = []
        self.selection_rows = ["Choose EPG Source for this channel"]
        self._set_list("selections", self.selection_rows)
        self.update_focus()
        self.update_source_title()

    def auto_map_current_channel(self):
        """Safely auto-map only the highlighted channel.

        It considers local/generated and already cached online feeds only, so
        pressing GREEN stays fast. If no confident unique result exists, the
        workflow jumps to the most likely source for manual review instead of
        saving a risky match.
        """
        service = self._current("channels", self.bouquet_services)
        if not service:
            self.session.open(MessageBox, "Select a channel first.", MessageBox.TYPE_INFO)
            return
        if service.get("ref") in self._mapped_refs:
            self["summary"].setText("Already mapped: %s" % service.get("name", "channel"))
            self._advance_to_next_channel()
            return
        channel_name = service.get("name", "")
        candidates = []
        for src in self._all_source_groups:
            if not src or src.get("source_group"):
                continue
            path = self._source_xml_path(src)
            if not path or not os.path.exists(path):
                continue
            candidates.append((self._source_score_for_channel(channel_name, src), src))
        candidates.sort(key=lambda pair: -pair[0])
        best = None
        second_score = -1
        for source_score, src in candidates[:8]:
            self._ensure_source_loaded(src)
            for epg in self.epg_by_source.get(src.get("source_id"), []):
                name_score = channel_mapper._token_score(channel_name, epg.get("display_name", ""))
                score = int(round(name_score * 0.88 + min(100, source_score) * 0.12))
                if best is None or score > best[0]:
                    if best is not None:
                        second_score = max(second_score, best[0])
                    best = (score, src, epg)
                else:
                    second_score = max(second_score, score)
        if best and best[0] >= 88 and (best[0] - second_score >= 2 or best[0] >= 97):
            score, src, epg = best
            try:
                self.store.set(epg.get("source_id"), epg.get("channel_id"), [service.get("ref")],
                               mode="auto-channel", display_name=epg.get("display_name"))
                self._rebuild_mapping_cache()
                self.mapping_revision += 1
                self.selection_cache.clear()
                idx = self._get_index("channels")
                rows = self["channels"].build_rows(self.bouquet_services, self._mapped_refs, self._mapping_suffix)
                self._set_list("channels", rows)
                self._set_index("channels", idx)
                self["summary"].setText("Auto mapped %s  →  %s / %s  (%d%%)" %
                                        (channel_name, src.get("source_name", "EPG"),
                                         epg.get("display_name", "channel"), score))
                self._advance_to_next_channel()
                return
            except Exception as exc:
                log.exception("Auto Map Channel failed")
                self.session.open(MessageBox, "Auto Map Channel failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)
                return
        self.focus = 2
        self._jump_to_best_source_for_current_channel()
        src = self._current("sources", self.source_groups)
        if src and src.get("external"):
            self._ensure_external_source_ready(src)
        elif src:
            self._ensure_source_loaded(src)
            self.refresh_selection()
        self.update_focus()
        self["summary"].setText("No safe auto-match for %s — review suggested source." % channel_name)

    def _reload_one_source_channels(self, src, path=None):
        """Refresh one XMLTV source in memory without reparsing every cached feed."""
        sid = src.get("source_id")
        name = src.get("source_name") or sid
        if path is None:
            if src.get("external"):
                path = external_sources.local_xml_path(src, self._epg_dir())
            else:
                for filename, pair in channel_mapper.SOURCE_OUTPUT_FILES.items():
                    if pair[0] == sid:
                        path = os.path.join(self._epg_dir(), filename)
                        break
        fresh = []
        try:
            if path and os.path.exists(path):
                fresh = channel_mapper._read_xmltv_channels(path, sid, name)
        except Exception:
            log.exception("Could not parse updated XMLTV source %s", sid)
        self.epg_channels = [x for x in self.epg_channels if x.get("source_id") != sid] + fresh
        self._rebuild_epg_index()
        return len(fresh)

    def update_selected_source(self):
        src = self._current("sources", self.source_groups)
        if not src:
            return
        if src.get("source_group"):
            self._toggle_current_source_group()
            return
        if not src.get("external"):
            self.session.open(
                MessageBox,
                "Legacy local source refresh is disabled.\nUse a Direct/online XMLTV source instead.",
                MessageBox.TYPE_INFO,
            )
            return
        if self._download_busy:
            return
        self._download_busy = True
        self._download_result = None
        self._download_error = None
        self["summary"].setText("Syncing %s..." % src.get("source_name", "EPG source"))
        self["key_yellow"].setText("Syncing...")

        def worker():
            try:
                self._download_result = external_sources.download_source(src, self._epg_dir())
            except Exception as exc:
                log.exception("External EPG source sync failed")
                self._download_error = str(exc)
        threading.Thread(target=worker, daemon=True).start()
        self._timer.start(250, False)

    def _poll_download(self):
        if not self._download_busy:
            return
        if self._download_result is None and self._download_error is None:
            return
        self._download_busy = False
        try:
            self._timer.stop()
        except Exception:
            pass
        self["key_yellow"].setText("Sync Source")
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
        self["key_yellow"].setText("Sync Source")
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

    def unmap_current_channel(self):
        """Remove mapping(s) for the currently highlighted receiver service."""
        service = self._current("channels", self.bouquet_services)
        if not service:
            return
        ref = service.get("ref")
        removed = 0
        try:
            # MappingStore stores EPG-key -> refs. Remove this ref from every mapping.
            all_maps = self.store.all()
            for key, saved in list(all_maps.items()):
                refs = list((saved or {}).get("refs") or [])
                if ref not in refs:
                    continue
                refs = [x for x in refs if x != ref]
                if refs:
                    # Preserve source/channel identity from the key where possible.
                    source_id, channel_id = key.split("::", 1) if "::" in key else (None, None)
                    if source_id and channel_id:
                        self.store.set(source_id, channel_id, refs, mode=(saved or {}).get("mode", "manual"),
                                       display_name=(saved or {}).get("display_name"))
                else:
                    if hasattr(self.store, "delete_key"):
                        self.store.delete_key(key)
                    elif hasattr(self.store, "remove"):
                        try:
                            source_id, channel_id = key.split("::", 1)
                            self.store.remove(source_id, channel_id)
                        except Exception:
                            pass
                removed += 1
            self._rebuild_mapping_cache()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self.refresh_channels()
            self["summary"].setText("Unmapped %s (%d mapping record%s changed)" %
                                    (service.get("name", "channel"), removed, "" if removed == 1 else "s"))
        except Exception as exc:
            log.exception("Could not unmap service")
            self.session.open(MessageBox, "Could not unmap channel.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _source_score_for_channel(self, channel_name, src):
        name = channel_mapper.normalize_name(channel_name)
        sid = src.get("source_id", "")
        sname = channel_mapper.normalize_name(src.get("source_name", ""))
        aliases = {
            "arryadia": ["arryadia", "riyadia"],
            "chada_2m": ["2m", "chada"],
            "medi1tv": ["medi1", "medi 1"],
            "bein_sports": ["bein", "be in"],
            "almajd": ["almajd", "al majd"],
            "snrt": ["al aoula", "aloula", "arrabiaa", "maghribia", "assadissa", "tamazight", "aflam", "snrt"],
        }
        best = channel_mapper._token_score(name, sname)
        for alias in aliases.get(sid, []):
            a = channel_mapper.normalize_name(alias)
            if a and (a in name or name in a):
                best = max(best, 99)
            else:
                best = max(best, channel_mapper._token_score(name, a))
        return best

    def _jump_to_best_source_for_current_channel(self):
        service = self._current("channels", self.bouquet_services)
        if not service or not self.source_groups:
            return
        # Existing manual mapping always wins.
        infos = self._mapped_info_by_ref.get(service.get("ref")) or []
        wanted = infos[0].get("source_id") if infos else None
        best_idx, best_score = None, -1
        for i, src in enumerate(self.source_groups):
            if src.get("source_group"):
                continue
            if wanted and src.get("source_id") == wanted:
                best_idx, best_score = i, 1000
                break
            score = self._source_score_for_channel(service.get("name", ""), src)
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx is not None and (wanted or best_score >= 55):
            self._set_index("sources", best_idx)
            self.update_source_title()
            # Immediately prepare the right pane if the source is already local.
            src = self._current("sources", self.source_groups)
            if src and not src.get("source_group"):
                self.refresh_selection()

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

    def auto_map_source(self):
        src = self._current("sources", self.source_groups)
        if not src:
            return
        if src.get("source_group"):
            self.session.open(MessageBox, "Open the source group and choose one EPG source first.", MessageBox.TYPE_INFO)
            return
        try:
            target = [x for x in self.epg_channels if x.get("source_id") == src.get("source_id")]
            threshold = self.config.get_safe_auto_map_threshold() if self.config and hasattr(self.config, "get_safe_auto_map_threshold") else 95
            results = channel_mapper.smart_match_channels(target, self.catalog, min_score=threshold, store=None)
            saved = 0
            for entry in results:
                matches = entry.get("matches") or []
                if not matches:
                    continue
                # Safe auto-map: one best match only; ambiguous equal-score matches stay manual.
                if len(matches) != 1:
                    continue
                match = matches[0]
                self.store.set(entry.get("source_id"), entry.get("channel_id"), [match.get("ref")],
                               mode="auto", display_name=entry.get("display_name"))
                saved += 1
            self._rebuild_mapping_cache()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self.refresh_channels()
            self._update_summary()
            self.session.open(MessageBox, "Auto Map completed: %d mapping(s) saved for %s." %
                              (saved, src.get("source_name", "source")), MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception("Auto map failed")
            self.session.open(MessageBox, "Auto Map failed.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def auto_map_all(self):
        """Safe global auto-map: exact/high confidence, one unambiguous best match."""
        try:
            threshold = self.config.get_safe_auto_map_threshold() if self.config and hasattr(self.config, 'get_safe_auto_map_threshold') else 95
            results = channel_mapper.smart_match_channels(self.epg_channels, self.catalog, min_score=threshold, store=None)
            saved = 0
            skipped = 0
            self.store.backup()
            for entry in results:
                matches = entry.get('matches') or []
                if len(matches) != 1 or int(entry.get('confidence') or 0) < threshold:
                    skipped += 1
                    continue
                m = matches[0]
                self.store.set(entry.get('source_id'), entry.get('channel_id'), [m.get('ref')],
                               mode='auto-safe', display_name=entry.get('display_name'))
                saved += 1
            self._rebuild_mapping_cache()
            self.mapping_revision += 1
            self.selection_cache.clear()
            self.refresh_channels()
            self._update_summary()
            self.session.open(MessageBox, 'Auto Map All: %d saved, %d left for review.\n\nBackup: epgmanager_mappings.json.bak' % (saved, skipped), MessageBox.TYPE_INFO)
        except Exception as exc:
            log.exception('Auto Map All failed')
            self.session.open(MessageBox, 'Auto Map All failed.\n\n%s' % exc, MessageBox.TYPE_ERROR)

    def repair_unmapped(self):
        """Focus the workflow on services that still have no mapping."""
        self.only_unmapped = True
        self["key_blue"].setText("Show All")
        unmapped = [x for x in self.catalog if x.get('ref') not in self._mapped_refs]
        cov = channel_mapper.mapping_coverage(self.catalog, self._mapped_refs)
        self["summary"].setText('Repair mode: %d unmapped service(s) • coverage %d%%' % (len(unmapped), cov.get('percent', 0)))
        if self.focus == 3:
            self.refresh_selection()

    def toggle_unmapped(self):
        self.only_unmapped = not self.only_unmapped
        self["key_blue"].setText("Show All" if self.only_unmapped else "Hide Mapped")
        self.refresh_selection()

    def apply(self):
        """Compatibility helper kept for callers/tests from older versions."""
        try:
            results = channel_mapper.smart_match_channels(self.epg_channels, self.catalog, min_score=82, store=self.store)
            return save_sourcexml(results)
        except Exception:
            log.exception("Failed to save compatibility XML")
            return None
