-- Month-to-date net spend per budget vs a linear projection. Fires :pace once (projection >= limit, spend < limit)
-- and :over once (spend >= limit). Silent before budget_min_day.
WITH month AS (
  SELECT date(:as_of, 'start of month') AS start,
         CAST(strftime('%d', :as_of) AS INTEGER) AS day,
         CAST(strftime('%d', date(:as_of, 'start of month', '+1 month', '-1 day')) AS INTEGER) AS days
),
spend AS (
  SELECT b.id, b.name, b.category, b.monthly_limit,
         COALESCE(SUM(CASE WHEN t.pending = 0
                            AND t.flow IN ('expense','refund')   -- budgets.SPEND
                            AND (t.category = b.category OR t.category_primary = b.category)
                           THEN t.amount END), 0) AS mtd
  FROM budgets b, month
  LEFT JOIN tx t ON t.date >= month.start AND t.date <= date(:as_of)
  WHERE b.active = 1
  GROUP BY b.id
),
calc AS (
  SELECT s.*, month.day, month.days,
         CASE WHEN month.day >= (SELECT value FROM g WHERE key='budget_min_day')
              THEN s.mtd * month.days / month.day END AS projection
  FROM spend s, month
)
SELECT NULL AS account_id, 'budget_pace' AS kind,
       'budget:' || id || ':' || strftime('%Y-%m', :as_of) || CASE WHEN mtd >= monthly_limit THEN ':over' ELSE ':pace' END AS key,
       NULL AS transaction_id,
       json_object('budget', name, 'category', category, 'limit', monthly_limit, 'spent', round(mtd, 2),
                   'projection', round(projection, 2), 'day', day, 'days', days,
                   'state', CASE WHEN mtd >= monthly_limit THEN 'over' ELSE 'pace' END, 'as_of', :as_of) AS payload
FROM calc
WHERE mtd >= monthly_limit
   OR (projection IS NOT NULL AND projection >= monthly_limit AND mtd < monthly_limit);
