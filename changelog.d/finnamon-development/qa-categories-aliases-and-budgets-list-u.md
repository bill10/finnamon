---
bump: minor
---
### Added
- **Aliases can be listed and undone.** `finnamon alias --list` shows each alias with the transactions it covers today; `finnamon alias --remove "<name>"` deletes one and says if a budget, a category rule or a "normal" rule stops covering its charges. An alias that matches no transaction (a typo) is refused with the raw names it might mean.
- **Change a category on the dashboard.** Any transaction row (Recent, Large, Uncategorized) opens a picker: this charge only, or every charge from that merchant. The Recent table shows the detailed category ("restaurant", not "food and drink").
- **Budgets pick what they count on the dashboard.** Manage budgets shows what each one counts, and its pencil opens a picker of categories and the merchants you've seen; a new budget can pick them too, so "subscriptions" can be Netflix and Spotify.
- **Uncategorized rows are visible.** After an import (CLI or dashboard) Finnamon says how many new rows have no category and offers to categorize them by merchant or by charge; the Budgets card has an "Uncategorized (N)" entry; `finnamon category --uncategorized` lists them. Nothing is guessed.

### Changed
- **`budget set` says exactly what it counts**, and when a name was only read as a category ("dining" → Restaurant). "utilities" now means every utility but rent; "subscriptions", "kids" and "car insurance" are refused with suggestions instead of quietly counting the wrong thing. It warns when another budget already counts the same charges, and the dashboard's Overall counts a shared charge once.
- **`threshold` refuses a card or brokerage account** (it could never fire), and a name that matches no account lists the closest ones. The docs' example now uses the bank's own account name.

### Fixed
- **The assistant's "dining" chart and table examples** counted all of food and drink (groceries and coffee too); they now count restaurants and fast food.
