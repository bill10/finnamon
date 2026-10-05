-- Same account, same merchant, same raw bank string, same amount, within dup_window_days, both posted, both after the baseline.
-- A rule goes straight to the chat, so money that repeats by design stays out: transfers (Plaid files Venmo, Zelle,
-- Cash App and ATM withdrawals under TRANSFER_OUT) and loan payments (card autopays, mortgages). The raw name keeps
-- two plans at one merchant (same entity id, different bank string) from pairing.
-- One alert per run of repeats, on its latest charge: b gathers every earlier twin a, and a b with a later twin of its own
-- stays quiet, so three charges are one "charged 3 times", not three pairs. A third charge that lands after the pair
-- was told is a new alert of its own (a new latest charge, a new key).
-- Outer leg is bounded to lookback + window days so a late-syncing second charge still meets its earlier twin;
-- inner leg is a sargable date range so idx_txn_account_date does the work (was quadratic: 8s at 30k rows).
SELECT
  b.account_id,
  'duplicate_charge' AS kind,
  'dup:' || min(min(a.transaction_id), b.transaction_id) || ':' || max(max(a.transaction_id), b.transaction_id)
         || CASE WHEN count(*) > 1 THEN ':' || (count(*) + 1) ELSE '' END AS key,   -- a pair keeps its old key
  b.transaction_id,
  json_object('merchant', b.display, 'amount', b.amount, 'account', b.account_name, 'mask', b.mask,
              'date_a', min(a.date), 'date_b', b.date, 'count', count(*) + 1,
              'txn_a', min(a.transaction_id), 'txn_b', b.transaction_id, 'as_of', :as_of) AS payload
FROM tx a
JOIN tx b
  ON b.account_id = a.account_id
 AND b.date BETWEEN a.date AND date(a.date, '+' || (SELECT text FROM g WHERE key='dup_window_days') || ' days')
 AND b.transaction_id <> a.transaction_id
 AND b.canonical  = a.canonical
 AND b.name       = a.name
 AND b.amount     = a.amount
 AND (b.date > a.date OR b.transaction_id > a.transaction_id)
WHERE a.pending = 0 AND b.pending = 0
  AND a.date >= date(:as_of, '-' || ((SELECT value FROM g WHERE key='lookback_days') + (SELECT value FROM g WHERE key='dup_window_days')) || ' days')
  AND a.date >= date(a.first_synced_at) AND b.date >= date(b.first_synced_at)
  AND a.amount >= (SELECT value FROM g WHERE key='dup_min_amount')
  AND COALESCE(a.category_primary, '') NOT IN ('TRANSFER_IN', 'TRANSFER_OUT', 'LOAN_PAYMENTS')
  AND COALESCE(b.category_primary, '') NOT IN ('TRANSFER_IN', 'TRANSFER_OUT', 'LOAN_PAYMENTS')
  AND NOT a.suppressed AND NOT b.suppressed
  AND NOT EXISTS (   -- a later twin carries this run's alert
    SELECT 1 FROM tx c
    WHERE c.account_id = b.account_id
      AND c.date BETWEEN b.date AND date(b.date, '+' || (SELECT text FROM g WHERE key='dup_window_days') || ' days')
      AND c.transaction_id <> b.transaction_id
      AND c.canonical = b.canonical AND c.name = b.name AND c.amount = b.amount
      AND (c.date > b.date OR c.transaction_id > b.transaction_id)
      AND c.pending = 0 AND c.date >= date(c.first_synced_at)
      AND COALESCE(c.category_primary, '') NOT IN ('TRANSFER_IN', 'TRANSFER_OUT', 'LOAN_PAYMENTS')
      AND NOT c.suppressed)
GROUP BY b.transaction_id;
