# -*- coding: utf-8 -*-
from __future__ import print_function

from enigma import eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, eTimer
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList

from .compat import MultiContentEntryText, adapt_skin
from . import theme
from ..core import github_direct_sync, direct_id_repair, srp_channel_map, channel_registry, srp_master_engine


_SOURCE_NAMESPACE = {
    "Morocco": "MOROCCO",
    "beIN": "beIN",
    "OSN": "OSN",
    "Shahid / MBC": "MBC",
    "Rotana": "ROTANA",
    "STARZPLAY GCC": "STARZPLAY",
    "Al Jazeera": "AL JAZEERA",
    "Al Kass": "AL KASS",
    "STC TV": "STC TV",
    "Dubai+": "Dubai+",
    "Sport24": "Sport24",
    "ElCinema": "ElCinema",
}


_GROUP_ORDER = (
    "beIN", "OSN", "MBC / SHAHID", "ROTANA", "STARZPLAY",
    "AL JAZEERA", "AL KASS", "ART",
    "MOROCCO", "UAE", "SAUDI", "QATAR", "EGYPT", "KUWAIT",
    "BAHRAIN", "LEBANON", "JORDAN", "IRAQ", "TUNISIA", "ALGERIA",
    "LIBYA", "PALESTINE", "YEMEN", "OMAN", "SYRIA", "MAURITANIA",
    "OTHER MENA", "INTERNATIONAL",
)
_GROUP_RANK = {name: idx for idx, name in enumerate(_GROUP_ORDER)}

# Major bouquets/providers remain standalone groups. Everything else is filed
# by channel country so the ID monitor stays compact without duplicating IDs.
_SOURCE_GROUP = {
    "beIN": "beIN",
    "OSN": "OSN",
    "Shahid / MBC": "MBC / SHAHID",
    "Rotana": "ROTANA",
    "STARZPLAY GCC": "STARZPLAY",
    "Al Jazeera": "AL JAZEERA",
    "Al Kass": "AL KASS",
    "Morocco": "MOROCCO",
}
_COUNTRY_GROUPS = {
    "ma": "MOROCCO", "ae": "UAE", "sa": "SAUDI", "qa": "QATAR",
    "eg": "EGYPT", "kw": "KUWAIT", "bh": "BAHRAIN", "lb": "LEBANON",
    "jo": "JORDAN", "iq": "IRAQ", "tn": "TUNISIA", "dz": "ALGERIA",
    "ly": "LIBYA", "ps": "PALESTINE", "ye": "YEMEN", "om": "OMAN",
    "sy": "SYRIA", "mr": "MAURITANIA",
}
_INTERNATIONAL_TOKENS = (
    "cnn", "bbc", "bloomberg", "euronews", "nat geo", "national geographic",
    "discovery", "fox", "mtv", "nick", "cartoon network", "cbeebies",
    "food network", "hgtv", "star movies", "star world", "paramount",
)

def _group_for_epg_id(source_label, name, raw_id):
    """UI-only group classifier. Never changes XMLTV/source ownership."""
    source_label = str(source_label or "").strip()
    if source_label in _SOURCE_GROUP:
        return _SOURCE_GROUP[source_label]

    text = ("%s %s" % (name or "", raw_id or "")).casefold()
    # Recognisable network groups win before country filing.
    if "art " in (text + " ") or str(raw_id or "").casefold().startswith("art"):
        return "ART"
    if "bein" in text:
        return "beIN"
    if "rotana" in text:
        return "ROTANA"
    if any(tok in text for tok in ("mbc", "al arabiya", "alarabiya", "al hadath", "alhadath", "wanasah")):
        return "MBC / SHAHID"
    if "alkass" in text or "al kass" in text:
        return "AL KASS"
    if "aljazeera" in text or "al jazeera" in text:
        return "AL JAZEERA"

    rid = str(raw_id or "").casefold()
    import re
    m = re.search(r"\.([a-z]{2})(?:@|$)", rid)
    if m and m.group(1) in _COUNTRY_GROUPS:
        return _COUNTRY_GROUPS[m.group(1)]

    # Opaque provider IDs need a few safe name hints before falling back.
    if any(tok in text for tok in ("dubai", "abu dhabi", "ad sports", "sharjah", "yas ")):
        return "UAE"
    if any(tok in text for tok in ("saudi", "sbc", "thmanyah", "thikrayat")):
        return "SAUDI"
    if any(tok in text for tok in ("qatar tv", "al rayyan", "alrayyan")):
        return "QATAR"
    if any(tok in text for tok in _INTERNATIONAL_TOKENS):
        return "INTERNATIONAL"
    if source_label == "Dubai+":
        return "UAE"
    if source_label == "Sport24":
        return "OTHER MENA"
    if source_label == "STC TV":
        return "SAUDI"
    if source_label == "ElCinema":
        return "OTHER MENA"
    return "OTHER MENA"


