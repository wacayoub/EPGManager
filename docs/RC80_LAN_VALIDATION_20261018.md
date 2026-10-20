# RC80 actual LAN validation — 18 October 2026

**Final release: NO GO pending GUI/native import acceptance. Receiver modified:
true by the explicitly authorized installation and GUI restart below.** No manual
EPG import was requested. One consolidated candidate:
[draft PR #38](https://github.com/wacayoub/EPGManager/pull/38), branch
`codex/rc80-final-qualification-20261018`, based on `948ca148`.
This supersedes the earlier SSH_NOT_TESTED report.

## Actual receiver baseline before authorized installation

Read AGENTS.md and codex/CODEX_SSH_TASK.md before the probe. Alias `epg-vu`
fails name resolution: no SSH alias configuration exists on this PC. Direct
SSH to the owner's previously verified LAN receiver succeeds with existing
session credentials. No credential is requested in chat or stored in Git.

| Observation | Actual result |
| --- | --- |
| EPGManager | 2026.10.08-rc79.1 |
| Schema / Python modules | 15 / 138 |
| Python | 3.14.7 |
| Image | OpenATV 8.0.1, build 20261003 |
| Enigma2 package | 8.0.1+git35583+57b7a510+57b7a5127d-r1 |
| EPGImport package | 1.1+git314+a32929f0+a32929f2d0-r0 |
| Enigma2 process / EPGImport extension | Active / present |
| Syntax on receiver | 138 modules parsed, zero syntax errors |
| Seven protected module hashes | All match known RC79.1 |
| Mapping/preferences files | Present; contents never read |
| Already tuned satellite service cache | 27 valid events: one current, 26 future |

Dependency discovery in standalone SSH Python found requests, urllib3, bs4,
lxml, twisted, SSL, gzip/zlib, bz2/lzma and both pytz/zoneinfo. The optional
cloudscraper package is absent. Enigma2's embedded modules and GUI path are
not discoverable in this standalone interpreter; this is not proof that the
running Enigma2 process lacks them. Standalone Python reports UTC (offset 0).
Actual GUI local-time display and dependency imports were not exercised.

The cache check used read-only localhost OpenWebif GETs. It does not certify a
new import or GUI operation. No channel name, service reference, private URL,
programme text, mapping or settings content is published. Source capture and
AST parsing took 7.27 seconds including capture overhead, not GUI latency.
Evidence: `docs/evidence/rc80-receiver-20261018.json`.

## Detected defects and candidate corrections

1. **RC79.1 false green reproduced on hardware.** Its classifier returns EPG_OK
   for a PASS slot report without a per-ID quality denominator. The SHA-locked
   private patch returns EPG_WARN. Qualified MENA metadata cannot promote
   UNKNOWN; absent/expired MENA cannot grant green in the legacy health path.
   Malformed numeric quality now returns UNKNOWN instead of raising into UI.
2. **MENA exact owner/ID vetoes.** NO_FUTURE_EPG/zero future programmes block
   green; degraded guides require review. MAIN/B1/B2 never certify quality.
   The legacy health worker downloads one bounded MENA report per batch and
   builds an index once; UI lookups are O(1), with no network on UI callbacks.
3. **Preview consistency.** Empty/degraded candidates lose automatic preview
   eligibility and show NO EPG/EPG REVIEW. Only four reconciled preview methods
   change. Original auto/manual/undo writers, class configuration and key
   actions pass AST parity; exact source SHA gates remain enforced.
4. **Preserved existing PR fixes.** Explicit occasional beIN INFO rows no
   longer invalidate healthy siblings. Empty MAX/XTRA/AFC IDs receive no fake
   programmes. Optional local XML absence reports WARN for direct GitHub
   sources, not false FAIL or proof of a successful native import.

No live defect in persistence, scheduler, source offsets or import execution
is claimed fixed: those functional cases remain untested. No language,
source priority, ID, programme content, mapping or time offset was rewritten.

## Actual candidate execution on Vu+ CPU

Candidate evidence/health modules ran via SSH stdin under an isolated
in-memory package, without importing/replacing installed plugin modules.
Synthetic logic fixtures are separate from the real cache check above.

| Synthetic case | RC79.1 | RC80 candidate |
| --- | --- | --- |
| Missing per-ID quality | EPG_OK | EPG_WARN |
| Qualified owner + quality | — | EPG_OK |
| Degraded owner | — | EPG_WARN |
| No future EPG | — | NO_EPG |
| Malformed numeric quality | — | UNKNOWN |

Final consolidated-code run: all six expected classification results matched.
Five trials ranking 500 synthetic candidates: median **42.48 ms**, range
42.33–69.57 ms on Vu+/Python 3.14.7. This is CPU ranking, not GUI rendering or
a measured overall speedup. Python uses optimization level 1 on the receiver;
the final test uses explicit exceptions, independent of disabled assertions.
The initial consolidated test script had an invalid future-import concatenation;
a subsequent probe exposed a sub-microsecond fixture timestamp rounding issue.
Neither attempt is counted as a successful final test. The corrected final
probe exited successfully and rechecked all seven unchanged protected hashes.

## Actual public-source tests (PC)

Evidence: `docs/evidence/rc80-public-20261018.json`, checked 19:55 UTC.
Before/after share downloaded bytes and observation time; baseline `948ca148`.
**14/14 compressed XMLTV hashes verified.** MENA: 517 IDs, 398 qualified at
build, 93 degraded, 26 without future EPG (publication-time classifications).

- beIN independently trusted current IDs: **0 → 65** at this observation time.
- Degraded selected-owner guides lose positive certification: Morocco 15 → 14,
  Shahid 71 → 43, Starzplay 110 → 74, Elcinema 56 → 44,
  Aljazeera 3 → 2, Alkass 8 → 7, Dubaiplus 9 → 7.
- Sport24 remains rejected for content quality; Tabie remains stale.
- Comparison: **8.08 s on PC**, two workers, one shared readiness GET.
  This is not receiver speed or proof that all 14 sources are healthy.

```sh
python tools/qualify_rc80_public_sources.py --output qualification-public.json --compare-ref 948ca14813bb1b47893af8fbd47be59196e82cc6
python -m unittest codex.test_receiver_readonly_probe -v
python -m unittest discover -s tests -v
```

## Offline tests and consolidated private candidate

Windows/Python 3.13.2: **192 tests: 170 pass, 7 failures, 12 errors, 3 skips**.
Remaining failures involve POSIX modes, shell/paths and symlink privileges.
They are not passing tests. All eight new MENA/legacy-health tests pass.
Linux CI for the previous PR code passed 184 suite + four probe tests on
Python 3.13/3.14; that does not validate the newly consolidated commit.
**Final consolidated Linux CI passed and logs were read:** 192/192 suite tests
plus 4/4 probe fixture tests, with no skips, on each of Python 3.13 and 3.14.
Tested code: `470ad45d634002a7d9eb992bf0f501b7be290579`.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39161235131).
Later report-only updates do not alter that tested code.

