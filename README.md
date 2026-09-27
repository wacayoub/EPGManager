# EPGManager

Enigma2 / OpenATV EPG Manager with Native Import, Smart Mapping and online updates.

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

EPGManager 6.2.0 consumes the validated daily feeds published by `wacayoub/EPG-Scrapers` instead of scraping provider websites on the receiver. Direct providers include Morocco, beIN MENA, OSN, Shahid/MBC, ElCinema, Rotana, Dubai+, Sport24, STC TV and Al Jazeera. STARZPLAY is temporarily on hold and hidden from the active direct catalogue.

The source screen prioritizes these direct feeds, supports a **Direct MENA only** filter with key `2`, and shows cached channel/coverage metadata when available. GoBX is intentionally not exposed until a validated feed is published.


## 6.2.1 multinational fix

STARZPLAY is temporarily hidden from active direct sources. Multinational channel feeds are expected to use original English titles with Arabic descriptions; Arabic-native channels keep Arabic titles and descriptions. National Geographic Abu Dhabi remains an Arabic-native exception.
