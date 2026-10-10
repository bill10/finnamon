@AGENTS.md

## Claude Code notes

- This checkout is developer-only. The household's assistant runs in `~/.finnamon/assistant/`, written from
  `finnamon/assistant_bundle/` by `finnamon install`, `init` and `update`; the headless `/triage`, the
  dashboard's intercom PTY (which also answers Telegram) and `import --browser` all run there. So a change under
  `finnamon/assistant_bundle/` lands on a household only after its next `finnamon update`, which rewrites that
  directory and restarts the daemon and the dashboard (both read it at startup).
- Developing the code: `python3 -m pytest tests -q -m "not eval"`; evals (real Claude, spends tokens,
  required after touching any prompt file) `python3 -m pytest tests/eval -q -m eval`. tests/conftest.py
  clears `CLAUDECODE` and `FINNAMON_FROM_AGENT` (and its old name `FINNAMON_FROM_CLAUDE`) so the suite runs from a Claude session; the human-only
  gates still hold outside pytest.
- **If Claude Code's Telegram plugin (`telegram@claude-plugins-official`) is still registered against this checkout
  or a worktree** (Finnamon's old channel mode registered it; Finnamon no longer registers it anywhere), any `claude`
  started there polls the household's bot and steals its updates from the daemon (409s): the chat goes quiet. Remove
  the stale registration: `claude plugin uninstall telegram@claude-plugins-official --scope local` in that directory,
  or start yours with `--strict-mcp-config`. Every `claude` Finnamon itself spawns already carries that flag and
  `--setting-sources project` (`docs/DEVELOPMENT.md`, the sealed-session rule). `finnamon status` lists stray
  registrations under `stray_telegram_plugins`. A registered checkout whose `.claude/settings.local.json` enables the
  plugin hands a registration to every worktree a `claude` starts in (`stray_telegram_plugins_seeding`), so uninstall
  it there first.
