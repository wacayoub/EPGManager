# Codex instructions — EPGManager Vu+ Zero 4K

This repository includes experimental RC80 branches. The user's operational receiver is OpenATV 8.0.1, Python 3.14.7, EPGManager RC79.1 (schema 15) until **measured evidence** proves otherwise. Never infer the installed state from the repository HEAD, prior logs, or offline CI alone. \`source/current.txt\` on \`main\` may refer to an older packaged release; preserve the working device.

## Goal

Improve EPGManager RC80 by comparing live read-only hardware evidence with its source and the current EPG-Scrapers MENA publication, then make reviewed fixes in a Git branch. Codex runs on a trusted PC on the Vu+'s local network, and reaches **only the user-authorized receiver** through a previously verified SSH host alias \`epg-vu\`.

## Receiver interaction contract

1. Initial diagnostic phase = **read only on receiver**. Valid test: \`ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'python3 -' < codex/receiver_readonly_probe.py\`.
2. Do not ask for or output SSH passwords, tokens, private keys, cookies, receiver mapping JSON, user settings, XMLTV programme logs or full process arguments. Keep credentials in the user's own OS SSH agent / secure configuration; never commit or post them.
3. Do not run \`opkg install\`, \`opkg remove\`, \`opkg upgrade\`, \`wget | sh\`, \`rm\`, \`mv\`, \`cp\`, \`sed -i\`, \`systemctl restart\`, \`init 4\`, \`init 3\`, or any remote write/GUI restart until **separate explicit user approval for that specific operation**. Do not treat this Codex task as permission to install RC80.
4. Do not edit \`/etc/enigma2\`, \`/etc/epgimport\`, mappings, source preferences, EPG cache, scheduler, credentials, or the 138-module RC79.1 operational plugin in phase 1.
5. Running local/offline unit tests in a disposable working tree is allowed. Git commits to a dedicated branch and a PR are allowed, but never install or push unreviewed candidate bits to the receiver.
6. The receiver's \`/usr/lib/enigma2/python/Plugins/Extensions/EPGManager\` is evidence for installed plugin code. If receiver shell/SSH is not available, mark **SSH_NOT_TESTED**, report which prerequisite is missing, and continue static/offline auditing. Never claim successful SSH, GUI or live EPG import without actual evidence.
7. An SSH alias is an address resolution/credentials convenience, **not a safety sandbox**. Review any extra remote command. No public router port forwarding for port 22; use LAN access or a deliberately configured private VPN.

## Required RC80 gate / quality semantics

- Protect working automated/manual mappings including saved owner, undo and best-match flows; do not change user mappings without separate authorization.
- Existing EPG-Scrapers MENA feed uses source SHA snapshots. A MAIN/B1/B2 role **never** guarantees real usable EPG. Consume or compare \`reports/mena-readiness.json\`: possible \`QUALIFIED_AT_BUILD\`, \`DEGRADED_WITH_EPG\`, \`NO_FUTURE_EPG\`; expired/zero EPG is not an automatically green match.
- Channel-language rules: Morocco editorial overrides preserved; genuine 2M French programmes stay French; international title EN + description AR; Nat Geo Abu Dhabi programme title/description AR; beIN league AR, club names EN except Morocco club FR; occasional beIN MAX/XTRA/AFC must not be filled with made-up schedules.
- Prefer real data / SHA-pinned donor events. Never synthesize programmes or collapse channel IDs that merely share similar names.
- Hardware validation requires a **real receiver GUI interaction** and **actual Enigma2 EPGImport verification** separately; static code symbols and read-only fingerprints do not certify either.
- Use short reproducible tests, preserve the other ongoing IPToSatPro and IPAudio projects.
- Produce final evidence: installed version/schema, Python runtime, module-count deltas, protected hashes, EPGImport availability, specific failed pass/fail test, source branch and PR, remaining unvalidated hardware cases. Mark receiver changed: **false** until actually changed with separate approval.

Follow \`codex/CODEX_SSH_TASK.md\` for the exact first-round work order.
