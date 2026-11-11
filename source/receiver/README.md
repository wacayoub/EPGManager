# Receiver plugin source

`rc81/` is the EPGManager plugin as installed on the Vu+ Zero 4K
(OpenATV 8.0.1), package `enigma2-plugin-extensions-epgmanager`
version `2026.11.10-rc81`.

It was copied from the receiver on 11 November 2026, using the file list in
`opkg files enigma2-plugin-extensions-epgmanager`. That covers 218 files, with
no compiled `.pyc` files. Every installed file matched the package list, and
none had changed after installation.

| Path | Content |
|---|---|
| `rc81/EPGManager/` | Plugin files, installed under `/usr/lib/enigma2/python/Plugins/Extensions/EPGManager/` |
| `rc81/CONTROL/` | The opkg `control`, `preinst`, `postinst`, `prerm` and `postrm` files from `/var/lib/opkg/info/` |
| `rc81/SHA256SUMS` | SHA-256 of each plugin file, computed on the receiver |

`tests/test_receiver_source.py` checks these files against `SHA256SUMS`, so
the snapshot stays identical to what the receiver runs. `.gitattributes` marks
this folder `-text`, so Git never changes line endings in it.

## Relation to the other source folders

- `source/current.txt`, the rc70 ZIP and `source/overlay/` are older. The ZIP
  contains a v6.0.1 tree, and the overlay is based on rc70. Neither matches
  the receiver.
- `update.json` still offers rc78, and no rc81 `.ipk` is published as a GitHub
  release. The Online Update screen on an rc81 receiver therefore reports that
  the installed version is newer than GitHub.

## Making changes

Do not edit `rc81/` in place. Copy it to a new version folder (for example
`rc82/`), change the copy, then build and publish the package. Update
`update.json` only when the new `.ipk` and its SHA-256 have been released.

Known defect to fix in the next version: `core/epg_priority_guard.py` turns
off satellite EIT (`dxNoEIT`, 0x1000) on every DVB service that ever received
an import, and only turns it back on during an explicit reset. When a feed
later stops covering a service, that service keeps an empty guide. On the
receiver this affects Al Jazeera English HD and Al Jazeera Documentary.
