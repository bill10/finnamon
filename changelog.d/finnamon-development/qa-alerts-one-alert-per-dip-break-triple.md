---
bump: minor
---
### Added
- **Subscription price changes alert directly: "Netflix went from $15.49 to $17.99".** A new `recurring_price` rule compares a subscription's latest charge with the two before it (which must agree, so a bill that varies every month stays quiet). The old check against Plaid's average almost never fired, because the average already includes the new price. "It's normal" on one acknowledges that subscription only.
- **The dashboard's alert list shows "Show N more"** when more than 8 are open, instead of stopping at 8.

### Changed
- **Three identical charges are one alert: "Charged 3 times".** A run of repeats is one alert on its latest charge, not one per pair (three Shell charges sent two messages).
- **Low balance alerts once per dip,** not every week while the balance stays low; it alerts again only after the account recovers above the threshold and drops again.
- **A bank that stops syncing is one alert per problem, not one a day, and it closes by itself** once the bank syncs again (or a newer problem at that bank replaces it). "Hasn't synced" and sync errors now say what to do: `finnamon doctor` on the Finnamon box; a bank that wants a login offers the re-login.
- **Card payments and transfers between your own accounts are no longer anomaly candidates** (both sides of every card payment used to come up as "can't tell what this is" each month), and two charges at a new merchant on one day are one "first time here", not two.

### Fixed
- **`link --remove` closes that bank's alerts,** sent ones included, so they leave the dashboard and `alerts --open`.
