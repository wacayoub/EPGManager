#!/bin/sh
# EPGManager rc80 preinstall: fail closed unless the working code can be recovered.
set -eu
umask 077
PLUGIN_DIR="${EPGMGR_PLUGIN_DIR:-/usr/lib/enigma2/python/Plugins/Extensions/EPGManager}"
CONFIG_DIR="${EPGMGR_CONFIG_DIR:-/etc/enigma2}"
BACKUP_ROOT="${EPGMGR_BACKUP_ROOT:-/etc/epgmanager_epg/backups}"
# Default to NO-GO on real upgrades. A supervised installer may proceed only
# after independently verifying an exact rc79.1 rollback IPK and documenting
# its package-manager recovery path. Ordinary Online Update must not bypass.
if [ "${1:-}" = "upgrade" ] && [ "${EPGMGR_RC80_SUPERVISED:-0}" != "1" ]; then
    echo 'EPGManager rc80: UPGRADE BLOCKED — previous IPK and opkg rollback not certified' >&2
    exit 90
fi
STAMP="$(date +%Y%m%d-%H%M%S)-$"
DEST="$BACKUP_ROOT/pre-rc80-$STAMP"
mkdir -p "$DEST"
chmod 700 "$DEST"
if [ -d "$PLUGIN_DIR" ]; then
    tar -czf "$DEST/plugin.tar.gz.part" -C "$(dirname "$PLUGIN_DIR")" "$(basename "$PLUGIN_DIR")"
    test -s "$DEST/plugin.tar.gz.part" || { echo 'EPGManager: no backup generated' >&2; exit 80; }
    mv "$DEST/plugin.tar.gz.part" "$DEST/plugin.tar.gz"
    chmod 600 "$DEST/plugin.tar.gz"
    # Fail closed when the backup is corrupt or omits Python modules.
    tar -tzf "$DEST/plugin.tar.gz" >/dev/null || {
        echo 'EPGManager: backup integrity check failed' >&2; exit 80;
    }
    tar -tzf "$DEST/plugin.tar.gz" | grep -Fxq "EPGManager/plugin.py" || {
        echo 'EPGManager: plugin.py missing in backup' >&2; exit 80;
    }
    tar -tzf "$DEST/plugin.tar.gz" | grep -Fxq "EPGManager/version.py" || {
        echo 'EPGManager: version.py missing in backup' >&2; exit 80;
    }
    actual="$(find "$PLUGIN_DIR" -type f -name '*.py' | wc -l | tr -d ' ')"
    archived="$(tar -tzf "$DEST/plugin.tar.gz" | grep -c '\.py$' || true)"
    [ "$actual" = "$archived" ] || {
        echo "EPGManager: backup Python file count mismatch: $actual/$archived" >&2; exit 80;
    }
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
