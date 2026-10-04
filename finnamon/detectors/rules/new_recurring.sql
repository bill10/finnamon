-- A recurring outflow stream Plaid first observed after the baseline, stored within the last 7 days.
-- Stream-derived: replay from payload_json.
SELECT
  r.account_id,
  'new_recurring' AS kind,
  'recurring:' || r.stream_id AS key,
  NULL AS transaction_id,
  json_object('merchant', COALESCE(NULLIF(trim(r.merchant_name),''), NULLIF(trim(r.description),'')), 'amount', r.last_amount, 'frequency', r.frequency,
              'first_date', r.first_date, 'account', acc.name, 'mask', acc.mask, 'stream_id', r.stream_id, 'as_of', :as_of) AS payload
FROM recurring r
JOIN accounts acc ON acc.account_id = r.account_id AND acc.mirror_of IS NULL
JOIN items i ON i.item_id = acc.item_id
WHERE r.direction = 'outflow'
  AND r.status <> 'EARLY_DETECTION'
  AND i.first_synced_at IS NOT NULL
  AND r.first_date >= date(i.first_synced_at)
  AND r.first_seen_at >= datetime(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days')
  AND r.first_seen_at <= :as_of;
