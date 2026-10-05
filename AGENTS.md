# Finnamon: the development checkout

This directory is the source of Finnamon, a personal finance assistant built on Claude: a daemon syncs the household's bank
accounts through Plaid into a local SQLite database, SQL detectors raise alerts, a Telegram bot delivers them,
and Claude Code is the assistant on top. **Working here means changing Finnamon, not being its assistant.**
The assistant is a separate thing: its instructions, permission set and skills are the bundle under
`finnamon/assistant_bundle/` (`CLAUDE.md`, `.claude/settings.json`, `.claude/skills/{finnamon,triage,import-browser}`),
which `finnamon install`, `init` and `update` write to `~/.finnamon/assistant/`, and every `claude` Finnamon
starts for the household runs there. Nothing in the household's money is reachable from this checkout by
design: a session here has no `finnamon` allow list, and the household's data lives in `~/.finnamon/`, which
tests never touch (`FINNAMON_HOME` points every run at a scratch directory).

## Working on the code

`docs/DEVELOPMENT.md` is the layout, the tests, and the rules that are not obvious from the code (the sealed
spawn, no web in unattended runs, read-only triage, detectors reviewed by a person, `_prelude.sql` and the
`tx_now` view kept in step). Design and decisions: `docs/designs/finance-watchdog-agent.md`. Open work: `TODOS.md`.

- Tests: `python3 -m pytest tests -q -m "not eval"`. Evals (a real Claude over a fixture household, spends tokens,
  required after touching any prompt file): `python3 -m pytest tests/eval -q -m eval`. The eval lane runs `claude`
  with cwd = `finnamon/assistant_bundle/`, so the bundle's source is what gets evaluated.
- Prompt files: `finnamon/assistant_bundle/**`, `finnamon/triage.py`, `finnamon/claude_runner.py`, `finnamon/daemon.py`.
- A change to the bundle reaches a household on its next `finnamon update` (it rewrites `~/.finnamon/assistant/`,
  keeps a household's own edits as `.bak`, and restarts the daemon and the dashboard, which read it at startup).
- `finnamon normal` rules (`suppressions`): a rule with no kind covers every detector except `recurring_changed`,
  which only honours its own kind and, from `--alert`, one `stream_id`; `--remove <id>` deletes a rule. `--kind` takes only a kind a rule can quiet (`budgets.rule_kinds()`); `--alert` on any other kind (low_balance, budget_pace, sync_health) resolves it with no rule. A merchant typed into `normal` or `category` goes through `budgets.resolve_merchant`, which refuses a name that covers no charge.
- Detectors are SQL under `finnamon/detectors/`; every one needs hit / miss / baseline / dedup tests in `tests/test_detectors.py`.
- Versioning: `VERSION` is the one source (`MAJOR.MINOR.PATCH.MICRO`). **Do not bump `VERSION` and do not edit
  `CHANGELOG.md` in a PR.** This overrides /ship's version-bump and CHANGELOG steps: skip them, and add
  `changelog.d/<branch-name>.md` instead (front matter `bump: major|minor|patch|micro`, then the `### Added/Changed/Fixed`
  notes; see `changelog.d/README.md`). Title the PR `<type>: <summary>` with no version prefix. The release workflow picks
  the version once the merge's tests pass, so parallel PRs never conflict on it.

## What not to do here

- Never read or modify the real `~/.finnamon/` (secrets, the database, the assistant directory), the household's
  Plaid keys or the Telegram bot. All testing goes through a scratch `FINNAMON_HOME` and a stub `claude`.
- Never install launchd or systemd units on the machine you develop on; `finnamon/scheduler.py` has test seams.
- Never write into `finnamon/detectors/rules/` or `candidates/` by hand: drafts go through `finnamon detect --draft`
  and a person's `finnamon detect --review`.
- Never change the household's Claude Code settings (`~/.claude/settings.json`); seal the spawn instead.
