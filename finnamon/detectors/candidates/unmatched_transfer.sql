-- A large transfer with no equal-and-opposite transaction on any household account within xfer_window_days.
-- While a manual account's last import predates the transfer, its side may simply not be in yet: hold the candidate
-- until an import that covers the date has landed (the key is per transaction, so it is judged at most once).
SELECT t.account_id, 'anomaly:unmatched_transfer' AS kind, 'anom:xfer:' || t.transaction_id AS key, t.transaction_id,
       json_object('name', t.display, 'amount', t.amount, 'date', t.date, 'direction', t.category_primary,
                   'account', t.account_name, 'mask', t.mask, 'as_of', :as_of) AS payload
FROM tx t
WHERE t.pending = 0 AND NOT t.suppressed
  AND t.category_primary IN ('TRANSFER_IN', 'TRANSFER_OUT')
  AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
  AND abs(t.amount) >= (SELECT value FROM g WHERE key='large_amount')
  AND NOT EXISTS (SELECT 1 FROM items mi WHERE mi.source = 'manual' AND COALESCE(mi.last_synced_at, mi.created_at) < t.date)
  AND NOT EXISTS (
    SELECT 1 FROM tx m
    WHERE m.transaction_id <> t.transaction_id
      AND m.amount = -t.amount
      AND abs(julianday(m.date) - julianday(t.date)) <= (SELECT value FROM g WHERE key='xfer_window_days')
  );
