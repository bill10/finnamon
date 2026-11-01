---
bump: patch
---
### Fixed
- **The Sunday roundup no longer asks about a charge you already sorted out.** Each held alert is re-checked when the roundup goes out: if its charge was recategorized (one-time or for the merchant), given an alias, or marked normal since it was flagged, the alert is closed as "Handled before the roundup" instead of sent. One that still goes out uses the merchant's current name, and drops the assistant's guess if the charge's category has changed since (#52).
