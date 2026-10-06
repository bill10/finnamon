---
bump: minor
---
### Added
- **Codex support.** Finnamon now runs on Claude Code or OpenAI's Codex CLI (0.157 or newer): "Turn Claude or Codex into your personal finance assistant." `finnamon init` asks which one when both are installed, and `finnamon settings set assistant codex|claude` switches later. Codex needs a ChatGPT Plus plan or higher, or an OpenAI API key, and shares your existing Codex login. Triage, chat, the dashboard's intercom, Talk and session-mode Telegram all work on Codex. What v1 does not do on Codex: web lookups, Telegram channel mode, and the "Fetch by AI" import ([INSTALL.md](docs/INSTALL.md#codex-what-v1-does-not-do)).
- **Evals on both CLIs.** Every eval runs on `claude` and on `codex` (a Codex household under a scratch home, sharing the developer's login), with `$triage` and the `finnamon` tool in place of `/triage` and Bash. The web-lookup control is skipped on Codex, with the reason.
- **`finnamon status` shows `codex_min_version`**, the oldest Codex it accepts.

### Changed
- **README, INSTALL, CHANNEL-MODE and the launch kit cover Codex.** The requirements, the cost table ("Free + your Claude or ChatGPT subscription"), what leaves your machine on Codex (turn off ChatGPT's "Improve the model for everyone"), a Codex install path (`codex login --device-auth` for a headless box, the shared login, what doctor checks), and channel mode marked Claude Code only. A Claude household is unchanged.
