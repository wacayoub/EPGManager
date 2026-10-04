# -*- coding: utf-8 -*-
"""EPGManager rc36 real native SaaS dashboard.

The UI intentionally stays 100% native Enigma2: no WebView, no browser, no
animations and no large artwork.  It recreates the visual hierarchy of a
modern web monitoring dashboard with opaque panels, KPI tiles, status badges
and compact source cards so it remains fast on Vu+ Zero 4K class hardware.
"""
from __future__ import print_function

import time

from enigma import eTimer, gRGB
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ProgressBar import ProgressBar
from Components.MenuList import MenuList

from . import theme
from .compat import adapt_skin
from ..core import ui_activity, activity_store, srp_channel_map, smart_mapping_warm_cache, smart_catalog_boot
from ..core import github_direct_sync, source_catalog, system_check, source_collision_audit
from ..core.source_preferences import SourcePreferences
from ..core.logger import get_logger
from ..version import __version__

log = get_logger(__name__)


def _kpi_skin(idx, x, width=252):
    return '''
      <widget name="kpi%(i)d_bg" position="%(x)d,184" size="%(w)d,126" backgroundColor="%(panel)s" />
      <widget name="kpi%(i)d_rail" position="%(x)d,184" size="%(w)d,4" backgroundColor="%(accent)s" />
      <widget name="kpi%(i)d_title" position="%(tx)d,202" size="%(tw)d,20" font="Regular;15" foregroundColor="%(dim)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="kpi%(i)d_value" position="%(tx)d,229" size="%(tw)d,39" font="Regular;30" foregroundColor="%(white)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="kpi%(i)d_note" position="%(tx)d,276" size="%(tw)d,21" font="Regular;14" foregroundColor="%(muted)s" backgroundColor="%(panel)s" transparent="1" />
    ''' % {"i": idx, "x": x, "w": width, "tx": x + 18, "tw": width - 36,
           "panel": theme.CARD, "accent": theme.CARD_RAILS[(idx - 1) % len(theme.CARD_RAILS)],
           "dim": theme.DIM_TEXT, "white": theme.WHITE, "muted": theme.MUTED_TEXT}


def _source_card_skin(idx, x, y, width=440, height=245):
    return '''
      <widget name="card%(i)d_bg" position="%(x)d,%(y)d" size="%(w)d,%(h)d" backgroundColor="%(panel)s" />
      <widget name="card%(i)d_rail" position="%(x)d,%(y)d" size="%(w)d,5" backgroundColor="%(accent)s" />
      <widget name="card%(i)d_title" position="%(tx)d,%(ty)d" size="255,30" font="Regular;23" foregroundColor="%(white)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="card%(i)d_status" position="%(sx)d,%(sy)d" size="126,31" font="Regular;15" halign="center" valign="center" foregroundColor="%(green)s" backgroundColor="%(badge)s" transparent="0" />
      <widget name="card%(i)d_subtitle" position="%(tx)d,%(suby)d" size="385,24" font="Regular;16" foregroundColor="%(muted)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="card%(i)d_sep" position="%(tx)d,%(sepy)d" size="385,1" backgroundColor="%(rule)s" />
      <widget name="card%(i)d_left" position="%(tx)d,%(my)d" size="185,83" font="Regular;16" foregroundColor="%(text)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="card%(i)d_right" position="%(rx)d,%(my)d" size="185,83" font="Regular;16" foregroundColor="%(text)s" backgroundColor="%(panel)s" transparent="1" />
      <widget name="card%(i)d_progress" position="%(tx)d,%(py)d" size="385,10" borderWidth="1" borderColor="%(rule)s" backgroundColor="%(track)s" foregroundColor="%(green)s" />
      <widget name="card%(i)d_footer" position="%(tx)d,%(fy)d" size="385,22" font="Regular;14" foregroundColor="%(dim)s" backgroundColor="%(panel)s" transparent="1" />
    ''' % {
        "i": idx, "x": x, "y": y, "w": width, "h": height,
        "tx": x + 20, "ty": y + 19, "sx": x + width - 146, "sy": y + 17,
        "suby": y + 54, "sepy": y + 84, "my": y + 101, "rx": x + 220,
        "py": y + 197, "fy": y + 215,
        "panel": theme.CARD, "accent": theme.CARD_RAILS[(idx - 1) % len(theme.CARD_RAILS)],
        "badge": theme.BADGE_BG, "green": theme.STATUS_GREEN, "muted": theme.MUTED_TEXT,
        "white": theme.WHITE, "text": theme.TEXT, "rule": theme.RULE_SOFT,
        "track": theme.PROGRESS_TRACK, "dim": theme.DIM_TEXT,
    }


