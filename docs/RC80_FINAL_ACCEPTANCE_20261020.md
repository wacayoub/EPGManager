# EPGManager RC80 FINAL — 2026.10.20

GO for the owner's qualified Vu+ Zero 4K / OpenATV 8.0.1 / Python 3.14.7.
This freezes the exact installed 704394-byte package; no application bytes,
version identifiers or installation hooks were changed after hardware validation.
All 217 installed application file bytes/modes were checked again over SSH at finalization.
No receiver write, installation, restart, settings/mapping edit or EPG import was needed.

Package SHA256: 99514e7c92ca6333931f469d03577beda5f5b73c890e3beca960adfc9d41cdbd
Version remains 2026.10.20-rc80, schema 15, 142 Python modules + 75 resources.
The archive contains the exact application payload, with bytes and modes checked;
no private receiver backups, settings, mapping exports or credentials are included.

Measured acceptance: actual Mapping/Best Match GUI navigation and cancellation;
native scheduled EPGImport verified separately; 277 suite + 4 probe tests per Linux
Python 3.13/3.14. The last observed native import succeeded with 15969 events,
0 failures and 739 services. Six protected mapping/preferences hashes and all
482 sampled nonexpired events were preserved in installed-workflow validation.

This IPK retains its exact-baseline and consent guards. It is a qualified upgrade
artifact, not a generic fresh-install or ordinary online-update package. The
already validated receiver needs no reinstallation. The source ZIP is an archive
of application files, not an installer. Do not execute historical build scripts
from it as a release procedure.

Public general rollout is separate: the retired legacy release workflow still
references RC70 and remains disabled; update.json/source/current.txt are not changed.
No general GitHub Release or automatic-update promotion is claimed here. The final
artifact is retained locally; public evidence records its immutable digest.
Other images, new live mapping mutations, forced source outages, and exhaustive
channel/language coverage remain outside the measured scope.
