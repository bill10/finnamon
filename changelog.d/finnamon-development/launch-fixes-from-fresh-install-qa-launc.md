---
bump: patch
---
### Fixed
- **A second Finnamon home can no longer replace the household's background jobs.** The plain `com.finnamon.*` launchd labels (and `finnamon-*` systemd units) now belong only to `~/.finnamon` of the real login, read from the password database rather than `$HOME`. Before, a trial install with a scratch `HOME` took them over. Every other home gets its own suffixed names. `install` and `install --uninstall` now refuse to touch a job written for a different `FINNAMON_HOME`, and refuse to install the household's jobs from a shell whose `HOME` is not your login's. Pass `--force` to override either. `status` lists only this home's jobs.
- **`finnamon install --uninstall --dry-run` only looks.** It names the unit files it would remove and removes nothing. Before, it really uninstalled.
- **A bad Telegram token is no longer silent.** `finnamon notify` (and `--roundup`) exits non-zero with one line naming the cause, such as "Telegram rejected the bot token (401: Unauthorized)", or that no household chat is recorded yet. `finnamon doctor` tells a refused token apart from a network that is down.
- **The intercom waits for a sign-in instead of showing Claude Code's first-run screens.** When Claude Code (or Codex) is signed out, the chat says "Sign in to Claude Code first: run `claude` in a terminal" and the health pill shows it too. The session starts by itself within 30 seconds of signing in. New: `finnamon status --login`.
- **Alerts say what to reply.** New-recurring and anomaly alerts end with "Expected? Reply *it's normal* and I won't ask again". A first-time merchant says "first time at Best Buy", and a two-day duplicate reads "charged Sep 17 and again Sep 18".
- **The demo dashboard no longer logs a 503** for its routine update check.
- **The Budgets card fits its content** instead of stretching beside a long alerts list.
