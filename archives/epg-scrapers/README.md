# EPG-Scrapers migration archive

EPGManager is the single maintained repository for the plugin, source adapters,
collection schedules and published source bundles. The owner requested retirement
of `wacayoub/EPG-Scrapers` on 3 November 2026, accepting that old hardcoded raw
URLs stop working when that repository is deleted.

## Preserved material

- All 55 original branch tips and their complete ancestry are available through
  tags named `archive/epg-scrapers/<original-branch>`. The former default branch
  is [archive/epg-scrapers/main](https://github.com/wacayoub/EPGManager/tree/archive/epg-scrapers/main).
- `manifest.json` records exact original commits and backup checksums.
- `github-inventory.json.gz` contains UTF-8 JSON with branches, tickets, pull
  requests, comments and reviews. The original repository had no tags/releases.
  This is a historical export; the four open issues were also transferred to
  EPGManager. [Open work and issue transfers](OPEN_WORK.md) link their new numbers
  and the eight unmerged PR snapshots.
- A verified complete Git bundle is saved beside the local checkouts as
  `EPG-Scrapers-complete-20261103.bundle`; it is not a runtime dependency.

Historical workflows remain in Git history. Archive tags do not activate their
schedules. The old mirror utility is retained only for historical recovery;
EPGManager's workflows do not call it.

## Current publication URLs

For each source, use `https://raw.githubusercontent.com/wacayoub/EPGManager/main/feeds/<source>.xml.gz`.
Sidecars use the same base with `.json` and `.txt`. Source reports are under
`https://raw.githubusercontent.com/wacayoub/EPGManager/main/reports/`.

The current source overlay maps saved EPG-Scrapers feed URLs to EPGManager at
load time. An installed historical rc78 package still hardcodes EPG-Scrapers
in `core/external_sources.py` and `core/github_direct_sync.py`; deleting the old
repository does not update that package. Its migration requires a separately
validated plugin update or receiver configuration change. Neither is performed
by this repository cleanup.

Vu+ release remains **NO_GO**. The canonical `mena-data` publication and receiver
validation are separate from source migration; no receiver was modified.
