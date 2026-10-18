# Codex → SSH direct → Vu+ Zero 4K / EPGManager RC80

**Scope:** Codex CLI running on a trusted PC/Mac/Linux device on the same LAN as the user's Vu+, or in an environment with an intentionally configured private VPN route. The user's receiver remains on its existing working EPGManager RC79.1. This handoff branch only contains documentation, a receiver **read-only** diagnostic, and its offline tests; it does not install a plugin or enable SSH access by itself.

## One-time setup (on the computer, not on the Vu+)

Install Codex CLI if needed using its official installation procedure:

\`\`\`sh
npm install -g @openai/codex
\`\`\`

Connect to your account through the CLI as instructed by the app. Clone the RC80 SSH qualification branch:

\`\`\`sh
git clone https://github.com/wacayoub/EPGManager.git
cd EPGManager
git checkout codex/rc80-vu-ssh-readonly-hardware-tests-20261018
\`\`\`

Set up your OWN SSH agent and a host alias \`epg-vu\` to resolve to the receiver privately. A typical entry in your computer's \`~/.ssh/config\` (not in GitHub) is:

\`\`\`sshconfig
Host epg-vu
    HostName vuzero4k
    User root
    IdentityFile ~/.ssh/id_ed25519_epg_vu
    IdentitiesOnly yes
    StrictHostKeyChecking yes
\`\`\`

Replace \`HostName\` with the receiver's **private** LAN hostname or private IP if its DNS name does not resolve. Use a key already authorized on the receiver; the first trust check of the host key must be performed by the owner. Do not paste the SSH private key, password, public WAN IP or network tokens into Codex/chat. Never expose router TCP/22 to the Internet. If operating from Codex Cloud, your cloud environment needs a **private network route** before SSH can reach a home-LAN host.

Test only connectivity to a trusted known host:

\`\`\`sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'printf "EPGM_SSH_REACHABLE\n"'
\`\`\`

A safe **Codex-run remote diagnostic** uses stdin to execute local reviewed code **in memory**, with no deployment to Vu+:

\`\`\`sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'python3 -' < codex/receiver_readonly_probe.py
\`\`\`

No IPK, Git pull, remote file copy, restart or configuration change occurs. Codex can run this command and read JSON output.

## Start Codex

Run \`codex\` from the working repository, then give it this exact task:

> Improve EPGManager RC80 against my actual Vu+ Zero 4K through my SSH alias \`epg-vu\`. Follow AGENTS.md and codex/CODEX_SSH_TASK.md. First run local offline receiver-probe tests and the read-only SSH stdin probe. Record actual receiver Python, installed EPGManager version/schema, protected module hashes, EPGImport presence and Enigma2 process state. Compare the installed working RC79.1 behavior and saved auto/manual mapping APIs with the RC80 candidate, without reading mapping/config contents. Compare EPG-Scrapers MENA per-ID source readiness (including 14 SHA-verified sources and no-future-guide status) with EPGManager Best Match. Implement isolated fixes on a Git branch and run offline tests. Do not copy, install, upgrade, delete, restart, or modify anything on Vu+ without my separate explicit approval. Return a precise list of verified receiver tests versus static/offline checks and the remaining GUI/EPGImport steps.

## Recommended first tests

\`\`\`sh
python3 -m unittest codex.test_receiver_readonly_probe -v
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'python3 -' < codex/receiver_readonly_probe.py
\`\`\`

Codex may also run existing audited files via the **same stdin-only SSH method**, after inspecting each file's code, for example:

\`\`\`sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'python3 -' < tools/audit_rc80_runtime_readonly.py
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes epg-vu 'python3 -' < tools/audit_rc80_hardware_handoff_readonly.py
\`\`\`

These are static/read-only assessments and **not** live GUI or real EPG import tests. Do not equate all-green offline tests with a receiver-validated release.

## Acceptance

* Working installed RC79.1 remains unchanged until a distinct approved install/test phase.
* Receiver evidence collected without copying or displaying settings, passwords, user mappings or XMLTV contents.
* Python 3.14 runtime and Enigma2/EPGImport state compared against what **actually** runs on the hardware.
* RC80 branch modifications reviewable as a Git diff/PR with tests.
* Best Match quality never green for MENA \`NO_FUTURE_EPG\` and does not blindly trust \`MAIN\` routing labels; inspect \`https://raw.githubusercontent.com/wacayoub/EPG-Scrapers/main/reports/mena-readiness.json\` for evidence (do not assume latest counts).
* GUI, native Enigma2 import, autostart scheduler and source sync counted **not validated** until observed with specific user-approved hardware testing.
* After any explicitly authorized future installation, avoid reboot, \`opkg\` rollback tests, deleting mappings or restarting GUI without specific separate consent.

**Connection failure:** Report \`SSH_NOT_TESTED\` with brief error category (DNS, host-key, auth, route), not credentials, and continue offline. Codex does not have automatic access to private local networks solely because GitHub is connected.
