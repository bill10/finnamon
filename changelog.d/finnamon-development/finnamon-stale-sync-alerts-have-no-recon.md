---
bump: patch
---
### Fixed
- **Reconnect on every "bank isn't syncing" alert.** The dashboard's Reconnect button showed only when Plaid said the bank wanted a re-login. A bank that stops syncing for another reason (stale, or a "couldn't sync" error) now gets it too, so the fix is one click instead of a terminal command. A stale bank keeps It's normal and Dismiss; manual accounts never get Reconnect.
- **Stale-bank alerts say what went wrong.** A bank whose syncs keep failing with a temporary Plaid error (INSTITUTION_NOT_RESPONDING and the like) stayed marked good with no trace of the error, so the alert said only "hasn't synced" and `finnamon doctor` showed nothing. The last error is now kept on the bank until it syncs again: the alert names it ("The last try got …"), and doctor lists it.
