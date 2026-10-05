---
bump: minor
---
### Added
- **Reconnect on the dashboard.** A bank that needs a re-login (or whose connection is about to expire) now has a
  Reconnect button on its alert, in place of "It's normal", and beside "needs a new login" in Accounts. It opens Plaid's
  login page for that bank in a new tab, with no trip through Telegram; a double click reuses the page it just opened.
  The alert on the page no longer says "Reply fix …"; the Telegram message is unchanged.

### Fixed
- **A finished re-login syncs right away.** After logging back in through Plaid (from the button or the chat's
  "fix Chase" link), the bank used to stay broken, and its alert open, until the next scheduled sync, up to 6 hours
  later. The daemon now notices within a minute, syncs that bank, resolves its alert as Reconnected, and says
  "Chase is reconnected." in the chat. A login page closed without logging in is dropped quietly.
