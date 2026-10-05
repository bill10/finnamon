-- Depository account whose latest available balance is below its low_balance_threshold. Once per dip: the key is the
-- first balance under the threshold since the last one at or above it, so it alerts again only after the account
-- recovers and drops again.
SELECT
  acc.account_id,
  'low_balance' AS kind,
  'lowbal:' || acc.account_id || ':' || (
    SELECT min(x.as_of) FROM balances x
    WHERE x.account_id = acc.account_id AND x.as_of <= :as_of AND x.available < s.value
      AND x.as_of > COALESCE((SELECT max(y.as_of) FROM balances y
                              WHERE y.account_id = acc.account_id AND y.as_of <= :as_of AND y.available >= s.value), '')
  ) AS key,
  NULL AS transaction_id,
  json_object('account', acc.name, 'mask', acc.mask, 'available', b.available, 'threshold', s.value, 'as_of', :as_of) AS payload
FROM accounts acc
JOIN sv s ON s.account_id = acc.account_id AND s.key = 'low_balance_threshold'
JOIN balances b ON b.account_id = acc.account_id
 AND b.as_of = (SELECT max(as_of) FROM balances WHERE account_id = acc.account_id AND as_of <= :as_of)
WHERE acc.mirror_of IS NULL
  AND acc.type = 'depository'
  AND b.available IS NOT NULL
  AND b.available < s.value;
