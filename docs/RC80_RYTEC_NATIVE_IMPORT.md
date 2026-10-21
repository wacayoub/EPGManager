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

Three hash-pinned modules change; module count, schema and native engine remain
unchanged. The package builder verifies the complete 217-file previous payload,
creates the existing guarded backup/verification hooks and retains passive
removal hooks. No deployment occurs from the builder or CI.

Validation before installation:
- 20 targeted regression tests pass on local Python 3.13.
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
