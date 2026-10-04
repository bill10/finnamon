-- How an alert was resolved, so the dashboard can say it and undo it (issue 74). resolution: 'normal' (`finnamon normal
-- --alert`, which wrote suppression_id) or 'dismissed' (`finnamon alerts --dismiss`, no rule). NULL with resolved_at set:
-- resolved before this column, or by Finnamon itself (a bank removed or fixed); nothing to undo.
ALTER TABLE alerts ADD COLUMN resolution TEXT;
ALTER TABLE alerts ADD COLUMN suppression_id INTEGER;
