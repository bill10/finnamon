---
bump: patch
---
### Fixed
- **Reconnect on every "bank isn't syncing" alert.** The dashboard's Reconnect button showed only when Plaid said the bank wanted a re-login. Now it also shows when a bank has gone quiet (stale) or couldn't sync, so the fix is one click instead of a terminal command. A stale bank keeps It's normal and Dismiss, since a quiet account can be normal. Manual accounts never get Reconnect, and neither do problems a login can't fix (a missing or revoked connection, or a fault on Finnamon's side).
- **Stale-bank alerts say what went wrong.** A bank whose syncs kept failing with a temporary Plaid error (the bank not responding, an outage at Plaid) stayed marked good with no record of the error. The alert said only "hasn't synced", and `finnamon doctor` showed nothing. Finnamon now keeps the last error on the bank until it syncs again: the alert names it in plain words ("The last try failed: the bank isn't responding"), and doctor lists it with the fix.
- **A Reconnect closed during an outage no longer retries every minute.** If someone opens Reconnect on a quiet bank and closes it without logging in while Plaid is down, Finnamon now leaves it to the next scheduled sync. Before, it re-synced that bank every minute for an hour.
