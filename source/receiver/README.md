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

## rc83 (installed on the receiver 2026-11-12)

`rc83/` is rc82 with two changes, covered by `tests/test_receiver_rc83.py`:

- `core/native_epgimport_bridge.py`, `core/epg_priority_guard.py`: a service
  counts as written only when it receives at least one programme that has not
  ended. A stale feed used to mark the service as owned, which turned off its
  SAT EIT and left the guide blank: 303 of 4,443 protected services on the
  receiver (BBC News, Bloomberg, Euronews, Disney Channel, Tunisia Nat 1/2, …).
  Services that only received finished programmes get SAT EIT back at the end
  of the same import.
- `core/direct_native_import.py`: the Fast Import plan is 2.3x faster
  (170 s to 73 s on the Vu+ Zero 4K, 72 selected sources) by caching
  duplicate-channel name keys, source ranking parts, the dead-feed check for
  sources that cannot have a verdict, and the shared-Rytec-map test. The plan
  was verified byte-identical to rc82's on the receiver.

## rc82 (installed 2026-11-12, replaced by rc83)

`rc82/` is rc81 with four fixes, covered by `tests/test_receiver_rc82.py`:

- `core/epg_priority_guard.py`: rc81 turned off satellite EIT (`dxNoEIT`,
  0x1000) on every DVB service that ever received an import and never turned
  it back on, so a service its feed stopped covering kept an empty guide
  (Al Jazeera English HD and Al Jazeera Documentary on the receiver). rc82
  records when each service was last written and returns SAT EIT after
  48 hours without EPGManager events. Upgrading releases nothing at once, and
  an import that wrote no DVB service releases nothing.
- `core/epgimport_export.py`: never writes `/tmp`, `/var/volatile` or `/run`
  paths into `/etc/epgimport/epgmanager.sources.xml`, since those files are
  gone after a reboot.
- `core/logger.py`: writes `epgmanager.log` to `/media/hdd/epgmanager/logs`
  when `/media/hdd` is mounted, so the log survives reboots and crashes.
- `core/direct_native_import.py`: when several feeds carry the same channel
  ID, a feed whose ID is proven empty (`github_direct_sync.dead_id`, NO_EPG)
  no longer keeps the service if another feed has a guide for it. Automatic
  saved owners compete again; manual mappings always win; UNKNOWN counts as
  live. With the 11 November feeds this affects 21 channels, including
  Thmanyah 1-3, Asharq Documentary, Sharjah TV, Oman TV and Al Kass 1-8.

Do not install rc82 on a receiver that is not known to be clean.
