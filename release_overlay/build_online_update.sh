#!/usr/bin/env bash
set -euo pipefail

TARGET_VERSION="2026.10.04-rc76"
TARGET_ASSET="EPGManager_rc76_GITHUB_ONLINE_UPDATE.ipk"

BASE_URL="$(python3 -c 'import json; print(json.load(open("update.json", encoding="utf-8"))["url"])')"
BASE_VERSION="$(python3 -c 'import json; print(json.load(open("update.json", encoding="utf-8"))["version"])')"
echo "Patching EPGManager $BASE_VERSION -> $TARGET_VERSION"

rm -rf work
mkdir -p work/ipk work/control work/data
curl -fL --retry 4 --retry-delay 2 "$BASE_URL" -o work/base.ipk

(
  cd work/ipk
  ar x ../base.ipk
)

CONTROL_ARCHIVE="$(find work/ipk -maxdepth 1 -type f -name 'control.tar.*' | head -n1)"
DATA_ARCHIVE="$(find work/ipk -maxdepth 1 -type f -name 'data.tar.*' | head -n1)"
test -n "$CONTROL_ARCHIVE"
test -n "$DATA_ARCHIVE"
tar -xf "$CONTROL_ARCHIVE" -C work/control
tar -xf "$DATA_ARCHIVE" -C work/data

PLUGIN_ROOT="$(find work/data -type f -path '*/Plugins/Extensions/EPGManager/plugin.py' -printf '%h\n' | head -n1)"
test -n "$PLUGIN_ROOT"
cp release_overlay/online_update.py "$PLUGIN_ROOT/online_update.py"

python3 - "$PLUGIN_ROOT/plugin.py" "$PLUGIN_ROOT/version.py" "$TARGET_VERSION" <<'PY'
import pathlib
import re
import sys

plugin_path = pathlib.Path(sys.argv[1])
version_path = pathlib.Path(sys.argv[2])
target = sys.argv[3]

text = plugin_path.read_text(encoding="utf-8")
marker = "# EPGMANAGER_ONLINE_UPDATE"

if marker not in text:
    idx = text.find("\ndef Plugins(")
    if idx < 0:
        raise SystemExit("Could not find Plugins() in plugin.py")

    helper = '''

# EPGMANAGER_ONLINE_UPDATE
def online_update_main(session, **kwargs):
    """Open the GitHub-backed online update screen."""
    from .online_update import EPGManagerOnlineUpdate
    session.open(EPGManagerOnlineUpdate)
'''
    text = text[:idx] + helper + text[idx:]

    plugins_idx = text.find("def Plugins(")
    tail = text[plugins_idx:]
    matches = list(re.finditer(r"(?m)^    \]\s*$", tail))
    if not matches:
        raise SystemExit("Could not locate PluginDescriptor list closing bracket")

    close_at = plugins_idx + matches[-1].start()
    descriptor = '''
        PluginDescriptor(
            name="EPG Manager Online Update",
            description="Check and install the latest EPG Manager release from GitHub",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon=icon,
            fnc=online_update_main,
        ),
'''
    text = text[:close_at] + descriptor + text[close_at:]

plugin_path.write_text(text, encoding="utf-8")

vtext = version_path.read_text(encoding="utf-8")
vtext, count = re.subn(
    r'(?m)^(__version__\s*=\s*)["\x27][^"\x27]+["\x27]',
    lambda m: m.group(1) + '"' + target + '"',
    vtext,
    count=1,
)
if count != 1:
    raise SystemExit("Could not update __version__ in version.py")

vtext = re.sub(
    r'(?m)^(__build__\s*=\s*)["\x27][^"\x27]+["\x27]',
    r'\1"2026.10.04.github-online-update"',
    vtext,
    count=1,
)
version_path.write_text(vtext, encoding="utf-8")
PY

python3 -m py_compile "$PLUGIN_ROOT/plugin.py" "$PLUGIN_ROOT/online_update.py" "$PLUGIN_ROOT/version.py"

CONTROL_FILE="$(find work/control -maxdepth 3 -type f -name control | head -n1)"
test -n "$CONTROL_FILE"
sed -Ei "s/^(Version:[[:space:]]*).*/\1$TARGET_VERSION/" "$CONTROL_FILE"

rm -f work/control.tar.gz work/data.tar.gz "work/$TARGET_ASSET"
tar --numeric-owner --owner=0 --group=0 -C work/control -czf work/control.tar.gz .
tar --numeric-owner --owner=0 --group=0 -C work/data -czf work/data.tar.gz .
printf '2.0\n' > work/debian-binary

(
  cd work
  ar r "$TARGET_ASSET" debian-binary control.tar.gz data.tar.gz
  ar t "$TARGET_ASSET"
)

SHA256="$(sha256sum "work/$TARGET_ASSET" | awk '{print $1}')"
SIZE="$(stat -c%s "work/$TARGET_ASSET")"
TAG="v$TARGET_VERSION"
NOTES="R76: GitHub permanent installer + built-in Online Update. update.json is checked online, IPK size and SHA-256 are verified, and GUI restart is offered after successful opkg installation."

if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "work/$TARGET_ASSET" --clobber
  gh release edit "$TAG" --title "EPGManager $TARGET_VERSION - GitHub Online Update" --notes "$NOTES"
else
  gh release create "$TAG" "work/$TARGET_ASSET" --title "EPGManager $TARGET_VERSION - GitHub Online Update" --notes "$NOTES"
fi

TARGET_VERSION="$TARGET_VERSION" TARGET_ASSET="$TARGET_ASSET" SHA256="$SHA256" SIZE="$SIZE" python3 - <<'PY'
import json
import os
import pathlib

v = os.environ["TARGET_VERSION"]
asset = os.environ["TARGET_ASSET"]
data = {
    "version": v,
    "build": "github-online-update",
    "url": "https://github.com/wacayoub/EPGManager/releases/download/v%s/%s" % (v, asset),
    "sha256": os.environ["SHA256"],
    "size": int(os.environ["SIZE"]),
    "notes": "R76: permanent GitHub installer and built-in Online Update with SHA-256 verification."
}
pathlib.Path("update.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add update.json
if ! git diff --cached --quiet; then
  git commit -m "Publish EPGManager $TARGET_VERSION online updater"
  git push
fi
