# Rytec native import repair

Rytec shares one official channel-reference map among country/programme feeds.
RC80 arbitrated those shared rows to a single source/ID before reading actual
programmes. A selected feed could lose all relevant mappings to a different
feed, including one containing no programmes for those IDs. The registration
path also reported zero channels/references for every official Rytec map.

The repair preserves shared official Rytec identities until EPGImport parses
the actual feed. Explicit saved targets and unrelated preferred sources retain
their ownership. Rytec adapters use the receiver-installed EPGConfig source
definitions, all programme and channel-map mirrors, EPGChannel custom-map
handling and refresh interval. Source-specific protection and saved overlays
never mutate EPGImport's shared channel map. Existing programme retention,
clear-before-import policy and mapping-store bytes are unchanged.

Five hash-pinned modules change; module count, schema and native engine remain
unchanged. The package builder verifies the complete 217-file previous payload,
creates the existing guarded backup/verification hooks and retains passive
removal hooks. No deployment occurs from the builder or CI.

Validation before installation:
- 25 targeted regression tests pass on local Python 3.13.
- Live SSH: installed 2026.10.20-rc80/schema 15, Python 3.14.7, 142 modules.
- Official cached XML: 3051 raw IDs, 10487 channel elements; 2135 IDs and 3711
  reference rows overlap the cached receiver registry.
- The installed native EPGConfig parser, executed separately with GUI config/log
  dependencies stubbed, loads 3014 normalized IDs and 10466 deduplicated refs
  for two native source definitions, each retaining three mirrors.
- Standalone parser checks are not GUI or actual eEPGCache import validation.

Hardware installation, GUI import results and conservation evidence must be
recorded separately after they have actually been observed. A provider-owned
mapping is not proof of future programme availability.

The existing zero counters are repaired on index reads using a shared, signature-
invalidated in-process cache of exact receiver reference matches. The on-disk
SRP index is not rewritten, and these links do not certify feed freshness.

Hardware import observed after the three-core-module installation:
- Verified 217 installed files, 142 modules compile on receiver Python 3.14.7.
- Verified 1,221,242-byte preinstall backup readback; 16 protected configuration
  files were byte-identical immediately after installation.
- One verified Enigma2 restart, 62.2 seconds, same kernel boot.
- Real GUI: highlighted France TNT in Sources, pressed Import Source without
  changing selected sources. Installed Plugins.Extensions.EPGImport.EPGImport
  wrote 12,584 events to 139 services, zero failures, 12.78 seconds import phase
  (23.2 seconds cycle). No purge requested.

GUI review also exposed repeated global provider-map totals and stale MAP NEEDED
labels. The consolidated two-UI-module follow-up counts a shared Rytec file once
on the overview and shows NATIVE/Shared SRP instead of pretending its global ID
list belongs to each country feed. Incomplete checks remain NATIVE MAP/unknown;
complete proven NO EPG results remain negative. The final combined artifact's
installation and conservation checks must be recorded separately.

Final consolidated hardware validation: the two-source France TNT + France Mixte
GUI batch imported 17,002 events to 180 services, zero failures, 20.28 seconds
native-import phase / 41.05 seconds total. The final overview shows 2,981 links
instead of repeating 115,871 shared-map claims. Source rows show NATIVE/Shared
SRP with unverified programme availability remaining unknown. Batch marks were
cleared without changing permanent source selection. All 217 installed files
match the consolidated payload; 142 modules compile on the receiver.

Six mapping/preference files remain byte-identical. Of 424 initial sample events
still unexpired at the last check, 359 retain identical fingerprints and 65 have
updated provider data on one service; all 424 original time slots remain covered.
One additional initial event expired normally during testing. Native donor
comparison matches all 224 checked cache intervals; 188 also match the plain
XMLTV title exactly (native parser formatting may differ). Other plugin code
(55 IPAudioPro + 17 SatIPTVBridge modules) is unchanged, and the initial tuned
service was restored using its unique existing registry reference.

GO is limited to the tested native Rytec mechanism on the owner's receiver;
all-country stress coverage, provider fault injection and Azman/Koala remain
unvalidated. Aggregate evidence: `evidence/rc80-rytec-native-installed-20261021.json`.
The single consolidated package SHA256 is
`6a8d00ebda2de0bc06a6c1eb27351d895f6c32badb10e243422d7f3e3a5ba3a6`.
