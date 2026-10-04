-- A detailed category the household has never spent in before this transaction.
SELECT t.account_id, 'anomaly:new_category' AS kind, 'anom:cat:' || t.transaction_id AS key, t.transaction_id,
       json_object('merchant', t.display, 'amount', t.amount, 'date', t.date, 'category', t.category,
                   'account', t.account_name, 'mask', t.mask, 'as_of', :as_of) AS payload
FROM tx t
WHERE t.pending = 0 AND NOT t.suppressed AND t.amount > 0
  AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
  AND t.amount >= (SELECT value FROM g WHERE key='outlier_min_amount')
  AND t.category IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM tx p WHERE p.category = t.category AND p.transaction_id <> t.transaction_id AND p.date < t.date);
