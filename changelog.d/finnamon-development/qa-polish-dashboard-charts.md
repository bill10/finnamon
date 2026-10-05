---
bump: patch
---
### Changed
- **Charts read like the rest of the page.** By-category labels are "Food and drink" (not Plaid's codes), no category is "Uncategorized", every dollar axis says `$`, a chart with no rows says "No data yet", and the balance chart leaves loans (the mortgage) out so checking is readable (`finnamon chart balance_history all` brings them back). On a phone, axis labels thin out instead of overlapping and the Recurring table wraps its merchant instead of cutting columns.
- **A ninth chart is refused, not dropped.** The board says "8 charts max; remove one"; the chart chips say whether a click adds or removes.
- **Dashboard wording.** A bad budget amount no longer blames the name (and `$1,200` works); error toasts drop the "error:" prefix; Overall budgets says how many budgets are over; the header counts a bank once when it has a Plaid link and a manual account; Settings says how to change things; the demo says it can't update.

### Fixed
- **Undo only for recent dismissals** (7 days), so last month's alert is not reopened by accident.
- **A blank Bank is asked for**, not guessed from the account name's first word ("Credit Union" was "Credit").
- **An account added this month is not "growth this month"** in the net-worth change.
