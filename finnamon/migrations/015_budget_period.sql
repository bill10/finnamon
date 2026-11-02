-- A budget's period (issue 53): its limit is per period, and spending is measured over the period's current window.
-- 'monthly' (the default, every budget before this), 'bimonthly', 'quarterly' or 'yearly'. anchor is the month (1-12)
-- a window starts in; NULL reads as January. budgets.monthly_limit keeps its name (older readers) and holds the limit
-- per period; budgets.window() is the window math, copied into detectors/rules/budget_pace.sql.
ALTER TABLE budgets ADD COLUMN period TEXT NOT NULL DEFAULT 'monthly';
ALTER TABLE budgets ADD COLUMN anchor INTEGER;
