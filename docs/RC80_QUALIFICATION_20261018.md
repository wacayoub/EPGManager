# RC80 candidate qualification — 18 October 2026

**Verdict: NO GO for installation. Receiver changed: false.**

One public source candidate: `codex/rc80-final-qualification-20261018`, based on
qualification commit `948ca14813bb1b47893af8fbd47be59196e82cc6`.
This is a review candidate, not a validated final release or an installable IPK.

## Receiver evidence

- Read AGENTS.md and codex/CODEX_SSH_TASK.md before the remote probe.
- Executed the reviewed stdin probe with BatchMode=yes and StrictHostKeyChecking=yes.
- `epg-vu`: DNS/name resolution failed; this Windows profile has no SSH config file.
- At the owner's supplied private LAN address, SSH reached the server but refused
  the connection because its ED25519 host key is not in known_hosts.
- Status: **SSH_NOT_TESTED / HOST_KEY_UNTRUSTED**. Authentication was not tested.
  No host-key verification was bypassed and no password was requested or used.
- No remote Python, GUI test, import, restart, installation, configuration access,
  mapping access or cache write occurred. IPToSatPro and IPAudio Pro untouched.
- RC79.1/schema 15/138 modules and Python 3.14.7 are repository baseline references,
  **not observations of this receiver in this session**. Installed versions,
  protected hashes, module deltas and EPGImport availability remain unknown.

## Reproduced defects and corrections

1. The RC80 evidence refresher did not consume `reports/mena-readiness.json`.
   A provider's real current XMLTV could therefore remain green despite a
   degraded MENA selected owner. It now applies exact owner + exact ID vetoes;
   MAIN/B1/B2 never grant quality. Qualified-at-build status cannot promote a
   direct guide without its independent XMLTV checks. Missing, malformed or
   stale readiness prevents new positive certification.
2. beIN's current 48h report contains 15 INFO rows explicitly labelled
   OCCASIONAL_EVENT_SCHEDULE. The old partial-audit validator rejected these
   and withheld all 67 independently healthy current IDs. It now accepts INFO
   only with the explicit occasional flag, matching reason and declared ID.
   Failed, duplicate, missing and inconsistent rows remain rejected. No fake
   event is generated for an empty occasional channel.
3. The preview annotation now removes automatic eligibility and SAFE-like
   grades from known empty/degraded/failed guides. Existing saved owners stay
   visible and first; input mappings and candidate dictionaries are not mutated.
4. Python/shell/YAML LF attributes make byte-hash gates reproducible on Windows.
   The three source-integrity tools pin the updated core module blobs. The
   receiver UI and its private reconciliation digest remain unchanged.

## Actual public-source comparison (workstation, not Vu+)

Evidence: `docs/evidence/rc80-public-20261018.json`. Measured at
2026-10-18 18:53 UTC on Windows/Python 3.13.2. Before and after use the same
downloaded bytes and observation time; before is qualification commit 948ca148.

- 14/14 compressed XMLTV SHA checks passed.
- MENA report at 11:41 UTC: 517 IDs, 398 qualified at build, 93 degraded,
  26 without future EPG. These are publication-time classifications.
- beIN independently trusted current IDs: **0 → 67**; the empty ID remains excluded.
- Selected-owner degraded guides cease to be trusted: Morocco **15 → 14**,
  Shahid **70 → 43**, Starzplay **110 → 74**, Elcinema **56 → 44**,
  Aljazeera **3 → 2**, Alkass **8 → 7**, Dubaiplus **9 → 7**.
- Sport24 remains rejected for content quality; Tabie remains stale. SHA validity
  alone does not establish usable EPG. All 14 sources remain supported.
- Whole comparison: **9.43 seconds on the PC**, two workers, one shared MENA
  readiness download. This is not a measured improvement in receiver latency.
- No channel language, donor events, priorities, IDs or time offsets were rewritten.

Reproduce on a workstation:

```sh
python tools/qualify_rc80_public_sources.py --output qualification-public.json --compare-ref 948ca14813bb1b47893af8fbd47be59196e82cc6
python -m unittest codex.test_receiver_readonly_probe -v
python -m unittest discover -s tests -v
```

## Offline validation

Windows targeted suites: **41 tests: 37 passed, 4 skipped**. Three skips require
POSIX /tmp/permissions; one requires Windows symlink privilege. No skipped test
is counted as a pass. Full Windows run: **184 tests, 162 passed, 7 failed,
12 errors, 3 skipped** (separate from the four probe tests). The failures expose
missing `sh`, POSIX modes/paths and unavailable symlink privileges; the complete
suite is not claimed green on Windows. Linux/Python 3.13 and 3.14 CI runs the complete suite,
including these POSIX checks, without accessing a receiver.

## Remaining installation gates

1. Owner verifies the receiver host fingerprint locally and configures noninteractive
   SSH with a locally held key/agent. Rerun only the initial read-only probe.
2. Compare the observed RC79.1 module hashes with the private 138-module baseline.
   `source/current.txt` still points to RC70 and cannot substitute for RC79.1.
   No complete receiver-derived baseline or private rescue IPK is present here;
   the full RC80 package cannot honestly be assembled from public overlays alone.
3. Separately authorize a specific candidate deployment/test operation before any
   receiver write. Existing permanently blocked packaging safeguards remain.
4. Verify actual GUI Auto/Manual Mapping, Best Match, persistence, native EPGImport,
   event retention during unavailable sources, UTC/local time display, scheduler,
   startup and UI latency on the real Vu+. No real opkg rollback test is required.

Until these checks succeed, retain installed RC79.1 and publish no final release.