def _compact_display_token(source_label, name, raw_id):
    """Return a short human channel token for SOURCE.ID display only.

    Stable XMLTV IDs remain untouched and continue to drive mapping/import.
    """
    text = str(name or raw_id or "").strip()
    if not text:
        return str(raw_id or "").strip()
    aliases = {
        "cnn international": "CNN",
        "cnn intl": "CNN",
    }
    alias = aliases.get(text.casefold())
    if alias:
        return alias
    # Avoid visually repeating the provider when the friendly channel name
    # already starts with it: beIN.beINSPORTS1 -> beIN.SPORTS1, etc.
    prefixes = {
        "beIN": ("bein",),
        "OSN": ("osn",),
        "Rotana": ("rotana",),
        "STARZPLAY GCC": ("starzplay", "starz play"),
        "Al Kass": ("al kass", "alkass"),
        "Al Jazeera": ("al jazeera", "aljazeera"),
        "STC TV": ("stc tv", "stctv"),
    }.get(str(source_label or ""), ())
    low = text.casefold()
    for prefix in prefixes:
        if low.startswith(prefix):
            candidate = text[len(prefix):].lstrip(" ._-+/•")
            if candidate:
                return candidate
    return text


def _display_epg_id(source_label, name, raw_id):
    namespace = _SOURCE_NAMESPACE.get(str(source_label or ""), str(source_label or "EPG").strip() or "EPG")
    token = _compact_display_token(source_label, name, raw_id)
    return "%s.%s" % (namespace, token) if token else namespace


class IdMonitorList(MenuList):
    def __init__(self, health_colors=True):
        MenuList.__init__(self, [], False, eListboxPythonMultiContent)
        self.health_colors = bool(health_colors)
        self.l.setFont(0, gFont("Regular", 21))
        self.l.setFont(1, gFont("Regular", 17))
        self.l.setItemHeight(54)

    @staticmethod
    def _health(item):
        health = str((item or {}).get("_health") or "").upper()
        if health == "NO_EPG":
            return "NO EPG", theme.STATUS_RED_INT
        if health == "OK":
            return "EPG OK", theme.STATUS_GREEN_INT
        if health == "WARNING":
            return "WARN", theme.STATUS_ORANGE_INT
        return "UNKNOWN", theme.STATUS_GREY_INT

    @staticmethod
    def _mapping(item):
        state = str((item or {}).get("map_state") or "UNMAPPED").upper()
        if state == "MAPPED":
            return state, theme.STATUS_GREEN_INT
        if state == "WRONG":
            return state, theme.STATUS_ORANGE_INT
        if state == "ALT":
            return state, theme.STATUS_GREY_INT
        if state == "NO EPG":
            return state, theme.STATUS_RED_INT
        return "UNMAPPED", theme.STATUS_YELLOW_INT

    def set_rows(self, rows):
        out=[]
        for item in rows:
            if item.get("_group_header"):
                title=str(item.get("group") or "OTHER MENA")
                count=int(item.get("count") or 0)
                out.append([
                    item,
                    MultiContentEntryText(pos=(18,0), size=(1760,54), font=0,
                        flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                        text="━━  %s  (%d IDs)" % (title, count),
                        color=theme.ACCENT_PRIMARY_INT, color_sel=theme.WHITE_INT),
                ])
                continue
            health, health_color = self._health(item)
            mapping, map_color = self._mapping(item)
            reason=str(item.get("reason") or "")
            details=("%d prog • %.1fh" % (int(item.get("future_programmes") or 0), float(item.get("future_hours") or 0.0)))
            if reason and health != "EPG OK":
                details += " • " + reason[:27]
            out.append([
                item,
                MultiContentEntryText(pos=(12,0), size=(105,54), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text="● "+health, color=health_color, color_sel=health_color),
                MultiContentEntryText(pos=(125,0), size=(465,54), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=str(item.get("name") or item.get("id") or ""), color=theme.TEXT_INT, color_sel=theme.WHITE_INT),
                # XMLTV identity shown as SOURCE.CHANNEL; real ID stays untouched.
                MultiContentEntryText(pos=(600,0), size=(495,54), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=str(item.get("display_id") or item.get("id") or ""),
                    color=(health_color if self.health_colors else theme.MUTED_TEXT_INT),
                    color_sel=(health_color if self.health_colors else theme.WHITE_INT)),
                MultiContentEntryText(pos=(1105,0), size=(225,54), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,
                    text=mapping, color=map_color, color_sel=map_color),
                MultiContentEntryText(pos=(1335,0), size=(465,54), font=1, flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,
                    text=details, color=health_color, color_sel=health_color),
            ])
        self.setList(out)


