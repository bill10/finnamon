---
bump: minor
---
### Changed
- **The dashboard's assistant asks instead of refusing.** The intercom session now runs in Claude Code's ask mode: the allow list still runs unasked and the deny list still wins (secrets, the database, the dashboard key, its own folder, every setup-changing command), and anything else, Python, another command, reading a file, shows an Allow / Deny dialog in the intercom. Channel mode is unchanged.
- **The web is back, one approved fetch at a time.** Web search and fetch are off the allow list, so each one asks and names the site, and the session that answers Telegram (`finnamon channel session`) no longer has them switched off. "What's my house worth?" works from the phone again. Unattended runs (triage, daemon-mode replies) still have no web.

### Added
- **The household's secret files are off limits to every tool, whoever approves.** The secrets, the database, the dashboard key, imported statements, the bot's token and the browser profiles are refused before any Allow / Deny is shown, for every tool (a shell command, a search, a file read), on the dashboard and the phone alike.
- **Permission prompts reach the phone.** When a Telegram message started the turn, the same request goes to the household chat: one line saying what the assistant wants (the command, the site, the path) with Allow and Deny buttons. The first answer wins, from the phone or the dashboard, and the other side is cleared. Only household members in that chat can press them. With no answer in 10 minutes the request is denied and the chat is told. Arrives with `finnamon update`, which installs the new hook and restarts the dashboard.
