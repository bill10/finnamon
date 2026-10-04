-- "House" and "house" are one property. Rebuild with NOCASE; where both spellings exist, the most recent row wins.
CREATE TABLE properties_nocase (
  name       TEXT PRIMARY KEY COLLATE NOCASE,
  value      REAL NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
INSERT INTO properties_nocase (name, value, updated_at)
  SELECT name, value, updated_at FROM properties p
  WHERE rowid = (SELECT rowid FROM properties q WHERE lower(q.name) = lower(p.name) ORDER BY updated_at DESC, rowid DESC LIMIT 1);
DROP TABLE properties;
ALTER TABLE properties_nocase RENAME TO properties;
