# -*- coding: utf-8 -*-
"""Satellite-by-satellite Mapping Repair launcher.

This screen deliberately stays cache-only.  It never downloads XMLTV data and
never scans programme bodies.  It groups the resident SAT registry by reception
list, overlays the already-validated Mapping View cache and opens Smart Mapping
on the selected satellite with the UNMAPPED filter already active.
"""
from __future__ import print_function

from enigma import eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, eTimer
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from .compat import MultiContentEntryText

from . import theme
from ..core import channel_mapper, channel_registry, smart_mapping_warm_cache, mapping_view_cache
from ..core.mapping_store import MappingStore
from .compat import adapt_skin, scale_y, ui_gFont

ROW_H = 68


class RepairSatelliteList(MenuList):
    def __init__(self):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 25))
        self.l.setFont(1, ui_gFont("Regular", 21))
        self.l.setItemHeight(scale_y(ROW_H))

    def set_rows(self, groups):
        rows = []
        for i, row in enumerate(groups or []):
            bg = theme.PANEL_ROW_HEX_INT if i % 2 == 0 else theme.PANEL_ROW_ALT_HEX_INT
            missing = row.get("missing")
            mapped = row.get("mapped")
            total = int(row.get("total") or 0)
            ignored = int(row.get("ignored") or 0)
            coverage = row.get("coverage")
            if missing is None:
                state_text = "ON DEMAND"
                state_color = theme.STATUS_YELLOW_INT
            elif int(missing or 0) > 0:
                state_text = "%d MISSING" % int(missing or 0)
                state_color = theme.STATUS_ORANGE_INT
            else:
                state_text = "COMPLETE"
                state_color = theme.STATUS_GREEN_INT
            if ignored:
                state_text += " • %d IGN" % ignored
            cov_text = ("%d%%" % int(coverage)) if coverage is not None else "—"
            mapped_text = str(int(mapped or 0)) if mapped is not None else "—"
            rows.append([
                row,
                MultiContentEntryText(pos=(14, 2), size=(350, ROW_H-4), font=0,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=str(row.get("label") or "Satellite"), color=theme.WHITE_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(370, 2), size=(430, ROW_H-4), font=1,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=str(row.get("name") or "Satellite"), color=theme.TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(815, 2), size=(120, ROW_H-4), font=1,
                    flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=str(total), color=theme.TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(950, 2), size=(125, ROW_H-4), font=1,
                    flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=mapped_text, color=theme.TEXT_INT,
                    color_sel=theme.WHITE_INT, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1090, 2), size=(150, ROW_H-4), font=1,
                    flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=cov_text, color=state_color,
                    color_sel=state_color, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
                MultiContentEntryText(pos=(1260, 2), size=(235, ROW_H-4), font=1,
                    flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=state_text, color=state_color,
                    color_sel=state_color, backcolor=bg, backcolor_sel=theme.PANEL_SELECTED_HEX_INT),
            ])
        self.setList(rows)

    def current_row(self):
        cur = self.getCurrent()
        return cur[0] if cur else None


