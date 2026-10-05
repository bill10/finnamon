---
bump: patch
---
### Fixed
- **One transaction is one alert, however many detectors fire on it.** An HSA deposit tripped both `no_source` and
  `unmatched_transfer` and the chat got the same line twice. Now every alert on one transaction goes out as one message
  (keeping the clearest reason), and all of them are marked sent together. A detector that fires later on a transaction
  already told folds into that message instead of sending another. An alert from a detector that recorded no
  transaction id is matched on account, date, amount and name.
- **Triage gives a transaction one verdict.** `finnamon triage set <key>` with any one candidate's key now stamps
  every candidate on that transaction, so one detector can no longer be promoted while the other is suppressed. A detector that
  fires after the transaction was judged (or resolved) takes that verdict (and that resolution).
- **The alerts list and the dashboard show one alert per transaction.** The others are listed under `folded`. Dismiss,
  `normal --alert` and Undo on it cover every alert on the transaction. A reply to the message counts as a reply to one alert.
