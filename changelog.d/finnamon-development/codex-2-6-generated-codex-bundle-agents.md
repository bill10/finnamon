---
bump: minor
---
### Added
- **Codex groundwork: the assistant's Codex bundle and home.** Every install now also writes `AGENTS.md` and `.agents/skills` beside `CLAUDE.md`, generated from the Claude files. `finnamon init` asks which assistant runs the household when OpenAI's Codex CLI is installed (Claude Code stays the default) and, for Codex, shares your existing Codex login through a link instead of a second login, and writes `~/.finnamon/codex/config.toml`: a permission profile that denies the secrets and the database, no web search, and Finnamon's hooks with their trust pinned. `finnamon doctor` checks all of it for a household that set Codex up. Codex cannot be selected as the assistant yet; that comes with the dashboard support.
