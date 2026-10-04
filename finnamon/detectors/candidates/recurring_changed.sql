-- A recurring stream whose last amount moved more than 20% from its average, or that skipped its predicted date by 7+ days.
-- Both branches only count what happened after the Item's first sync; streams Plaid marks inactive never count.
SELECT r.account_id, 'anomaly:recurring_changed' AS kind,
       'anom:rec:' || r.stream_id || ':' || strftime('%Y-%m', :as_of) AS key, NULL AS transaction_id,
       json_object('merchant', COALESCE(NULLIF(trim(r.merchant_name),''), NULLIF(trim(r.description),'')), 'avg_amount', r.avg_amount, 'last_amount', r.last_amount,
                   'last_date', r.last_date, 'predicted_next_date', r.predicted_next_date, 'frequency', r.frequency,
                   'account', acc.name, 'mask', acc.mask,
                   'change', CASE WHEN abs(r.last_amount - r.avg_amount) > 0.2 * abs(r.avg_amount) THEN 'amount' ELSE 'skipped' END,
                   'as_of', :as_of) AS payload
FROM recurring r
JOIN accounts acc ON acc.account_id = r.account_id AND acc.mirror_of IS NULL
JOIN items i ON i.item_id = acc.item_id
WHERE r.status NOT IN ('EARLY_DETECTION', 'TOMBSTONED') AND r.direction = 'outflow' AND r.avg_amount IS NOT NULL
  AND COALESCE(r.is_active, 1) = 1   -- Plaid says the stream stopped (cancelled): nothing is late
  AND r.first_seen_at <= :as_of
  AND (
        (abs(r.last_amount - r.avg_amount) > 0.2 * abs(r.avg_amount) AND r.last_date >= date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND r.last_date >= date(i.first_synced_at))
     OR (r.predicted_next_date IS NOT NULL AND r.predicted_next_date < date(:as_of, '-' || (SELECT text FROM g WHERE key='lookback_days') || ' days') AND r.last_date < r.predicted_next_date
         AND r.predicted_next_date >= date(i.first_synced_at))   -- a date missed before the first sync is backfill, not news
      )
  AND NOT EXISTS (
    SELECT 1 FROM sup WHERE sup.kind = 'anomaly:recurring_changed'   -- a kind-less "X is normal" means X charging is normal, not that X stopping is
      AND (sup.stream_id IS NULL OR sup.stream_id = r.stream_id)
      AND (sup.canonical IS NULL OR sup.canonical = COALESCE(r.merchant_entity_id, r.merchant_name, r.description))
      AND (sup.account_id IS NULL OR sup.account_id = r.account_id)
  );
