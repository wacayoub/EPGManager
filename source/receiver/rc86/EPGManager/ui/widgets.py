# -*- coding: utf-8 -*-
"""Source list renderer for the Matrix/OpenATV dashboard.

v2.3 uses one continuous band per source instead of a grid of disconnected
cells.  The cursor (highlighted row) and the update selection state are kept
visually distinct: the row highlight follows navigation; a compact cyan box
marks sources selected for update.
"""
from enigma import eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_VALIGN_CENTER
from Components.MenuList import MenuList
from .compat import MultiContentEntryText
from ..core.manager import (
    STATUS_IDLE, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED, STATUS_WARNING,
    STATUS_CANCELLED, STATUS_COOLDOWN,
)
from . import theme
from .compat import scale_y, ui_gFont

COLOR_BG_SEL = theme.PANEL_SELECTED_HEX_INT
COLOR_ROW = theme.PANEL_ROW_HEX_INT
COLOR_ROW_ALT = theme.PANEL_ROW_ALT_HEX_INT
COLOR_DISABLED = theme.PANEL_DISABLED_HEX_INT
COLOR_TEXT = theme.TEXT_INT
COLOR_MUTED = theme.MUTED_TEXT_INT
COLOR_DIM = theme.DIM_TEXT_INT
COLOR_GREEN = theme.STATUS_GREEN_INT
COLOR_YELLOW = theme.STATUS_YELLOW_INT
COLOR_RED = theme.STATUS_RED_INT
COLOR_GREY = theme.STATUS_GREY_INT
COLOR_ORANGE = theme.STATUS_ORANGE_INT
COLOR_CYAN = theme.ACCENT_SECONDARY_INT
COLOR_WHITE = theme.WHITE_INT

STATUS_COLOR = {
    STATUS_SUCCESS: COLOR_GREEN,
    STATUS_RUNNING: COLOR_YELLOW,
    STATUS_FAILED: COLOR_RED,
    STATUS_WARNING: COLOR_ORANGE,
    STATUS_CANCELLED: COLOR_ORANGE,
    STATUS_COOLDOWN: COLOR_ORANGE,
    STATUS_IDLE: COLOR_GREY,
}
STATUS_LABEL = {
    STATUS_SUCCESS: "UPDATED",
    STATUS_RUNNING: "UPDATING...",
    STATUS_FAILED: "FAILED",
    STATUS_WARNING: "WARNING",
    STATUS_CANCELLED: "CANCELLED",
    STATUS_COOLDOWN: "WAIT",
    STATUS_IDLE: "IDLE",
}

ROW_HEIGHT = 67
LIST_WIDTH = 1112
COL_RAIL = 0
COL_CHECK = 20
COL_NAME = 92
COL_STATUS = 397
COL_PROGS = 574
COL_DAYS = 726
COL_DURATION = 900


def _days_text(st):
    days = st.get("days_covered") or 0
    if not days:
        return "-"
    return "%d day%s" % (days, "" if days == 1 else "s")


