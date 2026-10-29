# RC80 dashboard hidden by incomplete MetrixNEO screen

## Actual receiver diagnosis

Read-only LAN SSH on 29 October 2026 measured Python 3.14.7, 142 plugin Python
modules, and the existing 217-file qualified baseline with matching bytes/modes.
The active theme is MetrixNEO. Its skin.xml defines EPGManagerMainScreen with
24 widgets: headings, navigation, runtime labels and progress. It contains none
of the 81 functional KPI/card/button widgets required by the dashboard. That
explains the user's screenshot: headings and status remain, all dashboard
cards and bottom actions are absent. This is independent of guide availability.

The installed OpenATV Screen constructor and readSkin bytecode both implement
mandatoryWidgets. An external definition missing one is rejected and the
plugin's embedded skin is selected. This behavior was checked against the
actual installed bytecode, not inferred solely from upstream source.

## UI-only candidate

The SHA-pinned transformation adds one mandatoryWidgets assignment immediately
after Screen initialization in EPGManagerMainScreen. It requires the 21 KPI
content widgets, 56 source-card content widgets and four colored action labels.
Decorative rails/backgrounds remain optional so complete third-party themes
are accepted. No global theme, configuration, mapping, cache, import, scheduler
or source-selection code is changed. AST comparison proves every existing
statement is unchanged after removing that assignment.

The guarded candidate IPK is 712,444 bytes, SHA256
0cd1cbb9e6c8c7fa99e97d9b75afe87cadb49844c98b4e25003ff8a86069dcc2.
It changes only ui/main.py; 216 other payload files/modes remain identical,
with 142 Python modules total. Exact baseline, explicit consent, verified
backup/readback, passive removal hooks and installed-tree verification remain.
It is a workstation-built candidate and has not been installed.

## Verification and limits

- Seven new regression tests pass on workstation Python 3.13.2.
- The embedded layout's 147 widgets remain in bounds at both 1920x1080 and
  1280x720 using the actual unchanged compatibility scaler.
- In-memory execution of the receiver's installed readSkin selector reproduced
  the broken override before the correction; with the actual candidate
  assignment it selected the complete embedded screen, and still accepted a
  complete external screen. Selection was stopped before GUI creation. No
  receiver files, mappings or programme data were changed.
- Six protected mapping/learning/SRP/source-preference/native-settings file
  hashes were captured privately as a fresh pre-install baseline. No raw
  settings or mappings were exported.
- The full Windows suite reports 317 tests, seven failures, twelve errors and
  three skips. Inspected failures concern existing POSIX file-mode and shell
  fixtures on Windows; do not call that run green. Linux Python 3.13/3.14 CI
  must pass separately; final results are linked in the PR.

The native GUI has not yet been tested with this candidate. Installation with
verified backup, one Enigma2 restart and actual on-screen verification require
the owner's separate explicit approval. Receiver changed: false. No EPG import
is necessary to validate this display-only correction.
