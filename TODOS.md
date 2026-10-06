# TODOS

## Infrastructure

### Move secrets from secrets.toml to the OS keychain

**What:** `secrets.py` backend abstraction with file and keychain implementations (macOS `security`, Linux `secret-tool`); `finnamon secrets migrate`.

**Why:** Plaintext-at-rest for Plaid access tokens and the Telegram bot token is the one security gap the design knowingly accepts (eng review issue 2 / decision 2A kept them in a 0600 file written only by init and link).

**Context:** `secrets.toml` is written atomically by `cli.py init` and `cli.py link`, read by `config.py`. Start by making `config.py` read through one `get_secret(name)` function; add the keychain backend behind it. Known footguns: keychain prompts from a launchd process on macOS; `secret-tool` needs a running keyring daemon, which headless Ubuntu often lacks.

**Effort:** M
**Priority:** P3
**Depends on:** Milestone 1 shipped; a real Linux box to test `secret-tool` on.

## Detectors

### Exact-amount suppression for "normal" on a duplicate charge

**What:** a `min_amount` (or `amount`) column on `suppressions`, honored by `_prelude.sql`, so "normal" on a duplicate_charge alert mutes that account, merchant and amount only.

**Why:** `budgets.normal` now scopes a duplicate_charge "normal" to the alert's account with `max_amount` = its amount (v0.8.2.1). A cap, not an exact match: a smaller double charge at the same merchant stays muted too. A bigger one still fires.

**Effort:** S (migration, prelude line, `budgets.normal`, `normal --list`)
**Priority:** P3
**Depends on:** a household hitting a muted smaller duplicate.

### Detector drafts and reviewed candidates live inside the package directory

**What:** `finnamon detect --draft` writes to `finnamon/detectors/pending/` and `detect --review` moves files into `rules/` or `candidates/`, all under the package (`detect.DETECTORS`). Move drafts, and the candidates a household approves, under `FINNAMON_HOME` (e.g. `~/.finnamon/detectors/{pending,rules,candidates}`) and have `detect.detectors()` read both trees.

**Why:** Since v0.8.0.0 the assistant runs from an installed bundle and the checkout is developer-only, so a wheel install is the intended shape, and there the package directory is site-packages: read-only under a tool-managed install (a traceback for the assistant on "watch for X"), and wiped of every draft and every approved candidate by the next `pip install -U`. On the editable install everyone runs today it works, which is why it is not in the v0.8.0.0 release.

**Context:** Found by the v0.8.0.0 red-team pass. `_draft` in `cli.py` confines the name to `pending/`; the same confinement applies wherever the directory moves. The bundle's finnamon skill tells the assistant that drafts go through `finnamon detect --draft`, so no skill text changes.

**Effort:** S
**Priority:** P2
**Depends on:** nothing.

### known_counterparties for the unmatched-transfer candidate

**What:** `known_counterparties(pattern, label, created_at)` table + `finnamon counterparty add`; `_prelude.sql` exposes `counterparty_label`; `unmatched_transfer.sql` excludes labeled rows.

**Why:** `unmatched_transfer` flags every payment to an account Finnamon can't see (rent, a card not on Plaid). Suppressions handle it one merchant at a time; a counterparty label handles it in one row and also improves the item-linked summary's "typical month."

**Context:** Milestone 2 feature; named in the design doc's Open Questions. Keep distinct from suppressions: a suppression means "don't tell me," a counterparty means "this is what it is." Decide after two weeks of real anomaly output whether suppressions alone are enough.

**Effort:** S
**Priority:** P3
**Depends on:** Milestone 2 running for two weeks.

### An empty merchant_name shadows the real name across the prelude and the money queries

**What:** `COALESCE(t.merchant_name, t.name)` and friends take an empty string as a real value, so a row whose name lives in the next column is labelled `''`. v0.6.1.0 fixed the two recurring detectors and the linked-bank summary with `COALESCE(NULLIF(trim(x),''), ...)`; the same shape is still in `detectors/_prelude.sql` and, since v0.7.4.0, its copy in `migrations/005_tx_now_view.sql` (`canonical` and `display`, which every detector and every read path selects from). Fixing it means changing both together, or they drift.

