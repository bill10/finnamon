-- Things the household owns that no bank reports: a house, a car. Stated values, counted into net worth.
CREATE TABLE properties (
  name       TEXT PRIMARY KEY,
  value      REAL NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
