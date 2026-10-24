# RC80 live UTC qualification — 24 October 2026

This follows the owner's approval to finish Mapping/Best Match and a temporary
scheduled native import, preserve mappings/programmes, then use 13:15 GMT+0.

## Measured baseline

Authenticated LAN SSH and OpenWebif through a loopback SSH forward succeeded.
Installed version is 2026.10.20-rc80, schema 15, 142 Python modules; receiver
Python is 3.14.7 and EPGImport is installed. These are fresh receiver results,
not inferred from repository HEAD. The initial probe did not certify GUI/import.

The real GUI subsequently opened EPGManager and Smart Mapping. The existing 2M
owner remained selected; Best EPG Match showed Morocco 2M at Match 100% with
CHECK and an actual programme, and alternatives with CHECK. No apply/save/undo
mapping operation was invoked. This is bounded navigation/quality-label evidence,
not validation of all channels or mapping mutation flows. Opening suggestions
was visibly slow; no UI speed improvement is claimed.

The main screen reported all 14 direct feeds and Morocco 6/15 with future EPG.
This does not mean every declared channel has a usable guide. External missing
guides and editorial issues from EPG-Scrapers PR #55 remain separately scoped.

## UTC help correction

Actual settings show GMT+0 in the time row but still say “Daily Casablanca local
time” in the contextual help. The offline timing transformation now corrects
both label and help to GMT+0 (UTC), without changing ConfigClock values or the
scheduler. All 15 existing timing/package tests pass on workstation Python 3.13.2.
All five SHA-pinned transformation preimages and parsed outputs were checked;
the resulting help is explicitly UTC. No new package was installed for this
text-only correction. Previously sealed packages retain their recorded bytes.

Qualification CI now also covers PRs targeting the Rytec branch. Both Linux
Python 3.13/3.14 jobs pass 302 regressions plus four probe tests each; both job
logs were inspected. Tested commit bd6a51701010ceb607e0ed6427ad7fdb3d24e702:
https://github.com/wacayoub/EPGManager/actions/runs/40046214331 .

## Live import trial

The original native schedule was enabled at 10:00, with clear-before-import false.
The relevant native settings were backed up privately, and protected mapping,
learning, SRP and preference hashes were recorded without exposing contents.
A bounded before sample contains 3,923 events on 32 services.

The owner-approved temporary slot was saved as 00:35 GMT+0 through the real GUI.
The actual scheduler log records the new import starting at 00:35:13, 13 seconds
after the intended slot, without a newly applied five-minute boot grace.
OpenWebif remained responsive during preparation: 0.346/0.086/0.067 seconds
for three API requests. These samples do not certify continuous UI latency.

The plan actually completed in 423.98 seconds: 11,013 receiver services,
112,661 prepared IDs, 326,749 refs and 68 ready sources. Measured phase timers
were 138.084 seconds for seed ownership and 261.136 seconds for winner expansion.
The source_build_overlay_ms timer is cumulative across earlier stages and must
not be added again. Sampled Enigma2 RSS reached 768.9 MiB. These are measured
performance limitations, not an optimization success or a controlled regression
comparison against the previous day's different cache/input state.

A separate SSH round trip measured the receiver 17.911 seconds ahead of the PC
midpoint (0.461-second round trip). The PC is not independently established as
an authoritative UTC clock. No clock/NTP setting was changed. The 13-second slot
lateness above is measured against the receiver's own configured UTC slot.

## Observed completion and conservation

The new cycle finished at receiver 00:55:50 (1,237.18 seconds total). Actual
Plugins.Extensions.EPGImport.EPGImport completed 394,472 event insertions,
zero failed insertions, 4,137 written services, 68 prepared sources and
807.952 seconds in the native phase. The scheduler acknowledged success at
00:56:31. There was no requested reboot. These are actual new import results,
not the previous day's 394,329-event record. A nonzero import is not proof
that every source or channel had future data; three sources were unmapped.

The completed scheduler result reports selective_flushed_services=0,
selective_flush_failures=0 and stale_cleanup skipped=preserve_existing_epg.
No purge/reset/install/restart was requested by this trial.

Of 3,923 initial events sampled on 32 services, six expired during the test.
Among 3,917 still unexpired, 3,810 retained identical fingerprints; 101 changed
records had their full original remaining time interval covered. Six changed
intervals had only partial coverage, and zero disappeared entirely. The after
sample contains 4,054 events. The partial cases are explicitly unresolved;
this sample does not establish universal programme conservation or certify
that every provider revision was appropriate.

All five protected mapping/learning/SRP/source-preference files retain their
original SHA256 values. The final native schedule is enabled at 13:15 GMT+0,
clear-before-import remains false, and native_import_time is the only changed
native JSON setting. The temporary 00:35 slot is no longer configured. Saving
used the real GUI and readback; no restart was required.

## Programme time checks and limits

All 14 public XMLTV payload hashes matched their metadata and their parsed
timestamps explicitly use +0000. A read-only generated-map/native-cache sample
queried 158 services (142 populated), with zero query errors. It found 3,155
exact begin/duration matches and 2,947 exact title/begin/duration matches across
13 sources. These count service/event pairs, not unique programmes.

For the 2M/Morocco owner observed in the GUI, every one of 55 native-cache
events on each of ten sampled equivalent refs matched the real donor title,
begin and duration exactly; no title match required a one-hour shift. Al Aoula
had 68 exact title/time/duration matches on its sampled ref and no shift-only
donor matches. Existing extra events are not automatically marked erroneous.

The broader map-based sample also produced shift-only title collisions on other
provider IDs (including ElCinema 2M Monde, OSN news and beIN Series 2). Generated
maps do not by themselves establish current saved ownership, and repeated titles
can collide across hours. Neither these collisions nor aggregate matches prove
an all-channel timezone PASS/FAIL. They remain outside this bounded acceptance.
The separate 32-service global fingerprint scan found 47 exact matches and one
shift-only fingerprint; this similarly does not establish channel identity.

## Verdict

GO for the observed automatic UTC-slot launch and real non-purging native import
on this receiver, with unchanged mappings/preferences and final 13:15 setting.
NO GO for claiming all guides restored, universal time/retention correctness,
or fully optimized RC80. Remaining work includes the six partial time intervals,
provider/owner-specific investigation of ambiguous time comparisons, missing
external schedules/editorial quality, and the measured costly ownership passes.
Manual apply/undo, all-channel languages and unattended future days are not newly
certified by this trial. The small help correction is reviewed/tested in source,
not installed, and no public final release is promoted.
