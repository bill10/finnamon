-- A suppression can name one recurring stream: "yes, I cancelled it" on a recurring_changed alert acknowledges
-- that subscription, not every stream at the merchant. NULL = any stream; only recurring_changed reads it.
ALTER TABLE suppressions ADD COLUMN stream_id TEXT;
