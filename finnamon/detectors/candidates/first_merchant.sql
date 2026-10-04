-- First-ever charge from this merchant on any household account, in the last 7 days, at least outlier_min_amount.
SELECT t.account_id, 'anomaly:first_merchant' AS kind, 'anom:first:' || t.transaction_id AS key, t.transaction_id,
       json_object('merchant', t.display, 'amount', t.amount, 'date', t.date, 'account', t.account_name, 'mask', t.mask,
                   'category', t.category, 'confidence', t.pfc_confidence, 'as_of', :as_of) AS payload
FROM tx t
WHERE t.pending = 0 AND NOT t.suppressed
  AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
  AND t.amount >= (SELECT value FROM g WHERE key='outlier_min_amount')
  AND NOT EXISTS (SELECT 1 FROM tx p WHERE p.canonical = t.canonical AND p.transaction_id <> t.transaction_id AND p.date < t.date);
