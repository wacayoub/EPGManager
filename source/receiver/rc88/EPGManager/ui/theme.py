# -*- coding: utf-8 -*-
"""EPGManager NEO Dark Comfortable visual system for OpenATV.

Enigma2 colours use #AARRGGBB where AA is the transparency level
(00 opaque, FF fully transparent).  The rc53 palette keeps the current TV service only faintly visible behind
EPGManager.  Large surfaces are now much more opaque, while lists, cards and
the active selection keep strong contrast for real-TV viewing distance.
"""
BG = "#000B1119"; BG_HEX_INT = 0x000B1119
HEADER = "#000F1925"; HEADER_HEX_INT = 0x000F1925
PANEL = HEADER; PANEL_HEX_INT = HEADER_HEX_INT
PANEL_ALT = "#00172230"; PANEL_ALT_HEX_INT = 0x00172230
PANEL_BLUE = "#00132437"; PANEL_BLUE_HEX_INT = 0x00132437
PANEL_AI = "#00132232"; PANEL_AI_HEX_INT = 0x00132232
PANEL_ROW = "#00182432"; PANEL_ROW_HEX_INT = 0x00182432
PANEL_ROW_ALT = "#001C2A3A"; PANEL_ROW_ALT_HEX_INT = 0x001C2A3A
PANEL_SELECTED = "#00265570"; PANEL_SELECTED_HEX_INT = 0x00265570
PANEL_DISABLED = "#00131B26"; PANEL_DISABLED_HEX_INT = 0x00131B26
NAV_PANEL = "#000F1925"; NAV_PANEL_HEX_INT = 0x000F1925
FOOTER_PANEL = "#000D1722"; FOOTER_PANEL_HEX_INT = 0x000D1722
CARD = "#0016212F"; CARD_HEX_INT = 0x0016212F
BADGE_BG = "#001E3341"; BADGE_BG_HEX_INT = 0x001E3341
NAV_ACTIVE = "#00265570"; NAV_ACTIVE_HEX_INT = 0x00265570
PROGRESS_TRACK = "#00253240"; PROGRESS_TRACK_HEX_INT = 0x00253240

# Smart Mapping uses a low-transparency dark layer because four large list
# panes are visible at once.  The active row is fully opaque for remote use.
GLASS_BG = "#000B1119"
GLASS_PANEL = "#00172330"
GLASS_RULE = "#10404A52"
GLASS_RULE_SOFT = "#18323B42"
GLASS_SELECTED = "#00265570"


TEXT = "#EDF6FA"; TEXT_INT = 0xEDF6FA
MUTED_TEXT = "#C1D2DC"; MUTED_TEXT_INT = 0xC1D2DC
DIM_TEXT = "#A4BAC8"; DIM_TEXT_INT = 0xA4BAC8
WHITE = "#FFFFFF"; WHITE_INT = 0xFFFFFF
RULE = "#DCE6F2"; RULE_INT = 0xDCE6F2
RULE_SOFT = "#294352"; RULE_SOFT_INT = 0x294352

ACCENT_PRIMARY = "#20C4D8"; ACCENT_PRIMARY_INT = 0x20C4D8
ACCENT_SECONDARY = "#45D9E7"; ACCENT_SECONDARY_INT = 0x45D9E7
STATUS_GREEN = "#56DBAE"; STATUS_GREEN_INT = 0x56DBAE
STATUS_YELLOW = "#F5C75B"; STATUS_YELLOW_INT = 0xF5C75B
STATUS_ORANGE = "#FFB66D"; STATUS_ORANGE_INT = 0xFFB66D
STATUS_VIOLET = "#8A63FF"; STATUS_VIOLET_INT = 0x8A63FF
STATUS_RED = "#FF8290"; STATUS_RED_INT = 0xFF8290
STATUS_GREY = "#8EA6C4"; STATUS_GREY_INT = 0x8EA6C4

CARD_RAILS = (STATUS_GREEN, ACCENT_SECONDARY, ACCENT_PRIMARY, STATUS_VIOLET,
              STATUS_YELLOW, STATUS_ORANGE, STATUS_RED)

BTN_RED = "#D94B5A"
BTN_GREEN = "#18A77B"
BTN_YELLOW = "#C7A12E"
BTN_BLUE = "#14879A"
BTN_TEXT = "#FFFFFF"


def key_bar_skin(name, x, y, width, height, color, font_size=28):
    rail_w = 7
    gap = 15
    return (
        '<widget name="%s_bar" position="%d,%d" size="%d,%d" backgroundColor="%s" />\n'
        '        <widget name="%s" position="%d,%d" size="%d,%d" font="Regular;%d" '
        'foregroundColor="%s" backgroundColor="%s" transparent="0" valign="center" />\n'
    ) % (name, x, y, rail_w, height, color,
         name, x + rail_w + gap, y, max(1, width - rail_w - gap), height,
         font_size, TEXT, FOOTER_PANEL)
