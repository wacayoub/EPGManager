# Repository cleanup — 3 November 2026

The maintained tree was reduced from 953 tracked files by removing 556 obsolete
files (66,576,856 bytes). This is the reduction in checked-out file content;
Git history remains intact and is not rewritten to reduce clone size.
The complete prior tree is preserved at
[`archive/pre-cleanup-20261103`](https://github.com/wacayoub/EPGManager/tree/archive/pre-cleanup-20261103).

## Removed

| Category | Files |
|---|---:|
| Retired workflow/test/feed archives, analysis downloads and encoded duplicates | 235 |
| Inspection copies, unused triggers and retired cache/config files | 73 |
| Superseded documentation and generated monitoring tables | 8 |
| Historical probe/test output reports | 102 |
| Superseded plugin packages | 6 |
| Unreferenced legacy tools and the retired inline-workflow test | 131 |
| Disabled historical rc70 release workflow | 1 |

Tool removal followed references from the active workflows, shell collector,
plugin overlay and current tests, including transitive local module imports.
The removed inline-workflow test only exercised the archived MENA workflow;
the maintained receiver contract/release guard and their regressions remain.
Historical scratch probes can be restored from the archive tag if needed.

## Retained

All 14 direct source bundles and their JSON/TXT sidecars, the diagnostic MENA
aggregate, current publication/quality/health reports, producer-run receipts,
source policies and Morocco local schedules remain intact. Report inputs used
by the current monitoring command are retained even if their producer is retired.

Arryadia history, Smart recurrence model, Botola/official-grid code, HD2/HD3
Grand Format recurrence and translation caches remain. `source/current.txt`
and its selected rc70 ZIP remain with the maintained overlay; only older ZIPs,
base64 duplicates and obsolete patching/inspection copies were removed.

The direct-stream migration helper and receiver preflight/release guard are
retained as supported manual entry points. EPG-Scrapers migration metadata,
archive refs and transferred issue tracking remain available.

The old release workflow was already disabled and has been removed entirely.
No replacement release pipeline is enabled. `install.sh`, `update.json` and
published release assets were not changed. Receiver release remains **NO_GO**;
the canonical `mena-data` branch and the receiver are untouched.

## Validation

The remaining tool suite, plugin migration/publication suite, all 14 published
bundle checks, workflow references and derived-feed/readiness reconstruction
are exercised by the existing Python 3.13/3.14 CI workflow.
