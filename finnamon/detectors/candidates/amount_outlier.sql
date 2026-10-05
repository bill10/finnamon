-- Amount above outlier_multiplier × this merchant's median, with at least 3 prior charges.
-- ponytail: median over history before the 7-day window, not per-row; per-row windows are O(n²) for no gain here.
WITH hist AS (
  SELECT canonical, amount,
         row_number() OVER (PARTITION BY canonical ORDER BY amount) AS rn,
         count(*)     OVER (PARTITION BY canonical) AS cnt
  FROM tx WHERE pending = 0 AND amount > 0 AND date < date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days')
),
med AS (
  SELECT canonical, avg(amount) AS median, max(cnt) AS cnt
  FROM hist WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
  GROUP BY canonical
)
SELECT t.account_id, 'anomaly:amount_outlier' AS kind, 'anom:outlier:' || t.transaction_id AS key, t.transaction_id,
       json_object('merchant', t.display, 'amount', t.amount, 'median', round(m.median, 2), 'priors', m.cnt,
                   'date', t.date, 'account', t.account_name, 'mask', t.mask, 'as_of', :as_of) AS payload
FROM tx t JOIN med m ON m.canonical = t.canonical
WHERE t.pending = 0 AND NOT t.suppressed
  AND t.flow NOT IN ('card_payment', 'transfer', 'mortgage')   -- money moving between the household's own accounts is not news
  AND t.date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND t.date >= date(t.first_synced_at) AND t.date <= date(:as_of)
  AND m.cnt >= 3
  AND t.amount >= (SELECT value FROM g WHERE key='outlier_min_amount')
  AND t.amount > m.median * (SELECT value FROM g WHERE key='outlier_multiplier');