class DirectIdMonitorScreen(Screen):
    skin = ("""
    <screen name="DirectIdMonitorScreen" position="0,0" size="1920,1080" title="EPG ID Monitor" backgroundColor="%(BG)s" flags="wfNoBorder">
      <widget name="head" position="0,0" size="1920,104" backgroundColor="%(PANEL)s" />
      <widget name="title" position="46,18" size="900,42" font="Regular;34" foregroundColor="%(WHITE)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="summary" position="970,26" size="860,36" font="Regular;22" halign="right" foregroundColor="%(MUTED_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="hint" position="48,66" size="1780,26" font="Regular;18" foregroundColor="%(DIM_TEXT)s" backgroundColor="%(PANEL)s" transparent="1" />
      <widget name="list" position="44,126" size="1832,826" scrollbarMode="showOnDemand" backgroundColor="%(PANEL_ALT)s" />
      <widget name="footer" position="0,990" size="1920,90" backgroundColor="%(FOOTER_PANEL)s" />
      <widget name="key_red" position="42,1008" size="330,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_green" position="405,1008" size="470,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
      <widget name="key_blue" position="930,1008" size="750,52" font="Regular;23" foregroundColor="%(WHITE)s" backgroundColor="%(FOOTER_PANEL)s" transparent="1" />
    </screen>""") % theme.__dict__

    def __init__(self, session, config=None, initial_filter="all"):
        Screen.__init__(self, session)
        self.config = config
        self.filter_mode = str(initial_filter or "all").strip().lower()
        self._repair_seen_running = False
        self._github_seen_running = False
        self._last_cache_stamp = (0, 0)
        self["head"]=Label(""); self["footer"]=Label("")
        self["title"]=Label("ZERO EPG IDs" if self.filter_mode == "zero" else "EPG SOURCE ID MONITOR")
        self["summary"]=Label("")
        self["hint"]=Label("Grouped by bouquet/country • EPG ID = SOURCE.CHANNEL • green = future EPG • red = zero EPG")
        try:
            _id_colors = bool(self.config.get_color_id_health()) if self.config is not None else True
        except Exception:
            _id_colors = True
        self["list"]=IdMonitorList(health_colors=_id_colors)
        self["key_red"]=Label("Back")
        self["key_green"]=Label("Refresh GitHub IDs")
        self["key_blue"]=Label("Repair unmapped / wrong IDs")
        self["actions"]=ActionMap(["OkCancelActions","ColorActions"], {
            "cancel":self.close,"red":self.close,"green":self._sync,"ok":self._sync,
            "blue":self._repair,
        }, -1)
        self._timer=eTimer()
        cb=self._timer.callback if hasattr(self._timer,"callback") else self._timer.timeout.get()
        cb.append(self._poll)
        self._timer.start(850, False)
        self.onClose.append(self._cleanup)
        self._load()

    @staticmethod
    def _live_service_refs():
        refs=set()
        try:
            sat,_ = channel_registry.load_satellite_registry(force=False)
        except Exception:
            sat=[]
        try:
            iptv,_ = channel_registry.load_iptv_registry(force=False)
        except Exception:
            iptv=[]
        for service in list(sat or []) + list(iptv or []):
            ref=channel_registry.canonical_service_ref((service or {}).get("ref"))
            if ref:
                refs.add(ref)
        return refs

    @staticmethod
    def _active_owner_index(live):
        """Return the one-owner SRP decisions keyed by (source_id, channel_id).

        rc65: EPG ID Monitor must reflect the global SRP master, not every
        per-source candidate map. The master engine already reduces all claims
        to exactly one active source/channel owner per canonical receiver
        ServiceRef. Alternative source candidates therefore never appear as
        additional MAPPED rows in this screen.
        """
        try:
            state=srp_master_engine.load_state() or {}
            service_map=state.get("service_map") or {}
            if not service_map:
                try:
                    srp_master_engine.recover_state_from_index(force=False)
                    state=srp_master_engine.load_state() or {}
                    service_map=state.get("service_map") or {}
                except Exception:
                    service_map={}
        except Exception:
            service_map={}
        owners={}
        for raw_ref, claims in (service_map or {}).items():
            cref=channel_registry.canonical_service_ref(raw_ref) or str(raw_ref or "").strip()
            if not cref or (live and cref not in live):
                continue
            rows=claims if isinstance(claims,list) else [claims]
            if not rows:
                continue
            # srp_master_engine._reduce_service_map() guarantees one winner.
            row=dict(rows[0] or {})
            sid=str(row.get("source_id") or "").strip().lower()
            cid=str(row.get("channel_id") or "").strip().casefold()
            if sid and cid:
                owners.setdefault((sid,cid),set()).add(cref)
        return owners

    def _map_states(self):
        index=srp_channel_map.load_index() or {}
        live=self._live_service_refs()
        owners=self._active_owner_index(live)
        out={}
        for sid,_label,_stem in github_direct_sync.DIRECT_SOURCES:
            sid_key=str(sid or "").strip().lower()
            row=((index.get("sources") or {}).get(sid) or (index.get("sources") or {}).get(sid_key) or {})
            path=str(row.get("map_path") or "")
            try:
                mapping=srp_channel_map._parse_channels_xml_preserve(path) if path else {}
            except Exception:
                mapping={}
            states={}
            for cid, refs in (mapping or {}).items():
                cid_key=str(cid or "").casefold()
                clean=[channel_registry.canonical_service_ref(x) for x in (refs or [])]
                clean=[x for x in clean if x]
                live_refs=[x for x in clean if x in live]
                owner_refs=owners.get((sid_key,cid_key)) or set()
                if owner_refs and (not live_refs or bool(set(live_refs) & set(owner_refs))):
                    states[cid_key]="MAPPED"
                elif live_refs:
                    # Valid candidate map, but another source owns the receiver
                    # service globally. Keep it visible as fallback, never as
                    # a second active mapping.
                    states[cid_key]="ALT"
                else:
                    states[cid_key]="WRONG"
            # A fresh master decision is authoritative even if the tiny
            # per-source channels.xml cache is older than the master state.
            for owner_sid, owner_cid in owners:
                if owner_sid == sid_key:
                    states[owner_cid]="MAPPED"
            out[sid]=states
        return out

    def _load(self):
        data=github_direct_sync.load() or {}
        self._last_cache_stamp=(int(data.get("checked") or 0), int(data.get("id_monitor_checked") or 0))
        mon=data.get("id_monitor") or {}
        map_states=self._map_states()
        rows=[]
        source_order={sid: idx for idx,(sid,_label,_stem) in enumerate(github_direct_sync.DIRECT_SOURCES)}
        for sid,label,stem in github_direct_sync.DIRECT_SOURCES:
            states=map_states.get(sid) or {}
            for item in mon.get(stem,[]) or []:
                x=dict(item)
                friendly=x.get("name") or x.get("id") or ""
                # UI-only identity: show SOURCE.CHANNEL while retaining the
                # original XMLTV ID untouched for all mapping/import logic.
                x["name"]=friendly
                x["source_label"]=label
                x["display_id"]=_display_epg_id(label, friendly, x.get("id"))
                x["group"]=_group_for_epg_id(label, friendly, x.get("id"))
                x["_source_order"]=source_order.get(sid,999)
                health=github_direct_sync.id_health(sid, x.get("id"), data=data)
                x["_health"]=health
                if health == "NO_EPG":
                    x["map_state"]="NO EPG"
                else:
                    x["map_state"]=states.get(str(x.get("id") or "").casefold(), "UNMAPPED")
                x["source_id"]=sid
                rows.append(x)
        if self.filter_mode == "zero":
            rows = [x for x in rows if str(x.get("_health") or "").upper() == "NO_EPG"]
        order={"NO EPG":0,"WRONG":1,"UNMAPPED":2,"ALT":3,"MAPPED":4}
        # Group first by major bouquet/provider, then by country for the rest.
        # Source ownership and raw XMLTV IDs are not modified.
        rows.sort(key=lambda x:(_GROUP_RANK.get(str(x.get("group") or "OTHER MENA"),999),
                                str(x.get("group") or "").casefold(),
                                int(x.get("_source_order") or 0),
                                order.get(str(x.get("map_state") or ""),4),
                                0 if int(x.get("future_programmes") or 0)<=0 else 1,
                                str(x.get("name") or "").casefold(), str(x.get("id") or "").casefold()))
        display_rows=[]
        current_group=None
        group_count=0
        for item in rows:
            group=str(item.get("group") or "OTHER MENA")
            if group != current_group:
                group_count=sum(1 for r in rows if str(r.get("group") or "OTHER MENA") == group)
                display_rows.append({"_group_header":True,"group":group,"count":group_count})
                current_group=group
            display_rows.append(item)
        self["list"].set_rows(display_rows)
        green=sum(1 for x in rows if str(x.get("_health") or "")=="OK")
        red=sum(1 for x in rows if str(x.get("_health") or "")=="NO_EPG")
        unmapped=sum(1 for x in rows if str(x.get("map_state") or "")=="UNMAPPED")
        wrong=sum(1 for x in rows if str(x.get("map_state") or "")=="WRONG")
        alt=sum(1 for x in rows if str(x.get("map_state") or "")=="ALT")
        mapped=sum(1 for x in rows if str(x.get("map_state") or "")=="MAPPED")
        if self.filter_mode == "zero":
            self["summary"].setText("%d ZERO EPG IDs • red = proven zero future programmes" % red)
        else:
            self["summary"].setText("%d mapped • %d ALT • %d green • %d red • %d unmapped • %d wrong • %d IDs" %
                                    (mapped,alt,green,red,unmapped,wrong,len(rows)))

    def _sync(self):
        if github_direct_sync.sync_async(force=True,min_age=0):
            self.session.open(MessageBox,
                "GitHub ID refresh started. The list will update automatically when the cache is refreshed.",
                MessageBox.TYPE_INFO, timeout=3)
        else:
            self.session.open(MessageBox,"GitHub sync is already running.",MessageBox.TYPE_INFO,timeout=2)

    def _repair(self):
        if direct_id_repair.is_running():
            self.session.open(MessageBox,"ID repair is already running.",MessageBox.TYPE_INFO,timeout=2)
            return
        if direct_id_repair.repair_async(config=self.config):
            self._repair_seen_running=True
            self["summary"].setText("Repairing direct IDs… current manual mappings are protected")
            self.session.open(MessageBox,
                "ID repair started in background.\n\nIt refreshes current IDs, removes stale automatic IDs, quarantines only zero-EPG IDs, then rebuilds receiver mappings. Manual mappings are preserved.",
                MessageBox.TYPE_INFO, timeout=5)

    def _poll(self):
        if github_direct_sync.is_running():
            self._github_seen_running=True
        elif self._github_seen_running:
            self._github_seen_running=False
            self._load()

        if direct_id_repair.is_running():
            self._repair_seen_running=True
            self["summary"].setText("Repairing direct IDs…")
            return
        if self._repair_seen_running:
            self._repair_seen_running=False
            result=direct_id_repair.last_result() or {}
            self._load()
            if result:
                msg=("ID repair %s\n\nStale AUTO removed: %d\nRed / no EPG: %d\nMapped services: %d\nUnresolved IDs: %d" % (
                    "completed" if result.get("ok") else "needs review",
                    int(result.get("removed_stale_auto") or 0), int(result.get("red_no_epg") or 0),
                    int(result.get("mapped_service_refs") or 0), int(result.get("unresolved_ids") or 0)))
                self.session.open(MessageBox,msg,MessageBox.TYPE_INFO if result.get("ok") else MessageBox.TYPE_ERROR,timeout=7)
            return

        # Detect a cache update made elsewhere without re-scanning maps/lamedb
        # every 850 ms. Only repaint when the tiny status file timestamp moves.
        data=github_direct_sync.load() or {}
        stamp=(int(data.get("checked") or 0), int(data.get("id_monitor_checked") or 0))
        if stamp != self._last_cache_stamp:
            self._load()

    def _cleanup(self):
        try:self._timer.stop()
        except Exception:pass

try:
    DirectIdMonitorScreen.skin=adapt_skin(DirectIdMonitorScreen.skin)
except Exception:
    pass
