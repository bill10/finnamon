-- Plaid couldn't identify the merchant and nobody has aliased the raw name: "I can't tell what this is."
-- Plaid accounts only: an imported row never has a merchant id or a clean name, so on a manual account this would fire for every row.
SELECT t.account_id, 'anomaly:no_source' AS kind, 'anom:nosrc:' || t.transaction_id AS key, t.transaction_id,
       json_object('name', t.name, 'amount', t.amount, 'date', t.date, 'category', t.category,
                   'account', t.account_name, 'mask', t.mask, 'as_of', :as_of) AS payload
FROM tx t
WHERE t.pending = 0 AND NOT t.suppressed AND t.flow NOT IN ('card_payment', 'transfer', 'mortgage') AND t.source = 'plaid'
  AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
  AND t.merchant_entity_id IS NULL AND t.merchant_name IS NULL
  AND t.alias_canonical IS NULL
  AND abs(t.amount) >= (SELECT value FROM g WHERE key='outlier_min_amount');
