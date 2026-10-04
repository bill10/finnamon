-- Same account, same merchant, same raw bank string, same amount, within dup_window_days, both posted, both after the baseline.
-- A rule goes straight to the chat, so money that repeats by design stays out: transfers (Plaid files Venmo, Zelle,
-- Cash App and ATM withdrawals under TRANSFER_OUT) and loan payments (card autopays, mortgages). The raw name keeps
-- two plans at one merchant (same entity id, different bank string) from pairing.
-- Outer leg is bounded to lookback + window days so a late-syncing second charge still meets its earlier twin;
-- inner leg is a sargable date range so idx_txn_account_date does the work (was quadratic: 8s at 30k rows).
SELECT
  a.account_id,
  'duplicate_charge' AS kind,
  'dup:' || min(a.transaction_id, b.transaction_id) || ':' || max(a.transaction_id, b.transaction_id) AS key,
  b.transaction_id,
  json_object('merchant', a.display, 'amount', a.amount, 'account', a.account_name, 'mask', a.mask,
              'date_a', a.date, 'date_b', b.date, 'txn_a', a.transaction_id, 'txn_b', b.transaction_id, 'as_of', :as_of) AS payload
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
  AND NOT a.suppressed AND NOT b.suppressed;
