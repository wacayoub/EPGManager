# -*- coding: utf-8 -*-
"""Runtime UI compatibility helpers for Enigma2 receivers.

The plugin UI is authored on a 1920x1080 canvas.  Enigma2 images may expose
1280x720, 1920x1080 or another desktop size depending on image/skin/receiver.
These helpers scale screen XML and MultiContent geometry at runtime without
changing matching/import logic.
"""
from __future__ import print_function
import re

BASE_W = 1920
BASE_H = 1080


def desktop_size():
    try:
        from enigma import getDesktop
        size = getDesktop(0).size()
        w, h = int(size.width()), int(size.height())
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return BASE_W, BASE_H


def _scales():
    w, h = desktop_size()
    return float(w) / BASE_W, float(h) / BASE_H


def scale_x(value):
    sx, _ = _scales()
    return int(round(float(value) * sx))


def scale_y(value):
    _, sy = _scales()
    return int(round(float(value) * sy))


def scale_font(value):
    sx, sy = _scales()
    return max(1, int(round(float(value) * min(sx, sy))))


def _pair(value, mode):
    parts = str(value).split(',')
    if len(parts) != 2:
        return value
    a, b = parts[0].strip(), parts[1].strip()
    if not re.match(r'^-?\d+$', a) or not re.match(r'^-?\d+$', b):
        return value
    if mode == 'position':
        return '%d,%d' % (scale_x(int(a)), scale_y(int(b)))
    return '%d,%d' % (max(1, scale_x(int(a))), max(1, scale_y(int(b))))


def adapt_skin(xml):
    """Scale one 1920x1080 plugin skin to the active Enigma2 desktop."""
    if not isinstance(xml, str):
        return xml
    w, h = desktop_size()
    if (w, h) == (BASE_W, BASE_H):
        return xml

    def pair_repl(match):
        key, value = match.group(1), match.group(2)
        return '%s="%s"' % (key, _pair(value, key))

    out = re.sub(r'\b(position|size)="([^"]+)"', pair_repl, xml)

    def font_repl(match):
        return 'font="%s;%d"' % (match.group(1), scale_font(int(match.group(2))))
    out = re.sub(r'font="([^";]+);(\d+)"', font_repl, out)

    def scalar_repl(match):
        key, value = match.group(1), int(match.group(2))
        scaled = scale_y(value) if key == 'itemHeight' else scale_font(value)
        if value > 0:
            scaled = max(1, scaled)
        return '%s="%d"' % (key, scaled)
    out = re.sub(r'\b(itemHeight|borderWidth)="(\d+)"', scalar_repl, out)
    return out


def MultiContentEntryText(*args, **kwargs):
    """Drop-in wrapper scaling MultiContent pos/size on non-FHD desktops."""
    from Components.MultiContent import MultiContentEntryText as _entry
    if 'pos' in kwargs and isinstance(kwargs.get('pos'), (tuple, list)) and len(kwargs['pos']) == 2:
        kwargs['pos'] = (scale_x(kwargs['pos'][0]), scale_y(kwargs['pos'][1]))
    if 'size' in kwargs and isinstance(kwargs.get('size'), (tuple, list)) and len(kwargs['size']) == 2:
        kwargs['size'] = (max(1, scale_x(kwargs['size'][0])), max(1, scale_y(kwargs['size'][1])))
    return _entry(*args, **kwargs)


def ui_gFont(name, size):
    from enigma import gFont as _gFont
    return _gFont(name, scale_font(size))
