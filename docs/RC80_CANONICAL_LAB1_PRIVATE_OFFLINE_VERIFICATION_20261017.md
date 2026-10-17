# RC80 canonical reconciliation and LAB1 blocked inspection — 2026-10-17

Scope: **private offline assembly only**. No receiver access, no `opkg`, no GUI restart, no update manifest modification, no publication of private source.

## Pin-verified canonical candidate

Starting from the owner-provided RC79.1 private rescue IPK (SHA256 `117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69`) and the separately reviewed RC80 public overlay, an independent offline reconstruction produced **exactly** the documented canonical inspection IPK:

- SHA256 `7c7e476c4e6fefb9c43de4d354e7307555fbe3a674634871fa269f408c644dee`
- 667,666 bytes; 140 Python files and 75 original non-Python assets; schema 15.
- 254 Mapping screen methods preserved; only four reviewed Best Match preview/health methods modified.
- Reconciled private Mapping GUI source SHA256 `a528d9105d8cb32b952c7f759b1f2b223792c5d75c73fcc9749538791fcd6bb8`.
- Permanent `preinst exit 90`, **not installable**, no installer or receiver operations executed.
- The different inspection archive SHA256 `ca43c8953a6c811cdcd5b36689c59d3773f47a7211874931958e251e6731dd49` is **not** equivalent and must never be used as the canonical artifact.

## Distinct, blocked LAB1 candidate

Built offline from the verified canonical payload, changing only `version.py`, the opkg control `Version`, and a static-only `postinst` validator. No functional runtime module or asset changed.

- Version: `2026.10.17-rc80-lab1`.
- SHA256 `8b1eb71e4fe1ce230c93e5e4b13a4b95e330c1238c0c1db3363ce4b7f90f0633`, 668,964 bytes, 215 application files.
- Retains unconditional `preinst exit 90`. **DO NOT INSTALL**, even for a refusal test.
- Synthetic validation suites: 10/10 postinstall and 10/10 release-gate tests PASS.
- The static postinstall hook was run on a temporary extracted copy of the actual LAB1 140-source payload and PASSed; deleting a required verified-EPG bridge was correctly rejected.
- Both archive hashes reproduced after a separate rebuild.

## Outstanding release gates

Real Enigma2 GUI/touch remote keys, native GitHub XMLTV import into eEPGCache, manual and automatic mapping persistence after reboot, EPG retention, Casablanca 24-hour scheduler and source programme quality are **NOT TESTED**. No installable LAB package was produced. A distinct, supervised, fail-closed device-specific install/test workflow needs owner approval and controlled acceptance evidence. Real `opkg` rollback rehearsal is `NOT_TESTED_WAIVED_BY_OWNER` (not PASS); still preserve backup/hash/recovery checks.

**Decision: NO-GO production RC80; preserve active RC79.1.** No `update.json`, release/tag, PR merge or public release asset changes.
