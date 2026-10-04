-- tx_now: the prelude's `tx` resolution (alias, category override, mirrors excluded) as a view, so the
-- paths that cannot go through detectors/_prelude.sql get it too -- above all `finnamon query`, which runs
-- arbitrary SQL from the assistant and had no resolved relation to select from (issue 23).
--
-- KEEP IN STEP WITH finnamon/detectors/_prelude.sql. Same columns and the same COALESCE order, minus
-- `suppressed` (needs the detector's kind) and minus the :as_of scoping: a detector must replay as of an
-- alert's time, a question about today's money wants today's aliases. A change here needs a new migration
-- (DROP VIEW IF EXISTS / CREATE VIEW); this one only ever runs once.
--
-- ponytail: alias_canonical is read four times below, and SQLite flattens the subquery, so the merchant_alias
-- scan runs four times per row. The prelude pays the same cost on the sync's detector run; here it lands on
-- interactive paths too (the chart panel, an MCP tool call, a query typed in the chat). Materialise it -- a
-- trigger-maintained column on transactions, or a refreshed table -- if a chart ever feels slow to draw.
DROP VIEW IF EXISTS tx_now;
DROP VIEW IF EXISTS tx_all_accounts;

-- The resolution itself, over every account. Only notify.py's linked-bank summary reads this one: it is
-- scoped to a single Item, and `finnamon link` marks a duplicate account as a mirror (cli.py, mark_mirror)
-- *before* it sends that summary, so excluding mirrors there would report the bank it just linked as empty.
-- Anything counting the household's money wants tx_now instead, or it double-counts a joint account.
CREATE VIEW tx_all_accounts AS
SELECT
  t.transaction_id, t.account_id, acc.item_id, acc.owner, acc.type AS account_type, acc.subtype AS account_subtype,
  acc.name AS account_name, acc.mask, acc.mirror_of,
  i.first_synced_at, i.source,
  t.date, t.datetime, t.amount, t.name, t.merchant_name, t.merchant_entity_id, t.pfc_confidence, t.pending,
  t.pfc_primary, t.pfc_detailed, t.alias_canonical,
  COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name)    AS canonical,
  COALESCE(t.alias_canonical, t.merchant_name, t.name)                          AS display,
  COALESCE(o.pfc_detailed, t.pfc_detailed)                                      AS category,
  COALESCE(o.pfc_primary,  t.pfc_primary)                                       AS category_primary
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
       ON o.canonical = COALESCE(t.merchant_entity_id, t.alias_canonical, t.merchant_name, t.name);

-- The one to query. Same rule, one copy of it, mirrors dropped; `mirror_of` is not re-exposed so the column
-- list stays exactly the prelude's `tx` minus `suppressed` (tests/test_detectors.py asserts that).
CREATE VIEW tx_now AS
SELECT transaction_id, account_id, item_id, owner, account_type, account_subtype, account_name, mask,
       first_synced_at, source, date, datetime, amount, name, merchant_name, merchant_entity_id,
       pfc_confidence, pending, pfc_primary, pfc_detailed, alias_canonical, canonical, display,
       category, category_primary
FROM tx_all_accounts WHERE mirror_of IS NULL;
