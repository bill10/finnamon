---
bump: patch
---
### Fixed
- The Accounts list shows Reconnect on a bank that is stale or failing, the same banks the alerts do. The server flags each bank in `finnamon status` (`reconnect`), so the page keeps no list of its own.
