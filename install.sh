#!/bin/sh
set -eu

MANIFEST_URL="https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json"
MANIFEST="/tmp/epgmanager-update.json"
IPK="/tmp/epgmanager-latest.ipk"

fetch() {
    url="$1"
    out="$2"
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$out" "$url"
    elif command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    else
        echo "ERROR: wget or curl is required" >&2
        exit 1
    fi
}

echo "EPGManager: fetching latest release manifest..."
fetch "$MANIFEST_URL" "$MANIFEST"

eval "$(python3 - "$MANIFEST" <<'PY'
import json, shlex, sys
data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
for key in ("version", "url", "sha256", "size"):
    if key not in data:
        raise SystemExit("Missing key in update.json: " + key)
print("VERSION=" + shlex.quote(str(data["version"])))
print("URL=" + shlex.quote(str(data["url"])))
print("SHA256=" + shlex.quote(str(data["sha256"])))
print("SIZE=" + shlex.quote(str(data["size"])))
PY
)"

# Prevent the public manifest from downgrading a newer installed receiver.
# The public manifest may lag locally hotfixed builds (e.g. rc79.1 vs rc78).
# Unknown version formats are refused rather than guessed.
INSTALLED_VERSION="$(opkg status enigma2-plugin-extensions-epgmanager 2>/dev/null | awk '/^Version:[[:space:]]*/ {print $2; exit}')"
if [ -n "$INSTALLED_VERSION" ]; then
    python3 - "$INSTALLED_VERSION" "$VERSION" <<'PY'
import re
import sys

def rc_key(value):
    m = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})-rc(\d+)(?:\.(\d+))?", value)
    return tuple(int(x or 0) for x in m.groups()) if m else None

installed, available = sys.argv[1:]
current_key, remote_key = rc_key(installed), rc_key(available)
if current_key is None or remote_key is None:
    raise SystemExit("EPGManager: unknown version format; refusing unsafe install")
if remote_key <= current_key:
    raise SystemExit("EPGManager: GitHub has %s, installed is %s; downgrade/reinstall blocked" %
                     (available, installed))
PY
fi

echo "EPGManager: downloading version $VERSION..."
fetch "$URL" "$IPK"

python3 - "$IPK" "$SHA256" "$SIZE" <<'PY'
import hashlib, os, sys
path, expected_sha, expected_size = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3])
size = os.path.getsize(path)
if size != expected_size:
    raise SystemExit("Size mismatch: got %d expected %d" % (size, expected_size))
h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
actual = h.hexdigest().lower()
if actual != expected_sha:
    raise SystemExit("SHA256 mismatch: got %s expected %s" % (actual, expected_sha))
print("EPGManager: checksum OK")
PY

echo "EPGManager: installing $VERSION..."
opkg install "$IPK"

rm -f "$MANIFEST" "$IPK"
echo "EPGManager $VERSION installed successfully."
echo "Restart Enigma2 GUI to load the new version."
