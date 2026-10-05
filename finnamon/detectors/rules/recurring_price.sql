-- A subscription's price moved: a recurring outflow stream's latest charge differs from the charge before it, while
-- the two before that were the same (a fixed price, so a utility bill that varies every month never fires). Plaid's
-- average already includes the new price, so the charges themselves are compared: Netflix $15.49, $15.49, then $17.99.
-- The last charge is the transaction at the stream's last_date and last_amount; the earlier ones are the same merchant on
-- the same account. One alert per charge (the key is that transaction).
WITH last AS (
  SELECT r.stream_id, r.frequency, t.transaction_id, t.account_id, t.account_name, t.mask, t.display, t.canonical, t.date, t.amount,
         (SELECT p.amount FROM tx p WHERE p.account_id = t.account_id AND p.canonical = t.canonical AND p.pending = 0 AND p.amount > 0
            AND p.date < t.date ORDER BY p.date DESC, p.transaction_id DESC LIMIT 1) AS prev1,
         (SELECT p.amount FROM tx p WHERE p.account_id = t.account_id AND p.canonical = t.canonical AND p.pending = 0 AND p.amount > 0
            AND p.date < t.date ORDER BY p.date DESC, p.transaction_id DESC LIMIT 1 OFFSET 1) AS prev2,
         (SELECT p.date FROM tx p WHERE p.account_id = t.account_id AND p.canonical = t.canonical AND p.pending = 0 AND p.amount > 0
            AND p.date < t.date ORDER BY p.date DESC, p.transaction_id DESC LIMIT 1) AS prev_date
  FROM recurring r
  JOIN tx t ON t.account_id = r.account_id AND t.date = r.last_date AND t.amount = r.last_amount AND t.pending = 0
  WHERE r.direction = 'outflow' AND r.status NOT IN ('EARLY_DETECTION', 'TOMBSTONED')
    AND COALESCE(r.is_active, 1) = 1
    AND r.first_seen_at <= :as_of
    AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
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
