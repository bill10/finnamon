---
bump: minor
---
### Added
- **`finnamon status` says which assistant runs the household (`agent`: `claude`), and an `assistant` setting names it.** This is the first step of Codex CLI support. Claude stays the default, and nothing a Claude household does changes. `finnamon settings set assistant codex` is refused, with "Codex support is being built", until the Codex dashboard support lands. Only a person at a terminal can change the setting, never an assistant session.

### Changed
- **The marker every assistant run carries is now `FINNAMON_FROM_AGENT`.** The human-only commands still refuse the old `FINNAMON_FROM_CLAUDE` and `CLAUDECODE` too. `claude_runner` is now `agent_runner`, and the old name still works.
