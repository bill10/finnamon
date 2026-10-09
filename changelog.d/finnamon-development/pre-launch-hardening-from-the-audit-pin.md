---
bump: patch
---
### Changed
- **Codex's tools never reach the network, by an explicit setting.** The generated `config.toml` now sets `[permissions.finnamon.network] enabled = false` instead of relying on Codex's default, and `finnamon doctor` (and every Codex start) refuses a config where it is missing or turned on.
- **One deny list for Claude and Codex.** The secret guard and Codex's permission profile are now built from the same list: Codex's tools can no longer reach `downloads/`, `chrome-extension-test/`, `claude-import/` or `~/.claude/projects`, and Claude's tools can no longer reach `backups/`, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.netrc`, `~/.config/gh`, `~/.kube`, Claude's own login files or the macOS keychains.
- **Imported statements don't pile up.** At the end of a Fetch by AI import, CSVs in `~/.finnamon/downloads/imported/` older than 30 days are deleted; the docs say where bank page text and screenshots are kept (the assistant's Claude transcripts).
- Docs and test fixtures no longer carry personal names, paths or bot handles; `THIRD_PARTY_NOTICES.md` lists xterm.js, Vega and the Geist fonts loaded from CDNs.
