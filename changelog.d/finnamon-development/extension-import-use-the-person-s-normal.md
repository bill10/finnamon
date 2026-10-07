---
bump: minor
---
### Changed
- **Fetch by AI through the extension no longer needs a second `/login`.** The session runs on your normal Claude login; the setup is now just `finnamon import --pair-extension`. The guard (paired device first, one tab, the bank's hosts only) is unchanged. `select_browser` writes its pick into `~/.claude.json`, so Finnamon remembers the old value and puts that one key back when the session ends (a SessionEnd hook), or at the next extension import if the session was killed.
