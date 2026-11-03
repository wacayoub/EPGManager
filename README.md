# EPGManager

EPGManager contains the Enigma2 plugin source and the source engine for its
14 direct EPG feeds. GitHub Actions collects provider schedules, verifies
publication bundles and preserves the last known good feed on failure.
The receiver downloads finished XMLTV.

> **Vu+ release: NO-GO.** Source CI and `feeds/mena.xml.gz` on `main` are
> diagnostics. The canonical receiver publication on `mena-data`, hardware
> validation and permission to refresh Enigma2 remain separate requirements.

## Sources and schedules

- [Source engine, Morocco local schedules and publication contract](docs/SOURCE_ENGINE.md)
- [Per-source review](docs/PER_SOURCE_SCHEDULE_AUDIT_20261103.md)
- [Source cleanup and remaining guide defects](docs/SOURCE_CLEANUP_20261103.md)
- [GitHub source-trigger checks](docs/GITHUB_SOURCE_TRIGGERS_20261103.md)
- [Morocco seven-day coverage](docs/MOROCCO_SEVEN_DAY_GUIDE.md)
- [Arryadia Smart EPG and Botola](docs/ARRYADIA_HD_SMART_BOTOLA.md)
- [Arryadia HD1 and local TNT official programme activation](docs/ARRYADIA_HD1_TNT_OFFICIAL_ACTIVATION.md)
- [Receiver release blockers](docs/VU_RELEASE_NO_GO_20261103.md)

Direct bundles are published under `feeds/<source>.xml.gz`, with matching JSON
metadata and TXT channel inventories. `reports/` contains current source
diagnostics, byte-pinned publication decisions and producer receipts.

## Repository layout

| Path | Purpose |
|---|---|
| `.github/workflows/` | Source collection, recovery, postprocessing and verification |
| `tools/` | Active adapters, publication guards, diagnostics and regression tests |
| `tests/` | Plugin source migration and publication tests |
| `config/` | Source, language and schedule policies |
| `data/` | Arryadia models/history and translation caches used by collection |
| `source/` | Selected plugin base package and maintained source overlay |
| `feeds/` | Fourteen direct bundles and the diagnostic MENA aggregate |
| `docs/` | Operating contracts, audits and migration records |
| `archives/epg-scrapers/` | Consolidation provenance and transferred issue index |

`source/current.txt` selects the retained plugin base ZIP. Changes are in
`source/overlay/`. The obsolete rc70 release builder has been removed; this
repository currently has no enabled plugin release workflow.

The historical installer is `install.sh`; its manifest is `update.json`.
These existing endpoints and the published package remain unchanged by cleanup.
The old package does not acquire new overlay changes automatically.

## Validation

Install `requirements.txt` and PyYAML, then run:

```sh
python -m unittest discover -s tools -p 'test_*.py' -q
python -m unittest discover -s tests -p 'test_*.py' -q
python tools/verify_unified_repository.py
```

CI also rebuilds and verifies the source aggregate on Python 3.13 and 3.14.

## Migration and historical files

EPGManager is the only active repository. EPG-Scrapers is archived; permanent
deletion remains blocked by the current token's missing `delete_repo` permission.
See [migration details](archives/epg-scrapers/README.md),
[consolidation](docs/REPOSITORY_CONSOLIDATION.md) and
[repository cleanup](docs/REPOSITORY_CLEANUP_20261103.md).

Retired probes, experimental workflows, inspection copies and old package
versions are available in Git history at `archive/pre-cleanup-20261103`.
