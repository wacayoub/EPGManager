# RC80 — hardware qualification and private package handoff (2026-10-15)

Status: **NO-GO for installation / publication.** The production Vu+ Zero 4K stays on EPGManager `2026.10.08-rc79.1`, OpenATV 8.0.1, Python 3.14.7, schema 15. This note only prepares the next milestone; it makes **no changes** to the receiver, installed plugin, mappings, preferences, themes, sources, XMLTV, update manifest, or release assets.

## Evidence already established

- Working installed receiver: 138 original Python modules; static module checks and native EPGImport presence were observed in read-only audits.
- Privately reconstructed RC79.1 rescue snapshot SHA-256: `117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69`. Receiver audit reported **213/213 application files** and file-mode parity; opkg restoration not exercised.
- Reviewed current public RC80 bridge adds exactly `core/verified_epg_match.py` and `core/verified_epg_refresh.py` to an independently authenticated full 138-module baseline. The separate revised suggestion UI must be byte-identical to the reviewed overlay; schema stays 15.
- CI for commit `f2e7840df154b96376dbe636a48c30fce5c9fcf2`: 142 offline Python 3.13 and 3.14 tests, successful ([run 38809807501](https://github.com/wacayoub/EPGManager/actions/runs/38809807501)). These tests do **not** establish an operable hardware package.

## Next hardware validation — read-only production probes only

The existing `tools/audit_rc80_receiver_preflight_readonly.py`, `tools/audit_rc80_runtime_readonly.py` and `tools/audit_rc80_rollback_package_readonly.py` can be run on the working receiver without installing or restarting anything. They may report package identity, installed source AST, version/schema, EPGImport prerequisites, package backup parity, and space. They cannot certify a functional RC80 GUI or opkg recovery while RC79.1 remains running. Do not classify static symbol presence as a passing button/import test.

No `opkg install`, `opkg remove`, GUI restart, automatic update, forced rollback, source/mapping writes, or environment override of `safe_preinst.sh` is authorized on the production receiver.

## Build input contract (private/offline staging host)

1. Keep the complete RC79.1 rescue IPK on an offline staging host, never in the public repository or a CI upload. Check its exact SHA-256 before reading it.
2. Extract only its 213 application files (138 Python + 75 other assets) through path/symlink/size-bounded archive parsing; validate `Package`, `Version`, `Architecture`, and each original Python file's SHA-256 against a private 138-module manifest.
3. Apply reviewed current public UI suggestion overlay and exactly two reviewed new Python bridge modules. Apply any approved private core patch only when its byte hash is separately recorded and reviewed. Version may advance to RC80; configuration schema must remain 15.
4. Run `tools/audit_rc80_package_manifest_readonly.py` against the **actual complete staged 140-module tree**; reject unknown path changes, missing assets, unreviewed private changes, stale baseline, bad syntax, and any rc70/rc78 source downgrade.
5. A candidate assembled before hardware certification must contain an **unconditionally blocking preinst (exit 90)**, must remain unpublished, and must never become an `update.json` target. This is a genuine structured IPK only for static inspection, *not* an installable release.

## Requirements for a later installable candidate

- First rehearse **real opkg downgrade/reinstall and Enigma2 restart on a separate test receiver**, including opkg database consistency. Filesystem-only restoration is insufficient.
- Validate GUI focus/remote keys, Smart Mapping manual-owner retention, verified GitHub current-program evidence, native import progress, stable 24-hour source scheduling and Casablanca timezone, no UI freeze, and retained EPG/settings.
- Confirm a complete pre- and post-upgrade rollback plan, recovery package path and hash, storage safety, as well as fail-closed hooks *without* relying solely on environment flags.
- Do not merge the draft PR, tag a production release, or change the public updater until every gate has independent hardware evidence.

This record is intentionally free of private receiver files, hashes beyond the already public rescue identity, mappings, EPG content, and credentials.
