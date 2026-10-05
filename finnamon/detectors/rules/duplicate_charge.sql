-- Same account, same merchant, same raw bank string, same amount, within dup_window_days, both posted, both after the baseline.
-- A rule goes straight to the chat, so money that repeats by design stays out: transfers (Plaid files Venmo, Zelle,
-- Cash App and ATM withdrawals under TRANSFER_OUT) and loan payments (card autopays, mortgages). The raw name keeps
-- two plans at one merchant (same entity id, different bank string) from pairing.
-- One alert per run of repeats, on its latest charge: charges no more than dup_window_days apart are one run (a chain
-- counts whole), so three charges are one "charged 3 times", not three pairs. A charge that lands after the run was told
-- makes a longer run: a new latest charge, a new key, a new alert. A run of two keeps the old pair key dup:<a>:<b>.
-- One pass over the window (lag / running sum), never a self-join: the old pair join was quadratic until indexed.
WITH c AS (
  SELECT transaction_id, account_id, canonical, name, amount, date, display, account_name, mask,
         CASE WHEN julianday(date) - julianday(lag(date) OVER w) <= (SELECT value FROM g WHERE key='dup_window_days') THEN 0 ELSE 1 END AS starts
  FROM tx
  WHERE pending = 0   -- no lookback bound here: a run cut at the bound would change its key as its head ages out, and alert again
    AND date >= date(first_synced_at)
    AND amount >= (SELECT value FROM g WHERE key='dup_min_amount')
    AND COALESCE(category_primary, '') NOT IN ('TRANSFER_IN', 'TRANSFER_OUT', 'LOAN_PAYMENTS')
    AND NOT suppressed
  WINDOW w AS (PARTITION BY account_id, canonical, name, amount ORDER BY date, transaction_id)
),
runs AS (
  SELECT *, sum(starts) OVER (PARTITION BY account_id, canonical, name, amount ORDER BY date, transaction_id ROWS UNBOUNDED PRECEDING) AS run
  FROM c
),
r AS (
  SELECT *,
         row_number() OVER k AS from_last,
         count(*) OVER (PARTITION BY account_id, canonical, name, amount, run) AS n,
         min(date) OVER (PARTITION BY account_id, canonical, name, amount, run) AS first_date,
         min(transaction_id) OVER (PARTITION BY account_id, canonical, name, amount, run) AS min_id,
         max(transaction_id) OVER (PARTITION BY account_id, canonical, name, amount, run) AS max_id,
         first_value(transaction_id) OVER (PARTITION BY account_id, canonical, name, amount, run ORDER BY date, transaction_id) AS first_id
  FROM runs
  WINDOW k AS (PARTITION BY account_id, canonical, name, amount, run ORDER BY date DESC, transaction_id DESC)
)
SELECT
  account_id,
  'duplicate_charge' AS kind,
  'dup:' || min_id || ':' || max_id || CASE WHEN n > 2 THEN ':' || n ELSE '' END AS key,
  transaction_id,
  json_object('merchant', display, 'amount', amount, 'account', account_name, 'mask', mask,
              'date_a', first_date, 'date_b', date, 'count', n,
              'txn_a', first_id, 'txn_b', transaction_id, 'as_of', :as_of) AS payload
FROM r
WHERE from_last = 1 AND n >= 2
  AND date >= date(:as_of, '-' || ((SELECT value FROM g WHERE key='lookback_days') + (SELECT value FROM g WHERE key='dup_window_days')) || ' days');
