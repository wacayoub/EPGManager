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

The owner explicitly waived an actual `opkg` rollback rehearsal on 2026-10-15. **Do not require a hardware rollback to advance this branch.** This waives a *test requirement*, not any backup, rescue SHA verification, package hook safety, or the obligation to be candid about the untested recovery path.

The next release gates are: (1) assemble the actual private 140-Python/75-asset candidate using the checksum-pinned rescue and the three exact reviewed public overlays; (2) reproduce the source hashes and verify the permanently blocked `preinst` (exit 90); (3) verify receiver functionality separately in a controlled smoke test (GUI keys, EPGImport, manual/auto mapping persistence, scheduler and Casablanca time, source health and programme quality). Keep the currently blocked inspection IPK non-installable; a **separately reviewed installable test package** is required for any hardware smoke test. Never install the blocked package merely to test its refusal, and never silently switch the production Vu+ away from RC79.1.

**Outstanding nonmandatory risk:** an actual opkg reinstall/downgrade and package-database recovery have NOT been exercised. Record this as `NOT_TESTED_WAIVED_BY_OWNER`, not as PASS.

RC79.1 remains production; do not change update.json, merge a release, or publish this blocked archive.


## One-command local staging (without building public releases)

The single-pass builder at `tools/assemble_rc80_one_shot_offline.py` auto-derives the complete private baseline manifest from the checksum-verified rescue IPK. Run only on an offline workstation with a local checkout of this branch. It validates the three pinned Git overlay blobs, builds exactly 215 files and refuses to generate an installable IPK. The original `--build` flow remains available for independent auditing.

## 2026-10-16: real private consolidation and AUTO mapping parity discovery

The owner-provided SHA-pinned RC79.1 rescue IPK was actually processed on an offline staging environment. An independently reproducible **private, non-installable** RC80 inspection IPK exists: 140 Python files plus 75 unchanged non-Python assets (215 total). 209 original application files are byte-identical; four existing Python files are reviewed changes (Mapping UI, system check, GitHub health, version); two are new reviewed verified-EPG modules. Version/schema remains schema 15; `preinst` unconditionally exits 90. Two local assemblies were byte-for-byte identical. **No private receiver code, manifest, mappings or rescue IPK has been uploaded here.**

**Important defect caught by real-source parity testing:** blindly overwriting the RC79.1 receiver `ui/channel_mapping.py` with the reviewed public RC80 overlay loses receiver-only, working Auto Mapping behavior. This discrepancy was not detectable in synthetic tests using the public UI snapshot. The *private* inspection candidate preserved all 254 original `ChannelMappingScreen` methods, changing only the four reviewed suggestion/health callbacks; all eight checked active auto/manual mapping APIs and non-method screen layout AST remain unchanged. The private candidate has **not** been tested on Enigma2 hardware.

`tools/audit_rc80_mapping_parity_readonly.py` now provides a fail-closed structural check between original and staged GUI source bytes, without importing Enigma2 or user data. `tools/build_rc80_offline_blocked_ipk.py` invokes it for the canonical privately SHA-pinned RC79.1 rescue. Therefore, **the public one-shot assembler is deliberately blocked** if it attempts the old whole-file UI replacement. Its synthetic CI fixture deliberately lacks the user's private source, so passing synthetic packaging tests does not certify the real-source reconciliation. The private, reconciled candidate is an inspection artifact only, not the output of a completed public release pipeline.

**Outstanding technical gate:** integrate a reviewed, SHA-pinned *private baseline preserving* GUI reconciliation into the canonical offline build path and test it against the exact private source, without publishing that source. Hardware GUI/EPG native import/24h scheduler acceptance and any later installable-beta decision remain separate and NOT TESTED. The owner waived the real opkg rollback rehearsal, which must remain recorded as NOT_TESTED_WAIVED_BY_OWNER.


## 2026-10-16: canonical assembler reconciles the real private rescue — offline PASS

The public canonical offline assembler now uses `tools/reconcile_rc80_private_mapping.py` (SHA-pinned method-only merge), and `tools/audit_rc80_package_manifest_readonly.py` requires the exact SHA of the privately reconciled GUI when the original receiver GUI SHA matches the audited RC79.1 baseline. Eight existing Auto/Manual Mapping APIs and all unreviewed UI methods and layout remain unchanged.

**Executed privately on an offline staging host (not merely mocked):** `tools/assemble_rc80_one_shot_offline.py` successfully consumed the exact SHA-256-pinned RC79.1 rescue and the reviewed public overlay exported by GitHub Actions. The canonical builder produced a 215-file inspection IPK (140 Python + 75 assets) whose SHA256 was `7c7e476c4e6fefb9c43de4d354e7307555fbe3a674634871fa269f408c644dee`, size 667,666 bytes. A separate check-only pass reproduced the exact package digest without installing. All 140 Python sources parsed. The canonical package and the independently assembled private inspection candidate have **214/215 byte-and-mode-identical files; the only difference is `version.py`**, reflecting their deliberately different candidate labels. All runtime functional module bytes match.

The private rescue, original receiver source, reconciled source and user settings were **not uploaded to GitHub**, and neither `opkg` nor the receiver was accessed. The canonical IPK is `0600`, with a mandatory unconditionally blocking `preinst exit 90`; it remains **NOT INSTALLABLE**. Python 3.13/3.14 synthetic tests passed for the updated canonical build flow at [run 38983197591](https://github.com/wacayoub/EPGManager/actions/runs/38983197591).

**Next gate:** separate supervised, installable *test* package preparation and controlled Enigma2 GUI, native GitHub EPG import, auto/manual mapping persistence, source health, and full scheduler/timezone smoke tests on safe hardware. This private inspection artifact must never be installed. Real opkg rollback remains `NOT_TESTED_WAIVED_BY_OWNER`, not PASS. No production release, `update.json` change or RC79.1 receiver modification is authorized by these offline results.

## 2026-10-16: isolated eight-case EPG behavior replay on actual private RC80 IPK

Against the SHA-256-pinned 215-file canonical private inspection IPK, a separate *local offline* harness ran the actual `github_match_health.classify` function in isolation, with fixed clock and synthetic inputs. The eight scenarios passed: missing report → UNKNOWN, stale report → UNKNOWN, proven no EPG → NO_EPG, future slots without quality proof → EPG_WARN, quality-certified live EPG → EPG_OK, majority generated placeholders → SYNTHETIC, repeated low-diversity events → SUSPECT, and failed quality row → EPG_WARN. No XMLTV or user mappings were accessed.

Actual 254 method ASTs from the original private Mapping UI were compared with the RC80 IPK. Only the four reviewed Best Match preview/health methods changed. `ui/main.py`, `ui/settings.py`, `ui/theme.py`, `plugin.py`, `core/epg_cycle.py` and `core/direct_native_import.py` remain byte-identical to the RC79.1 baseline. The new Overview/menu redesign is **not** included in this conservative RC80 candidate and must not be advertised as implemented.

Read-only lab-handoff PR #11 was merged into this staging branch after Python 3.13 and 3.14 tests passed. VuLink's previous relay issue had request comments only and did not independently verify receiver connectivity.

**Still NOT TESTED:** RC80 GUI/remote controls on hardware, native XMLTV import end-to-end, retained manual/auto mappings after reboot, source programme freshness on the box, and a complete Casablanca-local 24-hour scheduler cycle. There is no authorized installable package or production release. Hardware opkg rollback remains `NOT_TESTED_WAIVED_BY_OWNER`. The blocked inspection IPK MUST NOT be installed.
