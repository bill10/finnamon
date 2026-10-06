---
bump: minor
---
### Changed
- **The budget trend chart shows one budget at a time.** Pick the budget from the chips inside the chart card (a menu past six budgets; a dot marks one over its limit). It shows that budget's spend for each of the last 12 months as bars, green within the limit and red over, with the current month paler and marked "(so far)", and the limit as a line across the months. The page remembers your pick in this browser. Until you pick one, it opens on the first budget over its limit, or the first budget. `finnamon chart budgets --budget dining` sends the same view as a PNG. The chart is still named `budgets`, so saved boards keep working.

### Added
- **Budgets keep a history of their limits.** Every change to a limit is recorded, whether it comes from `finnamon budget set`, the dashboard or the assistant. The chart's limit line uses the limit that applied at the end of each month, so the line steps when a limit is raised or lowered. A budget that existed before this release starts with its current limit, dated from when the budget was created.
