---
bump: minor
---
### Changed
- **Fetch by AI through the extension no longer needs a second `/login`.** The session runs on your normal Claude login; the setup is now just `finnamon import --pair-extension`. The guard (paired device first, one tab, the bank's hosts only) is unchanged. `select_browser` writes its pick into `~/.claude.json`, so Finnamon remembers the old value and puts that one key back when the session ends (a SessionEnd hook), or at the next extension import if the session was killed.
- **No pairing step.** The first Fetch by AI on HSBC pairs the extension's browser by itself when exactly one is connected (otherwise the by-hand export, with a line naming `finnamon import --pair-extension`, which stays optional). Setup is: update finnamon and claude, then Fetch by AI.
- **An open extension Chrome no longer has to be quit.** If its downloads are not pinned, it is left alone and the CSV is also looked for in `~/Downloads` (Finnamon's folder first).