One private SOURCE-ONLY archive was assembled from the actual 138-module
snapshot, two public RC80 modules and SHA-locked deltas. Schema remains 15.
The structural gate confirms 140 modules, zero unapproved modifications and
four changed original paths: version, health, diagnostics, preview UI.
Snapshot/source archive stay on the owner's PC outside Git. They must not be
copied over the operational plugin. A complete inspection IPK has now also
been assembled as described below.

Archive: `EPGManager-RC80-candidate-SOURCE-ONLY.private.zip`.
SHA256: `3bd1a9368008eea922a6deef90bf40e6ff2e948221c6d86364658cd8449f932b`.

The specifically pinned rescue IPK was absent from bounded known locations.
No rescue was created remotely. Permanent IPK install blocks remain intact.
A real opkg rollback test is waived by the owner, not an outstanding gate.

## Complete offline inspection IPK

Following the owner's instruction to prepare the complete package, read-only
SSH inventory found exactly 138 Python modules and 75 application resources.
All 138 source hashes were rechecked before the resource snapshot. No mapping,
preferences or settings file under /etc was read, copied or modified. Private
application resources remain local and are never uploaded to GitHub.

`tools/build_rc80_snapshot_ipk.py` reconstructs the same reviewed source
candidate from these observations, applies the existing exact preimage,
mapping parity and source inventory gates, and adds the 75 unchanged resources
with their observed permissions. It does not substitute a different rescue
hash into the historical rescue builder. That builder is unchanged.

Package: `EPGManager_RC80_COMPLETE_OFFLINE_BLOCKED.ipk`, **670584 bytes**.
Version: `2026.10.18-rc80-candidate`, schema 15; **140 modules + 75 resources**.
SHA256: `d693dee2289eb2f907e0dd18b9616b3ee914603085a032d12d80628a0bb5b5b7`.

