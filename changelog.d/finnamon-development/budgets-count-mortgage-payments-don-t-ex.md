---
bump: minor
---
### Added
- **A budget can cover several categories and merchants.** `finnamon budget set dining 400 --category restaurants --category "fast food" --category coffee`, or `--merchant "Seattle Public Utilities" --merchant Recology` for a "water and trash" budget; each flag repeats and a charge several of them match counts once. `finnamon budget` keeps its `category` field and adds `categories`, `merchants` and `fixed`; the dashboard's budget card shows what each one covers. Existing budgets move over as they are.
- **`budget set … --fixed`** marks a budget that is one bill a month (childcare, the HOA): it is compared to its limit and never projected.
- **A merchant budget counts every charge of that merchant**, typed in any case, as the dashboard shows it, and on charges Plaid sent without its merchant id; `budget set --merchant` says how many past charges each one matches.

### Fixed
- **A mortgage budget counts the mortgage.** It reported $0 after the payment posted; now the checking-side payment counts once (the loan account's side never doubles it), even when the bank filed it as a transfer, it is never projected, and `budget suggest` shows it as the mortgage. A budget on `LOAN_PAYMENTS` (all loan payments) now counts the mortgage too. Transfers and card payments still never count.
- **Pace no longer multiplies a monthly bill.** A charge of a monthly or yearly bill Plaid sees as recurring (the same merchant, within 25% of its amount) counts once and only the rest of the month's spending is projected, so a $1,500 daycare bill on the 3rd no longer reads as $15,000 on pace or raises a pace alert.
- **Changing a limit on the dashboard keeps the budget's categories.** It used to re-derive the category from the budget's name.
