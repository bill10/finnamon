---
bump: minor
---
### Changed
- **The recurring table shows only live streams.** Streams Plaid marks inactive, TOMBSTONED ones, and ones whose last charge is older than their own interval plus a grace (monthly 45 days, annual 400) no longer dilute the list. They sit greyed behind a small "ended (N)" toggle under the table, so nothing is lost. `finnamon budget suggest` lists live streams too; `--include-ended` brings back the rest. Alerts are untouched.
