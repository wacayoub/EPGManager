# EPGManager

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

EPGManager consumes validated per-source feeds published by `wacayoub/EPG-Scrapers` as an upstream source layer. EPG-Scrapers owns source collection and per-source LKG only. EPGManager is the sole owner of receiver canonical IDs, global selection/LKG, language arbitration, provider ownership, MENA/Premium split, release gates and the final `mena-data` publication. Direct providers include Morocco, beIN MENA, OSN, Shahid/MBC, ElCinema, Rotana, Dubai+, Sport24, STC TV and Al Jazeera.

The source screen prioritizes these direct feeds, supports a **Direct MENA only** filter with key `2`, and shows cached channel/coverage metadata when available. GoBX is intentionally not exposed until a validated feed is published.


## 6.2.1 multinational fix

STARZPLAY is temporarily hidden from active direct sources. Multinational channel feeds are expected to use original English titles with Arabic descriptions; Arabic-native channels keep Arabic titles and descriptions. National Geographic Abu Dhabi remains an Arabic-native exception.


## 6.3.0 direct-feed mode

Receiver-side provider scraping remains disabled. EPG-Scrapers generates validated source feeds; EPGManager Cloud consumes those candidates, applies the canonical receiver contract and publishes the final receiver-safe MENA artifacts. The Vu+ must never treat EPG-Scrapers' former derived MENA output as the authoritative receiver aggregate. Settings remain receiver-focused: XMLTV cache directory, mapping threshold, logging and debug. Legacy local generators stay hidden from Sources and Smart Mapping.
