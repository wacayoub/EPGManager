#!/bin/sh
# EPGManager rc80 preinstall: fail closed unless the working code can be recovered.
set -eu
umask 077
PLUGIN_DIR="${EPGMGR_PLUGIN_DIR:-/usr/lib/enigma2/python/Plugins/Extensions/EPGManager}"
CONFIG_DIR="${EPGMGR_CONFIG_DIR:-/etc/enigma2}"
BACKUP_ROOT="${EPGMGR_BACKUP_ROOT:-/etc/epgmanager_epg/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
DEST="$BACKUP_ROOT/pre-rc80-$STAMP"
mkdir -p "$DEST"
chmod 700 "$DEST"
if [ -d "$PLUGIN_DIR" ]; then
    tar -czf "$DEST/plugin.tar.gz.part" -C "$(dirname "$PLUGIN_DIR")" "$(basename "$PLUGIN_DIR")"
    test -s "$DEST/plugin.tar.gz.part" || { echo 'EPGManager: no backup generated' >&2; exit 80; }
    mv "$DEST/plugin.tar.gz.part" "$DEST/plugin.tar.gz"
    chmod 600 "$DEST/plugin.tar.gz"
fi
mkdir -p "$DEST/config"
for f in "$CONFIG_DIR"/epgmanager*.json "$CONFIG_DIR"/epgmanager.conf; do
    [ -f "$f" ] || continue
    cp -p "$f" "$DEST/config/$(basename "$f")"
done
if [ -f "$CONFIG_DIR/settings" ]; then
    grep '^config\.plugins\.epgmanager\.' "$CONFIG_DIR/settings" > "$DEST/config/epgmanager_settings.conf" || true
fi
printf 'Prepared EPGManager recovery snapshot: %s\n' "$DEST"
exit 0