**Why:** In the prelude it is not cosmetic: `canonical` is the grouping key for suppressions, aliases and category overrides, so every blank-named transaction would collapse into one merchant. Left alone because changing it moves merchant grouping for every detector at once and needs its own hit/miss/baseline/dedup tests.

**Context:** No evidence yet that Plaid sends `''` rather than NULL; imported CSV rows cannot hit it (`imports.py:143` substitutes `(no description)`). Worth doing when something is observed in the wild, or alongside the next prelude change.

**Effort:** M
**Priority:** P2
**Depends on:** nothing.

## Reliability

### Persist the Plaid sync cursor per page, not per Item run

**What:** `sync.apply_page` already commits each page in its own transaction; store `next_cursor` alongside it and resume from the last committed page after a crash instead of restarting the Item.

**Why:** A crash mid-Item re-walks pages already applied. Correct today (upserts are idempotent) but wasteful on the first 24-month backfill.

**Context:** `sync.sync_transactions` loops `transactions_sync(cursor)`; the cursor is written to `items.cursor` only at the end. MAX_RESTARTS=3 handles Plaid's mutation-during-pagination error separately.

**Effort:** S
**Priority:** P3

### Codex headless runs: edges left by the card-4 review

**What:** (1) A triage run that fails as `busy` (Codex usage limit or capacity) still counts an attempt, so three in a row
suppress the groups as `triage_unavailable`; skip the bump for `busy` and raise a health line instead. (2) The daemon's one
Codex thread resets only on `session lost` or repeated timeouts; a thread that can never succeed again (context exhausted,
a rollout cut short by a timeout's SIGKILL) fails every chat as `error`: retry once fresh after N such failures. (3) Check
whether the `finnamon serve` MCP child shares codex's process group; if not, a timeout or `terminate_all()` leaves an
in-flight `finnamon` call running up to its own 600 s. (4) The triage deny list refuses `finnamon normal --list`, which the
triage skill asks for (both CLIs). (5) `codex.env()` passes the daemon's whole environment: drop
`OPENAI_*`/`CODEX_API_KEY`/proxy variables so a key or base URL in the service's environment cannot redirect the
household's runs. (6) Serialise Codex runs (triage and chat share one login with the person's own `codex`). (7) Prune
`FINNAMON_HOME/codex/sessions`: rollouts hold bank memos and grow with every turn.

**Why:** The Codex login is shared with the person's own `codex`, so a week of their coding can exhaust it and silently
drop the household's anomaly alerts; the other three were found reviewing the card-4 PR, none seen live.

**Context:** `triage.run_if_needed` / `_bump_attempts`, `daemon.converse`, `agent_runner.run` (killpg), `mcp_server.TIMEOUT_S`,
`agent_runner.TRIAGE_DISALLOWED`; docs/designs/codex-spike.md "Card 4 notes".

**Effort:** S
**Priority:** P2

### Dedupe the two enrolment paths in owners.py

**What:** `_poll_direct` (CLI polls getUpdates itself) and the daemon-side `pending_owner` path both parse the same message shape. Fold into one `enroll_from_update` used by both.