_KPIS = "".join(_kpi_skin(i + 1, 40 + i * 264) for i in range(7))
_CARDS = "".join([
    _source_card_skin(1, 40, 390), _source_card_skin(2, 500, 390),
    _source_card_skin(3, 960, 390), _source_card_skin(4, 1420, 390),
    _source_card_skin(5, 40, 650), _source_card_skin(6, 500, 650),
    _source_card_skin(7, 960, 650), _source_card_skin(8, 1420, 650),
])


class DuplicateAuditScreen(Screen):
    """Cache-only duplicate inspector opened from the dashboard DUPLICATES tab."""
    skin = ("""
    <screen name="DuplicateAuditScreen" position="0,0" size="1920,1080" title="EPG Duplicates" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="head" position="0,0" size="1920,104" backgroundColor="%(PANEL)s" />
      <widget name="title" position="46,18" size="700,42" font="Regular;34" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="770,26" size="1060,36" font="Regular;21" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="hint" position="48,66" size="1780,26" font="Regular;18" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="list" position="44,126" size="1832,826" font="Regular;20" itemHeight="48" scrollbarMode="showOnDemand" foregroundColor="%(TEXT)s" backgroundColor="%(PANEL_ALT)s" selectionForegroundColor="%(WHITE)s" selectionBackgroundColor="%(NAV_ACTIVE)s" />
      <widget name="footer" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_red" position="42,1008" size="330,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_green" position="405,1008" size="650,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
    </screen>""") % theme.__dict__

    def __init__(self, session, config=None):
        Screen.__init__(self, session)
        self.config = config
        self["head"] = Label(""); self["footer"] = Label("")
        self["title"] = Label("DUPLICATES")
        self["summary"] = Label("")
        self["hint"] = Label("Duplicate ServiceRef owners + shared XMLTV IDs • cache-only • no network scan")
        self["list"] = MenuList([])
        self["key_red"] = Label("Back")
        self["key_green"] = Label("Refresh")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "cancel": self.close, "red": self.close, "green": self._load, "ok": self._load,
        }, -1)
        self._load()

    def _load(self):
        rows = []
        try:
            mapping = system_check._mapping_summary()
        except Exception:
            mapping = {}
        dup = mapping.get("duplicates") or {}
        for ref, owners in sorted(dup.items(), key=lambda kv: str(kv[0])):
            labels = []
            for sid, cid, mode in owners or []:
                labels.append("%s:%s [%s]" % (sid or "source", cid or "ID", str(mode or "auto").upper()))
            rows.append("SERVICE REF • %s • %s" % (str(ref or "")[:76], "  |  ".join(labels)[:180]))
        try:
            selected = SourcePreferences().selected_mapping_ids()
            collisions = source_collision_audit.scan(selected, max_groups=200) or {}
        except Exception:
            collisions = {}
        for item in collisions.get("collisions") or []:
            owners = [str((x or {}).get("name") or (x or {}).get("source_id") or "source") for x in (item.get("owners") or [])]
            kind = "LANGUAGE PAIR" if item.get("known_parallel_language") else "SHARED ID"
            rows.append("%s • %s • %s" % (kind, item.get("channel_id") or "ID", " / ".join(owners)[:150]))
        if not rows:
            rows = ["No duplicate ServiceRef owners or shared selected-source IDs found"]
        self["list"].setList(rows)
        self["summary"].setText("%d duplicate ServiceRefs • %d shared IDs" % (
            int(mapping.get("duplicate_owner_refs") or 0), int(collisions.get("collision_count") or 0)))


