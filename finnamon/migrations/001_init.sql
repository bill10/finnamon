-- Finnamon schema. Everything an agent needs; nothing the agent is.
-- Amount sign is Plaid's: positive = money out, negative = money in. Never negate on write.

CREATE TABLE owners (
  owner            TEXT PRIMARY KEY,           -- 'bill', 'jane'; 'joint' is an account owner value, not a row
  telegram_user_id INTEGER,
  display_name     TEXT
);

CREATE TABLE items (
  item_id          TEXT PRIMARY KEY,
  institution_id   TEXT,
  institution      TEXT,
  cursor           TEXT,
  owner            TEXT NOT NULL REFERENCES owners(owner),
  status           TEXT NOT NULL DEFAULT 'good',   -- 'good' | plaid error_code
  last_error       TEXT,
  first_synced_at  TEXT,                          -- set on the first sync that returns transactions
  last_synced_at   TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE accounts (
  account_id            TEXT PRIMARY KEY,
  item_id               TEXT NOT NULL REFERENCES items(item_id),
  name                  TEXT,
  official_name         TEXT,
  type                  TEXT,
  subtype               TEXT,
  mask                  TEXT,
  persistent_account_id TEXT,
  owner                 TEXT NOT NULL,            -- 'bill' | 'jane' | 'joint'
  mirror_of             TEXT REFERENCES accounts(account_id)  -- same real account as this one; hidden by the prelude
);

CREATE TABLE balances (
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  as_of      TEXT NOT NULL,
  current    REAL,
  available  REAL,
  PRIMARY KEY (account_id, as_of)
);

CREATE TABLE transactions (
  transaction_id         TEXT PRIMARY KEY,
  account_id             TEXT NOT NULL REFERENCES accounts(account_id),
  date                   TEXT NOT NULL,
  datetime               TEXT,
  amount                 REAL NOT NULL,
  name                   TEXT,
  merchant_name          TEXT,
  merchant_entity_id     TEXT,
  pfc_primary            TEXT,
  pfc_detailed           TEXT,
  pfc_confidence         TEXT,
  pending                INTEGER NOT NULL DEFAULT 0,
  pending_transaction_id TEXT,
  location_city          TEXT,
  location_region        TEXT,
  raw_json               TEXT
);

CREATE TABLE recurring (
  stream_id           TEXT PRIMARY KEY,
  account_id          TEXT NOT NULL REFERENCES accounts(account_id),
  direction           TEXT NOT NULL,             -- 'inflow' | 'outflow'
  merchant_entity_id  TEXT,
  merchant_name       TEXT,
  description         TEXT,
  frequency           TEXT,
  avg_amount          REAL,
  last_amount         REAL,
  first_date          TEXT,
  last_date           TEXT,
  predicted_next_date TEXT,
  status              TEXT,
  first_seen_at       TEXT NOT NULL
);

CREATE TABLE merchant_alias (
  name       TEXT PRIMARY KEY,                    -- raw transactions.name
  canonical  TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE category_override (
  canonical    TEXT PRIMARY KEY,
  pfc_primary  TEXT NOT NULL,
  pfc_detailed TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE suppressions (
  id         INTEGER PRIMARY KEY,
  kind       TEXT,                                -- NULL = any kind
  canonical  TEXT,                                -- NULL = any merchant
  account_id TEXT,                                -- NULL = any account
  max_amount REAL,                                -- NULL = any amount
  note       TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  CHECK (canonical IS NOT NULL OR account_id IS NOT NULL OR (kind IS NOT NULL AND max_amount IS NOT NULL))  -- never an all-wildcard row
);

CREATE TABLE settings (
  account_id TEXT NOT NULL DEFAULT '*',           -- '*' = global; a real account_id overrides
  key        TEXT NOT NULL,
  value      TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (account_id, key)
);

CREATE TABLE budgets (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  category      TEXT NOT NULL,                    -- a pfc_primary or a pfc_detailed
  monthly_limit REAL NOT NULL,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE alerts (
  id                  INTEGER PRIMARY KEY,
  tier                TEXT NOT NULL,              -- 'rule' | 'anomaly'
  kind                TEXT NOT NULL,
  key                 TEXT NOT NULL UNIQUE,
  account_id          TEXT,
  transaction_id      TEXT,
  payload_json        TEXT NOT NULL,
  as_of               TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  verdict             TEXT,                       -- anomaly only: 'promote' | 'suppress'
  confidence          TEXT,                       -- anomaly only: 'high' | 'low'
  reason              TEXT,
  triage_attempts     INTEGER NOT NULL DEFAULT 0,
  sent_at             TEXT,
  telegram_chat_id    TEXT,                       -- message ids are per chat; both are needed to resolve a reply
  telegram_message_id INTEGER,
  resolved_at         TEXT
);

CREATE TABLE roundup_items (
  telegram_chat_id    TEXT NOT NULL,
  telegram_message_id INTEGER NOT NULL,
  n                   INTEGER NOT NULL,
  alert_id            INTEGER NOT NULL REFERENCES alerts(id),
  PRIMARY KEY (telegram_chat_id, telegram_message_id, n)
);

CREATE TABLE feedback (
  id            INTEGER PRIMARY KEY,
  alert_id      INTEGER REFERENCES alerts(id),
  owner         TEXT,
  text          TEXT,
  parsed_action TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE holdings (
  account_id  TEXT NOT NULL REFERENCES accounts(account_id),
  security_id TEXT NOT NULL,
  as_of       TEXT NOT NULL,
  name        TEXT,
  ticker      TEXT,
  quantity    REAL,
  price       REAL,
  value       REAL,
  PRIMARY KEY (account_id, security_id, as_of)
);

CREATE TABLE state (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- Indexes: every detector's access pattern. test_detectors asserts no full scan of transactions.
CREATE INDEX idx_txn_account_date ON transactions(account_id, date);
CREATE INDEX idx_txn_entity       ON transactions(merchant_entity_id);
CREATE INDEX idx_txn_name         ON transactions(name);
CREATE INDEX idx_txn_pending_id   ON transactions(pending_transaction_id);
CREATE INDEX idx_bal_account_asof ON balances(account_id, as_of);
CREATE INDEX idx_alerts_txn       ON alerts(transaction_id);
CREATE INDEX idx_alerts_msg       ON alerts(telegram_chat_id, telegram_message_id);
CREATE INDEX idx_alerts_unsent    ON alerts(sent_at) WHERE sent_at IS NULL;

-- Defaults. Detectors read these through the prelude's settings CTE.
INSERT INTO settings(account_id, key, value) VALUES
  ('*', 'dup_min_amount', '10'),
  ('*', 'dup_window_days', '3'),
  ('*', 'budget_min_day', '5'),
  ('*', 'health_max_age_hours', '12'),
  ('*', 'outlier_multiplier', '3'),
  ('*', 'outlier_min_amount', '25'),
  ('*', 'large_amount', '200'),
  ('*', 'xfer_window_days', '5'),
  ('*', 'lookback_days', '7'),
  ('*', 'sync_interval_hours', '6'),
  ('*', 'claude_timeout_seconds', '120');
