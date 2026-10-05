---
bump: patch
---
### Fixed
- **Recategorizing or calling a merchant normal by any name you see now works, and never fakes success.** `finnamon category` and `finnamon normal "<merchant>"` take the display name, the raw bank text, an alias's name or its pattern, and say how many charges the rule covers. A name that matches no charge (a typo, a partial name) is refused with the merchant it probably meant ("0 charges match 'Amazn'; did you mean Amazon?"), a name shared by several merchants lists them, and no rule is written that covers nothing. `normal --kind` refuses a kind no rule can quiet and lists the valid ones.
- **"It's normal" works on low-balance, budget and stale-sync alerts.** The dashboard button and the assistant used to error; now it resolves that alert (Undo reopens it), writes no rule, and says when it will alert again and the fix to offer: a lower threshold, a new budget limit, or reconnecting the bank.
