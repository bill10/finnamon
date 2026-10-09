# Fresh-install QA, v0.56.2.0 (2026-10-08)

Scratch HOME, clean clone of main, no real ~/.finnamon / Plaid / Telegram / Chrome profile touched.
**Not run (blocked, see "Not done"):** Plaid sandbox link + first sync, live intercom chart, anomaly triage, live install/uninstall.
Screenshots: `screenshots/` (demo household 1280 + 390, empty state 1280 + 390, empty "Link account" click, intercom on a logged-out claude).

## Incident (mine)
`finnamon install --yes` with `HOME=<scratch>` and `FINNAMON_HOME=$HOME/.finnamon` is the *default* home name, so it used the plain
`com.finnamon.*` labels and booted out the owner's real daemon/web/heartbeat jobs. I ran `install --uninstall`; the owner's
jobs were then restored (by Billion/owner, 21:26; verified pids 48458/48462 point at ~/.finnamon again). No real data touched.
Product note: job labels collide whenever two homes are both named `.finnamon` (different HOME), e.g. a trial next to a real install.

## Step log
1. `git clone` (private repo, 1.8s, 15 MB) + `uv tool install -e .` (1s) + `finnamon doctor`: works. `cd web && npm install`: 2.4s. Fast.
   README/INSTALL clone line is still `git clone <repo> ~/finnamon` (README 48-50, 116-119; INSTALL 30, WSL step 4): copy-paste fails.
   README demo block runs `uv tool install -e .` then `npm install`; fine. `finnamon demo` took <1s and **opened a tab in the default browser** (no --print in README).
   Demo port: "first free from 8890", got 8891; docs never say that. INSTALL/README dashboard port 8888 (card said 7077: only via PORT).
2. `finnamon init` (needs a real tty and an unset agent env; I used `script` + `env -u CLAUDECODE`): Plaid/Telegram skipped; wording clear;
   step 3 says "Claude Code is not logged in" and still writes the bundle; step 5 declined. Prompt order fine.
   Sandbox: INSTALL says linking *always* uses Production and sandbox secrets are "for the test suite"; `FINNAMON_PLAID_ENV=sandbox`
   (config.py:58) is the only way and appears in no public doc. A stranger cannot try the sandbox flow from the docs.
   Empty dashboard (`node web/server.js`, PORT=7077): good empty states ("Link a bank to start", "No budgets yet", "Daemon stopped").
   "Link account" with no keys: clear message pointing at `finnamon init --plaid`. `finnamon link` same (NO_CREDENTIALS). Good.
   Demo dashboard: numbers self-consistent (cash 24,792 + inv 244,270 + property 706,500 - liab 416,099 = 559,464 net worth;
   budget bars match alert text). One 503 in console: `/api/update?load=1`. Budgets card has a large blank area under 4 categories.
3. Alerts (demo household, dummy Telegram token `000000:DUMMY-QA-TOKEN`, chat_id 1; removed after): rule texts read well:
   duplicate, new recurring, low balance, budget pace x3 (over / on pace). Anomaly `first_merchant`: "$899.99 to Best Buy on Blue Cash Preferred …3005, Oct 1."
   (no "first time" cue, no reply hint). `finnamon notify` with a bad token: JSON `{"sent":0}` + a log WARNING `401 invalid token`, exit 0:
   easy to miss. `notify --roundup` fails the same way (warning only). Triage path not run (no Claude login in scratch HOME).
   README sample alerts are *nicer than the real text* ("Expected? Reply 'it's normal' and I won't ask again", "charged Sep 17 and again Sep 18",
   "if you filled up twice"); real: "Reply it's normal if it was meant", new-recurring has no reply hint.
4. Intercom: scratch HOME => claude logged out => the intercom shows Claude Code's first-run theme picker ("Let's get started"). Not a bug
   (a real new user has run `claude`), but nothing in the page says "sign in to claude in this terminal first" and the dashboard health pill
   shows "ready". "dining by month" chart NOT run. Codex: docs read consistently (INSTALL "Codex instead of Claude Code", "what v1 does not do",
   COMMANDS row, DASHBOARD "On Codex"); installed codex is 0.157.0 = the minimum; settings gate (`_codex_selectable`) has clear refusal text. No live run.
5. Fetch by AI docs (COMMANDS.md row, ~5.8k characters in ONE table cell, line 63): accurate to code as far as I could check (flags `--extension`, `--no-cdp`,
   `--attach`, `--newest-download`, `--pair-extension`, `--diagnose`, `--reset-profile` all exist) but unreadable: HSBC-specific EAC history,
   owner test dates ("the owner's tests on 2026-10-06") and internals are in the user docs. Not run.
