#!/bin/sh
# EPGManager rc80 postinstall: NEVER invalidate XMLTV, user mappings or preferences.
set -eu
PLUGIN_DIR="${EPGMGR_PLUGIN_DIR:-/usr/lib/enigma2/python/Plugins/Extensions/EPGManager}"
for path in plugin.py version.py ui/main.py ui/channel_mapping.py online_update.py \
    core/direct_native_import.py core/epg_cycle.py core/github_direct_stream.py core/github_match_health.py; do
    [ -s "$PLUGIN_DIR/$path" ] || { echo "EPGManager: missing $path" >&2; exit 81; }
done
python3 - "$PLUGIN_DIR" <<'PY'
from __future__ import print_function
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
version = (root / 'version.py').read_text(encoding='utf-8')
if '__version__ = "2026.10.14-rc80-beta1"' not in version or 'CONFIG_SCHEMA_VERSION = 15' not in version:
    sys.exit('EPGManager: unexpected version or configuration schema')
files = list(root.rglob('*.py'))
if len(files) != 138:
    sys.exit('EPGManager: incomplete modules (%d, expected 138)' % len(files))
for f in files:
    ast.parse(f.read_text(encoding='utf-8'), filename=str(f))
print('EPGManager: 138 modules validated, config/EPG data untouched')
PY
# Runtime GUI, scheduler and native importer must be tested after installation.
# A maintainer script must not launch Enigma2 modules or require optional
# Python helpers that may not be present in the target package.
printf '%s\n' 'EPGManager: package static verification complete; runtime acceptance still required.'
exit 0
