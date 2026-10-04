-- The household's own type for an account (`finnamon account type`): Merrill sends a managed CMA as depository /
-- cash management, but it is an investment account. type/subtype stay the effective type every reader uses (net worth,
-- tx_now's flow, the dashboard); source_type/source_subtype keep what Plaid (or `account add`) said, so `--clear`
-- restores it. Sync writes the source columns always and type/subtype only while type_override is NULL.
ALTER TABLE accounts ADD COLUMN type_override TEXT;
ALTER TABLE accounts ADD COLUMN source_type TEXT;
ALTER TABLE accounts ADD COLUMN source_subtype TEXT;
UPDATE accounts SET source_type = type, source_subtype = subtype;
