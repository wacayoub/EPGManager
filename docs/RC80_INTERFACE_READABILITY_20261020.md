# RC80 interface readability candidate

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
