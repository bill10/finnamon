-- A subscription's price moved: a recurring outflow stream's latest charge differs from the charge before it, while
-- the two before that were the same (a fixed price, so a utility bill that varies every month never fires). Plaid's
-- average already includes the new price, so the charges themselves are compared: Netflix $15.49, $15.49, then $17.99.
-- The last charge is the transaction at the stream's last_date and last_amount; the earlier ones are the same merchant on
-- the same account. One alert per charge (the key is that transaction).
WITH hist AS (   -- one pass over the charges: each with the two before it, same merchant, same account
  SELECT transaction_id, account_id, account_name, mask, display, canonical, date, amount, first_synced_at,
         COALESCE(merchant_entity_id, merchant_name, name) AS stream_key,
         lag(amount, 1) OVER w AS prev1, lag(amount, 2) OVER w AS prev2, lag(date, 1) OVER w AS prev_date
  FROM tx WHERE pending = 0 AND amount > 0
  WINDOW w AS (PARTITION BY account_id, canonical, name ORDER BY date, transaction_id)   -- the raw name keeps two plans apart, as in duplicate_charge
),
last AS (
  SELECT r.stream_id, r.frequency, h.*
  FROM recurring r
  JOIN hist h ON h.account_id = r.account_id AND h.date = r.last_date AND h.amount = r.last_amount
             AND h.stream_key = COALESCE(r.merchant_entity_id, r.merchant_name, r.description)   -- the stream's own merchant
  WHERE r.direction = 'outflow' AND r.status NOT IN ('EARLY_DETECTION', 'TOMBSTONED')
    AND COALESCE(r.is_active, 1) = 1
    AND r.first_seen_at <= :as_of
    AND NOT EXISTS (   -- several live subscriptions at one merchant on one account (Apple, Google Play): their charges cannot be told apart
      SELECT 1 FROM recurring o WHERE o.account_id = r.account_id AND o.stream_id <> r.stream_id AND o.direction = 'outflow'
        AND o.status NOT IN ('EARLY_DETECTION', 'TOMBSTONED') AND COALESCE(o.is_active, 1) = 1
        AND COALESCE(o.merchant_entity_id, o.merchant_name, o.description) = COALESCE(r.merchant_entity_id, r.merchant_name, r.description))
    AND h.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND h.date >= date(h.first_synced_at) AND h.date <= date(:as_of)
)
SELECT account_id, 'recurring_price' AS kind, 'recprice:' || transaction_id AS key, transaction_id,
       json_object('merchant', display, 'old_amount', prev1, 'amount', amount, 'date', date, 'prev_date', prev_date, 'frequency', frequency,
                   'account', account_name, 'mask', mask, 'stream_id', stream_id, 'as_of', :as_of) AS payload
FROM last
WHERE round(prev1, 2) = round(prev2, 2)
  AND round(amount, 2) <> round(prev1, 2)
  AND NOT EXISTS (   -- tx.suppressed cannot see a stream: `normal --alert` on one of these acknowledges that subscription only
    SELECT 1 FROM sup WHERE (sup.kind IS NULL OR sup.kind = 'recurring_price')
      AND (sup.stream_id IS NULL OR sup.stream_id = last.stream_id)
      AND (sup.canonical IS NULL OR sup.canonical = last.canonical)
      AND (sup.account_id IS NULL OR sup.account_id = last.account_id)
      AND (sup.max_amount IS NULL OR sup.max_amount >= last.amount))
GROUP BY transaction_id;   -- two streams on one charge: one alert
