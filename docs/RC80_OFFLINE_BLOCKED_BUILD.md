# RC80 offline sealed build — NO-GO

This step prepares a strictly non-installable archive for private, offline inspection. It does not touch the working Vu+ receiver, settings, mappings, EPG, updater or public releases.

## Inputs — keep private, never upload to GitHub/CI

- Exact RC79.1 private rescue IPK: SHA256 117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69 (NOT the incomplete AUTOMAPPING_SAFE IPK).
- rc79_manifest.json: original 138 Python files, with SHA256 and length; schema 15, receiver version 2026.10.08-rc79.1.
- A complete offline staged EPGManager directory: exactly 140 Python modules and 75 original non-Python files, preserving all original asset bytes and file modes.
- Optional reviewed-deltas.json for separately authorized private source hashes. Public channel_mapping.py and exactly two verified_epg modules must match the reviewed overlay byte-for-byte.

## Offline staging-host commands (NEVER on production Vu+)

Check only:

    python3 tools/build_rc80_offline_blocked_ipk.py --check --rescue-ipk /private/EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk --staged-plugin /private/staging/EPGManager --private-manifest /private/rc79_manifest.json --reviewed-deltas /private/reviewed-deltas.json

Create an un-installable inspection archive only, in an existing private output folder:

    python3 tools/build_rc80_offline_blocked_ipk.py --build --rescue-ipk /private/EPGManager_rc79_1_LOCAL_RESCUE_UNTESTED.ipk --staged-plugin /private/staging/EPGManager --private-manifest /private/rc79_manifest.json --reviewed-deltas /private/reviewed-deltas.json --output /private/output/EPGManager_RC80_OFFLINE_BLOCKED_UNTESTED.ipk

The file is created mode 0600, never overwritten, verified for content and a preinstall hook that **always exits 90**. It is **not an installable beta**. This is not a live rescue test and has never been exercised with the owner's full private inputs during CI.

## Exact version and recovery-hook safety gates

The staged Python file `version.py` must literally declare `__version__ = "2026.10.15-rc80-offline-blocked"`, exactly matching the generated IPK's `Version:` control field. A different reviewed RC80 version is **not** sufficient: the offline assembly refuses mismatches.

The pinned RC79.1 rescue must contain `preinst`, `postinst`, `prerm` and `postrm` with the expected passive rescue content and executable modes. Missing, modified, unsafe or non-executable hooks abort construction. The resulting RC80 inspection archive replaces `preinst` with an unconditional `exit 90`; this block is independently exercised in the CI's isolated synthetic shell test. **Do not try this IPK on production to see whether it refuses:** package managers may have side effects before reporting a failed installation.

The new code and CI are still synthetic validation: they do not establish a successful `opkg` downgrade, package-database restoration, Enigma2 reboot or native EPG import.

## Hardware handoff

VuLink's currently visible GitHub relay issue contains outbound request comments but does not independently establish a completed receiver-side reply or hardware certification. Hardware tests remain blocked until a **separate non-production Vu+** is available, with before/after evidence collected using the repository's existing read-only receiver audit tools. Never turn synthetic successes into a production install approval.

## Still blocked

Actual opkg reinstall/downgrade, package-manager database recovery, Enigma2 restart, GUI key navigation, manual mapping retention, native EPG import, 24-hour source scheduler and Casablanca time correctness must be validated on a **separate test receiver** before any installable RC80 exists.

RC79.1 remains production; do not change update.json, merge a release, or publish this blocked archive.