def _duration_text(st):
    value = st.get("duration_seconds")
    if value is None:
        return "-"
    try:
        value = float(value)
    except Exception:
        return "-"
    if value >= 60:
        return "%dm %02ds" % (int(value // 60), int(value % 60))
    return "%.1fs" % value


def _cell(pos_x, width, text, font, color, row_bg, align=RT_HALIGN_LEFT):
    """A gap-free row cell so the selected band reads as one continuous row."""
    return MultiContentEntryText(
        pos=(pos_x, 4), size=(width, ROW_HEIGHT - 8), font=font,
        flags=align | RT_VALIGN_CENTER, text=text,
        color=color, color_sel=color,
        backcolor=row_bg, backcolor_sel=COLOR_BG_SEL,
    )


def build_source_row(status_dict, selected, enabled, row_index=0):
    status = status_dict.get("status", STATUS_IDLE)
    status_color = STATUS_COLOR.get(status, COLOR_GREY)
    row_bg = COLOR_ROW if row_index % 2 == 0 else COLOR_ROW_ALT
    if not enabled:
        row_bg = COLOR_DISABLED
        status_color = COLOR_DIM

    name = status_dict.get("name", status_dict.get("id", "Source"))
    if not enabled:
        name = "%s  (disabled)" % name
    count = status_dict.get("program_count") or 0
    progs = str(count) if count else "-"

    # The selection marker is a real compact tile rather than textual [X].
    # The list cursor still highlights the entire row independently.
    check_bg = COLOR_CYAN if selected else theme.PANEL_ALT_HEX_INT
    check_text = "X" if selected else ""
    check_fg = COLOR_WHITE if selected else COLOR_MUTED
    rail_color = COLOR_CYAN if selected else row_bg

    return [
        status_dict,
        # One full-width base band first.  This removes the small transparent
        # gaps that made the cursor/selected row look stepped or misaligned
        # on OpenATV/Metrix.  All row content is then drawn on top of it.
        MultiContentEntryText(
            pos=(0, 4), size=(LIST_WIDTH - 4, ROW_HEIGHT - 8), font=1,
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text="",
            color=COLOR_TEXT, color_sel=COLOR_TEXT,
            backcolor=row_bg, backcolor_sel=COLOR_BG_SEL),
        MultiContentEntryText(
            pos=(COL_RAIL, 4), size=(7, ROW_HEIGHT - 8), font=1,
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text="",
            color=rail_color, backcolor=rail_color, backcolor_sel=COLOR_CYAN),
        MultiContentEntryText(
            pos=(COL_CHECK, 12), size=(50, ROW_HEIGHT - 24), font=1,
            flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER, text=check_text,
            color=check_fg, color_sel=COLOR_WHITE,
            backcolor=check_bg, backcolor_sel=(COLOR_CYAN if selected else COLOR_BG_SEL)),
        _cell(COL_NAME, COL_STATUS - COL_NAME, name, 0,
              COLOR_TEXT if enabled else COLOR_DIM, row_bg),
        _cell(COL_STATUS, COL_PROGS - COL_STATUS, STATUS_LABEL.get(status, str(status)), 1,
              status_color, row_bg),
        _cell(COL_PROGS, COL_DAYS - COL_PROGS, progs, 0,
              COLOR_TEXT if enabled else COLOR_DIM, row_bg),
        _cell(COL_DAYS, COL_DURATION - COL_DAYS, _days_text(status_dict), 0,
              COLOR_TEXT if enabled else COLOR_DIM, row_bg),
        _cell(COL_DURATION, LIST_WIDTH - COL_DURATION - 36, _duration_text(status_dict), 0,
              COLOR_TEXT if enabled else COLOR_DIM, row_bg),
        MultiContentEntryText(
            pos=(LIST_WIDTH - 36, 4), size=(32, ROW_HEIGHT - 8), font=0,
            flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER, text=">",
            color=COLOR_MUTED, color_sel=COLOR_WHITE,
            backcolor=row_bg, backcolor_sel=COLOR_BG_SEL),
        MultiContentEntryText(
            pos=(0, ROW_HEIGHT - 2), size=(LIST_WIDTH - 4, 2), font=1,
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text="",
            color=theme.RULE_SOFT_INT, color_sel=theme.RULE_SOFT_INT,
            backcolor=theme.RULE_SOFT_INT, backcolor_sel=theme.RULE_SOFT_INT),
    ]


class SourceStatusList(MenuList):
    def __init__(self, entries=None):
        MenuList.__init__(self, entries or [], False, eListboxPythonMultiContent)
        self.l.setFont(0, ui_gFont("Regular", 29))
        self.l.setFont(1, ui_gFont("Regular", 24))
        self.l.setItemHeight(scale_y(ROW_HEIGHT))

    def update_rows(self, statuses, selected_ids, enabled_map):
        rows = [build_source_row(st, st.get("id") in selected_ids,
                                 enabled_map.get(st.get("id"), True), i)
                for i, st in enumerate(statuses)]
        try:
            current_index = self.getCurrentIndex()
        except Exception:
            current_index = 0
        self.setList(rows)
        self._restore_index(current_index, len(rows))

    def _restore_index(self, index, row_count):
        if row_count <= 0:
            return
        index = max(0, min(index, row_count - 1))
        try:
            if self.instance is not None:
                self.instance.moveSelectionTo(index)
        except Exception:
            pass

    def get_selected_status_dict(self):
        current = self.getCurrent()
        return current[0] if current else None
