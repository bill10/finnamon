-- Month-to-date net spend per budget vs a projection. Fires :pace once (projection >= limit, spend < limit)
-- and :over once (spend >= limit). Silent before budget_min_day. Recurring charges (a bill, a subscription) count once,
-- only the rest is projected; a fixed budget is never projected. KEEP IN STEP WITH budgets.py (SPEND, MATCH, RECURRING).
WITH month AS (
  SELECT date(:as_of, 'start of month') AS start,
         CAST(strftime('%d', :as_of) AS INTEGER) AS day,
         CAST(strftime('%d', date(:as_of, 'start of month', '+1 month', '-1 day')) AS INTEGER) AS days
),
spend AS (
  SELECT b.id, b.name, b.category, b.monthly_limit, b.fixed,
         COALESCE(SUM(t.amount), 0) AS mtd,
         COALESCE(SUM(CASE WHEN t.flow = 'mortgage' OR EXISTS (   -- budgets.RECURRING (not :as_of-scoped: the run's as_of predates its sync)
                SELECT 1 FROM recurring r WHERE r.direction = 'outflow' AND COALESCE(r.is_active, 1) = 1
                  AND COALESCE(r.status, '') NOT IN ('TOMBSTONED', 'EARLY_DETECTION') AND r.frequency IN ('MONTHLY', 'ANNUALLY')
                  AND (r.merchant_entity_id = t.merchant_entity_id OR lower(r.merchant_name) = lower(t.merchant_name) OR (r.description = t.name AND r.account_id = t.account_id))
                  AND (abs(abs(t.amount) - r.avg_amount) <= 0.25 * abs(r.avg_amount) OR abs(abs(t.amount) - r.last_amount) <= 0.25 * abs(r.last_amount)))
             THEN t.amount END), 0) AS recurring
  FROM budgets b, month
  LEFT JOIN tx t ON t.date >= month.start AND t.date <= date(:as_of) AND t.pending = 0
                AND t.flow IN ('expense','refund','mortgage')   -- budgets.SPEND
                AND EXISTS (SELECT 1 FROM budget_selectors s WHERE s.budget_id = b.id     -- budgets.MATCH: any selector, counted once
                              AND ((s.kind = 'category' AND (s.value IN (t.category, t.category_primary)
                                   OR (t.flow = 'mortgage' AND s.value IN ('LOAN_PAYMENTS_MORTGAGE_PAYMENT', 'LOAN_PAYMENTS'))))   -- paired with the loan, whatever Plaid filed it under
                                OR (s.kind = 'merchant' AND (lower(s.value) IN (lower(t.canonical), lower(t.display), lower(t.name))
                                                             OR lower(COALESCE(s.label, s.value)) IN (lower(t.canonical), lower(t.display), lower(t.name))))))
  WHERE b.active = 1
  GROUP BY b.id
),
calc AS (
  SELECT s.*, month.day, month.days,
         CASE WHEN month.day >= (SELECT value FROM g WHERE key='budget_min_day')
              THEN CASE WHEN s.fixed THEN s.mtd ELSE MAX(s.mtd, s.recurring + (s.mtd - s.recurring) * month.days / month.day) END END AS projection
  FROM spend s, month
)
SELECT NULL AS account_id, 'budget_pace' AS kind,
       'budget:' || id || ':' || strftime('%Y-%m', :as_of) || CASE WHEN mtd >= monthly_limit THEN ':over' ELSE ':pace' END AS key,
       NULL AS transaction_id,
       json_object('budget', name, 'category', NULLIF(category, ''), 'limit', monthly_limit, 'spent', round(mtd, 2), 'recurring', round(recurring, 2),
                   'projection', round(projection, 2), 'day', day, 'days', days,
                   'state', CASE WHEN mtd >= monthly_limit THEN 'over' ELSE 'pace' END, 'as_of', :as_of) AS payload
FROM calc
WHERE mtd >= monthly_limit
   OR (projection IS NOT NULL AND projection >= monthly_limit AND mtd < monthly_limit);
