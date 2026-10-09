---
bump: patch
---
### Added
- **The budget trend chart has an "Overall" choice, first in its picker.** Each month shows everything spent across your budgets against the sum of their limits, drawn like a single budget (over-limit months in red, this month paler). A budget counts from the month its first limit took effect, so one set up recently does not raise the older months' line. It is the sum of the budgets as they are now: two budgets that overlap (dining inside food) count a shared charge in each, as their limits do. The picker remembers Overall like any budget; `finnamon chart --spec budgets --budget overall` opens on it.
