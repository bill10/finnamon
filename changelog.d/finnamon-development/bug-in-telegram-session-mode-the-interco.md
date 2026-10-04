---
bump: patch
---
### Fixed
- **Telegram messages reach the intercom again in session mode.** The dashboard's intercom session now starts sealed
  (`--setting-sources project --strict-mcp-config`, the same seal every other `claude` Finnamon spawns carries) in session
  and daemon modes, so a Telegram plugin enabled in your Claude Code config no longer starts there and long-polls the bot
  beside the daemon (the "Another program is reading this bot's messages" 409 notice). Only channel mode loads the plugin.
- **`finnamon channel session` / `off` restart the dashboard themselves**, so the intercom picks up the new mode without a
  manual `finnamon update --no-pull`, and say so; without a dashboard installed they point at `finnamon install`.
