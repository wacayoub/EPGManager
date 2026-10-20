"""Offline-only, SHA-locked presentation patch for the installed RC80 baseline.

Returns three Python modules; never connects to or writes to a receiver.
Mapping decisions, persistence, timers and native import code are untouched.
"""
import ast
import hashlib
import re

ORIGINAL = {
    "ui/main.py": "e92ff22cc09cc55d62e2578357059d98fd581cab5694180a0fab0e9a3702ea82",
    "ui/channel_mapping.py": "bcf159795a66028ee8c6f6ab68bce7b3abba9e2197968750905083c7c4f96df1",
    "ui/theme.py": "68853f1f7b2bbcbec434a0c05f8f8992e69a3744818e4f23b9e8c525900a27b6",
}


def one(text, old, new):
    if text.count(old) != 1:
        raise ValueError("readable UI source pattern drift")
    return text.replace(old, new, 1)


def corrected(source):
    if set(source) != set(ORIGINAL):
        raise ValueError("readable UI inventory drift")
    for name, pin in ORIGINAL.items():
        if hashlib.sha256(source[name]).hexdigest() != pin:
            raise ValueError("readable UI preimage drift: " + name)
    main = source["ui/main.py"].decode("utf-8")
    # Keep every widget, action and refresh interval in its existing position.
    for old, new in (("font=\"Regular;15\" foregroundColor=\"%(dim)s\"", "font=\"Regular;17\" foregroundColor=\"%(dim)s\""),
                     ("font=\"Regular;30\" foregroundColor=\"%(white)s\"", "font=\"Regular;32\" foregroundColor=\"%(white)s\""),
                     ("font=\"Regular;14\" foregroundColor=\"%(muted)s\"", "font=\"Regular;16\" foregroundColor=\"%(muted)s\"")):
        main = one(main, old, new)
    main = one(main, 'Label("Add Source")', 'Label("Sources")')
    main = one(main, 'self._age(imp.get("timestamp")) if imp else "no import yet"',
               'self._import_note(imp)')
    marker = "    @staticmethod\n    def _age(ts):"
    addition = '''    @staticmethod
    def _import_note(receipt):
        if not receipt:
            return "No import yet"
        try:
            stamp = float(receipt.get("timestamp") or 0)
            if stamp <= 0:
                return "Time unknown"
            return time.strftime("%d/%m %H:%M GMT+0", time.gmtime(stamp))
        except (TypeError, ValueError, OverflowError, OSError):
            return "Time unknown"

'''
    main = one(main, marker, addition + marker)

    mapping = source["ui/channel_mapping.py"].decode("utf-8")
    start = mapping.index("class SafeMatchPaneList(")
    end = mapping.index("class AutoScanResultList(", start)
    pane = mapping[start:end]
    pane = one(pane, 'ui_gFont("Regular", 18)', 'ui_gFont("Regular", 20)')
    pane = one(pane, 'ui_gFont("Regular", 16)', 'ui_gFont("Regular", 18)')
    pane = one(pane, '            # rc74: green text already means healthy. Do not waste the narrow\n            # right rail repeating an "OK" badge on every row.\n            health_text = ""',
               '            # Text accompanies colour for viewing at TV distance.\n            health_text = "EPG OK"')
    pane = one(pane, 'health_text = "?"', 'health_text = "CHECK"')
    # Fit seven-character warning labels at the larger font without overlap.
    pane = one(pane, 'size=(690,25)', 'size=(670,25)')
    pane = one(pane, 'pos=(735,0), size=(120,48)', 'pos=(715,0), size=(140,48)')
    mapping = mapping[:start] + pane + mapping[end:]

    theme = source["ui/theme.py"].decode("utf-8")
    palette = {
        "BG": "#000B1119", "GLASS_BG": "#000B1119",
        "GLASS_SELECTED": "#00265570", "PANEL_SELECTED": "#00265570",
        "NAV_ACTIVE": "#00265570", "TEXT": "#EDF6FA",
        "MUTED_TEXT": "#C1D2DC", "DIM_TEXT": "#A4BAC8",
        "STATUS_GREEN": "#56DBAE", "STATUS_YELLOW": "#F5C75B",
        "STATUS_ORANGE": "#FFB66D", "STATUS_RED": "#FF8290",
    }
    for name, value in palette.items():
        theme, count = re.subn(r'(^{0} = ")[^"]+(";)'.format(name),
                               lambda m: m[1] + value + m[2], theme, flags=re.M)
        if not count:  # GLASS constants have no trailing semicolon.
            theme, count = re.subn(r'(^{0} = ")[^"]+("$)'.format(name),
                                   lambda m: m[1] + value + m[2], theme, flags=re.M)
        if count != 1:
            raise ValueError("theme token drift: " + name)
    # Enigma2 XML and MultiContent must render the same shared palette.
    for name, value in re.findall(r'^(\w+) = "#([0-9A-Fa-f]{6,8})";', theme, re.M):
        theme = re.sub(r"\b" + name + r"_HEX_INT = 0x[0-9A-Fa-f]+", name + "_HEX_INT = 0x" + value, theme)
        theme = re.sub(r"\b" + name + r"_INT = 0x[0-9A-Fa-f]+", name + "_INT = 0x" + value, theme)
    result = {"ui/main.py": main, "ui/channel_mapping.py": mapping, "ui/theme.py": theme}
    for text in result.values():
        ast.parse(text)
    return {name: text.encode("utf-8") for name, text in result.items()}
