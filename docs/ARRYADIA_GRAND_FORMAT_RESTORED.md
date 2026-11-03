# Restored Arryadia HD2/HD3 Grand Format

The user confirmed **Grand Format every hour on HD2 and HD3 only**. Smart forecasts on HD/TNT remain disabled. The existing `arryadia_hourly_grand_format.py` implementation survived in the repository, but was not connected to the unified production collector. This integration omission made both services disappear from future-EPG coverage despite the earlier viewer-reported solution.

The Morocco collector now invokes that implementation by default. It creates one-hour calendar slots on HD2 and HD3 over the requested collection window, keeps the French title **Grand Format** and the Arabic description explaining the viewer observation, and gives announced same-channel programmes priority over every intersecting recurring hour. Other Arryadia variants do not receive this recurrence.

Every generated row carries the XMLTV category **Viewer-reported recurring schedule**, which survives the source packer, last-known-good reload and derived MENA publication. Quality reports explicitly count these channels and programmes as viewer-reported. They remain usable in the EPG while requiring editorial review, without claiming current official SNRT announcements. Recognized declared recurrence is not reported as unexplained channel-title filler.

Official source status is still reported independently. An old September grid can stay `STALE_OFFICIAL_GRID` while the output includes the user-authorized HD2/HD3 recurrence. History learning never treats these generated slots as official observations.

Regression tests cover 24 hourly slots per channel per day, service scope, priority of announced programmes, default activation during an outage, LKG provenance, packing, derived output and prevention of an official-quality PASS. These tests protect the solution against future unification or cleanup changes.

The source can show 11/15 channels with future data when the previous nine source guides remain available: **nine source guides plus two viewer-reported recurring schedules**. Vu+ release stays **NO_GO**, with no receiver change.
