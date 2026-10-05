-- An Item that errored, or hasn't synced in health_max_age_hours; a manual Item nobody has imported into for import_max_age_days.
-- Item-derived: replay from payload_json, not from the (upserted) items table.
-- One alert per problem, not per day: the key is the problem (an error status, or stale) and the last good sync, so it
-- stays one alert until the bank syncs again; detect.resolve_recovered closes it then, and when a newer problem replaces it.
SELECT
  NULL AS account_id,
  'sync_health' AS kind,
  'health:' || i.item_id || ':' || CASE WHEN i.status <> 'good' THEN i.status WHEN i.source = 'manual' THEN 'import' ELSE 'stale' END
           || ':' || COALESCE(i.last_synced_at, i.created_at) AS key,
  NULL AS transaction_id,
  json_object('item_id', i.item_id, 'institution', i.institution, 'status', i.status, 'source', i.source,
              'last_error', i.last_error, 'last_synced_at', i.last_synced_at, 'as_of', :as_of, 'owner', i.owner,
              'duplicate', EXISTS (SELECT 1 FROM items j WHERE j.institution = i.institution AND j.item_id <> i.item_id AND j.source = 'plaid')) AS payload
FROM items i
WHERE NOT EXISTS (   -- the same error still open: a sync that applies a page and then fails moves last_synced_at, and must not re-alert
    SELECT 1 FROM alerts a WHERE a.kind = 'sync_health' AND a.resolved_at IS NULL AND i.status <> 'good'
      AND json_extract(a.payload_json, '$.item_id') = i.item_id AND json_extract(a.payload_json, '$.status') = i.status)
  AND (i.status <> 'good'
   OR (i.source = 'plaid' AND COALESCE(i.last_synced_at, i.created_at) < datetime(:as_of, '-' || (SELECT text FROM g WHERE key='health_max_age_hours') || ' hours'))
   OR (i.source = 'manual' AND COALESCE(i.last_synced_at, i.created_at) < datetime(:as_of, '-' || (SELECT text FROM g WHERE key='import_max_age_days') || ' days')));
