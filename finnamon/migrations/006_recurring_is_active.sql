-- Plaid's is_active: false once a stream has stopped (a cancelled subscription). recurring_changed skips inactive
-- streams, so a 24-month backfill's dead subscriptions stop reading as "skipped" every month.
-- NULL until the next sync fills it; read as active.
ALTER TABLE recurring ADD COLUMN is_active INTEGER;
