# RC80 — Steps 4/5 and 5/5 evidence, 2026-10-17

Status: **NO-GO for production publication**. No receiver install or opkg action was performed.

Independent offline recheck of owner-provided private RC79.1 rescue IPK: SHA256 `117e9a562b557c9afc18366d02392a11031a7178dfcacaeb86b9d756c2bb6a69`; 138 Python modules. The separately archived RC80 *inspection* package SHA256 `ca43c8953a6c811cdcd5b36689c59d3773f47a7211874931958e251e6731dd49` contains 140 Python modules, 75 preserved assets, zero syntax errors, schema 15, and an unconditional `preinst exit 90`.

**Important branch ambiguity:** this inspection archive modifies 15 methods within `ChannelMappingScreen` (while retaining 254), unlike the documented canonical SHA256 `7c7e476c4e6fefb9c43de4d354e7307555fbe3a674634871fa269f408c644dee`, whose source-parity report limits the modified mapping methods to 4. Do NOT substitute the 667592-byte inspection archive for the 667666-byte canonical candidate.

The separate `safe_postinst.sh` patch for reserved lab version `2026.10.17-rc80-lab1` was run against an extracted real 140-source staging copy with an altered *temporary* version label. Static check passed; no Enigma2 GUI, import, scheduler or reboot was tested. Local synthetic tests: 10/10 PASS. The lab patch is not present in the remote branch because remote file updates were blocked.

Required before publication: reproduce and verify the canonical artifact, separately controlled lab build, evidence from real Enigma2 GUI and remote keys, native GitHub XMLTV import into eEPGCache, manual/automatic mapping persistence, Casablanca-local 24-hour scheduling, source freshness and EPG retention. The owner waived real opkg rollback rehearsal; record `NOT_TESTED_WAIVED_BY_OWNER`, never PASS. Publication and update manifest remain blocked.