class MappingRepairScreen(Screen):
    skin = ("""
    <screen name="MappingRepairScreen" position="0,0" size="1920,1080" title="EPG Manager - Mapping Repair" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="header_bg" position="0,0" size="1920,116" backgroundColor="%(PANEL)s" />
      <widget name="title" position="50,24" size="900,50" font="Regular;38" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="subtitle" position="52,76" size="1500,28" font="Regular;20" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="status" position="1510,32" size="350,48" font="Regular;22" halign="right" foregroundColor="%(ACCENT_SECONDARY)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="rule" position="40,113" size="1840,3" backgroundColor="%(ACCENT_PRIMARY)s" />

      <widget name="left_bg" position="40,138" size="1510,820" backgroundColor="%(PANEL)s" />
      <widget name="h_sat" position="66,160" size="330,30" font="Regular;18" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_name" position="420,160" size="390,30" font="Regular;18" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_total" position="830,160" size="120,30" font="Regular;18" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_mapped" position="965,160" size="125,30" font="Regular;18" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_health" position="1110,160" size="150,30" font="Regular;18" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="h_state" position="1280,160" size="240,30" font="Regular;18" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="list" position="58,200" size="1470,710" backgroundColor="%(PANEL)s" />

      <widget name="right_bg" position="1570,138" size="310,820" backgroundColor="%(PANEL_BLUE)s" />
      <widget name="detail_title" position="1592,175" size="266,42" font="Regular;28" halign="center" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="detail" position="1594,242" size="262,500" font="Regular;20" halign="center" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />
      <widget name="hint" position="1592,770" size="266,150" font="Regular;18" halign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL_BLUE)s" transparent="1" />

      <widget name="footer_bg" position="0,982" size="1920,98" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="footer_rule" position="30,982" size="1860,2" backgroundColor="%(RULE_SOFT)s" />
      <widget name="key_red_bar" position="30,1002" size="9,56" backgroundColor="%(BTN_RED)s" />
      <widget name="key_red" position="52,1002" size="330,56" font="Regular;24" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
      <widget name="key_green_bar" position="470,1002" size="9,56" backgroundColor="%(BTN_GREEN)s" />
      <widget name="key_green" position="492,1002" size="500,56" font="Regular;24" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
      <widget name="key_yellow_bar" position="1020,1002" size="9,56" backgroundColor="%(BTN_YELLOW)s" />
      <widget name="key_yellow" position="1042,1002" size="235,56" font="Regular;22" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
      <widget name="key_blue_bar" position="1300,1002" size="9,56" backgroundColor="%(BTN_BLUE)s" />
      <widget name="key_blue" position="1322,1002" size="540,56" font="Regular;24" foregroundColor="%(TEXT)s" backgroundColor="%(FOOTER_PANEL)s" transparent="0" valign="center" />
    </screen>
    """) % theme.__dict__

    _SAT_NAMES = {
        -30.0: "Hispasat 30W-5/6", -8.0: "Eutelsat 8 West B", -7.0: "Nilesat 201/301",
        -1.0: "Thor / Intelsat 10-02", -0.8: "Thor 5/6/7", 4.8: "Astra 4A",
        7.0: "Eutelsat 7B/7C", 9.0: "Eutelsat 9B", 13.0: "Hot Bird 13F/13G",
        16.0: "Eutelsat 16A", 19.2: "Astra 1KR/1L/1M/1N", 21.6: "Eutelsat 21B",
        23.5: "Astra 3B", 25.5: "Es'hail 1 / Eutelsat 25B", 26.0: "Badr 4/5/6/7/8",
        28.2: "Astra 2E/2F/2G", 31.5: "Astra 5B", 39.0: "Hellas Sat 3/4", 42.0: "Turksat 3A/4A/5B",
    }

    def __init__(self, session, config=None, manager=None):
        Screen.__init__(self, session)
        self.session = session
        self.config = config
        self.manager = manager
        self.groups = []
        self._fingerprint = None
        self._cache_waits = 0

        for n in ("header_bg", "rule", "left_bg", "right_bg", "footer_bg", "footer_rule",
                  "key_red_bar", "key_green_bar", "key_yellow_bar", "key_blue_bar"):
            self[n] = Label("")
        self["title"] = Label("MAPPING REPAIR  /  SATELLITE SCANNER")
        self["subtitle"] = Label("Automatic search satellite by satellite • auto-fix SAFE matches • unresolved channels stay untouched")
        self["status"] = Label("PREPARING")
        self["h_sat"] = Label("RECEPTION")
        self["h_name"] = Label("SATELLITE")
        self["h_total"] = Label("TOTAL")
        self["h_mapped"] = Label("MAPPED")
        self["h_health"] = Label("HEALTH")
        self["h_state"] = Label("REPAIR STATE")
        self["list"] = RepairSatelliteList()
        self["detail_title"] = Label("SELECT SATELLITE")
        self["detail"] = Label("")
        self["hint"] = Label("GREEN / OK  Auto Search + Fix\nBLUE  Review Auto fixes\n0  Undo last Auto\nRED  Back")
        self["key_red"] = Label("Back")
        self["key_green"] = Label("Auto Search + Fix")
        self["key_yellow"] = Label("")
        self["key_blue"] = Label("Review Auto")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions", "NumberActions"],
            {"cancel": self.close, "red": self.close, "green": self.auto_repair, "ok": self.auto_repair,
             "blue": self.review_auto, "0": self.undo_last_auto_repair,
             "up": self._up, "down": self._down}, -1)

        self._timer = eTimer()
        cb = self._timer.callback if hasattr(self._timer, "callback") else self._timer.timeout.get()
        cb.append(self._refresh)
        try:
            smart_mapping_warm_cache.ensure_async()
        except Exception:
            pass
        self.onClose.append(self._cleanup)
        try:
            self.onShown.append(self._resume_refresh)
        except Exception:
            pass
        # beta130: never perform receiver/mapping work synchronously from the
        # screen constructor. Paint first, then consume the resident RAM cache.
        # This keeps key 1 instant even on a busy Zero 4K.
        self._timer.start(40, True)

    def _resume_refresh(self):
        # Child Smart Mapping/Auto Repair may have updated the satellite-only
        # health cache while this launcher was hidden. Refresh once on return;
        # never start a continuous polling loop.
        try:
            self._timer.start(35, True)
        except Exception:
            pass

    @classmethod
    def _sat_name(cls, orbital):
        try:
            val = round(float(orbital), 1)
        except Exception:
            return "Satellite"
        if val in cls._SAT_NAMES:
            return cls._SAT_NAMES[val]
        for pos, name in cls._SAT_NAMES.items():
            if abs(val - pos) <= 0.15:
                return name
        return "Satellite"

    @staticmethod
    def _canon(ref):
        raw = str(ref or "").strip()
        try:
            return channel_registry.canonical_service_ref(raw) or raw.rstrip(":")
        except Exception:
            return raw.rstrip(":")

    def _build_groups(self, snap):
        sat = list((snap or {}).get("sat") or [])
        global_view = (snap or {}).get("mapping_view") or {}
        repair_views = dict((snap or {}).get("repair_views") or {})
        repair_results = dict((snap or {}).get("repair_results") or {})
        ignored_raw = list((snap or {}).get("ignored_refs") or [])
        ignored_keys = set()
        for _ref in ignored_raw:
            _raw = str(_ref or "").strip()
            if not _raw:
                continue
            ignored_keys.add(_raw)
            ignored_keys.add(_raw.rstrip(":"))
            ignored_keys.add(self._canon(_raw))
        view_cache = {}

        def prepared_view(label):
            # A freshly repaired satellite view wins for that satellite. The
            # global ownership cache remains the fallback for untouched lists.
            label_key = str(label or "")
            specific = repair_views.get(label_key) or {}
            key = label_key if specific else ("__global__" if global_view else label_key)
            if key in view_cache:
                return view_cache[key]
            view = specific if specific else (global_view or {})
            refs = set(str(x or "") for x in (view.get("mapped_refs") or []) if x) if view else set()
            info = dict(view.get("mapped_info_by_ref") or {}) if view else {}
            result = (bool(view), refs, info)
            view_cache[key] = result
            return result

        groups = {}
        for service in sat:
            if (service.get("service_type") or channel_mapper.classify_service_ref(service.get("ref"))) != "SAT":
                continue
            label = service.get("reception_list") or channel_registry._orbital_label(service.get("orbital"))
            _last = repair_results.get(str(label or "")) or {}
            row = groups.setdefault(label, {"label": label, "name": self._sat_name(service.get("orbital")),
                                            "orbital": service.get("orbital"), "total": 0, "mapped": 0,
                                            "missing": 0, "ignored": 0, "locked": 0, "conflicts": 0, "coverage": None,
                                            "last_review": int(_last.get("review") or 0),
                                            "last_ambiguous": int(_last.get("ambiguous") or 0),
                                            "last_nomatch": int(_last.get("nomatch") or 0)})
            row["total"] += 1
            raw_ref = str(service.get("ref") or "").strip()
            canon = self._canon(raw_ref)
            if raw_ref in ignored_keys or raw_ref.rstrip(":") in ignored_keys or canon in ignored_keys:
                row["ignored"] += 1
                continue
            have_view, mapped_refs, info = prepared_view(label)
            if have_view:
                mapped = canon in mapped_refs or raw_ref in mapped_refs
                if mapped:
                    row["mapped"] += 1
                    entries = info.get(canon) or info.get(str(service.get("ref") or "")) or []
                    if len(entries) > 1:
                        row["conflicts"] += 1
                    if any(str((x or {}).get("mode") or "").lower() == "manual" for x in entries):
                        row["locked"] += 1
                else:
                    row["missing"] += 1
            else:
                row["mapped"] = None
                row["missing"] = None
        out = list(groups.values())
        for row in out:
            actionable = max(0, int(row.get("total") or 0) - int(row.get("ignored") or 0))
            if row.get("mapped") is not None and actionable:
                row["coverage"] = int(round(100.0 * int(row.get("mapped") or 0) / actionable))
            elif row.get("mapped") is not None and not actionable:
                row["coverage"] = 100
        def sort_key(row):
            try:
                return float(row.get("orbital"))
            except Exception:
                return 9999.0
        out.sort(key=sort_key)
        return out

    def _refresh(self):
        """Paint from the already-prepared RAM snapshot only.

        beta128/129 started a full PrecisionMatch ownership validation as soon
        as this screen opened. Although that work ran in a Python thread, it is
        CPU-bound and can still starve Enigma2 because of the GIL. Mapping
        Repair is now a true launcher/health view: entry never starts a mapping
        scan, disk parse, XMLTV parse or network request.
        """
        try:
            snap = smart_mapping_warm_cache.snapshot()
        except Exception:
            snap = None
        if not snap:
            self["status"].setText("CACHE PREPARING")
            self._cache_waits += 1
            # Only poll the tiny in-memory handoff. No service/mapping scan is
            # performed here. The timer is single-shot to avoid a 250 ms loop.
            try:
                self._timer.start(180 if self._cache_waits < 8 else 500, True)
            except Exception:
                pass
            return

        groups = self._build_groups(snap)
        fp = tuple((x.get("label"), x.get("total"), x.get("mapped"), x.get("missing"), x.get("ignored"), x.get("locked"), x.get("conflicts"), x.get("last_review"), x.get("last_ambiguous"), x.get("last_nomatch")) for x in groups)
        if fp != self._fingerprint:
            idx = 0
            try:
                idx = self["list"].getCurrentIndex()
            except Exception:
                pass
            self.groups = groups
            self["list"].set_rows(groups)
            if groups:
                try:
                    self["list"].instance.moveSelectionTo(max(0, min(idx, len(groups)-1)))
                except Exception:
                    pass
            self._fingerprint = fp

        known = [x for x in groups if x.get("missing") is not None]
        missing = sum(int(x.get("missing") or 0) for x in known)
        ignored = sum(int(x.get("ignored") or 0) for x in groups)
        if known and len(known) == len(groups):
            self["status"].setText("%d SAT • %d MISSING • %d IGNORED" % (len(groups), missing, ignored))
        elif known:
            self["status"].setText("%d/%d SAT HEALTH • %d MISSING" % (len(known), len(groups), missing))
        else:
            # A stale/absent ownership cache must never trigger a global repair
            # scan here. GREEN will open only the selected satellite and Smart
            # Mapping will validate that smaller scope on demand.
            self["status"].setText("%d SAT • HEALTH ON DEMAND" % len(groups))
        self._detail()

    def _detail(self):
        row = self["list"].current_row()
        if not row:
            self["detail_title"].setText("NO SATELLITE")
            self["detail"].setText("No SAT reception list was found in the cached receiver database.")
            return
        self["detail_title"].setText(str(row.get("label") or "SATELLITE"))
        missing = row.get("missing")
        if missing is None:
            body = ("%s\n\n%d receiver services\n\nHealth cache is not available yet.\n\n"
                    "No global scan is started here. GREEN searches and auto-fixes only this satellite; Unresolved channels are left untouched for normal Manual Smart Mapping.\n\n%d ignored / No EPG.") % (
                        row.get("name") or "Satellite", int(row.get("total") or 0), int(row.get("ignored") or 0))
        else:
            body = ("%s\n\n%d receiver services\n%d mapped\n%d unresolved\n%d ignored / No EPG\n%d%% mapping health\n\n"
                    "%d manual lock(s)\n%d conflict(s)\n\n"
                    "Last Auto Search: %d review • %d ambiguous • %d no match\n\n"
                    "GREEN searches + fixes SAFE matches. BLUE reviews Auto fixes; 0 undoes the last Auto Repair. Unresolved channels stay unmapped.") % (
                        row.get("name") or "Satellite", int(row.get("total") or 0), int(row.get("mapped") or 0),
                        int(missing or 0), int(row.get("ignored") or 0), int(row.get("coverage") or 0), int(row.get("locked") or 0), int(row.get("conflicts") or 0),
                        int(row.get("last_review") or 0), int(row.get("last_ambiguous") or 0), int(row.get("last_nomatch") or 0))
        self["detail"].setText(body)

    def _up(self):
        try: self["list"].up()
        except Exception: pass
        self._detail()

    def _down(self):
        try: self["list"].down()
        except Exception: pass
        self._detail()

    def _open(self, filter_mode):
        row = self["list"].current_row()
        if not row:
            return
        try:
            from .channel_mapping import ChannelMappingScreen
            self.session.open(ChannelMappingScreen, self.config, True, self.manager,
                              initial_filter=filter_mode, initial_reception=row.get("label"), repair_mode=True)
        except Exception as exc:
            self.session.open(MessageBox, "Unable to open Mapping Repair.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def auto_repair(self):
        row = self["list"].current_row()
        if not row:
            return
        try:
            from .channel_mapping import ChannelMappingScreen
            self.session.open(ChannelMappingScreen, self.config, True, self.manager,
                              initial_filter="unmapped", initial_reception=row.get("label"),
                              repair_mode=True, auto_repair_start=True)
        except Exception as exc:
            self.session.open(MessageBox, "Unable to start Automatic Mapping Repair.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def review_auto(self):
        self._open("auto")

    def open_all(self):
        self._open("all")

    def undo_last_auto_repair(self):
        try:
            store = MappingStore()
            if not store.can_undo_last_auto_repair():
                self.session.open(MessageBox,
                    "No Auto Repair snapshot is available to undo.\n\nUndo is intentionally disabled after later manual edits so they can never be erased.",
                    MessageBox.TYPE_INFO, timeout=5)
                return
            self.session.openWithCallback(self._undo_last_auto_answer, MessageBox,
                "Undo the last Automatic Mapping Repair?\n\nThis restores the mapping state and generated EPG links from before that Auto Repair.",
                MessageBox.TYPE_YESNO, default=False)
        except Exception as exc:
            self.session.open(MessageBox, "Unable to prepare Undo.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _undo_last_auto_answer(self, answer):
        if not answer:
            return
        try:
            store = MappingStore()
            if not store.undo_last_auto_repair():
                self.session.open(MessageBox, "The last Auto Repair can no longer be undone safely.", MessageBox.TYPE_INFO, timeout=4)
                return
            try:
                mapping_view_cache.invalidate()
                smart_mapping_warm_cache.update_mapping_view({})
                smart_mapping_warm_cache.clear_repair_result()
            except Exception:
                pass
            self._fingerprint = None
            self["status"].setText("AUTO REPAIR UNDONE • HEALTH ON DEMAND")
            try:
                self._timer.start(40, True)
            except Exception:
                pass
            self.session.open(MessageBox, "Last Auto Repair restored successfully.", MessageBox.TYPE_INFO, timeout=4)
        except Exception as exc:
            self.session.open(MessageBox, "Undo failed safely.\n\n%s" % exc, MessageBox.TYPE_ERROR)

    def _cleanup(self):
        try: self._timer.stop()
        except Exception: pass


# Universal receiver UI: adapt FHD-authored skin to active Enigma2 desktop.
for _epgm_screen in (MappingRepairScreen,):
    try:
        _epgm_screen.skin = adapt_skin(_epgm_screen.skin)
    except Exception:
        pass
try:
    del _epgm_screen
except Exception:
    pass