**Why:** Two parsers for one message format will drift; cycle-3 added a deadline to one path only (the daemon's) because the direct path has its own `time.time()` deadline.

**Effort:** S
**Priority:** P3

### Hard split of a single over-long Telegram line can cut an HTML entity

**What:** `telegram.split_message` hard-cuts a line longer than 4096 chars at the byte limit, which can land inside `&amp;` or a `<b>` tag; Telegram then rejects the chunk.

**Why:** No renderer produces a 4 KB line today (roundup chunks are 3500 chars of many lines), so it cannot fire; it would if a future kind rendered one huge line.

**Context:** Fix is to split at the last whitespace or `>` before the limit, or to send that part with `parse_mode` unset. Found by the ship-time adversarial pass; not reproduced.

**Effort:** S
**Priority:** P3

### Resolve recent alerts when a mirror's primary is removed

**What:** `link.remove` clears `mirror_of` on the surviving joint account, so its transactions become visible to detectors at once; ones inside `lookback_days` that were already alerted under the removed account's transaction ids can fire again under the survivor's ids.

**Why:** A few duplicate alerts for up to a week after unlinking one half of a joint account. Low impact; found by the ship-time adversarial pass on v0.2.0.0.

**Context:** Either resolve alerts whose `transaction_id` belongs to the removed accounts in the same tx, or key duplicate/outlier detectors on the canonical account.

**Effort:** S
**Priority:** P3

### Sandbox E2E for Link token fields and /item/remove

**What:** `tests/e2e/test_sandbox.py` creates Items with `/sandbox/public_token/create`, which bypasses `link_token_create`; the `optional_products`, `days_requested` and `/item/remove` bodies are pinned only by the fake-server unit test.

**Why:** A Plaid-side rejection of those fields would only show up on a real link.

**Effort:** S
**Priority:** P3

### Pending-link edges: concurrent starts and Plaid's session expiration

**What:** `link.start_pending` creates the Plaid session outside the run lock, so two `--start` calls in the same second from two terminals open two sessions and the first is never polled. It also discards the `expiration` Plaid returns; after the ~4h session lifetime with no login the daemon keeps polling for the rest of the 10h TTL.

**Why:** Both are rare (one household, one assistant that runs serially) and bounded; found by the v0.3.0.0 adversarial pass.

**Context:** Hold `run.lock()` across check + create in `start_pending`; store `expiration` and expire when past it with no session ever opened.

**Effort:** S
**Priority:** P3

## Telegram channel

### Channel mode: resolve which alert a reply quoted

**What:** When a message arrives through Claude Code's Telegram channel plugin (`<channel source="telegram" …>`), pass the alert id the person replied to, the way the daemon's `[Telegram, jane; replying to alert 1841]` prefix does.

**Why:** In channel mode "it is normal" falls back to the most recent alert in `finnamon alerts`; the skill says which one it took it to mean, but a reply to an older alert is guessed.

**Context:** The daemon resolves `reply_to_message.message_id` against `alerts.telegram_message_id`. The plugin's channel envelope (v0.4.0.0) carries `chat_id`, `message_id`, `user_id` but not the quoted message id. When it does, the skill can look the id up with `finnamon query`; until then a CLI helper is pointless. Documented as "for now" in the README experiment section.

**Effort:** S
**Priority:** P3
**Depends on:** The Telegram channel plugin exposing the quoted message id.

### Channel mode: put the speaker gate back in code

**What:** In channel mode the owners-table check (`daemon.py` drops unknown senders before Claude sees them) exists only as a skill instruction: the plugin's allow list decides who gets through, and the assistant is told to refuse ids with no owner row. (The attachment and chat-id gates are code: `finnamon hook reply-guard`.)

**Why:** A prompt is not a boundary. One instruction-following slip lets a guest in the group change thresholds.

**Context:** A `PreToolUse` hook on `Bash(finnamon …)` writes would need the current speaker, which the plugin doesn't expose to hooks. Also: conversation in channel mode writes no `feedback` rows. Documented in README under the experiment.

**Effort:** M
**Priority:** P2
**Depends on:** Claude Code channel plugin exposing the speaker to hooks.

## Quality

### The chart eval fails: the assistant writes no chart for "restaurant spending month by month"

**What:** `tests/eval/test_budget_skill.py::test_a_chart_no_preset_covers_is_a_spec_not_a_code_change` fails with `FileNotFoundError` on `<fixture_home>/charts/current.json` — the assistant never runs `finnamon chart --spec-json -`, so the board file is never written. The other two evals in that file pass. Posted to the Agent 007 board as `job-1790352311300-94b9a9`.

**Why:** It is the only automated check that the "a chart nobody built yet is a spec, never a code change" rule actually holds, which exists because the assistant once edited `finnamon/charts.py` instead of writing a spec. While it is red, the eval lane cannot gate a prompt change.

**Context:** Pre-existing, not from the browser-import work: verified on 2026-09-25 by running the same eval in a clean detached worktree at `origin/main` = `0c4ed0e` (v0.7.10.1), where it fails identically. Reproduced three times, so not flaky. All 432 offline tests pass, so the `chart` verb itself is very likely fine and the bug is in what the assistant does. First step: the `FileNotFoundError` fires before the assertion that would have printed the transcript, so log `out` before the `json.loads` and read what the assistant actually said. Check too whether `fixture_home` has any restaurant-category rows to chart — an empty result set is a plausible reason for it to give up rather than write a spec.

**Effort:** S
**Priority:** P0
**Depends on:** nothing.


### `finnamon update` adopts code without checking who wrote it

**What:** `git pull --ff-only --verify-signatures`, or record the expected `remote.origin.url` at install time and compare before pulling.

**Why:** `--ff-only` is a history-shape guarantee (local HEAD must be an ancestor of the fetched tip); it says nothing about authorship. Anyone who can push to the tracked branch gets code executed as the household user on the next `update`, because the restart re-execs the pulled `finnamon/*.py` and `web/server.js`. Two amplifiers: the pulled tree carries `finnamon/assistant_bundle/.claude/settings.json` (the assistant's own permission allow-list, which `update` then writes to `~/.finnamon/assistant/` since v0.8.0.0), and `store.migrate` globs `*.sql` off disk at call time, so pulled SQL runs against the household DB before anything restarts.

**Context:** v0.7.0.0 prints the remote and the `before..after` range and names a release that touches the assistant bundle (`finnamon/assistant_bundle/`; the root `.claude/` before v0.8.0.0), so the adoption is at least visible. Signature verification needs the repo's commits signed first, which is the real work.

**Effort:** M
**Priority:** P2
**Depends on:** signing commits.

### Two adds of the same account name race, and the loser shows a raw SQLite error

**What:** In `imports.py.add_account` the duplicate-name check and the id-collision check both run outside `store.tx` (`BEGIN IMMEDIATE`). Two simultaneous `POST /api/account` calls spawn two `finnamon` processes, both pass both checks, and the loser fails on the `accounts.account_id` primary key. The household sees `UNIQUE constraint failed: accounts.account_id`, or `database is locked` if it loses the 5s `busy_timeout` instead.

**Why:** No bad row is ever created, so this is a message-quality bug, not corruption. The in-flight guard added in v0.6.1.0 disables the button in one tab only; two tabs, or the dashboard plus the assistant, still race.

**Context:** Move both checks inside the `with store.tx(conn)` block and map `sqlite3.IntegrityError` on this table to the friendly "an account named X already exists" that the pre-check already produces.

**Effort:** S
**Priority:** P2
**Depends on:** nothing.

### Phone touch targets and primary-button contrast on the dashboard

**What:** Two pre-existing style gaps, uniform across the dashboard. (1) `.erow input, .erow select` computes to about 36.5px tall; the `max-width: 700px` block in `style.css:245` grants buttons a 44px minimum but never extends it to inputs, so the budget, property and add-account rows are all under the phone target. (2) `--cinnamon` `#CE623C` with white text measures 3.9:1, under WCAG AA's 4.5:1; `--cinnamon-hover` `#B8562F` measures 4.6:1.

**Why:** Neither was introduced by v0.6.1.0 and both are repo-wide, so they belong in their own commit where every affected panel can be looked at together rather than riding along with a bug fix.

**Effort:** S
**Priority:** P2
**Depends on:** nothing, but do the two together: both repaint panels this changes did not touch.

### Triage eval against a labelled candidate set

**What:** `tests/eval/test_triage_skill.py`: seed 20 candidates with hand labels (promote high / low / suppress), run `/triage` through `claude -p`, score agreement.

**Why:** The budget skill has an eval; triage, the one place LLM judgment reaches the household, has none. Calibration drift in the skill text would go unnoticed.

**Effort:** M
**Priority:** P3
**Depends on:** two weeks of real candidates to label.

### Remote access (`finnamon remote`): edges left after the review cycle cap

**What:** Findings the `finnamon remote` PR shipped with, deferred by the job owner when /ship hit its three-fix-cycle cap:
(1) a hand-made TCP Funnel (`tailscale funnel --tcp`/`--tls-terminated-tcp` to 8888) sits under `TCP[port].TCPForward`, which `serve_targets` never counts as ours, and raw TCP carries no `Tailscale-Funnel-Request`, so neither `remote`, doctor nor the server's 403 notices it [P2];
(2) `remote --off` runs `serve --https=<port> off`, which clears every route on that port, and removes the whole `web-hosts` file rather than only the names it added [P2];
(3) a dashboard mounted below `/` counts as served at the root [P3]; `add_host` truncates then writes instead of temp + `os.replace` [P3]; doctor ignores a foreground-only serve [P3].

**Why:** Each is reachable only by hand-configuring Tailscale beyond what `finnamon remote` does, or by editing `web-hosts` by hand; the key still gates every request.

**Context:** `finnamon/remote.py` (`serve_targets`, `off`, `add_host`, `doctor_line`), `web/server.js` `funnel()`. Fix (1) by counting a `TCPForward` to `127.0.0.1|localhost|[::1]:PORT` as ours and folding its `AllowFunnel` into `funnel`; (2) by `--set-path=<mount> off` per dashboard mount and removing only `serve_targets` names from the file.

**Effort:** S
**Priority:** P2
**Depends on:** nothing.

## Completed

### Remove a single manual account

**What:** `finnamon account remove "<name>"` and a `DELETE /api/account/:name` behind it, plus a delete control on the account in the Import CSV panel.

**Why:** `link --remove manual:<bank>` deletes the whole manual item, so HSBC Checking and HSBC Savings go together; a typo'd account name stayed in net worth until then.

**Effort:** S
**Priority:** P1
**Completed:** v0.8.9.0 (2026-09-27). Removing the last account at a bank takes the bank's item with it.

### A person running plain `claude` in the checkout kills the household's Telegram

**What:** Say so where someone would look: AGENTS.md's "what you never do", the README's dashboard section, or a note at the top of the intercom. Better, make it not matter by landing the `health()` half of the intercom-plugin item below.

**Why:** v0.7.1.0 closes every spawn Finnamon controls (triage, the conversation worker, the browser import, the eval lane), but the plugin is registered local to the checkout, so any `claude` a person starts there loads a second copy of its MCP server and the household session's dies about a second later, with no reconnect and nothing reporting it. Developing in the checkout is the normal case, so this is a foot-gun aimed at the household's Telegram.

**Context:** `~/.claude/plugins/installed_plugins.json` carries one `scope: local` registration per project path. Measured 2026-09-23: a second server killed the household's in under a second, every time.

**Effort:** S (a note) / M (detection)
**Priority:** P2
**Depends on:** nothing.
**Completed:** v0.8.0.0 (2026-09-27) — the assistant runs from `~/.finnamon/assistant/` now, so the checkout is developer-only: `install`/`update` register the Telegram plugin for that directory and the root `CLAUDE.md` names the hazard and the `claude plugin uninstall --scope local` that removes the stale registration from a checkout. The `health()` detection half is still open under the intercom-plugin item.

### Move budget queries onto the detector prelude

**What:** `budgets.month_to_date` and `budgets.suggest` build their own SQL over `transactions`; `budget_pace.sql` uses `_prelude.sql`'s `tx` CTE (aliases, category overrides, mirror exclusion). Have budgets.py assemble the prelude too so the CLI and the detector agree to the cent.

**Why:** Today they can differ when an alias or category override applies; the pace alert would name a number `finnamon budget` doesn't show. Since v0.5.4.0 the alias rule is a second copy in `budgets.ALIAS_CANONICAL` / `ALIASED_TX` (held equal by a test; the only intended difference is the prelude's `created_at <= :as_of`); that copy is what this item deletes.

**Effort:** M
**Priority:** P3
**Completed:** v0.7.4.0 (2026-09-23) — done as a SQL view (`tx_now`, migration 005) rather than by assembling the prelude in Python, so `finnamon query` and the chart panel get the same resolution; `budgets.ALIAS_CANONICAL` / `ALIASED_TX` deleted.
