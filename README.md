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
