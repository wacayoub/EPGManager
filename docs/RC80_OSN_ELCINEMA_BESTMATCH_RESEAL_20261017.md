# RC80 OSN + ElCinema + Best Match consolidation: public blob reseal (2026-10-17)

**State: REVIEW ONLY / NO-GO FOR LIVE INSTALLATION OR RELEASE.** This branch starts from PR #14 head `b5237d3570ae1f9847367d8260727618ef3c1e8b`, which includes the merged OSN staged fix #12 and ElCinema correction #15. It is intentionally separate from RC80 sealed staging PR #9, production `main`, the updater manifest, and the user's Vu+ receiver.

## Public overlay source identity review

The **actual GitHub content blobs**, read from the reviewed PR #14 branch, are:

| Path | Prior approved Git blob | Reviewed new Git blob |
|---|---|---|
| `ui/channel_mapping.py` | `26165496d9c530058dbdbc858c34794c0a4fb1a2` | **unchanged** |
| `core/verified_epg_match.py` | `03d5b779ec639ed7e8f82f7205cf34af4e4af56d` | `6cc0e755b8f2c15065402f560ed8705358801872` |
| `core/verified_epg_refresh.py` | `ce9c57773c739977782d22c373993a81e3a68361` | `fae7c10b0f3e87aa8217fef3a0ba7ac11c38248d` |

The latter two identities are updated **together** in `tools/assemble_rc80_one_shot_offline.py` and `tools/audit_rc80_package_manifest_readonly.py`. No source-level guard or negative check is removed. These SHA-1 values are Git blob identity pins, *not* a replacement for the private rescue IPK SHA-256 integrity requirement.

## Functional handling of incomplete programmes

- OSN: source-wide 48h FAIL due to 19 IDs with **NO_FUTURE_EPG** does not erase current legitimate events from the other 82 IDs. The zero-ID set must match the SHA-checked published XMLTV exactly; these 19 stay non-auto-mappable.
- ElCinema after official midnight fix: the 2026-10-17 13:21 UTC publication has 97 IDs, 53 48h PASS, 9 SHORT_HORIZON warnings, 35 TOO_SHORT failures, 0 large 12h holes, 0 invalid timestamps and 0 orphans. Authentic short guides remain manual review/yellow and are **not** green, with no fabricated extension.
- ElCinema per-ID evidence, verified against that publication by prior focused CI: 61 actual current IDs green, 35 too-short yellow/manual, 1 placeholder disallowed. This is not a claim of full 48h EPG on 97 channels; each subsequent refresh needs a fresh audit.
- Runtime preview and independent read-only provenance checks preserve the existing manual mapping ownership. `REAL_CURRENT_EPG` only after valid per-ID verification, not merely a provider status/metadata timestamp.

## Non-negotiable build/hardware gates

1. The only acceptable original baseline is the **private receiver-matching** RC79.1 rescue IPK SHA-256 `117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69`, 138 original Python files / 75 other assets, schema 15. Do not upload the rescue, mappings, original source tree, programme data or credentials to GitHub.
2. The canonical candidate SHA-256 `7c7e476c4e6fefb9c43de4d354e7307555fbe3a674634871fa269f408c644dee` was verified **before** these overlay blobs changed. It is **not** a passing artifact for this newly pinned source set. A new canonical candidate must be rebuilt offline against the exact private baseline; no candidate IPK was constructed here.
3. The offline builder must retain permanent `preinst exit 90`, passive other hooks, matched version/schema 15 and absence of any receiver/update.json changes. No installable package is authorized.
4. Owner's last successful **RC79.1** hardware baseline: tuned DVB 2M cached EPG had 61 valid events (1 NOW, 60 future), both NOW/NEXT endpoints worked. This proves only current EPG-cache visibility, **not** RC80 native import, GUI, mapping persistence, source provenance or 24-hour scheduling. Do not repeat this test.
5. Actual `opkg` rollback remains `NOT_TESTED_WAIVED_BY_OWNER`, not PASS. The real RC80 GUI, native GitHub direct import, mapping and scheduler tests remain `NOT_TESTED`.

## Promotion policy

CI may certify public-branch source integrity and **synthetic** regression correctness only. Keep PRs DRAFT, leave the existing receiver on RC79.1, do not merge to public release branches, do not modify update.json, and do not claim RC80 final/installed. Final release depends on separately vetted private blocked-build parity and controlled receiver acceptance.
