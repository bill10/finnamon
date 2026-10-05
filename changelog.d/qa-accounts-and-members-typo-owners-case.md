---
bump: minor
---
### Added
- **`finnamon owner rename` and `owner remove`** (a person at a terminal; on the assistant's deny list). Rename moves every account, bank and merge record to the new name; remove refuses while the member still owns an account.
- **Dashboard: "Same account seen twice?"** in Accounts marks two copies of a joint account as one (with an Undo), and Add a manual account has an owner field (members or Joint). Link account offers Joint.
- **A joint account linked from the dashboard or Telegram is now marked joint automatically**, the same way the terminal's yes does it, so it is no longer counted twice. A re-link over a broken login is still left for you to decide.

### Changed
- **A mistyped owner is refused** (`account add --owner`, `link --owner`, `account owner`) and lists the members, instead of quietly becoming a new household member. Names match without regard to case. `joint` is allowed for accounts and reserved as a name.
- **`account merge` checks first**: different account types or very different balances are refused unless `--force`. `account unmerge` gives both accounts their own owners back.
- **The assistant may add a joint manual account**; `account owner` is human-only.
- **The dashboard lists a bank's Plaid link and its by-hand accounts as separate groups**, each with its own sync time, so a stale Plaid sync is no longer hidden.
