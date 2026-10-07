---
bump: minor
---
### Changed
- **HSBC: Fetch by AI is the by-hand export now.** HSBC's login refuses any Chrome with a debugging port open ("reference: EAC"), however Chrome is started: the owner's tests on 2026-10-06 got EAC on every launch with the port and logged in on the same fresh profile without it. So `finnamon import --browser hsbc` (and the dashboard's Fetch by AI) says why and goes straight to Fetch without AI: no port, no AI, you download the CSV and it is imported as it lands. The dashboard shows only Fetch without AI for an HSBC account. An explicit `--attach` still does what you typed. Starting Chrome through LaunchServices (0.53) does not fix EAC, whatever earlier notes said.
- **`finnamon import --browser <bank> --diagnose` adds a login with no debugging port** and says which it is: the debugging port (use Fetch without AI), or a bank that refuses every launch, the port-less one too (likely flagged after repeated attempts: wait a day, or log in from your everyday Chrome).
