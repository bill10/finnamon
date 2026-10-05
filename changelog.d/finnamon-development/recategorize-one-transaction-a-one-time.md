---
bump: minor
---
### Added
- **Recategorize one transaction without touching the merchant.** `finnamon category --tx <transaction_id> <category>` is a one-time edit of that charge only (`--clear` undoes it); `finnamon category <merchant> <category>` stays the rule for every past and future charge of a merchant. The one-time edit wins over the rule in budgets, charts, spending totals and the detectors. Ask the assistant "that Costco charge was groceries" and it edits just that one; "Costco is always groceries" sets the rule; when it can't tell which you mean, it asks. The MCP `search_transactions` tool now returns `transaction_id`.
