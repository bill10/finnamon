---
bump: minor
---
### Changed
- **Telegram always shares the dashboard's conversation.** Session mode, the default since v0.32, is now the only way the chat is answered: the daemon reads the bot and types each message into the dashboard's assistant session, so the phone and the dashboard see one conversation. Claude Code's Telegram channel plugin (`finnamon channel on`) and the legacy separate session (`finnamon channel off`, the daemon's own `claude -p`) are gone. `finnamon channel` now only says so. Telegram replies need the dashboard; without it the chat still gets alerts, and `finnamon doctor` says so.
- **Existing installs move over on their own.** On `finnamon update` or the daemon's next start, a household on channel or the legacy mode switches to the shared conversation, with one line saying so (in the daemon's log on the first update, which still runs the previous release's code). Both also unregister `telegram@claude-plugins-official` from the assistant directory (`~/.finnamon/assistant`, local scope) and remove its `enabledPlugins` entry there. Your `~/.claude/channels/telegram/.env` (now an unused copy of the bot token; the update line says you can delete it) and any user-scope plugin are left alone.

### Removed
- **The channel-only machinery:** the "I stopped hearing this chat" notice, the `reply-guard` hook, `finnamon notify --restarted`, `update --force` (it only forced the plugin registration), and the plugin's reply/react permissions in the assistant's settings. Alerts, Allow/Deny prompts, group sender tags, voice and the Codex relay are unchanged.