class EPGManagerMainScreen(Screen):
    _KEYS = (
        theme.key_bar_skin("key_red", 34, 1015, 300, 48, theme.BTN_RED, 21) +
        theme.key_bar_skin("key_green", 355, 1015, 410, 48, theme.BTN_GREEN, 21) +
        theme.key_bar_skin("key_yellow", 790, 1015, 410, 48, theme.BTN_YELLOW, 21) +
        theme.key_bar_skin("key_blue", 1225, 1015, 660, 48, theme.BTN_BLUE, 21)
    )
    skin = ('''
    <screen name="EPGManagerMainScreen" position="0,0" size="1920,1080" title="EPG Manager" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="top" position="0,0" size="1920,92" backgroundColor="%(HEADER)s" />
      <widget name="brand" position="42,17" size="315,36" font="Regular;30" foregroundColor="%(WHITE)s" backgroundColor="%(HEADER)s" transparent="1" />
      <widget name="brand_sub" position="44,56" size="315,22" font="Regular;14" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="1" />

      <widget name="nav_overview" position="388,16" size="176,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(WHITE)s" backgroundColor="%(NAV_ACTIVE)s" transparent="0" />
      <widget name="nav_sources" position="574,16" size="156,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="0" />
      <widget name="nav_mapping" position="740,16" size="205,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="0" />
      <widget name="nav_duplicates" position="955,16" size="178,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="0" />
      <widget name="nav_zero" position="1143,16" size="162,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="0" />
      <widget name="nav_logs" position="1315,16" size="120,53" font="Regular;18" halign="center" valign="center" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(HEADER)s" transparent="0" />
      <widget name="settings_btn" position="1460,16" size="174,53" font="Regular;17" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(CARD)s" transparent="0" />
      <widget name="online" position="1650,16" size="226,53" font="Regular;16" halign="center" valign="center" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(CARD)s" transparent="0" />
      <widget name="header_rule" position="0,90" size="1920,2" backgroundColor="%(RULE_SOFT)s" />

      <widget name="headline" position="42,112" size="760,37" font="Regular;29" foregroundColor="%(WHITE)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="headline_sub" position="44,151" size="990,23" font="Regular;16" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="target" position="1550,113" size="326,46" font="Regular;17" halign="center" valign="center" foregroundColor="%(TEXT)s" backgroundColor="%(CARD)s" transparent="0" />

      %(KPIS)s

      <widget name="sources_heading" position="42,333" size="700,34" font="Regular;27" foregroundColor="%(WHITE)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="sources_sub" position="44,365" size="950,21" font="Regular;15" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(BG)s" transparent="1" />
      <widget name="catalog_meta" position="1140,338" size="735,28" font="Regular;16" halign="right" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(BG)s" transparent="1" />

      %(CARDS)s

      <widget name="runtime_bar" position="40,919" size="1840,64" backgroundColor="%(CARD)s" />
      <widget name="runtime_state" position="64,932" size="520,29" font="Regular;20" foregroundColor="%(STATUS_GREEN)s" backgroundColor="%(CARD)s" transparent="1" />
      <widget name="runtime_detail" position="600,934" size="1248,26" font="Regular;16" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(CARD)s" transparent="1" />
      <widget name="runtime_progress" position="64,968" size="1784,6" borderWidth="0" backgroundColor="%(PROGRESS_TRACK)s" foregroundColor="%(ACCENT_SECONDARY)s" />

      <widget name="footer" position="0,1000" size="1920,80" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="footer_rule" position="30,1000" size="1860,1" backgroundColor="%(RULE_SOFT)s" />
      %(KEYS)s
    </screen>''') % dict(theme.__dict__, KPIS=_KPIS, CARDS=_CARDS, KEYS=_KEYS)

    CARD_DEFS = (
        ("MOROCCO", "All Morocco channels • SNRT • 2M • Medi1 • Chada", "morocco"),
        ("beIN", "Sports / entertainment", "bein"),
        ("ELCINEMA", "Arabic entertainment", "elcinema"),
        ("OSN", "Official schedule", "osn"),
        ("EMIRATES / AL JAZEERA", "Sport24 • Dubai+ • Al Jazeera", "emirates"),
        ("SHAHID / MBC", "MBC channels • Al Arabiya • Al Hadath", "shahid"),
        ("ROTANA / STC TV", "Rotana specialists • STC TV fallback catalogue", "rotana"),
        ("SYSTEM HEALTH", "Mappings • ID health • timezone • SRP", "system_health"),
    )

    def __init__(self, session, manager, config):
        Screen.__init__(self, session)
        self.session = session
        self.manager = manager
        self.config = config
        self.preferences = SourcePreferences()
        try:
            ui_activity.begin()
        except Exception:
            pass

        for name in ("top", "header_rule", "runtime_bar", "footer", "footer_rule"):
            self[name] = Label("")
        self["brand"] = Label("EPG MANAGER")
        self["brand_sub"] = Label("LEFT/RIGHT + OK • 1-7 tabs • Direct EPG")
        self["nav_overview"] = Label("OVERVIEW")
        self["nav_sources"] = Label("SOURCES")
        self["nav_mapping"] = Label("SMART MAPPING")
        self["nav_duplicates"] = Label("DUPLICATES")
        self["nav_zero"] = Label("ZERO EPG")
        self["nav_logs"] = Label("LOGS")
        self["settings_btn"] = Label("SETTINGS")
        self["online"] = Label("● CHECKING SYSTEM")
        self["headline"] = Label("EPG Control Center")
        self["headline_sub"] = Label("One screen: sources, IDs, mapping, timezone and last import")
        self["target"] = Label("10 DIRECT SOURCES • USER-CONTROLLED MAPPING")
        self["sources_heading"] = Label("Direct Sources & Health")
        self["sources_sub"] = Label("Only validated direct feeds are promoted for MENA mapping")
        self["catalog_meta"] = Label("")

        for i in range(1, 8):
            self["kpi%d_bg" % i] = Label("")
            self["kpi%d_rail" % i] = Label("")
            self["kpi%d_title" % i] = Label("")
            self["kpi%d_value" % i] = Label("")
            self["kpi%d_note" % i] = Label("")

        for idx, (title, subtitle, _kind) in enumerate(self.CARD_DEFS, 1):
            self["card%d_bg" % idx] = Label("")
            self["card%d_rail" % idx] = Label("")
            self["card%d_title" % idx] = Label(title)
            self["card%d_status" % idx] = Label("SYNCING")
            self["card%d_subtitle" % idx] = Label(subtitle)
            self["card%d_sep" % idx] = Label("")
            self["card%d_left" % idx] = Label("")
            self["card%d_right" % idx] = Label("")
            self["card%d_progress" % idx] = ProgressBar()
            self["card%d_progress" % idx].setValue(5)
            self["card%d_footer" % idx] = Label("")

        self["runtime_state"] = Label("SYSTEM READY")
        self["runtime_detail"] = Label("")
        self["runtime_progress"] = ProgressBar(); self["runtime_progress"].setValue(100)
        self["key_red_bar"] = Label(""); self["key_red"] = Label("Exit")
        self["key_green_bar"] = Label(""); self["key_green"] = Label("Smart Mapping")
        self["key_yellow_bar"] = Label(""); self["key_yellow"] = Label("Import All")
        self["key_blue_bar"] = Label(""); self["key_blue"] = Label("Add Source")

        # rc49: the top bar is a real TV-remote navigator, not decorative labels.
        self._nav_widgets = ("nav_overview", "nav_sources", "nav_mapping", "nav_duplicates", "nav_zero", "nav_logs", "settings_btn", "online")
        self._nav_index = 0
        self["actions"] = ActionMap(["OkCancelActions", "NumberActions", "ColorActions", "MenuActions", "DirectionActions"], {
            "cancel": self.close, "red": self.close, "ok": self._nav_activate,
            "left": self._nav_left, "right": self._nav_right,
            "green": self.open_channel_mapping, "yellow": self.run_cycle, "blue": self.open_sources,
            "1": lambda: self._nav_open(0), "2": lambda: self._nav_open(1), "3": lambda: self._nav_open(2),
            "4": lambda: self._nav_open(3), "5": lambda: self._nav_open(4), "6": lambda: self._nav_open(5),
            "7": lambda: self._nav_open(6), "8": self.open_online_update, "menu": self.open_settings,
        }, -1)
        try:
            self.onLayoutFinish.append(self._nav_paint)
        except Exception:
            pass

        self._refresh_timer = eTimer()
        cb = self._refresh_timer.callback if hasattr(self._refresh_timer, "callback") else self._refresh_timer.timeout.get()
        cb.append(self._refresh)
        self._refresh_timer.start(1500, False)
        self.onClose.append(self._cleanup)
        github_direct_sync.sync_async(force=False, min_age=45)
        try:
            smart_mapping_warm_cache.ensure_async(); smart_catalog_boot.ensure_async()
        except Exception:
            pass
        self._refresh()

    def _set_fg(self, name, color):
        try:
            self[name].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _set_bg(self, name, color):
        try:
            self[name].instance.setBackgroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    @staticmethod
    def _age(ts):
        try:
            delta = max(0, int(time.time() - float(ts)))
        except Exception:
            return "never"
        if delta < 60: return "%ds ago" % delta
        if delta < 3600: return "%dm ago" % (delta // 60)
        if delta < 86400: return "%dh ago" % (delta // 3600)
        return "%dd ago" % (delta // 86400)

    def _mapping_stats(self):
        mapped_ids = mapped_refs = unmapped = 0
        try:
            idx = srp_channel_map.load_index() or {}
            rows = list((idx.get("sources") or {}).values())
            mapped_ids = sum(int((r or {}).get("channels") or 0) for r in rows)
            mapped_refs = sum(int((r or {}).get("refs") or (r or {}).get("service_refs") or (r or {}).get("mapped_service_refs") or 0) for r in rows)
            unmapped = sum(int((r or {}).get("unmapped") or (r or {}).get("unmapped_channel_ids") or 0) for r in rows)
        except Exception:
            pass
        if not mapped_ids and not unmapped:
            try:
                m = (activity_store.load().get("mapping") or {})
                mapped_ids = int(m.get("mapped_channel_ids") or 0)
                mapped_refs = int(m.get("mapped_service_refs") or 0)
                unmapped = int(m.get("unmapped_channel_ids") or 0)
            except Exception:
                pass
        return mapped_ids, mapped_refs, unmapped

    def _set_card_status(self, idx, text, color, progress):
        self["card%d_status" % idx].setText(text)
        self["card%d_progress" % idx].setValue(max(0, min(100, int(progress))))
        self._set_fg("card%d_status" % idx, color)
        self._set_bg("card%d_rail" % idx, color)
        try:
            self["card%d_progress" % idx].instance.setForegroundColor(gRGB(int(color[1:], 16)))
        except Exception:
            pass

    def _direct_card(self, idx, source_ids, rows, checked):
        selected_rows = [rows.get(sid) or {} for sid in source_ids]
        online = sum(1 for r in selected_rows if r.get("ok"))
        degraded = sum(1 for r in selected_rows if r.get("degraded"))
        stale = sum(1 for r in selected_rows if r.get("stale"))
        channels = sum(int(r.get("channels") or 0) for r in selected_rows)
        active = sum(int(r.get("active_channels") or 0) for r in selected_rows)
        zero = sum(int(r.get("zero_epg_channels") or 0) for r in selected_rows)
        programmes = sum(int(r.get("programmes") or 0) for r in selected_rows)
        coverages = [float(r.get("coverage_pct") or 0.0) for r in selected_rows if r]
        coverage = sum(coverages) / len(coverages) if coverages else 0.0
        latency = sum(int(r.get("latency_ms") or 0) for r in selected_rows if r)
        expected = len(source_ids)
        if online == expected and not degraded:
            status, color = "HEALTHY", theme.STATUS_GREEN
        elif online:
            status, color = "WARNING", theme.STATUS_YELLOW
        else:
            status, color = ("SYNCING" if github_direct_sync.is_running() else "OFFLINE"), theme.STATUS_RED
        self._set_card_status(idx, status, color, coverage if online else 5)
        self["card%d_left" % idx].setText("SOURCES ONLINE\n%d / %d\nACTIVE IDS\n%d / %d" % (online, expected, active, channels))
        self["card%d_right" % idx].setText("COVERAGE\n%.1f%%\nZERO EPG\n%d" % (coverage, zero))
        warn = []
        if stale:
            warn.append("%d stale" % stale)
        if zero:
            warn.append("%d zero" % zero)
        tail = " • ".join(warn) if warn else "%d programmes" % programmes
        self["card%d_footer" % idx].setText("Updated %s  •  %s  •  %d ms" % (self._age(checked) if checked else "waiting", tail, latency))

    def _provider_card(self, idx, provider_names, selected_ids, all_sources):
        rows = [x for x in all_sources if str(x.get("provider") or "").upper() in provider_names]
        ids = set(str(x.get("id") or "") for x in rows)
        selected = len(ids.intersection(selected_ids))
        total = len(rows)
        status = "AVAILABLE" if total else "OFFLINE"
        color = theme.STATUS_GREEN if total else theme.STATUS_RED
        progress = 100 if total else 5
        self._set_card_status(idx, status, color, progress)
        self["card%d_left" % idx].setText("CATALOGUE\n%d feeds\nSELECTED\n%d" % (total, selected))
        self["card%d_right" % idx].setText("MODE\nON DEMAND\nARAB MENA\nBLOCKED" if "OPENEPG" in provider_names or "EPGSHARE" in provider_names else "MODE\nON DEMAND\nNATIVE SRP\nYES")
        self["card%d_footer" % idx].setText("Ready for Smart Mapping / Import  •  no boot download")

    def _refresh(self):
        data = github_direct_sync.load() or {}
        rows = data.get("sources") or {}
        checked = int(data.get("checked") or 0)
        online = sum(1 for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES if (rows.get(sid) or {}).get("ok"))
        healthy = sum(1 for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES if (rows.get(sid) or {}).get("ok") and not (rows.get(sid) or {}).get("degraded"))
        degraded_sources = sum(1 for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES if (rows.get(sid) or {}).get("degraded"))
        channels = sum(int((rows.get(sid) or {}).get("channels") or 0) for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES)
        programmes = sum(int((rows.get(sid) or {}).get("programmes") or 0) for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES)
        cov = [float((rows.get(sid) or {}).get("coverage_pct") or 0.0) for sid, _label, _stem in github_direct_sync.DIRECT_SOURCES if rows.get(sid)]
        avg_cov = sum(cov) / len(cov) if cov else 0.0

        all_sources = source_catalog.all_sources()
        selected_ids = set(self.preferences.selected_catalogue_ids())
        providers = {}
        for src in all_sources:
            p = str(src.get("provider") or "").upper()
            providers[p] = providers.get(p, 0) + 1
        self["catalog_meta"].setText("%d feeds • %d selected • GitHub %s" % (len(all_sources), len(selected_ids), self._age(checked) if checked else "syncing"))

        mapped, refs, unmapped = self._mapping_stats()
        total_map = mapped + unmapped
        mapping_pct = int(round(100.0 * mapped / total_map)) if total_map else 0
        activity = activity_store.load()
        imp = activity.get("last_import") or {}
        cycle = activity.get("cycle") or {}
        imported = int(imp.get("imported_events") or 0)

        sysc = system_check.load_cache() or {}
        mon = data.get("id_monitor") or {}
        green_ids = red_ids = unknown_ids = 0
        for sid, _label, stem in github_direct_sync.DIRECT_SOURCES:
            for row in mon.get(stem, []) or []:
                health = github_direct_sync.id_health(sid, row.get("id"), data=data)
                if health == "OK": green_ids += 1
                elif health == "NO_EPG": red_ids += 1
                else: unknown_ids += 1
        total_ids = green_ids + red_ids + unknown_ids
        system_state = str(sysc.get("status") or "NOT RUN")
        tz_state = str((sysc.get("timezone") or {}).get("status") or "CHECK")
        kpis = (
            ("DIRECT FEEDS", "%d/%d" % (online, len(github_direct_sync.DIRECT_SOURCES)), "%d clean • %d warning" % (healthy, degraded_sources), theme.STATUS_GREEN if online == len(github_direct_sync.DIRECT_SOURCES) and not degraded_sources else theme.STATUS_YELLOW),
            ("EPG IDs OK", "%d/%d" % (green_ids, total_ids) if total_ids else "—", "%d no EPG • %d unknown" % (red_ids, unknown_ids), theme.STATUS_GREEN if total_ids and not red_ids else theme.STATUS_YELLOW),
            ("MAPPED", str(mapped) if total_map else "—", "%d service refs" % refs, theme.ACCENT_PRIMARY),
            ("UNRESOLVED", str(unmapped) if total_map else "—", "needs attention", theme.STATUS_ORANGE if unmapped else theme.STATUS_GREEN),
            ("NO EPG", str(red_ids) if total_ids else "—", "proven zero future", theme.STATUS_RED if red_ids else theme.STATUS_GREEN),
            ("LAST IMPORT", str(imported) if imp else "—", self._age(imp.get("timestamp")) if imp else "no import yet", theme.ACCENT_SECONDARY),
            ("TIME / SYSTEM", tz_state, system_state, theme.STATUS_GREEN if system_state == "PASS" else (theme.STATUS_YELLOW if system_state == "WARN" else theme.STATUS_ORANGE)),
        )
        for idx, (title, value, note, color) in enumerate(kpis, 1):
            self["kpi%d_title" % idx].setText(title)
            self["kpi%d_value" % idx].setText(value)
            self["kpi%d_note" % idx].setText(note)
            self._set_fg("kpi%d_value" % idx, color)
            self._set_bg("kpi%d_rail" % idx, color)

        self._direct_card(1, ("ext_epgscrapers_morocco",), rows, checked)
        self._direct_card(2, ("ext_epgscrapers_bein",), rows, checked)
        self._direct_card(3, ("ext_epgscrapers_elcinema",), rows, checked)
        self._direct_card(4, ("ext_epgscrapers_osn",), rows, checked)
        self._direct_card(5, ("ext_epgscrapers_sport24", "ext_epgscrapers_dubaiplus", "ext_epgscrapers_aljazeera", "ext_epgscrapers_alkass"), rows, checked)
        self._direct_card(6, ("ext_epgscrapers_shahid",), rows, checked)
        self._direct_card(7, ("ext_epgscrapers_rotana", "ext_epgscrapers_stctv", "ext_epgscrapers_starzplay"), rows, checked)
        # Card 8 is an actionable health summary instead of an empty tile.
        sys_status = str(sysc.get("status") or "NOT RUN")
        sys_color = theme.STATUS_GREEN if sys_status == "PASS" else (theme.STATUS_YELLOW if sys_status == "WARN" else theme.STATUS_ORANGE)
        self._set_card_status(8, sys_status, sys_color, 100 if sys_status == "PASS" else (70 if sys_status == "WARN" else 25))
        self["card8_left"].setText("MAPPED REFS\n%d\nUNRESOLVED\n%d" % (int(sysc.get("mapped_refs") or refs or 0), int(sysc.get("unresolved") or unmapped or 0)))
        self["card8_right"].setText("NO EPG\n%d\nDUP OWNERS\n%d" % (int(sysc.get("red_ids") or red_ids or 0), int(sysc.get("duplicate_owner_refs") or 0)))
        self["card8_footer"].setText("System Check → LOGS  •  timezone %s" % str((sysc.get("timezone") or {}).get("status") or "not checked"))

        live = self.manager.get_cycle_status() if self.manager else {}
        if live.get("running"):
            pct = max(0, min(100, int(live.get("percent") or 0)))
            self["runtime_state"].setText("● WORKING • %s" % (live.get("stage") or "EPG CYCLE"))
            self["runtime_detail"].setText(str(live.get("detail") or "Processing")[:150])
            self["runtime_progress"].setValue(pct)
            self._set_fg("runtime_state", theme.STATUS_YELLOW)
        elif cycle and cycle.get("ok") is False:
            self["runtime_state"].setText("● REVIEW REQUIRED")
            self["runtime_detail"].setText(str(cycle.get("message") or "Last cycle reported an error")[:150])
            self["runtime_progress"].setValue(10)
            self._set_fg("runtime_state", theme.STATUS_ORANGE)
        else:
            sys_status = str(sysc.get("status") or "NOT RUN")
            if sys_status == "PASS":
                self["runtime_state"].setText("● SYSTEM CHECK PASS")
                self._set_fg("runtime_state", theme.STATUS_GREEN)
            elif sys_status == "WARN":
                self["runtime_state"].setText("● SYSTEM CHECK WARNING")
                self._set_fg("runtime_state", theme.STATUS_YELLOW)
            else:
                self["runtime_state"].setText("● READY • SYSTEM CHECK IN LOGS")
                self._set_fg("runtime_state", theme.ACCENT_SECONDARY)
            self["runtime_detail"].setText("%d direct feeds • %d mapped • %d unresolved • %d no EPG • v%s" % (len(github_direct_sync.DIRECT_SOURCES), mapped, unmapped, red_ids, __version__))
            self["runtime_progress"].setValue(100 if sys_status == "PASS" else int(healthy * 100 / max(1, len(github_direct_sync.DIRECT_SOURCES))) if healthy else 5)
            try:
                self["online"].setText("ONLINE UPDATE • v%s" % __version__)
                self._set_fg("online", theme.STATUS_GREEN)
            except Exception:
                pass

    def _nav_paint(self):
        for idx, name in enumerate(self._nav_widgets):
            active = idx == int(self._nav_index or 0)
            self._set_bg(name, theme.NAV_ACTIVE if active else (theme.CARD if name == "settings_btn" else theme.HEADER))
            self._set_fg(name, theme.WHITE if active else theme.MUTED_TEXT)

    def _nav_left(self):
        self._nav_index = (int(self._nav_index or 0) - 1) % len(self._nav_widgets)
        self._nav_paint()

    def _nav_right(self):
        self._nav_index = (int(self._nav_index or 0) + 1) % len(self._nav_widgets)
        self._nav_paint()

    def _nav_open(self, index):
        self._nav_index = max(0, min(int(index), len(self._nav_widgets) - 1))
        self._nav_paint()
        self._nav_activate()

    def _nav_activate(self):
        actions = (self._refresh, self.open_sources, self.open_channel_mapping,
                   self.open_duplicates, self.open_zero_epg, self.open_logs, self.open_settings, self.open_online_update)
        try:
            actions[int(self._nav_index or 0)]()
        except Exception:
            log.exception("Dashboard tab activation failed")

    def open_duplicates(self):
        self.session.open(DuplicateAuditScreen, self.config)

    def open_zero_epg(self):
        from .id_monitor import DirectIdMonitorScreen
        self.session.open(DirectIdMonitorScreen, self.config, "zero")

    def run_cycle(self):
        started = self.manager.run_fast_import_async(on_complete=lambda _result: None)
        if not started:
            self.session.open(MessageBox, "EPG Manager is already busy.", MessageBox.TYPE_INFO, timeout=3)

    def open_system_check(self):
        from .system_check import SystemCheckScreen
        self.session.open(SystemCheckScreen, self.config)

    def open_github_sync(self):
        from .github_sync import GitHubSyncScreen
        self.session.open(GitHubSyncScreen, self.manager, self.config)

    def open_sources(self):
        # New dashboard entry: the old Source Manager name/workflow is retired;
        # this opens the provider catalogue only when the user explicitly asks.
        try:
            from .native_sources import SmartSourcesScreen
            self.session.open(SmartSourcesScreen, self.manager, self.config)
        except Exception:
            log.exception("Unable to open Sources catalogue")
            self.open_github_sync()

    def open_status(self):
        from .status import EPGStatusScreen
        self.session.open(EPGStatusScreen, self.manager, self.config)

    def open_settings(self):
        from .settings import EPGManagerSettingsScreen
        self.session.open(EPGManagerSettingsScreen, self.config)

    def open_logs(self):
        try:
            from .screens import LogsAboutScreen
            self.session.open(LogsAboutScreen, self.config)
        except Exception:
            log.exception("Unable to open Logs / About")

    def open_online_update(self):
        from .online_update import OnlineUpdateScreen
        self.session.open(OnlineUpdateScreen)

    def open_channel_mapping(self):
        from .channel_mapping import ChannelMappingScreen
        self.session.open(ChannelMappingScreen, self.config, True, self.manager)

    def open_native_import(self):
        from .native_import import NativeImportScreen
        self.session.open(NativeImportScreen, self.config, self.manager)

    def _cleanup(self):
        try:
            ui_activity.end()
        except Exception:
            pass
        try:
            self._refresh_timer.stop()
        except Exception:
            pass


for _screen in (DuplicateAuditScreen, EPGManagerMainScreen):
    try:
        _screen.skin = adapt_skin(_screen.skin)
    except Exception:
        pass
