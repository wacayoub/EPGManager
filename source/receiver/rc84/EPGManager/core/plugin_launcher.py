# -*- coding: utf-8 -*-
"""
core/plugin_launcher.py

Lets EPG Manager's GUI jump directly into other installed plugins (EPG
Importer, JediEPGXtream) without hardcoding their internal module paths,
which aren't part of EPG Manager and could change between versions/images.

Approach: Enigma2's own PluginComponent (Components.PluginComponent.plugins)
already knows about every installed plugin, the same registry the
"Plugin Browser" screen (see your screenshot) reads from. We search that
registry by name/description substring and invoke the matching
PluginDescriptor exactly the way the Plugin Browser itself does
(`plugin(session=session)`), so this works for whichever build of
EPG-Importer / JediEPGXtream is actually installed on the box - it does
not need to know their code layout at all.

Picking the RIGHT descriptor (2026-08-09 fix)
----------------------------------------------
Checked the actual public source of several real EPG-Importer forks:
  - OpenPLi's `enigma2-plugin-extensions-epgimport`: the WHERE_EXTENSIONSMENU
    entry's fnc is `extensionsmenu()`, which just calls `main()` - SAFE, no
    import triggered.
  - The older oe-alliance `XMLTV-Import` fork: the WHERE_EXTENSIONSMENU
    entry is literally named "EPG-Importer Now" with `fnc=start_import` -
    UNSAFE, runs the import immediately on open. Its WHERE_PLUGINMENU entry
    is named "EPG-Importer" with `fnc=main` - SAFE.

Across every fork checked, WHERE_PLUGINMENU consistently maps to the safe
"just open the screen" entry point, while WHERE_EXTENSIONSMENU is a coin
flip depending on the fork. So the strategy is now:
  1. Prefer WHERE_PLUGINMENU matches over WHERE_EXTENSIONSMENU matches.
  2. Within either group, prefer a descriptor whose name/description
     doesn't contain an ACTION_WORD (now includes a standalone "now",
     which is what caught the "EPG-Importer Now" case above).
  3. Only if nothing else matches, fall back to whatever was found.

This is still fundamentally a heuristic - if a fork uses genuinely
different naming/behavior, this can still get it wrong. There is no way to
guarantee this from outside another plugin's code.

If no matching plugin is installed, a clear MessageBox is shown instead of
failing silently or crashing.
"""

import re

from .logger import get_logger

log = get_logger(__name__)

# Name/description substrings to match, in priority order. Both EPG-Importer
# and JediEPGXtream ship under a few different display names across images,
# so we check several plausible variants rather than assuming one exact
# string (see your Plugin Browser screenshot: "EPG-Importer", "JediEPGXtream").
KNOWN_TARGETS = {
    "epgimport": ["epg-importer", "epg importer", "epgimport"],
    "jediepgxtream": ["jediepgxtream", "jedi epgxtream", "jedi epg xtream", "epgxtream"],
}

# Words/phrases that suggest a plugin entry point performs an ACTION
# immediately on open (starts a download/import/scan) rather than just
# showing its normal screen. Checked as whole words via regex, since a bare
# substring check for "now" would also match inside unrelated words.
ACTION_WORDS = [
    r"\brun\b", r"\bnow\b", r"\bstart\b", r"\bmanual import\b",
    r"\bimport now\b", r"\bdownload now\b", r"\bforce\b",
    r"\bscan now\b", r"\bupdate now\b",
]
_ACTION_WORD_RE = re.compile("|".join(ACTION_WORDS), re.IGNORECASE)


def _looks_actiony(haystack):
    return bool(_ACTION_WORD_RE.search(haystack))


def _iter_candidate_plugins():
    """Yields (PluginDescriptor, where) for every installed plugin under
    the Extensions and Plugin menus - the same two lists the Plugin
    Browser screen shows."""
    try:
        from Components.PluginComponent import plugins as plugin_registry
        from Plugins.Plugin import PluginDescriptor
    except ImportError:
        return

    for where in (PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU):
        try:
            for p in plugin_registry.getPlugins(where):
                yield p, where
        except Exception:
            continue


def find_plugin(target_key):
    """Return the best installed PluginDescriptor matching
    KNOWN_TARGETS[target_key]: prefers WHERE_PLUGINMENU over
    WHERE_EXTENSIONSMENU, and within each prefers a non-"actiony" name.
    Returns None if nothing matches at all."""
    substrings = KNOWN_TARGETS.get(target_key, [])
    if not substrings:
        return None

    try:
        from Plugins.Plugin import PluginDescriptor
    except ImportError:
        return None

    matches = []  # (plugin, where, haystack)
    for p, where in _iter_candidate_plugins():
        haystack = ((getattr(p, "name", "") or "") + " " +
                    (getattr(p, "description", "") or "")).lower()
        if any(s in haystack for s in substrings):
            matches.append((p, where, haystack))

    if not matches:
        return None

    def rank(entry):
        _, where, haystack = entry
        where_rank = 0 if where == PluginDescriptor.WHERE_PLUGINMENU else 1
        action_rank = 1 if _looks_actiony(haystack) else 0
        return (where_rank, action_rank)

    matches.sort(key=rank)
    best = matches[0]

    if rank(best)[1] == 1:
        log.warning("Every installed descriptor matching '%s' looks like it "
                    "might trigger an action on open (no 'safe' variant "
                    "found) - opening the least-risky match anyway: %s",
                    target_key, best[0].name)

    return best[0]


def launch(session, target_key, friendly_name):
    """Find and open the target plugin. Shows a MessageBox if it isn't
    installed, instead of failing silently."""
    plugin = find_plugin(target_key)
    if plugin is None:
        log.warning("Could not find an installed plugin matching '%s'", target_key)
        try:
            from Screens.MessageBox import MessageBox
            session.open(
                MessageBox,
                "%s does not appear to be installed.\n\n"
                "Install it from the Plugin Browser first." % friendly_name,
                MessageBox.TYPE_INFO,
            )
        except Exception:
            pass
        return False

    try:
        plugin(session=session)
        return True
    except Exception:
        log.exception("Failed to launch plugin for target '%s'", target_key)
        try:
            from Screens.MessageBox import MessageBox
            session.open(
                MessageBox,
                "Could not launch %s (see EPG Manager log for details)." % friendly_name,
                MessageBox.TYPE_ERROR,
            )
        except Exception:
            pass
        return False

