---
bump: minor
---
### Added
- **`finnamon init --plaid` replaces the Plaid keys** (Sandbox → Production): it prompts without echoing the secrets, keeps what you leave blank, verifies, and skips the other steps. `doctor`, link errors and INSTALL.md point to it; linking always uses Production.
- **A manual account with no balance says so, and takes one.** The dashboard shows "no balance yet: enter it" and saves the figure in place; `finnamon account balance "<name>" <amount>` does the same.
- **Escape closes the intercom** instead of interrupting Claude's answer; a Stop button (and Ctrl-C) still interrupts.

### Changed
- **Detector drafts live in `~/.finnamon/detector-drafts/`**, not the code checkout; drafts an older version left in `finnamon/detectors/pending/` move over on the next `update`, `detect --draft` or `--review`.
- **`finnamon update` works for a household that skipped scheduling**, runs `npm ci` in `web/` when the dashboard's package files changed, and works from a git worktree. `help update` no longer claims `--dry-run` pulls and migrates.
- **Setup hints name the assistant's directory** (`cd ~/.finnamon/assistant && claude --strict-mcp-config`) or the dashboard's intercom, not a bare `claude`.
- **A non-member writing in the household chat gets one reply a day** naming `finnamon owner add <name> --user-id <id>`.

### Fixed
- **`link` with no keys, `link --update <unknown>` and `owner add` with no bot print one line and the next step**, not a traceback; `link --update` also takes an institution name when only one bank has it.
- **Link account in the dashboard checks the keys first** (and says the demo cannot link banks) before asking whose bank it is.
- **A Bank of America checking export imports**: a summary block above the header is skipped.
