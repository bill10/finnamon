-- Manual accounts: a bank Plaid can't reach (HSBC US personal banking), fed by the CSV files its site exports.
-- `finnamon account add` makes the Item (source = 'manual', no token, never synced); `finnamon import` writes the rows.
ALTER TABLE items ADD COLUMN source TEXT NOT NULL DEFAULT 'plaid' CHECK (source IN ('plaid', 'manual'));   -- sync walks 'plaid', sync_health nags both; a third value would fall through both
-- sync_health nags about a manual account nobody has imported into for this long.
INSERT OR IGNORE INTO settings(account_id, key, value) VALUES ('*', 'import_max_age_days', '35');
