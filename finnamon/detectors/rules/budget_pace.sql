-- Net spend per budget over its period's current window (a calendar month for a monthly budget).
-- Monthly: fires :pace once (projection >= limit, spend < limit) and :over once (spend >= limit). Silent before
-- budget_min_day. Recurring charges (a bill, a subscription) count once, only the rest is projected; a fixed budget is
-- never projected.
-- Bimonthly / quarterly (a bill every few months): never projected. :over once a window's spend reaches the limit;
-- :missing once a whole window went by with no charge and none has come in the next one by budget_min_day.
-- Yearly (lumpy spending): never projected. One alert as spend passes 50%, 75% and 100% (:over) of the year's limit.
-- KEEP IN STEP WITH budgets.py (SPEND, MATCH, RECURRING, and window(): the `win` CTE below).
WITH month AS (
  SELECT CAST(strftime('%d', :as_of) AS INTEGER) AS day,
         CAST(strftime('%d', date(:as_of, 'start of month', '+1 month', '-1 day')) AS INTEGER) AS days
),
win AS (   -- budgets.window(): windows start in the anchor month (NULL: January), every n months; 12 % n = 0
  SELECT b.*, p.n,
         date(:as_of, 'start of month', '-' || ((CAST(strftime('%m', :as_of) AS INTEGER) - COALESCE(b.anchor, 1) + 12) % p.n) || ' months') AS start
  FROM budgets b
  JOIN (SELECT 'monthly' AS period, 1 AS n UNION ALL SELECT 'bimonthly', 2 UNION ALL SELECT 'quarterly', 3 UNION ALL SELECT 'yearly', 12) p ON p.period = b.period
  WHERE b.active = 1
),
w AS (
  SELECT win.*, date(start, '+' || n || ' months', '-1 day') AS end_,
         date(start, '-' || n || ' months') AS prev_start
  FROM win
),
spend AS (
  SELECT w.id, w.name, w.category, w.monthly_limit, w.fixed, w.period, w.created_at, w.start, w.end_, w.prev_start,
         COALESCE(SUM(CASE WHEN t.date >= w.start THEN t.amount END), 0) AS mtd,
         COUNT(CASE WHEN t.date >= w.start AND t.amount > 0 THEN 1 END) AS cur_n,
         COUNT(CASE WHEN t.date < w.start AND t.amount > 0 THEN 1 END) AS prev_n,
         COALESCE(SUM(CASE WHEN t.date >= w.start AND (t.flow = 'mortgage' OR EXISTS (   -- budgets.RECURRING (not :as_of-scoped: the run's as_of predates its sync)
                SELECT 1 FROM recurring r WHERE r.direction = 'outflow' AND COALESCE(r.is_active, 1) = 1
                  AND COALESCE(r.status, '') NOT IN ('TOMBSTONED', 'EARLY_DETECTION') AND r.frequency IN ('MONTHLY', 'ANNUALLY')
                  AND (r.merchant_entity_id = t.merchant_entity_id OR lower(r.merchant_name) = lower(t.merchant_name) OR (r.description = t.name AND r.account_id = t.account_id))
                  AND (abs(abs(t.amount) - r.avg_amount) <= 0.25 * abs(r.avg_amount) OR abs(abs(t.amount) - r.last_amount) <= 0.25 * abs(r.last_amount))))
             THEN t.amount END), 0) AS recurring
  FROM w
  LEFT JOIN tx t ON t.date >= CASE WHEN w.period IN ('bimonthly', 'quarterly') THEN w.prev_start ELSE w.start END   -- a bill's window and the one before
                AND t.date <= date(:as_of) AND t.pending = 0
                AND t.flow IN ('expense','refund','mortgage')   -- budgets.SPEND
                AND EXISTS (SELECT 1 FROM budget_selectors s WHERE s.budget_id = w.id     -- budgets.MATCH: any selector, counted once
                              AND ((s.kind = 'category' AND ((t.flow <> 'mortgage' AND s.value IN (t.category, t.category_primary))
                                   OR (t.flow = 'mortgage' AND s.value IN ('LOAN_PAYMENTS_MORTGAGE_PAYMENT', 'LOAN_PAYMENTS'))))   -- paired with the loan, whatever Plaid filed it under
                                OR (s.kind = 'merchant' AND (lower(s.value) IN (lower(t.canonical), lower(t.display), lower(t.name))
                                                             OR lower(COALESCE(s.label, s.value)) IN (lower(t.canonical), lower(t.display), lower(t.name))))))
  GROUP BY w.id
),
calc AS (
  SELECT s.*, month.day, month.days,
         CASE WHEN s.period = 'monthly' AND month.day >= (SELECT value FROM g WHERE key='budget_min_day')
              THEN CASE WHEN s.fixed THEN s.mtd ELSE MAX(s.mtd, s.recurring + (s.mtd - s.recurring) * month.days / month.day) END END AS projection
  FROM spend s, month
),
st AS (
  SELECT c.*,
         CASE WHEN mtd >= monthly_limit THEN 'over'
              WHEN period = 'monthly' AND projection IS NOT NULL AND projection >= monthly_limit THEN 'pace'
              WHEN period = 'yearly' AND mtd >= 0.75 * monthly_limit THEN '75'
              WHEN period = 'yearly' AND mtd >= 0.5 * monthly_limit THEN '50'
              WHEN period IN ('bimonthly', 'quarterly') AND prev_n = 0 AND cur_n = 0 AND created_at < prev_start
                   AND day >= (SELECT value FROM g WHERE key='budget_min_day') THEN 'missing'
         END AS tag
  FROM calc c
)
SELECT NULL AS account_id, 'budget_pace' AS kind,
       'budget:' || id || ':' || strftime('%Y-%m', CASE tag WHEN 'missing' THEN prev_start ELSE start END) || ':' || tag AS key,
       NULL AS transaction_id,
       json_object('budget', name, 'category', NULLIF(category, ''), 'limit', monthly_limit, 'spent', round(mtd, 2), 'recurring', round(recurring, 2),
                   'projection', round(projection, 2), 'day', day, 'days', days,
                   'state', CASE WHEN tag IN ('50', '75') THEN 'used' ELSE tag END, 'used_pct', CASE WHEN tag IN ('50', '75') THEN CAST(tag AS INTEGER) END,
                   'period', period, 'left', round(monthly_limit - mtd, 2),
                   'window_start', CASE tag WHEN 'missing' THEN prev_start ELSE start END,
                   'window_end', CASE tag WHEN 'missing' THEN date(start, '-1 day') ELSE end_ END, 'as_of', :as_of) AS payload
FROM st
WHERE tag IS NOT NULL;