6. Uninstall: INSTALL has no uninstall section. `finnamon install --uninstall` only removes the jobs ("removed"). Nothing documents: removing
   `~/.finnamon` (db, secrets, backups), `uv tool uninstall finnamon`, the `projects[<assistant dir>]` trust entry init wrote to `~/.claude.json`
   (init says it writes there), logs in `~/Library/Logs/finnamon`, the Telegram plugin registration, `~/.finnamon/chrome*` profiles.
   **Bug:** `finnamon install --uninstall --dry-run` ignores `--dry-run` and really uninstalls (cli.py:2491). Live install/uninstall was not
   re-run after the incident (the classifier, rightly, stopped me).
7. README claims: platforms Mac/Linux/WSL2 (WSL CI workflow referenced; not testable here). "syncs every 6 hours": default 6 (daemon.py:100, heartbeat.py:50);
   `settings` allows 1-48 in store.py:148 but caps 12 in :191 and docs say 1 to 12 (fine). "Sunday roundup": notify.send_roundup sends Sunday, with a Monday
   catch-up, "all quiet" message when nothing; roundup 401 not surfaced. COMMANDS.md flags all exist except none missing from `--help` (all top-level
   commands present). README links (docs/*, screenshots, teaser gif) resolve; the demo .mp4 and CI badge 404 while the repo is private (expected).

## Launch blockers (ranked)
1. **`<repo>` placeholder** in every clone command (README x2, INSTALL x2). A stranger's first copy-paste fails. (Set when the public repo exists, #113.)
2. **No sandbox path in public docs** and "init" only verifies Production: a stranger cannot try the flow without real bank access/Plaid approval
   (which INSTALL itself calls "the slow part"). The only try-it path is `finnamon demo`; say so loudly near the top, or document `FINNAMON_PLAID_ENV=sandbox`.
3. **`install --uninstall --dry-run` actually uninstalls**, and there is **no uninstall/cleanup doc** (see step 6). A "self-hosted, nobody can shut it down" launch will get "how do I remove it" first.
4. **`finnamon demo` opens the browser by default**, and its README block is the first command people run; also port is auto-picked (8891 here), not stated. Mention `--print`/the port.
5. Plaid Trial claims in README ("includes Chase, BofA, Wells Fargo") vs INSTALL's own "Open question: does Chase link on a Trial key? Nobody has confirmed": README states as fact what INSTALL says is unconfirmed.

## Polish
- COMMANDS.md row 63 (Fetch by AI) is a 5.8k-character table cell with test dates and internals; split into a section.
- README sample alert text does not match real text (step 3); new-recurring and anomaly alerts carry no "reply it's normal" cue.
- Bad Telegram token only produces a log warning from `notify`/roundup, exit 0; `doctor` should be where it shows (not checked with a bad token).
- `/api/update?load=1` returns 503 on the demo dashboard (console error).
- Budgets card blank space on the demo dashboard at 1280.
- Intercom with logged-out claude shows the raw first-run theme picker with a "ready" pill; add a pre-check/message.
- `finnamon status` "scheduler" field is the raw global `launchctl list | grep finnamon` (showed the owner's jobs while doctor said "not installed" for the scratch home).
- docs/launch/ (Show HN facts, Reddit drafts, X thread) and docs/research ship in the public tree: probably move out before launch.
- DEVELOPMENT/INSTALL: "Docs say init is for a person at a terminal": `init`, `install`, `open --print`, `notify` refuse under agent env; fine for the assistant, but the card-style QA from a Claude session needs `env -u CLAUDECODE -u FINNAMON_FROM_AGENT` and a tty.

## Not done / assumptions
- No Plaid sandbox client_id/secret available (only in the owner's real secrets.toml): asked Billion, no reply. So no `link`, first sync, or live detectors on sandbox data;
  alerts were examined on the `finnamon demo` household, which runs the same detectors.
- Scratch HOME is not logged in to claude, and I would not hunt for credentials: no live intercom chart, no triage/anomaly verdict.
- Live install/uninstall skipped after the incident above.
- Headless screenshots used Playwright's chrome-headless-shell, not Google Chrome.
- Daemons I started: the scratch dashboard on :7077 (killed), the demo dashboard on :8891 (`demo --stop`). Final `launchctl list | grep finnamon` shows only the owner's three jobs, as before.
