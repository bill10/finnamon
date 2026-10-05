---
bump: minor
---
### Added
- **Readable terminal output.** `alerts`, `budget`, `budget overall`, `networth`, `property` and `settings` print tables and sentences on a terminal; piped, or with `--json`, they print the same JSON as before, so the assistant and the dashboard are unaffected.
- **Every typed amount takes `$` and thousands separators** (`budget set groceries 1,200`, `threshold Checking $500`, `settings set`, `property set`, `normal --max-amount`), through one shared parser.
- **Help text.** `budget`, `threshold`, `networth` and the `alerts` flags have descriptions, `chart` lists its presets, and `help` ends with "needs / next" for networth, run, daemon, heartbeat, notify, triage, settings, import, open, remote and web. `finnamon settings` says what each key does.

### Changed
- **Alert wording.** Dates read "Oct 1", duplicate-charge alerts end with "Reply it's normal", budget names are capitalized, a bank error is a plain sentence with Plaid's code in parentheses, and the quiet-week line has no seconds and says what is still waiting.
- **`budget suggest` medians count months with no spending as zero**, so one $642 flight in six months is no longer a $642 median.
- **`category --tx` and `category <merchant>` refuse a group like "Travel" the same way**, naming its members.
- **`normal --list` shows merchant names**, not `mch_shell`; `property remove nope` says "no property called nope"; `heartbeat` prints a sentence.
- **`doctor` shows one optional "Telegram not set up" line** on a dashboard-only household; `open` warns when the dashboard isn't running; `link` only says "Summary sent to Telegram" when it was; the post-link summary of an investment or loan bank no longer says "0 transactions since ?".
- **The database is created 0600** (and tightened if an older version made it 0644). The `sync_interval_hours` cap message names the check it protects. DASHBOARD.md says the dashboard is step 4, and COMMANDS.md gives one way to remove a manual account.
