-- Every limit a budget has had, so the budget trend chart draws the limit in effect each month. budgets.monthly_limit
-- stays the current one. The triggers record every change however it is made (`budget set` from the CLI, the dashboard
-- or the assistant); a budget that predates this table starts with its limit as of its created_at.
CREATE TABLE IF NOT EXISTS budget_limit_history (
  budget_id      INTEGER NOT NULL,             -- budgets.id
  monthly_limit  REAL NOT NULL,
  effective_from TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS budget_limit_history_budget ON budget_limit_history (budget_id, effective_from);
INSERT INTO budget_limit_history (budget_id, monthly_limit, effective_from) SELECT id, monthly_limit, created_at FROM budgets;
CREATE TRIGGER IF NOT EXISTS budget_limit_insert AFTER INSERT ON budgets BEGIN
  INSERT INTO budget_limit_history (budget_id, monthly_limit) VALUES (NEW.id, NEW.monthly_limit);
END;
CREATE TRIGGER IF NOT EXISTS budget_limit_update AFTER UPDATE OF monthly_limit ON budgets WHEN NEW.monthly_limit IS NOT OLD.monthly_limit BEGIN
  INSERT INTO budget_limit_history (budget_id, monthly_limit) VALUES (NEW.id, NEW.monthly_limit);
END;
