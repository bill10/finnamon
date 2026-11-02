---
bump: minor
---
### Added
- **Budgets with a period: bimonthly, quarterly or yearly.** `finnamon budget set water 130 --period bimonthly --anchor March`
  sets a limit per bill, counted over the bill's window, never projected; it alerts when a bill passes the limit or a window
  goes by with no bill. `finnamon budget set travel 6000 --category travel --period yearly` counts the year to date, lists
  "<amount> left this year" and alerts at 50%, 75% and 100% of the year. `budget suggest` gives a bimonthly, quarterly or
  annual bill's stream its period and anchor. Monthly stays the default; existing budgets are unchanged.
- **Overall counts each budget's monthly share, and names the budgets that are over.** A $130 bill every 2 months is $65
  of the month's total, in the bill's month and the one without; `finnamon budget overall` lists every budget over its own
  limit, so underspending elsewhere can't hide it. `chart budgets` draws a non-monthly budget by window, with a "you are
  here" mark on the current one.
