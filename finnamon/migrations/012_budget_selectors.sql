-- A budget covers several categories and merchants (`finnamon budget set dining 400 --category restaurants --category
-- coffee --merchant "Blue Bottle"`): a transaction counts if any selector matches it, once however many do.
-- budgets.category stays (NOT NULL, read by older callers) and holds the first category, or '' for a merchant-only budget.
-- `fixed`: one bill a month (childcare, HOA): pace is what was spent, never a projection.
CREATE TABLE IF NOT EXISTS budget_selectors (
  budget_id INTEGER NOT NULL,                    -- budgets.id
  kind      TEXT NOT NULL CHECK (kind IN ('category', 'merchant')),
  value     TEXT NOT NULL,                       -- a pfc_primary or pfc_detailed; for a merchant, a tx_now.canonical (budgets.resolve_merchant: one row each)
  label     TEXT,                                -- the merchant as the person typed it
  PRIMARY KEY (budget_id, kind, value)
);
INSERT OR IGNORE INTO budget_selectors (budget_id, kind, value) SELECT id, 'category', category FROM budgets WHERE category <> '';
ALTER TABLE budgets ADD COLUMN fixed INTEGER NOT NULL DEFAULT 0;