- Two independently serialized packages are byte-identical.
- All 215 payload bytes/modes survive archive read-back exactly.
- The 140 modules are byte-identical to the already tested private source ZIP.
- Installed package identity/dependencies are preserved; runtime/control
  versions agree. Resources are declared as conffiles to retain local edits
  in a separately reviewed deployment. Actual opkg conffile behavior is untested.
- On the PC, all four embedded scripts pass shell syntax checks. Preinst
  exits **90**, including with EPGMGR_RC80_SUPERVISED=1; the other hooks exit 0
  without an action. No script or opkg command was run on the receiver.
- Five new regressions test exact asset preservation, deterministic archives,
  unconditional install block, malformed metadata, path escapes, missing
  modules, changed asset bytes, duplicate assets and mode drift.

**Package-builder Linux CI passed:** 197/197 suite tests plus 4/4 probe tests
on each of Python 3.13 and 3.14, no skips. Job logs were read for tested commit
`45bd0091f5859c4c899540185369cccfd714cd7c`.
[Successful package qualification](https://github.com/wacayoub/EPGManager/actions/runs/39163452728).
Public count/hash evidence: `docs/evidence/rc80-package-20261018.json`.

This is a complete **inspection** IPK with an unconditional installation
block. It is not an approved deployment package. Preparing this artifact did
not authorize or perform an installation, restart, import or cache change.

## Supervised installation candidate

The owner authorized improving/finalizing preparation, not a receiver install
or restart. A separate supervised test artifact is now prepared while the
permanently blocked inspection artifact remains unchanged. Both contain the
same application payload (140 modules and 75 resources); this is one source
candidate, not a second RC80 release.

`EPGManager_RC80_SUPERVISED_TEST.ipk`: **708464 bytes**.
SHA256: `00bb8cfc6cfc22df2fdf348c1524f6b7123e9e37fe86c79d47fdffbb4434499f`.
Version/schema remain `2026.10.18-rc80-candidate` / 15.

The new preinst refuses an ordinary installation without package-specific
consent. It checks original hashes/permissions, requires passive or absent old
removal hooks, checks backup space, then writes and reads back one private
recovery archive containing the working plugin and EPGManager configuration.
Only EPGManager settings lines are copied; settings for other plugins are
excluded. The postinst verifies the expected 140-module payload and performs
no settings/cache reset, scheduler operation, EPG import or Enigma2 restart.

A read-only preflight must run **before** any opkg invocation, because old
removal hooks participate in upgrades. Its exact code was sent through SSH
stdin and **passed on the actual receiver**, with no backup/write/install.
Installed prerm is passive; postrm is absent. The installed legacy postinst
contains cache overwrites and EPG-file deletion, observed in code only and
never executed in this task. The supervised candidate does not carry those
legacy postinst operations; its new postinst only verifies file integrity.

Eight new synthetic tests pass locally: consent refusal before any write,
rejection of active old hooks, source/resource drift, verified backup while
preserving original mappings/EPG, exclusion of other-plugin settings, low-space
refusal, hook syntax/compilation and postinstall verification of 140 modules.
All embedded hooks pass local shell syntax checks; the actual embedded preinst
was executed on the PC without consent and refused before receiver-path I/O.
At that preparation stage backup creation/restoration had not been tested on
the receiver. The authorized installation below subsequently tested backup
creation/read-back, but no restoration was exercised.

Private baseline manifests, scripts and both IPKs stay outside Git. Public
summary evidence: `docs/evidence/rc80-supervised-20261018.json`.
Final Linux CI for the new guard **passed: 205/205 suite tests plus 4/4 probe
tests on each of Python 3.13 and 3.14, with no skips**. Job logs were read.
Tested code commit: `b992b4c7c10923affa087bfae3a91978f2041a52`.
[Successful supervised-candidate CI](https://github.com/wacayoub/EPGManager/actions/runs/39167039826).

The owner subsequently explicitly confirmed the installation with verified
backup, without import/restart. Installation alone does not reload the GUI;
Enigma2 restart remains a separately authorized operation.

## Authorized installation actually executed

The installed artifact has the same 215-file application payload as the tested
inspection IPK. Its control metadata corrects the inherited `python3` dependency
to OpenATV's installed `python3-core` 3.14.7 package. A dedicated regression
checks that only that dependency token changes. The nine targeted guard tests
passed before installation. Current artifact:

- Version `2026.10.18-rc80-candidate`, schema 15, **140 modules + 75 resources**.
- IPK SHA256 `a38b0cbd635611472d58112e8e13cb35b6ce137c97b7a9565d7406cbdbb60b69`.
- Bytes: **708468**. The earlier 00bb8cfc artifact is superseded for deployment;
  the permanent inspection package remains unchanged.

Pre-install attempts stopped without any receiver write because this opkg has
different option/status-query behavior. The installer then verified each of
the 13 required installed dependency packages individually and used opkg's
`--nodeps` to isolate this upgrade from other package/dependency changes. No
dependency was installed to work around a missing runtime.

The actual authorized `opkg install` returned **0**. Its preinst created one
private RC79 backup, then read it back and verified all archived bytes/modes
before replacing the package payload:

`/etc/epgmanager_epg/backups/rc79-before-rc80-1792358774825996001-5701.tar.gz`

Backup SHA256: `13640001a06ce505823a5f3758fedf1f44a7a4703410169c5942aaa83f7a128d`
(1193540 bytes). No real restore/rollback was executed.

Post-install results:

- opkg reports `install ok installed`, version `2026.10.18-rc80-candidate`.
- Every candidate file matches the expected content/mode; 140 modules parse
  without syntax errors on the receiver.
- Configuration hashes, EPG file metadata and the Enigma2 process are unchanged.
- No import or GUI restart was requested. The running GUI has not been reloaded;
  RC80 GUI/native import behavior remains unvalidated.

**Recorded opkg warning:** the upgrade attempted to remove an obsolete `/.`
entry inherited from the previous package; unlinking the root directory failed
with "Is a directory". No root directory was removed and no manual registry
repair was performed. Subsequent read-only inspection confirms the current
registry has **215 entries, zero missing files, zero foreign paths and no root
entry**. This opkg stores `path<TAB>mode`, accounted for by the final probe.
The warning is retained in the installation evidence, not hidden by exit code 0.

Linux CI for the dependency correction passed **206/206 suite tests + 4/4 probe
tests on each of Python 3.13 and 3.14**, no skips; logs inspected. Code commit
`273efb55e0de3c66f7cebb7f100a8e4b97a2fd9f`.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39167932586).
Evidence: `docs/evidence/rc80-installation-20261018.json`.

## Remaining gates

1. Separate explicit authorization for the actual native EPG import test.
2. Actual GUI Auto/Manual Mapping, Best Match, persistence, native EPGImport,
   unavailable-source retention, GMT+0/local display, scheduler and UI latency.
   Reboot/restart remains separately authorized.

RC80 candidate is installed on disk; a verified RC79.1 recovery archive is
preserved. IPToSatPro and IPAudio Pro were not touched. No final release has
been published.

## Authorized GUI restart actually executed

The owner separately authorized the Enigma2 restart. One localhost OpenWebif
`powerstate?newstate=3` request was sent, following the
[official API documentation](https://github.com/E2OpenPlugins/e2openplugin-OpenWebif/wiki/OpenWebif-API-documentation).
The HTTP connection closed during the restart; the command was not retried.
Measured recovery: **51.85 seconds**, Enigma2 PID **5904 → 7195**, with three
consecutive successful OpenWebif power-state samples. The receiver boot ID
remained unchanged: this was a GUI restart, not a receiver reboot.

The comparison included 95 tracked plugin/configuration files. Two hashes
changed: `epgmanager_sat_registry.json` and `epgmanager_mapping_view_cache.json`.
These are registry/view cache files used by startup warming, not saved mappings.
A separate read-only comparison against the verified RC79 recovery archive
confirmed **13 of 15 archived configuration entries identical**, including
`epgmanager_mappings.json`, plugin settings, native settings, source preferences,
learning and SRP mappings. The only differences were the same two cache files.
No recent crash file was found in the inspected top-level log directories;
this limited check does not prove absence of every possible runtime error.

No manual import, mapping edit, cache reset, or further installation was issued.
The installed candidate and successful GUI process recovery do **not** certify
opening EPGManager screens or native EPGImport behavior. These acceptance gates
remain pending. Evidence: `docs/evidence/rc80-gui-restart-20261018.json`.

## Owner GUI confirmation and authorized import attempt — 20 October

The owner confirmed the TV picture returned and EPGManager opens. This is actual
owner-observed GUI opening evidence; Auto/Manual Mapping and Best Match screen
behavior remain separate acceptance checks.

The owner explicitly authorized a native EPG import test with no deletion of
saved mappings or existing programmes. **No import was executed in this attempt.**
Direct SSH reached authentication, but the tool's automatic approval policy
rejected interactive input (`sandbox_approval` disabled). No credential was
requested or disclosed in chat. Alias `epg-vu` still fails name resolution.
A read-only direct LAN OpenWebif request was refused on TCP port 80
(Windows error 10061); the earlier successful localhost API does not establish
current LAN accessibility. SSH key access has been requested as the prerequisite.

Review of the captured native import source identifies the installed OE-Alliance
engine bridge as the appropriate test path. The manual screen passes the saved
clear-before-import preference, so the authorized conservation test must explicitly
enforce `clear_before_import=False` before starting; it must not blindly press
Manual Import with an unknown setting. Standalone SSH Python is insufficient to
prove a write into Enigma2's embedded cache. No installed code/configuration,
mapping, cache, service, or scheduler was changed by this attempt.

Final release remains **NO GO** pending actual native import, programme retention,
time correctness and remaining mapping/scheduler acceptance. Evidence:
`docs/evidence/rc80-native-import-attempt-20261020.json`.

## Actual native-import GUI attempt through owner SSH tunnel — 20 October

The owner established a Windows SSH local forward to the receiver's localhost
OpenWebif. HTTP power-state and OSD capture succeeded. Remote-control API
navigation opened EPGManager, Main Settings and Native Import options on the real
GUI. The displayed installed version is `2026.10.18-rc80-candidate`.

**First concrete problem:** the saved **Delete current EPG before import** option
was **Yes**. In accordance with the owner's explicit conservation instruction,
it was changed to **No** before selecting Manual Import and accepting the prompt.
The screen's normal save operation persisted the setting. No mapping editing,
source-selection change, purge, installation or restart was requested.

**Actual test result: NOTHING TO IMPORT.** Preparation found **zero local XMLTV
files** under the configured `/etc/epgmanager_epg`. This manual screen uses the
local-only bridge, whereas GitHub-direct feeds are imported by the dashboard's
Import All/direct runner. The native engine did not start; this is not an import
success. The screen's suggestion to use Import All was not followed blindly.

**Second concrete code defect:** `DirectEPGImportRunner` constructs
`FirstWriteFlushProxy(..., enabled=not self.clear_before_import)` in both the
OE-Alliance engine and fallback paths, and runs migration sanitation before the
engine. Consequently `clear_before_import=False` still permits a per-service
purge, losing existing programmes when a replacement feed is partial or the
subsequent event write fails. This was found by inspection of the SHA-verified
receiver source and reproduced offline, not by destructive testing on the Vu+.

The source-locked corrective candidate disables both implicit first-write
flushes, makes the proxy default safe and skips migration sanitation during
non-destructive imports. Explicit `clear_before_import=True` remains a separate
destructive request and was not used. Five targeted tests pass: partial feeds,
failed writes in both paths, safe proxy default, reproduction of old loss and
source-pattern drift rejection. The full private 140-module candidate assembles;
75 resource bytes/modes are preserved. Its inspection-only IPK is permanently
installation-blocked (670606 bytes, SHA256
`81cff2c29ef3c7716bb1a6d88ad6f7a5996103c17570f2261fecb9a954ece1ee`).
It is **not installed or hardware-qualified**.

Cache conservation sampling queried 32 receiver services, of which six had EPG:
**543 event fingerprints before, 543 after, all 543 retained**, zero query errors.
This does not certify the entire cache or mapping-file hash identity. No saved
mapping edit was requested; native import preparation only consumed maps.

The initial Windows full-suite attempt hit sandbox temporary-directory errors.
Repeating with a workspace temporary directory ran 211 tests but still produced
7 failures, 12 errors and 3 skips involving POSIX modes, symlink permissions,
Linux paths and shell commands. This is explicitly **not a passing full suite**.
Probe tests then ran four, zero failures, one Windows skip. Linux CI for the new
code is required; the earlier 206+4 successful run does not certify these changes.

Evidence: `docs/evidence/rc80-native-import-20261020.json`. **Final release NO GO**
pending a separately authorized corrective deployment and actual non-destructive
direct native import, cache retention and the other acceptance gates.

Subsequent Linux CI for code commit `907a56f318bb2b23826aa19b3899293ed8b30159`
passed **211/211 suite tests + 4/4 probe tests on each Python 3.13 and 3.14**,
zero skips. Both job logs were inspected. This resolves the full-suite execution
gate for the new code without concealing the Windows limitations above.
[Successful run](https://github.com/wacayoub/EPGManager/actions/runs/39346462328).
It does not resolve corrective deployment or native-import hardware acceptance.
