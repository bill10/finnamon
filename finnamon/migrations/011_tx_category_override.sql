-- A one-time category edit on a single transaction (`finnamon category --tx <transaction_id> <category>`), apart
-- from the merchant rule in category_override (`finnamon category <merchant> <category>`, every past and future
-- charge of that merchant). The one-time edit wins over the merchant rule, which wins over Plaid's category.
--
-- The views below are 008's, unchanged except for the `x` join and the COALESCEs that put it first.
-- KEEP IN STEP WITH finnamon/detectors/_prelude.sql (which scopes `x` on created_at <= :as_of, like `o`).
CREATE TABLE IF NOT EXISTS tx_category_override (
  transaction_id TEXT PRIMARY KEY,               -- transactions.transaction_id; no FK: a row whose transaction is gone just matches nothing
  pfc_primary    TEXT NOT NULL,
  pfc_detailed   TEXT NOT NULL,
  created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

DROP VIEW IF EXISTS tx_now;
DROP VIEW IF EXISTS tx_all_accounts;

-- The resolution itself, over every account. Only notify.py's linked-bank summary reads this one: it is
-- scoped to a single Item, and `finnamon link` marks a duplicate account as a mirror (cli.py, mark_mirror)
-- *before* it sends that summary, so excluding mirrors there would report the bank it just linked as empty.
-- Anything counting the household's money wants tx_now instead, or it double-counts a joint account.
CREATE VIEW tx_all_accounts AS
SELECT r.*,
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
  END AS flow
FROM (
  SELECT
    t.transaction_id, t.account_id, acc.item_id, acc.owner, acc.type AS account_type, acc.subtype AS account_subtype,
    acc.name AS account_name, acc.mask, acc.mirror_of,
    i.first_synced_at, i.source,
    t.date, t.datetime, t.amount, t.name, t.merchant_name, t.merchant_entity_id, t.pfc_confidence, t.pending,
    t.pfc_primary, t.pfc_detailed, t.alias_canonical,
    COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name)    AS canonical,
    COALESCE(t.alias_canonical, t.merchant_name, t.name)                          AS display,
    COALESCE(x.pfc_detailed, o.pfc_detailed, t.pfc_detailed)                     AS category,
    COALESCE(x.pfc_primary,  o.pfc_primary,  t.pfc_primary)                      AS category_primary,
    x.transaction_id IS NOT NULL OR o.canonical IS NOT NULL                       AS overridden
  FROM (
    SELECT t.*,
           (SELECT a.canonical FROM merchant_alias a
             WHERE (a.name = t.name OR (instr(a.name, '%') > 0 AND t.name LIKE a.name))
             ORDER BY instr(a.name, '%') > 0, length(a.name) DESC, a.name LIMIT 1) AS alias_canonical   -- exact (no %) first, then the longest pattern, then by name so a tie is stable
    FROM transactions t
  ) t
  JOIN accounts acc ON acc.account_id = t.account_id
  JOIN items i ON i.item_id = acc.item_id
  LEFT JOIN category_override o
         ON o.canonical = COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name)
  LEFT JOIN tx_category_override x ON x.transaction_id = t.transaction_id
) r;

-- The one to query. Same rule, one copy of it, mirrors dropped; `mirror_of` and `overridden` are not re-exposed so the
-- column list stays exactly the prelude's `tx` minus `suppressed` (tests/test_detectors.py asserts that).
CREATE VIEW tx_now AS
SELECT transaction_id, account_id, item_id, owner, account_type, account_subtype, account_name, mask,
       first_synced_at, source, date, datetime, amount, name, merchant_name, merchant_entity_id,
       pfc_confidence, pending, pfc_primary, pfc_detailed, alias_canonical, canonical, display,
       category, category_primary, flow
FROM tx_all_accounts WHERE mirror_of IS NULL;
