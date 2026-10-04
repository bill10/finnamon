-- Shared prelude: detect.py prepends this to every detector. Detectors SELECT FROM tx / g / sv / sup.
-- Bound parameter :as_of (local 'YYYY-MM-DD HH:MM:SS'); __KIND__ is replaced with the detector's kind.
--
--   transactions ──┬── accounts (mirror_of IS NULL)  ── items (first_synced_at, source: 'plaid' | 'manual')
--                  ├── merchant_alias   (name = t.name, or t.name LIKE a %pattern; exact first, then the longest;
--                  │                     created_at <= as_of)                              → alias_canonical
--                  ├── category_override(canonical,           created_at <= as_of)   → category, category_primary
--                  └── suppressions     (kind/canonical/acct/amount, created_at <= as_of) → suppressed
--
-- Time-scoping every join on :as_of is what makes an alert replayable from the DB. That is the one difference from
-- the `tx_now` view (migrations/005_tx_now_view.sql, `flow` added by 008_tx_flow.sql), which every non-detector read path uses;
-- KEEP THE TWO IN STEP.
WITH
g AS (                                    -- global settings: (SELECT value FROM g WHERE key='x')
  SELECT key, CAST(value AS REAL) AS value, value AS text FROM settings WHERE account_id = '*'
),
sv AS (                                   -- per-account settings: join on account_id
  SELECT account_id, key, CAST(value AS REAL) AS value, value AS text FROM settings WHERE account_id <> '*'
),
sup AS (
  SELECT * FROM suppressions WHERE created_at <= :as_of
),
tx0 AS (
  SELECT
    t.transaction_id, t.account_id, acc.item_id, acc.owner, acc.type AS account_type, acc.subtype AS account_subtype,
    acc.name AS account_name, acc.mask,
    i.first_synced_at, i.source,
    t.date, t.datetime, t.amount, t.name, t.merchant_name, t.merchant_entity_id, t.pfc_confidence, t.pending,
    t.pfc_primary, t.pfc_detailed, t.alias_canonical,
    COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name)    AS canonical,
    COALESCE(t.alias_canonical, t.merchant_name, t.name)                          AS display,
    COALESCE(o.pfc_detailed, t.pfc_detailed)                                      AS category,
    COALESCE(o.pfc_primary,  t.pfc_primary)                                       AS category_primary,
    o.canonical IS NOT NULL                                                       AS overridden,
    EXISTS (
      SELECT 1 FROM sup
      WHERE (sup.kind IS NULL OR sup.kind = '__KIND__')
        AND (sup.canonical IS NULL OR sup.canonical = COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name))
        AND (sup.account_id IS NULL OR sup.account_id = t.account_id)
        AND (sup.max_amount IS NULL OR sup.max_amount >= t.amount)
    ) AS suppressed
  FROM (
    SELECT t.*,                                                   -- ponytail: SQLite flattens this, so the subquery runs per use site (~2x a detector run at 50k rows x 30 aliases);
           (SELECT a.canonical FROM merchant_alias a               --   materialise it if a run nears a minute. An alias whose name holds a % is a LIKE pattern
             WHERE a.created_at <= :as_of
               AND (a.name = t.name OR (instr(a.name, '%') > 0 AND t.name LIKE a.name))
             ORDER BY instr(a.name, '%') > 0, length(a.name) DESC, a.name LIMIT 1) AS alias_canonical   -- exact (no %) first, then the longest pattern, then by name so a tie is stable
    FROM transactions t
  ) t
  JOIN accounts acc ON acc.account_id = t.account_id AND acc.mirror_of IS NULL
  JOIN items i ON i.item_id = acc.item_id
  LEFT JOIN category_override o
         ON o.canonical = COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name)
        AND o.created_at <= :as_of
),
tx AS (                                   -- tx0 plus `flow` (migrations/008_tx_flow.sql: what each value means; the CASE is a verbatim copy)
  SELECT transaction_id, account_id, item_id, owner, account_type, account_subtype, account_name, mask,
       first_synced_at, source, date, datetime, amount, name, merchant_name, merchant_entity_id,
       pfc_confidence, pending, pfc_primary, pfc_detailed, alias_canonical, canonical, display,
       category, category_primary,
  CASE
    WHEN r.account_type IS NULL OR r.account_type NOT IN ('depository', 'credit') THEN 'skipped'
    WHEN r.overridden AND r.category IN ('TRANSFER_OUT_ACCOUNT_TRANSFER', 'TRANSFER_OUT_SAVINGS', 'TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS',
                                         'TRANSFER_IN_ACCOUNT_TRANSFER', 'TRANSFER_IN_SAVINGS', 'TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS') THEN 'transfer'
    WHEN r.amount > 0 AND r.account_type = 'depository' AND (r.category = 'LOAN_PAYMENTS_MORTGAGE_PAYMENT' OR EXISTS (
           SELECT 1 FROM transactions u JOIN accounts ua ON ua.account_id = u.account_id AND ua.mirror_of IS NULL JOIN items ui ON ui.item_id = ua.item_id
           WHERE u.amount = -r.amount AND u.account_id <> r.account_id AND u.date BETWEEN date(r.date, '-5 days') AND date(r.date, '+5 days')
             AND ua.type = 'loan' AND COALESCE(ua.subtype, 'mortgage') IN ('mortgage', 'home equity', 'loan')
             AND (u.pfc_primary IS NOT NULL OR ui.source <> 'manual') AND (r.category_primary IS NOT NULL OR r.source <> 'manual'))) THEN 'mortgage'
    WHEN r.amount > 0 AND (r.category_primary IN ('TRANSFER_OUT', 'LOAN_PAYMENTS') OR (r.category_primary IS NULL AND r.source <> 'manual')) AND EXISTS (
           SELECT 1 FROM transactions u JOIN accounts ua ON ua.account_id = u.account_id AND ua.mirror_of IS NULL JOIN items ui ON ui.item_id = ua.item_id
           WHERE u.amount = -r.amount AND u.account_id <> r.account_id AND u.date BETWEEN date(r.date, '-5 days') AND date(r.date, '+5 days')
             AND ua.type IN ('depository', 'credit') AND (u.pfc_primary IS NOT NULL OR ui.source <> 'manual')) THEN 'transfer'
    WHEN r.amount > 0 AND r.category IN ('TRANSFER_OUT_ACCOUNT_TRANSFER', 'TRANSFER_OUT_SAVINGS', 'TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS')
         AND lower(COALESCE(r.name, '')) NOT LIKE '%zelle%' THEN 'transfer'
    WHEN r.amount > 0 THEN 'expense'
    WHEN r.account_type = 'credit' THEN
      CASE WHEN r.category_primary IN ('LOAN_PAYMENTS', 'INCOME', 'TRANSFER_IN') OR EXISTS (
             SELECT 1 FROM transactions u JOIN accounts ua ON ua.account_id = u.account_id AND ua.mirror_of IS NULL JOIN items ui ON ui.item_id = ua.item_id
             WHERE u.amount = -r.amount AND u.account_id <> r.account_id AND u.date BETWEEN date(r.date, '-5 days') AND date(r.date, '+5 days')
               AND ua.type IN ('depository', 'credit') AND (u.pfc_primary IS NOT NULL OR ui.source <> 'manual')
               AND (r.category_primary IS NOT NULL OR r.source <> 'manual')) THEN 'card_payment' ELSE 'refund' END
    WHEN r.category IN ('TRANSFER_IN_ACCOUNT_TRANSFER', 'TRANSFER_IN_SAVINGS', 'TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS')
         AND lower(COALESCE(r.name, '')) NOT LIKE '%zelle%' THEN 'transfer'
    WHEN r.category_primary = 'TRANSFER_IN' AND EXISTS (
           SELECT 1 FROM transactions u JOIN accounts ua ON ua.account_id = u.account_id AND ua.mirror_of IS NULL JOIN items ui ON ui.item_id = ua.item_id
           WHERE u.amount = -r.amount AND u.account_id <> r.account_id AND u.date BETWEEN date(r.date, '-5 days') AND date(r.date, '+5 days')
             AND ua.type IN ('depository', 'credit') AND (u.pfc_primary IS NOT NULL OR ui.source <> 'manual')) THEN 'transfer'
    WHEN r.category_primary IS NULL OR r.category_primary IN ('INCOME', 'TRANSFER_IN', 'LOAN_PAYMENTS') THEN 'income'
    ELSE 'refund'
  END AS flow,
  suppressed
  FROM tx0 r
)
