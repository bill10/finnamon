-- `account merge` marks both copies joint; this remembers whose each one was so `account unmerge` can give it back.
ALTER TABLE accounts ADD COLUMN owner_before_merge TEXT;
