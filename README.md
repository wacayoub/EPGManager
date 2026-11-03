# EPGManager

> **Vu+ release: NO-GO.** Source CI and `feeds/mena.xml.gz` on `main` are
> source diagnostics. They do not certify the canonical receiver publication
> on `mena-data`, hardware validation or permission to refresh Enigma2.
> The historical installer and release instructions below are not a release recommendation.

EPGManager now contains both the Enigma2 plugin and its MENA source engine.
Provider scrapers, publication guards and the 14 direct feeds live in this repository.
The receiver downloads finished XMLTV; it does not run the provider scrapers.

See [source engine and schedules](docs/SOURCE_ENGINE.md) and
[consolidation verification](docs/REPOSITORY_CONSOLIDATION.md).

The following installer information describes the historical release channel;
this repository consolidation does not install or update the receiver.


Enigma2 / OpenATV EPG Manager with Native Import, Smart Mapping and online updates.

## Install from GitHub

Permanent installer URL:

`https://raw.githubusercontent.com/wacayoub/EPGManager/main/install.sh`

Recommended SSH command on OpenATV:

```sh
wget -qO- https://raw.githubusercontent.com/wacayoub/EPGManager/main/install.sh | sh
```

The installer reads the permanent `update.json` manifest, downloads the current IPK, verifies its file size and SHA-256, then installs it with `opkg`.

After installation, restart the Enigma2 GUI.

## Online Update

Starting with **2026.10.04-rc76**, open **Plugins > EPG Manager Online Update**.

- **GREEN**: install a newer GitHub release.
- **YELLOW**: check GitHub again.
- **BLUE**: force reinstall the current GitHub release.
- The updater verifies the IPK size and SHA-256 before calling `opkg`.
- After a successful update, EPG Manager offers to restart the Enigma2 GUI.

The permanent update manifest is:

`https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json`

## Automatic release system

The repository contains the current plugin source package under `source/` and a GitHub Actions workflow at `.github/workflows/build-release.yml`.

To publish the current version:

1. Open **Actions** on GitHub.
2. Select **Build and Publish EPGManager**.
3. Click **Run workflow**.
4. Optionally enter release notes.

The workflow automatically:

- restores the current source package;
- builds the IPK;
- calculates SHA-256 and file size;
- creates or updates the GitHub Release;
- uploads the IPK;
- regenerates `update.json`;
- commits the new update manifest to `main`.

The Enigma2 plugin checks this permanent manifest URL:

`https://raw.githubusercontent.com/wacayoub/EPGManager/main/update.json`

The active source package is selected by `source/current.txt`.


## Direct MENA feeds

EPGManager contains source collection, per-source LKG and all direct publications. The former EPG-Scrapers repository is being retired; its history is preserved under `archive/epg-scrapers/*` tags here. See [migration and archives](archives/epg-scrapers/README.md) for current URLs and old-client limitations. The source aggregate on `main` preserves catalogue IDs and reports missing guide data. It has a different contract from the canonical receiver shards and MENA/Premium split on `mena-data`. The legacy full publication workflow is retired; the unified source checks do not replace its release gates.

The source screen prioritizes these direct feeds, supports a **Direct MENA only** filter with key `2`, and shows cached channel/coverage metadata when available. GoBX is intentionally not exposed until a validated feed is published.


## 6.2.1 multinational fix

STARZPLAY is temporarily hidden from active direct sources. Multinational channel feeds are expected to use original English titles with Arabic descriptions; Arabic-native channels keep Arabic titles and descriptions. National Geographic Abu Dhabi remains an Arabic-native exception.


## 6.3.0 direct-feed mode

Historical 6.3.0 behavior: receiver-side scraping was disabled. The current unified engine publishes source candidates and diagnostics; a fresh canonical receiver publication and hardware validation remain separate unmet requirements. Settings remain receiver-focused. Legacy local generators stay hidden from Sources and Smart Mapping.
