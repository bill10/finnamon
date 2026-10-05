---
bump: minor
---
### Added
- **Delete a merchant's category rule, and list the rules.** `finnamon category <merchant> --clear` removes the rule so that merchant's charges fall back to the bank's category (it says what it removed, or that there was no rule); one-time `--tx` edits are left alone. `finnamon category --rules` lists every rule (merchant, category, when set, how many charges it covers) and the one-time edits. Ask the assistant "stop putting Costco in groceries" or "undo the Costco rule"; it no longer has to overwrite the rule with the bank's original category.
