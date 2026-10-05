@AGENTS.md

## Claude Code notes

- This checkout is developer-only. The household's assistant runs in `~/.finnamon/assistant/`, written from
  `finnamon/assistant_bundle/` by `finnamon install`, `init` and `update`; the daemon's `claude -p --resume`,
  the headless `/triage`, the dashboard's intercom PTY and `import --browser` all run there. So a change under
  `finnamon/assistant_bundle/` lands on a household only after its next `finnamon update`, which rewrites that
  directory and restarts the daemon and the dashboard (both read it at startup).
- Developing the code: `python3 -m pytest tests -q -m "not eval"`; evals (real Claude, spends tokens,
  required after touching any prompt file) `python3 -m pytest tests/eval -q -m eval`. tests/conftest.py
  clears `CLAUDECODE` and `FINNAMON_FROM_AGENT` (and its old name `FINNAMON_FROM_CLAUDE`) so the suite runs from a Claude session; the human-only
  gates still hold outside pytest.
- **If the Telegram channel plugin is still registered against this checkout** (it was, before the assistant
  moved; `finnamon update` registers it for `~/.finnamon/assistant/` instead), a plain `claude` here loads a
  second copy of its MCP server and the household session's copy dies about a second later, with no reconnect:
  the chat goes quiet until someone restarts the dashboard. Start yours with `--strict-mcp-config`, or remove
  the stale registration: `claude plugin uninstall telegram@claude-plugins-official --scope local` in this
  directory. Every `claude` Finnamon itself spawns already carries that flag and `--setting-sources project`
  (`docs/DEVELOPMENT.md`, the sealed-session rule).
- **Never run channel setup from a worktree or a scratch dir** (`claude plugin install telegram@…`, or `finnamon
  install`/`update` in channel mode with a scratch `FINNAMON_HOME`). A folder the plugin is registered for steals the
  bot's updates from any `claude` started there. Finnamon refuses to register it outside `~/.finnamon/assistant`, and
  `finnamon status` lists stray registrations under `stray_telegram_plugins`. A registered checkout whose
  `.claude/settings.local.json` enables the plugin hands a registration to every worktree a `claude` starts in
  (`stray_telegram_plugins_seeding`), so uninstall it there first.
