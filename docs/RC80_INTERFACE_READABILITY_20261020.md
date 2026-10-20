# RC80 interface readability candidate

**Current acceptance: owner-authorized UI IPK installed and Enigma2 restarted once. Actual dashboard, Mapping and Best Match interaction passed; six protected mapping/preferences hashes and all EPGManager settings are preserved. All 489 sampled nonexpired programmes survived. GO for this receiver.** [Installed evidence](evidence/rc80-interface-readability-installed-20261020.json).

The preparation and pending-approval statements below retain the chronology and are superseded by the installed acceptance at the end.

Prepared offline; **not installed**. The three installed UI module SHA256 hashes were checked over read-only SSH and match the pinned package baseline. Receiver changed: false. Separate owner approval is required before installation or Enigma2 restart.

## Changes

- Opaque navy background and brighter secondary text; XML skin and MultiContent colour constants use the same values.
- Dashboard KPI title/value/note sizes: 15/30/14 to 17/32/16, within existing widget bounds.
- Last-import receipt shows day/month and UTC time explicitly, with safe placeholders for missing/invalid timestamps. This does not imply that a failed import succeeded.
- Dashboard blue button says Sources, matching its existing action.
- Best Match fonts: 18/16 to 20/18. Healthy rows explicitly say EPG OK; unknown rows say CHECK. Existing NO EPG, generated-guide and repeated-guide warnings remain distinct. The badge rail is 20 pixels wider and the title shrinks by 20 pixels to avoid overlap.

## Validation

Actual candidate modules compile; both generated dashboard skins parse as XML and all widgets remain inside 1920x1080. Fourteen actual Best Match rendering cases and five import timestamp cases pass. AST comparison confirms every other class method is unchanged; no mapping, persistence, native import, scheduler or action changes.

These checks used the exact private packaged receiver code locally, not a substitute implementation. `tools/prepare_rc80_readable_ui.py` reproduces the patch from the three SHA-pinned application modules in memory. No owner settings, mapping data, credentials or programme content are included.

Previews use example data and desktop fonts; they are not Enigma2 screenshots. Font fallback, downscaling and TV-distance readability still require a real GUI check after separately approved installation. No speed gain is claimed. No network calls, images, animations or refresh timers are added.

The existing 247-test suite was attempted on Windows. The first run encountered sandbox temp-directory permission errors. A second run used a writable workspace temp directory: 247 tests ran with 7 failures, 12 errors and 3 skips. The full suite is not green on this workstation. The existing dashboard (3) and mapping-overlay (4) focused regression tests passed. These existing tests do not replace the actual candidate checks above. Linux CI from the prior release does not certify this new candidate.

Reproduce candidate checks: `python tools/audit_rc80_readable_ui.py <private-staging-directory>` (requires the baseline and candidate subdirectories). Baseline hashes and regenerated candidate bytes are verified before executing isolated render methods.

## Installation candidate

The consolidated IPK is 701998 bytes, SHA256 `bafee7f5d8911449a44ef3578fa2da4c74e55e8870e049cc6ee4c5259758ef64`. It retains 142 modules, 75 resources, schema 15 and version 2026.10.20-rc80. Only the three UI modules change; all 214 other files remain byte-identical. Build reproducibility and archive bytes/modes roundtrip pass. Existing package-specific consent, verified backup, exact baseline inventory verification and passive removal hooks are preserved. This same-version package requires an explicitly approved reinstall; neither hook restarts Enigma2 or launches an EPG import.

The actual candidate rendering checks also passed over SSH in memory on receiver Python 3.14.7, with no Enigma2 imports, disk writes or settings access. This validates syntax, isolated row rendering and skin construction on the target interpreter; it does **not** validate the native GUI, fonts or TV picture.

Three additional package-consent/source-drift tests pass. A separate GitHub branch runs the complete suite on Linux Python 3.13/3.14 before deployment approval is requested.

## Qualification gate passed; awaiting specific deployment approval

[Linux qualification](https://github.com/wacayoub/EPGManager/actions/runs/39402505556) of code commit `12ca985c8969ad2238752054904a8dcb2dae8b3c` passes: **250 suite + 4 probe tests on each Python 3.13 and 3.14**, zero failures/skips; both job logs inspected. Windows packaging/mode/symlink limitations remain recorded above and were not hidden by weakening any test.

The actual receiver read-only package preflight also passed (exit 0). All 142 payload Python modules parse. Missing package-specific consent refuses installation before backup or receiver writes. Source hashes, passive hooks and 214 unchanged file bytes/modes were checked. Native GUI validation of this new presentation still requires installation plus one Enigma2 restart under separate explicit owner approval. [Draft PR #39](https://github.com/wacayoub/EPGManager/pull/39) contains only this interface follow-up to #38. No receiver modification occurred during preparation.

## Installed and native GUI validated

The owner granted control directly after the specific UI installation/restart/test request. The exact `bafee7f5…` IPK was installed with `opkg --force-reinstall`, exit 0, after its verified backup. All 15 protected configuration file hashes remained identical across installation. One Enigma2 restart recovered in **47.73 seconds**, with a changed Enigma2 PID, unchanged receiver boot ID and three stable API responses.

Actual receiver screenshots were inspected privately: the new dashboard fonts, GMT+0 import receipt and Sources button are visible. Smart Mapping opened, the satellite/channel panes navigated normally, and Best Match displayed three cached candidates with explicit EPG OK badges. The selected candidate moved with the remote direction key; no apply/save/undo action was requested. CHECK was also visible on the initial placeholder. NO EPG/generated/repeated badge paths were covered by the isolated rendering cases, not claimed as newly observed live channels. The receiver was left on the dashboard. Physical TV viewing-distance judgement remains the owner's.

All **217 installed application files/modes match the reviewed candidate**, 142 Python modules, schema 15, Python 3.14.7. EPGImport is present as the installed compiled `EPGImport.pyc`; testing only for `EPGImport.py` would incorrectly report it missing.

Preservation: six protected mapping/preferences/native-setting files retain their pre-installation SHA256 hashes. No EPGManager setting value changed. Full 15-file equality across restart/navigation is false because normal registry/view-cache signatures, source metadata receipts and mapping activity update; the global Enigma2 settings file was also serialized, while all EPGManager keys remained equal. No source-preference restoration was needed. The EPG sample contains **489 events on 32 services before and after**, all 489 nonexpired fingerprints retained, zero query errors. Final API checks passed in 0.066/0.063/0.094 seconds.

This was a presentation deployment: no new EPG import was requested and the native engine remained byte-identical. The earlier native import/scheduler acceptance is separate evidence in #38. No IPToSatPro/IPAudio changes, public release or PR merge occurred. **GO for this installed interface on the qualified receiver.**
